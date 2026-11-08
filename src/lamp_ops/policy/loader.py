"""Load external SQL checks, YAML policy packs, and inline config policies."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from lamp_ops.config import LampConfig, find_config, load_config
from lamp_ops.errors import LampError
from lamp_ops.policy.models import CheckConfig, PolicyCheck, PolicyPack, PresetCheck, SqlCheck
from lamp_ops.policy.presets import compile_preset


@dataclass(frozen=True)
class LoadedCheck:
    name: str
    description: str
    config: CheckConfig
    path: Path
    sql: str
    parameters: dict[str, Any]


def parse_parameters(values: list[str]) -> dict[str, Any]:
    parameters = {}
    for value in values:
        name, separator, raw = value.partition("=")
        if not separator or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise LampError("--param must be NAME=JSON, for example --param 'limit=5'")
        if name in parameters:
            raise LampError(f"Duplicate parameter: {name}")
        try:
            parameters[name] = json.loads(raw)
        except json.JSONDecodeError as error:
            raise LampError(f"Invalid JSON for parameter {name}: {error}") from error
    return parameters


def config_policy_source(config_path: Path | None) -> tuple[list[Path], LampConfig | None]:
    """Return the config policy path and inline policies, or ./policies without a config."""
    if config_path is None:
        try:
            config_path = find_config()
        except LampError:
            return [Path("policies")], None
    config = load_config(config_path)
    if config.policy_path is not None:
        return [config.resolve_config_path(config.policy_path)], config
    default = config.config_path.parent / "policies"
    return ([default] if default.is_dir() else []), config


def _loaded(
    check: PolicyCheck, path: Path, sql: str | None, overrides: dict[str, Any]
) -> LoadedCheck:
    if isinstance(check, PresetCheck):
        sql, parameters = compile_preset(check.preset, check.parameters, overrides)
    else:
        parameters = {**check.parameters, **overrides}
    if not sql or not sql.strip():
        raise LampError(f"{check.name}: SQL check is empty")
    return LoadedCheck(check.name, check.description, check.config, path, sql, parameters)


def _load_yaml_checks(
    path: Path,
    checks: list[PolicyCheck],
    root: Path,
    discovered: dict[str, Path],
    referenced: set[Path],
    overrides: dict[str, Any],
) -> list[LoadedCheck]:
    loaded = []
    for check in checks:
        query = None
        if isinstance(check, SqlCheck):
            sql_path = (
                (path.parent / check.sql).resolve()
                if check.sql is not None
                else discovered.get(check.name, (path.parent / f"{check.name}.sql").resolve())
            )
            if not sql_path.is_relative_to(root):
                raise LampError(f"{check.name}: SQL path must stay inside {root}")
            query = sql_path.read_text(encoding="utf-8")
            referenced.add(sql_path)
        loaded.append(_loaded(check, path, query, overrides))
    return loaded


def load_checks(
    paths: list[Path], overrides: dict[str, Any], config: LampConfig | None = None
) -> tuple[LoadedCheck, ...]:
    loaded: list[LoadedCheck] = []
    if config is not None and config.lamp_policies:
        try:
            inline = PolicyPack.model_validate({"lamp_policies": config.lamp_policies})
        except ValueError as error:
            raise LampError(f"Invalid lamp_policies in {config.config_path}: {error}") from error
        loaded += _load_yaml_checks(
            config.config_path,
            inline.lamp_policies,
            config.config_path.parent,
            {},
            set(),
            overrides,
        )
    for source in paths:
        if not source.exists():
            raise LampError(f"Policy path does not exist: {source}")
        if source.is_dir():
            sql_paths = sorted(source.rglob("*.sql"))
            yaml_paths = sorted(
                path for path in source.rglob("*") if path.suffix in {".yaml", ".yml"}
            )
        elif source.suffix in {".yaml", ".yml"}:
            sql_paths, yaml_paths = [], [source]
        elif source.suffix == ".sql":
            sql_paths, yaml_paths = [source], []
        else:
            raise LampError(f"Policy path must be a directory, SQL or YAML file: {source}")

        discovered: dict[str, Path] = {}
        for path in sql_paths:
            if path.stem in discovered:
                raise LampError(f"Duplicate SQL check filename: {path.stem}")
            discovered[path.stem] = path.resolve()
        referenced: set[Path] = set()
        root = source.resolve() if source.is_dir() else source.parent.resolve()
        for path in yaml_paths:
            try:
                pack = PolicyPack.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
            except (yaml.YAMLError, ValueError) as error:
                raise LampError(f"Invalid policy pack {path}: {error}") from error
            loaded += _load_yaml_checks(
                path, pack.lamp_policies, root, discovered, referenced, overrides
            )
        for name, path in discovered.items():
            if path not in referenced:
                check = SqlCheck(name=name)
                loaded.append(_loaded(check, path, path.read_text(encoding="utf-8"), overrides))
    names = [item.name for item in loaded]
    if not names:
        raise LampError("No policy checks found")
    if len(names) != len(set(names)):
        raise LampError("Policy check names must be unique across all policy paths")
    return tuple(loaded)
