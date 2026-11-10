"""Asset discovery, immutable promotion planning, and execution."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from lamp_ops.config import (
    AssetConfig,
    AssetGroup,
    AssetMode,
    AssetTargetConfig,
    LampConfig,
    TargetConfig,
)
from lamp_ops.errors import LampError
from lamp_ops.replacements import replace_strings

AssetKind = Literal["dashboards", "genie"]
Action = Literal["create", "update", "skip"]
SUFFIXES: dict[AssetKind, str] = {
    "dashboards": ".lvdash.json",
    "genie": ".geniespace.json",
}


@dataclass(frozen=True)
class DiscoveredAsset:
    kind: AssetKind
    identifier: str
    source: Path
    config: AssetConfig


@dataclass(frozen=True)
class PromotionTask:
    kind: AssetKind
    identifier: str
    target_name: str
    source: Path
    target: Path
    substitutions: tuple[tuple[str, str], ...]
    action: Action
    reason: str


@dataclass(frozen=True)
class PromotionPlan:
    tasks: tuple[PromotionTask, ...]

    @property
    def totals(self) -> dict[str, int]:
        return {
            action: sum(task.action == action for task in self.tasks)
            for action in ("create", "update", "skip")
        }


def _asset_group(config: LampConfig, kind: AssetKind) -> AssetGroup:
    return getattr(config.assets, kind)


def _asset_path(
    base: Path, kind: AssetKind, identifier: str, environment: str, mode: AssetMode
) -> Path:
    filename = f"{identifier}{SUFFIXES[kind]}"
    if mode == "env":
        return base / environment / filename
    return base / identifier / environment / filename


def _source_path(
    config: LampConfig,
    kind: AssetKind,
    group: AssetGroup,
    identifier: str,
    asset: AssetConfig,
) -> Path:
    mode = asset.mode or config.settings.mode
    environment = asset.source_target or config.settings.source_target
    base = group.path or Path(kind)
    return config.resolve_path(
        asset.source or _asset_path(base, kind, identifier, environment, mode)
    )


def _auto_discover(
    config: LampConfig, kind: AssetKind, group: AssetGroup
) -> dict[str, DiscoveredAsset]:
    base = config.resolve_path(group.path or Path(kind))
    environment = config.settings.source_target
    suffix = SUFFIXES[kind]
    discovered: dict[str, DiscoveredAsset] = {}
    if config.settings.mode == "env":
        source_dir = base / environment
        candidates = sorted(source_dir.glob(f"*{suffix}")) if source_dir.is_dir() else []
        for source in candidates:
            identifier = source.name.removesuffix(suffix)
            discovered[identifier] = DiscoveredAsset(kind, identifier, source, AssetConfig())
        return discovered
    if not base.is_dir():
        return discovered
    for asset_dir in sorted(path for path in base.iterdir() if path.is_dir()):
        source = asset_dir / environment / f"{asset_dir.name}{suffix}"
        if source.is_file():
            discovered[asset_dir.name] = DiscoveredAsset(
                kind, asset_dir.name, source, AssetConfig()
            )
    return discovered


def _excluded(config: LampConfig, group: AssetGroup, discovered: DiscoveredAsset) -> bool:
    relative = discovered.source.relative_to(config.root).as_posix()
    return (
        discovered.config.exclude
        or discovered.identifier in group.exclusions
        or relative in group.exclusions
    )


def _configured_assets(config: LampConfig, kind: AssetKind) -> dict[str, DiscoveredAsset]:
    group = _asset_group(config, kind)
    configured = _auto_discover(config, kind, group)
    configured.update(
        {
            identifier: DiscoveredAsset(
                kind,
                identifier,
                _source_path(config, kind, group, identifier, asset),
                asset,
            )
            for identifier, asset in group.items.items()
        }
    )
    if len({item.source for item in configured.values()}) != len(configured):
        raise LampError(f"Duplicate configured {kind} source path")
    return configured


def discover_assets(
    config: LampConfig,
    kind: AssetKind,
    selected: list[str] | None = None,
) -> tuple[DiscoveredAsset, ...]:
    """Resolve configured asset names or source paths."""
    group = _asset_group(config, kind)
    configured = _configured_assets(config, kind)
    by_path = {item.source: item for item in configured.values()}

    if selected:
        requested = []
        for value in selected:
            item = configured.get(value)
            if item is None and Path(value).suffix:
                item = by_path.get(config.resolve_path(Path(value)))
            if item is None:
                raise LampError(f"Configured {kind} asset not found: {value}")
            requested.append(item)
    else:
        requested = list(configured.values())

    included = list(
        {item.identifier: item for item in requested if not _excluded(config, group, item)}.values()
    )
    if not included:
        raise LampError(f"No non-excluded {kind} assets found")
    for item in included:
        if not item.source.is_file():
            raise LampError(f"Source {kind} asset not found: {item.source}")
        if not item.source.name.endswith(SUFFIXES[kind]):
            raise LampError(f"Expected a {SUFFIXES[kind]} asset: {item.source}")
        try:
            json.loads(item.source.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise LampError(f"Invalid JSON in {item.source}: {error}") from error
    return tuple(included)


def _targets(config: LampConfig, discovered: DiscoveredAsset):
    shared = {target.name: target for target in config.settings.targets}
    overrides = discovered.config.targets
    if overrides is None:
        return tuple((target, None) for target in config.settings.targets)
    return tuple((shared.get(target.name), target) for target in overrides)


def _merged_replacements(
    shared: TargetConfig | None, override: AssetTargetConfig | None
) -> dict[str, str]:
    replacements = dict(shared.replacements) if shared else {}
    if override:
        replacements.update(override.replacements)
    return replacements


def _target_path(
    config: LampConfig,
    discovered: DiscoveredAsset,
    target_name: str,
    override: AssetTargetConfig | None,
) -> Path:
    if override and override.path:
        return config.resolve_path(override.path)
    base = _asset_group(config, discovered.kind).path or Path(discovered.kind)
    mode = discovered.config.mode or config.settings.mode
    return config.resolve_path(
        _asset_path(base, discovered.kind, discovered.identifier, target_name, mode)
    )


def environment_path(
    config: LampConfig, discovered: DiscoveredAsset, environment: str
) -> Path | None:
    """Return the asset's source or promoted path for `environment`, or None if not promoted."""
    if environment == (discovered.config.source_target or config.settings.source_target):
        return discovered.source
    for shared, override in _targets(config, discovered):
        if (override.name if override else shared.name) == environment:
            return _target_path(config, discovered, environment, override)
    return None


