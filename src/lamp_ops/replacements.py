"""Literal substitutions shared by local promotion and bundle mutators."""

from typing import Any


def replace_strings(value: Any, replacements: tuple[tuple[str, str], ...]) -> Any:
    """Replace JSON string values, preserving keys and non-string values."""
    if isinstance(value, str):
        for old, new in sorted(replacements, key=lambda item: len(item[0]), reverse=True):
            value = value.replace(old, new)
        return value
    if isinstance(value, list):
        return [replace_strings(item, replacements) for item in value]
    if isinstance(value, dict):
        return {key: replace_strings(item, replacements) for key, item in value.items()}
    return value
