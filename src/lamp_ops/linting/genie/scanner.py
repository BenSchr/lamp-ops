"""Genie asset scanner."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from lamp_ops.config import GenieLintConfig, LampConfig
from lamp_ops.errors import LampError
from lamp_ops.linting.genie.bundle import (
    BundleIndex,
    directory_index,
    find_resource_directory,
    search_boundary,
)
from lamp_ops.linting.genie.rules import RULES, JsonObject
from lamp_ops.linting.results import AssetReport, CheckResult, CheckStatus
from lamp_ops.promotion import SUFFIXES, discover_assets, environment_path

# databricks.sdk takes ~1.5s to import, so it is loaded only when metadata is fetched.
if TYPE_CHECKING:
    from databricks.sdk import WorkspaceClient
    from databricks.sdk.service.catalog import TableInfo

METADATA_RULE_IDS = {2, 3}
METADATA_WORKERS = 8
GENIE_SUFFIX = SUFFIXES["genie"]


@dataclass(frozen=True)
class GenieSource:
    """One Genie JSON file to lint, from configured discovery or an explicit path."""

    identifier: str
    path: Path
    display_path: Path
    target: str | None


def _description_skip_reason(
    bundle: BundleIndex | None, source: GenieSource, raw: JsonObject
) -> str | None:
    """Explain why no bundle description applies, or None to evaluate the JSON's own."""
    if bundle is not None:
        if source.target is None and bundle.only_under_targets(source.path, source.identifier):
            return (
                "Genie bundle resource is declared only under bundle targets; "
                "pass --target or set lint.target"
            )
        if bundle.explicit:
            return f"Genie bundle resource not found in {bundle.origin}"
    if "description" in raw:
        return None
    if bundle is None:
        return (
            "No bundle resources (resources directory, dab_resource_path, or "
            "--dab-resource-path) and JSON has no description"
        )
    return f"Genie bundle resource not found in {bundle.origin} and JSON has no description"


def _bundle_lookup(
    config: LampConfig | None, dab_resource_path: Path | None
) -> Callable[[GenieSource], BundleIndex | None]:
    """Choose bundle YAML: the flag, the configured path, else a nearby `resources`."""
    if dab_resource_path is not None:
        if not dab_resource_path.is_dir():
            raise LampError(f"Bundle resource directory not found: {dab_resource_path}")
        shared = directory_index(dab_resource_path.resolve(), "dab_resource_path", explicit=True)
        return lambda source: shared
    if config is not None and config.dab_resource_path is not None:
        configured = directory_index(
            config.resolve_config_path(config.dab_resource_path),
            "dab_resource_path",
            explicit=True,
        )
        return lambda source: configured
    config_directory = config.config_path.parent if config is not None else None
    cache: dict[Path, BundleIndex] = {}

    def discovered(source: GenieSource) -> BundleIndex | None:
        directory = find_resource_directory(
            source.path.parent, search_boundary(source.path, config_directory)
        )
        if directory is None:
            return None
        if directory not in cache:
            cache[directory] = directory_index(
                directory, f"discovered {_display_path(directory)}", explicit=False
            )
        return cache[directory]

    return discovered


def _configured_sources(
    config: LampConfig, selected: list[str] | None, target: str | None
) -> list[GenieSource]:
    """Resolve configured assets at their source path, or at their promoted `target` path."""
    sources = []
    for asset in discover_assets(config, "genie", selected):
        environment = target or asset.config.source_target or config.settings.source_target
        path = environment_path(config, asset, environment)
        if path is None:
            continue
        if not path.is_file():
            raise LampError(
                f"Genie asset {asset.identifier} for target {environment} not found: "
                f"{path}; run lamp promote first"
            )
        sources.append(
            GenieSource(asset.identifier, path, path.relative_to(config.root), environment)
        )
    if not sources:
        raise LampError(f"No configured genie assets are promoted to target {target}")
    return sources


def _select(sources: list[GenieSource], selected: list[str] | None) -> list[GenieSource]:
    if not selected:
        return sources
    identifiers = {source.identifier for source in sources}
    missing = [value for value in selected if value not in identifiers]
    if missing:
        raise LampError(f"Genie asset not found in lint paths: {', '.join(missing)}")
    return [source for source in sources if source.identifier in selected]


def _display_path(path: Path) -> Path:
    cwd = Path.cwd().resolve()
    return path.relative_to(cwd) if path.is_relative_to(cwd) else path