def plan_promotion(
    config: LampConfig,
    kinds: tuple[AssetKind, ...],
    selected: list[str] | None = None,
    *,
    force: bool = False,
) -> PromotionPlan:
    tasks: list[PromotionTask] = []
    seen_targets: set[Path] = set()
    for kind in kinds:
        for discovered in discover_assets(config, kind, selected):
            for shared, override in _targets(config, discovered):
                target_name = override.name if override else shared.name
                target = _target_path(config, discovered, target_name, override)
                replacements = _merged_replacements(shared, override)
                if target == discovered.source:
                    raise LampError(f"Target cannot overwrite its source: {target}")
                if target in seen_targets:
                    raise LampError(f"Duplicate target path: {target}")
                seen_targets.add(target)
                if not target.exists():
                    action: Action = "create"
                    reason = "target does not exist"
                elif not force and target.stat().st_mtime >= discovered.source.stat().st_mtime:
                    action = "skip"
                    reason = "target is newer than or equal to source"
                else:
                    action = "update"
                    reason = "forced" if force else "source is newer than target"
                tasks.append(
                    PromotionTask(
                        kind=kind,
                        identifier=discovered.identifier,
                        target_name=target_name,
                        source=discovered.source,
                        target=target,
                        substitutions=tuple(replacements.items()),
                        action=action,
                        reason=reason,
                    )
                )
    return PromotionPlan(tuple(tasks))


def sort_genie_sources(definition: dict[str, Any]) -> None:
    """Order Genie table and volume sources by their API-required fields."""
    sources = definition.get("data_sources")
    if sources is None:
        return
    if not isinstance(sources, dict):
        raise LampError("Genie data_sources must be an object")
    for collection, key in (("tables", "identifier"), ("volumes", "path")):
        if collection not in sources:
            continue
        entries = sources[collection]
        if not isinstance(entries, list) or any(
            not isinstance(entry, dict) or not isinstance(entry.get(key), str) for entry in entries
        ):
            raise LampError(f"Genie data_sources.{collection} must be a list of objects with {key}")
        entries.sort(key=lambda entry: entry[key])


def execute_plan(plan: PromotionPlan, *, dry_run: bool = False) -> PromotionPlan:
    if dry_run:
        return plan
    for task in plan.tasks:
        if task.action == "skip":
            continue
        try:
            source_data = json.loads(task.source.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise LampError(f"Invalid JSON in {task.source}: {error}") from error
        rendered = replace_strings(source_data, task.substitutions)
        if task.kind == "genie":
            if not isinstance(rendered, dict):
                raise LampError(f"Expected a JSON object in {task.source}")
            sort_genie_sources(rendered)
        task.target.parent.mkdir(parents=True, exist_ok=True)
        task.target.write_text(
            json.dumps(rendered, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return plan
