"""Optional native DAB mutators; no Lamp configuration, deployment, or source-file writes."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from databricks.bundles.core import Bundle, dashboard_mutator, genie_space_mutator
from databricks.bundles.dashboards import Dashboard
from databricks.bundles.genie_spaces import GenieSpace
from pydantic import TypeAdapter, ValidationError

from lamp_ops.errors import LampError
from lamp_ops.promotion import AssetKind, sort_genie_sources
from lamp_ops.replacements import replace_strings


def _replacements(bundle: Bundle) -> tuple[tuple[str, str], ...]:
    """Read replacements from the bundle only; Lamp configuration is never consulted."""
    if "lamp_replacements" not in bundle.variables:
        raise LampError("Bundle mutators need a lamp_replacements bundle variable")
    try:
        replacements = TypeAdapter(dict[str, str]).validate_python(
            bundle.variables["lamp_replacements"], strict=True
        )
    except ValidationError as error:
        raise LampError("lamp_replacements must be a mapping of strings to strings") from error
    if "" in replacements:
        raise LampError("lamp_replacements must not contain an empty search string")
    return tuple(replacements.items())


def _mutate_definition(
    bundle: Bundle, kind: AssetKind, file_path: str | None, serialized: Any
) -> dict[str, Any] | None:
    if file_path is None and serialized is None:
        return None
    source = Path(file_path).resolve() if file_path is not None else None
    replacements = _replacements(bundle)
    if not replacements:
        return None
    label = str(source) if source else f"inline {kind} definition"
    # The CLI may already have inlined file contents before applying Python mutators.
    if source is not None and serialized is None:
        try:
            serialized = source.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            raise LampError(f"Could not read {label}: {error}") from error
    if isinstance(serialized, str):
        try:
            serialized = json.loads(serialized)
        except json.JSONDecodeError as error:
            raise LampError(f"Invalid JSON in {label}: {error}") from error
    if not isinstance(serialized, dict):
        raise LampError(f"Expected a JSON object in {label}")
    rendered = replace_strings(serialized, replacements)
    return rendered if rendered != serialized else None


@genie_space_mutator
def mutate_genie_spaces(bundle: Bundle, space: GenieSpace) -> GenieSpace:
    """Substitute Genie JSON values for the selected bundle target."""
    definition = _mutate_definition(
        bundle,
        "genie",
        bundle.resolve_variable(space.file_path),
        bundle.resolve_variable(space.serialized_space),
    )
    if definition is None:
        return space
    sort_genie_sources(definition)
    return replace(
        space, file_path=None, serialized_space=json.dumps(definition, ensure_ascii=False)
    )


@dashboard_mutator
def mutate_dashboards(bundle: Bundle, dashboard: Dashboard) -> Dashboard:
    """Substitute dashboard JSON values for the selected bundle target."""
    definition = _mutate_definition(
        bundle,
        "dashboards",
        bundle.resolve_variable(dashboard.file_path),
        bundle.resolve_variable(dashboard.serialized_dashboard),
    )
    if definition is None:
        return dashboard
    return replace(
        dashboard,
        file_path=None,
        serialized_dashboard=json.dumps(definition, ensure_ascii=False),
    )