def _path_sources(paths: Sequence[Path], target: str | None) -> list[GenieSource]:
    """Collect explicit JSON files and `*.geniespace.json` files below directories."""
    found: dict[Path, GenieSource] = {}
    for value in paths:
        path = value.resolve()
        if path.is_dir():
            # Hidden directories (.databricks, .venv, .git) never hold authored sources.
            files = sorted(
                file
                for file in path.rglob(f"*{GENIE_SUFFIX}")
                if file.is_file()
                and not any(part.startswith(".") for part in file.relative_to(path).parts[:-1])
            )
            if not files:
                raise LampError(f"No *{GENIE_SUFFIX} files found below {value}")
        elif path.is_file():
            if path.suffix != ".json":
                raise LampError(f"Expected a Genie JSON file: {value}")
            files = [path]
        else:
            raise LampError(f"Genie path not found: {value}")
        for file in files:
            identifier = (
                file.name.removesuffix(GENIE_SUFFIX)
                if file.name.endswith(GENIE_SUFFIX)
                else file.stem
            )
            found.setdefault(file, GenieSource(identifier, file, _display_path(file), target))
    return list(found.values())


def _is_excluded(config: LampConfig | None, source: GenieSource) -> bool:
    if config is None:
        return False
    exclusions = config.lint.genie.exclusions
    return source.identifier in exclusions or (
        source.path.is_relative_to(config.root)
        and source.path.relative_to(config.root).as_posix() in exclusions
    )


def _source_identifier(source: JsonObject) -> str:
    return str(
        source.get("identifier") or source.get("name") or source.get("table_name") or ""
    ).strip()


def _merge_metadata(source: JsonObject, table: TableInfo) -> None:
    if not (source.get("description") or source.get("comment")) and table.comment:
        source["comment"] = table.comment

    configured = {}
    for key in ("columns", "column_configs"):
        values = source.get(key)
        if isinstance(values, list):
            for column in values:
                if isinstance(column, dict):
                    name = str(column.get("column_name") or column.get("name") or "").strip()
                    if name:
                        configured.setdefault(name.lower(), []).append(column)

    remote_columns = []
    for column in table.columns or []:
        if not column.name:
            continue
        matches = configured.get(column.name.lower(), [])
        if any(match.get("exclude") is True for match in matches):
            continue
        if matches:
            for match in matches:
                if not (match.get("description") or match.get("comment")) and column.comment:
                    match["comment"] = column.comment
        else:
            remote_columns.append({"name": column.name, "comment": column.comment})
    if remote_columns:
        columns = source.setdefault("columns", [])
        if isinstance(columns, list):
            columns.extend(remote_columns)


def _data_sources(data: JsonObject) -> list[JsonObject]:
    sources = data.get("data_sources", {})
    if not isinstance(sources, dict):
        return []
    return [
        item
        for key in ("tables", "metric_views")
        for item in sources.get(key, [])
        if isinstance(item, dict)
    ]


def _fetch_metadata(client: WorkspaceClient, identifiers: set[str]) -> dict[str, TableInfo | str]:
    """Look up each unique table once, concurrently."""
    from databricks.sdk.errors import DatabricksError, NotFound, PermissionDenied, Unauthenticated

    def fetch(identifier: str) -> TableInfo | str:
        try:
            return client.tables.get(full_name=identifier)
        except (Unauthenticated, ValueError):
            return "Databricks authentication unavailable; check --profile or log in"
        except NotFound:
            return "table not found in Unity Catalog"
        except PermissionDenied:
            return "access denied to Unity Catalog table"
        except DatabricksError:
            return "Unity Catalog lookup failed"

    ordered = sorted(identifiers)
    with ThreadPoolExecutor(max_workers=METADATA_WORKERS) as pool:
        return dict(zip(ordered, pool.map(fetch, ordered), strict=True))


def _enrich_metadata(data: JsonObject, cache: dict[str, TableInfo | str]) -> str | None:
    if not isinstance(data.get("data_sources", {}), dict):
        return None
    errors = []
    for source in _data_sources(data):
        identifier = _source_identifier(source)
        if not identifier:
            errors.append("data source has no identifier")
            continue
        metadata = cache[identifier]
        if isinstance(metadata, str):
            errors.append(f"{identifier}: {metadata}")
        else:
            _merge_metadata(source, metadata)
    if errors:
        return "Unity Catalog metadata unavailable: " + "; ".join(errors)
    return None


