"""Bundle Genie resources that supply agent descriptions to the Genie linter."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from lamp_ops.errors import LampError

JsonObject = dict[str, Any]
RESOURCE_DIRECTORY = "resources"
SEARCH_UP = 3
SEARCH_DOWN = 2
BundleSpaces = dict[str | None, tuple[dict[Path, JsonObject], dict[str, JsonObject]]]


@dataclass(frozen=True)
class BundleIndex:
    """Genie spaces from one source of bundle YAML.

    `spaces` stores top-level `resources` under `None` and `targets.<name>.resources`
    under `<name>`. A missing space skips the description check for `explicit`
    (configured or passed) directories; discovered ones may be the wrong guess, so
    they fall back to the JSON description.
    """

    spaces: BundleSpaces
    origin: str
    explicit: bool

    def _match(self, scope: str | None, path: Path, identifier: str) -> JsonObject | None:
        by_path, by_key = self.spaces.get(scope, ({}, {}))
        if path in by_path:
            return by_path[path]
        return by_key.get(identifier)

    def find(self, path: Path, identifier: str, target: str | None) -> JsonObject | None:
        """Prefer the bundle target over top-level resources; `file_path` beats the key."""
        for scope in (target, None):
            if (space := self._match(scope, path, identifier)) is not None:
                return space
        return None

    def only_under_targets(self, path: Path, identifier: str) -> bool:
        return any(
            self._match(scope, path, identifier) is not None
            for scope in self.spaces
            if scope is not None
        )


def _load(path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as error:
        raise LampError(f"Invalid YAML in {path}: {error}") from error


def _index(files: Iterable[Path]) -> BundleSpaces:
    index: BundleSpaces = {}
    for path in files:
        data = _load(path)
        if not isinstance(data, dict):
            continue
        scopes: list[tuple[str | None, Any]] = [(None, data.get("resources"))]
        targets = data.get("targets")
        if isinstance(targets, dict):
            scopes.extend(
                (str(name), target.get("resources"))
                for name, target in targets.items()
                if isinstance(target, dict)
            )
        for target_name, resources in scopes:
            spaces = resources.get("genie_spaces") if isinstance(resources, dict) else None
            if not isinstance(spaces, dict):
                continue
            by_path, by_key = index.setdefault(target_name, ({}, {}))
            for key, space in spaces.items():
                if not isinstance(space, dict):
                    continue
                by_key[str(key)] = space
                file_path = space.get("file_path")
                if isinstance(file_path, str) and file_path:
                    # Bundle paths are relative to the YAML file that declares them.
                    by_path[(path.parent / file_path).resolve()] = space
    return index


def directory_index(directory: Path, origin: str, *, explicit: bool) -> BundleIndex:
    """Index every YAML file below a resource directory."""
    files = (
        sorted((*directory.rglob("*.yml"), *directory.rglob("*.yaml")))
        if directory.is_dir()
        else []
    )
    return BundleIndex(_index(files), origin, explicit=explicit)


def search_boundary(path: Path, config_directory: Path | None) -> Path | None:
    """Highest directory the search may reach: the config, git root, or working directory."""
    if config_directory is not None:
        return config_directory
    for directory in path.parents:
        if (directory / ".git").exists():
            return directory
    cwd = Path.cwd().resolve()
    return cwd if path.is_relative_to(cwd) else None


def _visible_children(directory: Path) -> list[Path]:
    try:
        return sorted(
            child
            for child in directory.iterdir()
            if child.is_dir() and not child.name.startswith(".")
        )
    except OSError:
        return []


def _below(directory: Path) -> Path | None:
    level = [directory]
    for _ in range(SEARCH_DOWN):
        for candidate in level:
            if (candidate / RESOURCE_DIRECTORY).is_dir():
                return candidate / RESOURCE_DIRECTORY
        level = [child for candidate in level for child in _visible_children(candidate)]
    return None


def find_resource_directory(start: Path, boundary: Path | None) -> Path | None:
    """Find the nearest `resources` directory a few levels above or below `start`.

    Each of `start` and up to SEARCH_UP parents (never past `boundary`) is searched
    SEARCH_DOWN levels deep; nearer levels, then shallower and sorted paths, win.
    """
    for level, directory in enumerate((start, *start.parents)):
        if level > SEARCH_UP:
            break
        if directory.name == RESOURCE_DIRECTORY:
            return directory
        if (found := _below(directory)) is not None:
            return found
        if directory == boundary:
            break
    return None
