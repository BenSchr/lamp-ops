"""Genie asset scanner."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from lamp_ops.config import LampConfig, LampError
from lamp_ops.linting.genie.rules import RULES, JsonObject
from lamp_ops.linting.results import AssetReport, CheckResult, CheckStatus
from lamp_ops.promotion import DiscoveredAsset, discover_assets

# databricks.sdk takes ~1.5s to import, so it is loaded only when metadata is fetched.
if TYPE_CHECKING:
    from databricks.sdk import WorkspaceClient
    from databricks.sdk.service.catalog import TableInfo

METADATA_RULE_IDS = {2, 3}
METADATA_WORKERS = 8


def _bundle_spaces(config: LampConfig) -> tuple[dict[Path, JsonObject], dict[str, JsonObject]]:
    """Index bundle Genie resources once per lint run, by source path and resource key."""
    configured = config.dab_resource_path
    if configured is None:
        return {}, {}
    base = config.config_path.parent
    if configured.is_absolute():
        raise LampError(f"Configured paths must be relative: {configured}")
    directory = (base / configured).resolve()
    if not directory.is_relative_to(base):
        raise LampError(f"Configured path escapes configuration directory: {configured}")
    by_path: dict[Path, JsonObject] = {}
    by_key: dict[str, JsonObject] = {}
    if not directory.is_dir():
        return by_path, by_key
    for path in sorted((*directory.rglob("*.yml"), *directory.rglob("*.yaml"))):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as error:
            raise LampError(f"Invalid YAML in {path}: {error}") from error
        resources = data.get("resources") if isinstance(data, dict) else None
        spaces = resources.get("genie_spaces") if isinstance(resources, dict) else None
        if not isinstance(spaces, dict):
            continue
        for key, space in spaces.items():
            if not isinstance(space, dict):
                continue
            by_key[str(key)] = space
            file_path = space.get("file_path")
            if isinstance(file_path, str) and file_path:
                by_path[(path.parent / file_path).resolve()] = space
    return by_path, by_key


def _is_excluded(config: LampConfig, identifier: str, path) -> bool:
    exclusions = config.lint.genie.exclusions
    return identifier in exclusions or path.relative_to(config.root).as_posix() in exclusions


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
    config: LampConfig,
    selected: list[str] | None = None,
    *,
    profile: str | None = None,
    offline: bool = False,
    client: WorkspaceClient | None = None,
) -> tuple[AssetReport, ...]:
    assets = [
        asset
        for asset in discover_assets(config, "genie", selected)
        if not _is_excluded(config, asset.identifier, asset.source)
    ]
    if not assets:
        raise LampError("No non-excluded genie assets found for linting")
    metadata_enabled = any(
        rule.id in METADATA_RULE_IDS and getattr(config.lint.genie.rules, rule.key).enabled
        for rule in RULES
    )
    metadata_error = "Unity Catalog metadata lookup disabled by --offline" if offline else None
    loaded: list[tuple[DiscoveredAsset, JsonObject]] = []
    for asset in assets:
        path = asset.source
        try:
            raw: Any = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise LampError(f"Invalid JSON in {path}: {error}") from error
        if not isinstance(raw, dict):
            raise LampError(f"Genie asset must contain a JSON object: {path}")
        loaded.append((asset, raw))

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
    spaces_by_path, spaces_by_key = (
        _bundle_spaces(config) if config.lint.genie.rules.agent_description.enabled else ({}, {})
    )
    evaluations = []
    for asset, raw in loaded:
        path = asset.source
        bundle_space = (
            spaces_by_path[path] if path in spaces_by_path else spaces_by_key.get(asset.identifier)
        )
        if bundle_space is not None:
            raw["description"] = bundle_space.get("description")
        asset_metadata_error = metadata_error
        if metadata_enabled and asset_metadata_error is None:
            asset_metadata_error = _enrich_metadata(raw, metadata_cache)
        checks = tuple(
            (
                rule,
                ("skip", asset_metadata_error, None)
                if rule.id in METADATA_RULE_IDS and asset_metadata_error
                else (
                    "skip",
                    "Genie bundle resource not found in dab_resource_path"
                    if config.dab_resource_path is not None
                    else "dab_resource_path not configured and JSON has no description",
                    None,
                )
                if rule.id == 1
                and bundle_space is None
                and (config.dab_resource_path is not None or "description" not in raw)
                else rule.evaluate(raw, getattr(config.lint.genie.rules, rule.key)),
            )
            for rule in RULES
            if getattr(config.lint.genie.rules, rule.key).enabled
        )
        evaluations.append((asset, checks))

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
    for asset, evaluations_for_asset in evaluations:
        checks = []
        for rule, (outcome, message, advisory) in evaluations_for_asset:
            rule_config = getattr(config.lint.genie.rules, rule.key)
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
                asset.identifier,
                asset.source.relative_to(config.root),
                tuple(checks),
                asset.config.source_target or config.settings.source_target,
            )
        )
    return tuple(reports)
