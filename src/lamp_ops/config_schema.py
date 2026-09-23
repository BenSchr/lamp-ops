"""Lamp configuration JSON Schema output."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lamp_ops.config import LampConfig, LampError

SCHEMA_VERSION = "2.0"


def generate_schema() -> dict[str, Any]:
    schema = LampConfig.model_json_schema()
    schema["x-lamp-schema-version"] = SCHEMA_VERSION
    return schema


def schema_json() -> str:
    return json.dumps(generate_schema(), indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def write_schema(path: Path, *, force: bool = False) -> None:
    if path.exists() and not force:
        raise LampError(f"Refusing to overwrite existing file: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(schema_json(), encoding="utf-8")