def lint_genie(
    config: LampConfig | None,
    selected: list[str] | None = None,
    *,
    paths: Sequence[Path] | None = None,
    dab_resource_path: Path | None = None,
    target: str | None = None,
    profile: str | None = None,
    offline: bool = False,
    client: WorkspaceClient | None = None,
) -> tuple[AssetReport, ...]:
    """Lint Genie JSON from `paths`, configured `lint.path`, or the configured asset layout.

    Paths need no configuration; `selected` then filters by asset name. `target`
    (default: `lint.target`) selects target-scoped bundle resources and, for configured
    assets, their promoted copy. Agent descriptions come from `dab_resource_path`
    (relative to the working directory), else the configured `dab_resource_path`,
    else the nearest `resources` directory around each file.
    """
    if target is None and config is not None:
        target = config.lint.target
    if not paths and config is not None:
        paths = config.lint_paths
    if paths:
        sources = _select(_path_sources(paths, target), selected)
    elif config is None:
        raise LampError("Linting configured assets needs a Lamp configuration")
    else:
        sources = _configured_sources(config, selected, target)
    sources = [source for source in sources if not _is_excluded(config, source)]
    if not sources:
        raise LampError("No non-excluded genie assets found for linting")
    lint_config = config.lint.genie if config else GenieLintConfig()
    bundle_for = (
        _bundle_lookup(config, dab_resource_path)
        if lint_config.rules.agent_description.enabled
        else lambda source: None
    )
    metadata_enabled = any(
        rule.id in METADATA_RULE_IDS and getattr(lint_config.rules, rule.key).enabled
        for rule in RULES
    )
    metadata_error = "Unity Catalog metadata lookup disabled by --offline" if offline else None
    loaded: list[tuple[GenieSource, JsonObject]] = []
    for source in sources:
        path = source.path
        try:
            raw: Any = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise LampError(f"Invalid JSON in {path}: {error}") from error
        if not isinstance(raw, dict):
            raise LampError(f"Genie asset must contain a JSON object: {path}")
        loaded.append((source, raw))

    metadata_cache: dict[str, TableInfo | str] = {}
    if metadata_enabled and metadata_error is None:
        identifiers = {
            _source_identifier(source) for _, raw in loaded for source in _data_sources(raw)
        } - {""}
        if identifiers and client is None:
            from databricks.sdk import WorkspaceClient
            from databricks.sdk.errors import DatabricksError

            try:
                client = WorkspaceClient(profile=profile) if profile else WorkspaceClient()
            except (DatabricksError, ValueError):
                metadata_error = "Databricks authentication unavailable; check --profile or log in"
        if identifiers and client is not None:
            metadata_cache = _fetch_metadata(client, identifiers)
    evaluations = []
    for source, raw in loaded:
        bundle = bundle_for(source)
        bundle_space = (
            bundle.find(source.path, source.identifier, source.target) if bundle else None
        )
        description_skip = None
        if bundle_space is not None:
            raw["description"] = bundle_space.get("description")
        else:
            description_skip = _description_skip_reason(bundle, source, raw)
        asset_metadata_error = metadata_error
        if metadata_enabled and asset_metadata_error is None:
            asset_metadata_error = _enrich_metadata(raw, metadata_cache)
        checks = tuple(
            (
                rule,
                ("skip", asset_metadata_error, None)
                if rule.id in METADATA_RULE_IDS and asset_metadata_error
                else ("skip", description_skip, None)
                if rule.id == 1 and description_skip
                else rule.evaluate(raw, getattr(lint_config.rules, rule.key)),
            )
            for rule in RULES
            if getattr(lint_config.rules, rule.key).enabled
        )
        evaluations.append((source, checks))

    failures = {
        rule.key: sum(
            outcome == "fail"
            for _, checks in evaluations
            for candidate, (outcome, _, _) in checks
            if candidate.key == rule.key
        )
        for rule in RULES
    }
    reports = []
    for source, evaluations_for_asset in evaluations:
        checks = []
        for rule, (outcome, message, advisory) in evaluations_for_asset:
            rule_config = getattr(lint_config.rules, rule.key)
            if outcome == "skip":
                status: CheckStatus = "skipped"
            elif outcome == "fail":
                status = (
                    "error"
                    if rule_config.severity == "error"
                    and failures[rule.key] > rule_config.threshold
                    else "warn"
                )
            else:
                status = "pass"
            checks.append(
                CheckResult(
                    rule.id,
                    rule.label,
                    status,
                    message,
                    rule.remediation if outcome == "fail" else None,
                    advisory,
                )
            )
        reports.append(
            AssetReport(
                "genie",
                source.identifier,
                source.display_path,
                tuple(checks),
                source.target,
            )
        )
    return tuple(reports)
