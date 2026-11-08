#!/usr/bin/env python3
"""Update generated README and policy doc sections from headings and the real code."""

from __future__ import annotations

import argparse
import inspect
import re
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import duckdb
import typer.main
import yaml

from lamp_ops.cli import app
from lamp_ops.config import GenieLintConfig, GenieRulesConfig
from lamp_ops.linting.genie.rules import RULES
from lamp_ops.policy.database import HELPERS, RELATIONS, TYPED_METADATA, create_database
from lamp_ops.policy.presets import PRESETS, Parameters, Permission

TOC_BEGIN = "<!-- BEGIN GENERATED TOC -->"
TOC_END = "<!-- END GENERATED TOC -->"
CLI_BEGIN = "<!-- BEGIN GENERATED CLI REFERENCE -->"
CLI_END = "<!-- END GENERATED CLI REFERENCE -->"
LINT_BEGIN = "<!-- BEGIN GENERATED GENIE LINT REFERENCE -->"
LINT_END = "<!-- END GENERATED GENIE LINT REFERENCE -->"
PRESETS_BEGIN = "<!-- BEGIN GENERATED POLICY PRESETS -->"
PRESETS_END = "<!-- END GENERATED POLICY PRESETS -->"
TABLES_BEGIN = "<!-- BEGIN GENERATED POLICY TABLES -->"
TABLES_END = "<!-- END GENERATED POLICY TABLES -->"
# Smallest summary and schema that create every generic relation and one typed table.
_SAMPLE_SUMMARY = {"bundle": {"target": "dev"}, "resources": {}}
_SAMPLE_SCHEMA = {
    "properties": {
        "resources": {
            "type": "object",
            "properties": {
                "jobs": {
                    "type": "object",
                    "additionalProperties": {
                        "type": "object",
                        "properties": {"name": {"type": "string"}},
                    },
                }
            },
        }
    }
}


class ReadmeError(ValueError):
    """Raised when generated README markers are malformed."""


def _bounds(text: str, begin: str, end: str) -> tuple[int, int]:
    if text.count(begin) != 1 or text.count(end) != 1:
        raise ReadmeError(f"Expected exactly one {begin!r} and {end!r} marker")
    start = text.index(begin)
    finish = text.index(end)
    if start >= finish:
        raise ReadmeError(f"Marker {begin!r} must precede {end!r}")
    return start, finish + len(end)


def _replace(text: str, begin: str, end: str, content: str) -> str:
    start, finish = _bounds(text, begin, end)
    return text[:start] + f"{begin}\n{content.rstrip()}\n{end}" + text[finish:]


def _anchor(title: str, seen: dict[str, int]) -> str:
    anchor = re.sub(r"[^\w\- ]", "", title.strip().lower()).replace(" ", "-")
    count = seen.get(anchor, 0)
    seen[anchor] = count + 1
    return f"{anchor}-{count}" if count else anchor


def generate_toc(text: str, *, max_level: int = 6) -> str:
    visible = text
    if CLI_BEGIN in text:
        cli_start, cli_finish = _bounds(text, CLI_BEGIN, CLI_END)
        visible = text[:cli_start] + text[cli_finish:]
    in_fence = False
    seen: dict[str, int] = {}
    lines = []
    for line in visible.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        match = re.match(r"^(#{2,6})\s+(.+?)\s*#*$", line)
        if in_fence or not match:
            continue
        level, title = len(match.group(1)), match.group(2).strip()
        if level > max_level:
            continue
        lines.append(f"{'  ' * (level - 2)}- [{title}](#{_anchor(title, seen)})")
    return "\n".join(lines)


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def _option_value(param: Any) -> str:
    if getattr(param, "is_flag", False):
        return ""
    choices = getattr(param.type, "choices", None)
    if choices:
        return " \\| ".join(f"`{choice}`" for choice in choices)
    # Match the value names Click prints in --help.
    return f"`{ {'str': 'TEXT'}.get(param.type.name, param.type.name.upper()) }`"


def _option_default(param: Any) -> str:
    if param.required:
        return "required"
    if getattr(param, "is_flag", False) or param.default in (None, (), []):
        return ""
    return f"`{param.default}`"


def _command_section(name: str, command: Any) -> str:
    """Render one command from the Click tree Typer builds, so the docs match the CLI."""
    arguments = [p for p in command.params if p.param_type_name == "argument"]
    options = [p for p in command.params if p.param_type_name == "option"]
    subcommands = getattr(command, "commands", {})
    usage = [name, "[OPTIONS]"]
    if subcommands:
        # Groups such as `lamp promote` also run on their own.
        optional = getattr(command, "invoke_without_command", False)
        usage.append("[COMMAND] [ARGS]..." if optional else "COMMAND [ARGS]...")
    for argument in arguments:
        label = argument.name.upper() + ("..." if argument.nargs == -1 else "")
        usage.append(label if argument.required else f"[{label}]")
    lines = [
        f"### `{name}`",
        "",
        inspect.cleandoc(command.help or ""),
        "",
        "```text",
        " ".join(usage),
        "```",
    ]
    if subcommands:
        lines += ["", "| Command | Description |", "|---|---|"]
        for sub_name, sub in subcommands.items():
            anchor = re.sub(r"[^\w\- ]", "", f"{name} {sub_name}").replace(" ", "-")
            summary = inspect.cleandoc(sub.help or "").split("\n")[0]
            lines.append(f"| [`{sub_name}`](#{anchor}) | {_cell(summary)} |")
    if arguments:
        lines += ["", "| Argument | Description |", "|---|---|"]
        for argument in arguments:
            label = argument.name.upper() + ("..." if argument.nargs == -1 else "")
            lines.append(f"| `{label}` | {_cell(getattr(argument, 'help', None) or '')} |")
    if options:
        lines += ["", "| Option | Value | Default | Description |", "|---|---|---|---|"]
        for option in options:
            names = ", ".join(f"`{flag}`" for flag in option.opts)
            if option.secondary_opts:
                names += " / " + ", ".join(f"`{flag}`" for flag in option.secondary_opts)
            description = option.help or ""
            if option.multiple and "repeatable" not in description.lower():
                description += " Repeatable."
            lines.append(
                f"| {names} | {_option_value(option)} | {_option_default(option)} | "
                f"{_cell(description)} |"
            )
    return "\n".join(lines)


def generate_cli_reference() -> str:
    """Document every command by walking the real command tree in registration order."""
    sections = []
    pending = [("lamp", typer.main.get_command(app))]
    while pending:
        name, command = pending.pop(0)
        sections.append(_command_section(name, command))
        pending[:0] = [
            (f"{name} {sub_name}", sub)
            for sub_name, sub in getattr(command, "commands", {}).items()
        ]
    return "\n\n".join(sections)


def generate_lint_reference() -> str:
    keys = [rule.key for rule in RULES]
    configured = list(GenieRulesConfig.model_fields)
    if len(keys) != len(set(keys)) or len({rule.id for rule in RULES}) != len(RULES):
        raise ReadmeError("Genie lint rule keys and IDs must be unique")
    if set(keys) != set(configured):
        raise ReadmeError("Genie lint registry and configuration fields do not match")

    defaults_model = GenieRulesConfig()
    table = [
        "| ID | Rule | Configuration key | Pass condition | Upper limit |",
        "|---:|---|---|---|---|",
    ]
    details = []
    for rule in RULES:
        fields = getattr(defaults_model, rule.key).model_dump(mode="python")
        lower = rule.lower_limit.format(**fields)
        upper = rule.upper_limit.format(**fields)
        advisory = rule.advisory.format(**fields)
        table.append(
            f"| {rule.id} | {_cell(rule.label)} | `{rule.key}` | {_cell(lower)} | {_cell(upper)} |"
        )
        details.append(
            f"- **{rule.id}. {rule.label}:** {rule.checks}\n"
            f"  - Warning: {advisory}\n"
            f"  - Fix: {rule.remediation}"
        )

    defaults = {"lint": {"genie": GenieLintConfig().model_dump(mode="json")}}
    yaml_defaults = yaml.safe_dump(defaults, sort_keys=False, allow_unicode=True).rstrip()
    return "\n".join(
        [
            *table,
            "",
            *details,
            "",
            "<details>",
            "<summary>Default configuration</summary>",
            "",
            "```yaml",
            yaml_defaults,
            "```",
            "",
            "</details>",
        ]
    )


def _parameter_table(model: type[Parameters]) -> list[str]:
    lines = ["| Parameter | Type | Required | Description |", "|---|---|---|---|"]
    for name, field in model.model_fields.items():
        annotation = field.annotation
        kind = annotation.__name__ if isinstance(annotation, type) else str(annotation)
        kind = re.sub(r"\b(?:\w+\.)+(\w+)", r"\1", kind)
        kind, description = (value.replace("|", "\\|") for value in (kind, field.description or ""))
        required = "yes" if field.is_required() else "no"
        lines.append(f"| `{name}` | `{kind}` | {required} | {description} |")
    return lines


def generate_policy_preset_reference() -> str:
    sections = []
    for name, (model, _) in PRESETS.items():
        if not model.__doc__:
            raise ReadmeError(f"Policy preset {name} needs a model docstring")
        description = inspect.cleandoc(model.__doc__)
        table = "\n".join(_parameter_table(model))
        sections.append(f"### `{name}`\n\n{description}\n\n{table}")
    permission = "\n".join(_parameter_table(Permission))
    sections.append(f"### Permission entries\n\n{Permission.__doc__}\n\n{permission}")
    return "\n\n".join(sections)


def _escape(value: str) -> str:
    return value.replace("|", "\\|")


def _columns(connection: duckdb.DuckDBPyConnection, relation: str) -> list[tuple[str, str]]:
    return [
        (row[0], row[1])
        for row in connection.execute(f"DESCRIBE SELECT * FROM {relation}").fetchall()
    ]


def generate_policy_table_reference() -> str:
    """Describe the real query database; fail when code and descriptions disagree."""
    with duckdb.connect() as connection:
        create_database(connection, _SAMPLE_SUMMARY, _SAMPLE_SCHEMA)
        kinds = dict(
            connection.execute(
                "SELECT table_name, 'table' FROM duckdb_tables() WHERE schema_name = 'main' "
                "UNION ALL SELECT view_name, 'view' FROM duckdb_views() "
                "WHERE schema_name = 'main' AND NOT internal"
            ).fetchall()
        )
        documented = [relation.name for relation in RELATIONS]
        if set(kinds) != set(documented):
            raise ReadmeError(f"Documented relations {documented} do not match {sorted(kinds)}")
        macros = {
            row[0]
            for row in connection.execute(
                "SELECT function_name FROM duckdb_functions() "
                "WHERE schema_name = 'main' AND NOT internal AND function_type IN ('macro', 'table_macro')"
            ).fetchall()
        }
        if macros != set(HELPERS):
            raise ReadmeError(f"Documented helpers {sorted(HELPERS)} do not match {sorted(macros)}")

        overview = ["| Relation | Kind | Columns |", "|---|---|---|"]
        sections = []
        for relation in RELATIONS:
            columns = _columns(connection, f'"{relation.name}"')
            if {name for name, _ in columns} != set(relation.columns):
                raise ReadmeError(f"Columns of {relation.name} do not match their descriptions")
            names = ", ".join(f"`{name}`" for name, _ in columns)
            overview.append(
                f"| [`{relation.name}`](#{relation.name}) | {kinds[relation.name]} | {names} |"
            )
            connection.execute(relation.example).fetchall()
            table = [
                "| Column | Type | Description |",
                "|---|---|---|",
                *(
                    f"| `{name}` | `{kind}` | {_escape(relation.columns[name])} |"
                    for name, kind in columns
                ),
            ]
            sections.append(
                f"### `{relation.name}`\n\n{relation.description}\n\n"
                + "\n".join(table)
                + f"\n\n```sql\n{relation.example};\n```"
            )

        sections.insert(0, "\n".join(overview))
        helpers = ["| Helper | Description |", "|---|---|"]
        for signature, description, example in HELPERS.values():
            connection.execute(example).fetchall()
            helpers.append(f"| `{signature}` | {_escape(description)} |")
        sections.append("### Helpers\n\n" + "\n".join(helpers))

        typed = [
            (name, kind)
            for name, kind in _columns(connection, "bundle_resources.jobs")
            if name.startswith("_")
        ]
        if [name for name, _ in typed] != list(TYPED_METADATA):
            raise ReadmeError("Typed metadata columns do not match their descriptions")
        typed_table = [
            "| Column | Type | Description |",
            "|---|---|---|",
            *(f"| `{name}` | `{kind}` | {_escape(TYPED_METADATA[name])} |" for name, kind in typed),
        ]
        sections.append(
            "### `bundle_resources.<type>`\n\n"
            "Only with `--schema`: one table per resource type in the schema, with these "
            "metadata columns followed by one column per schema property.\n\n"
            + "\n".join(typed_table)
        )
    return "\n\n".join(sections)


def rendered_policy_docs(text: str) -> str:
    updated = _replace(text, PRESETS_BEGIN, PRESETS_END, generate_policy_preset_reference())
    updated = _replace(updated, TABLES_BEGIN, TABLES_END, generate_policy_table_reference())
    return _replace(updated, TOC_BEGIN, TOC_END, generate_toc(updated, max_level=2))


def rendered_readme(text: str) -> str:
    updated = _replace(text, LINT_BEGIN, LINT_END, generate_lint_reference())
    updated = _replace(updated, CLI_BEGIN, CLI_END, generate_cli_reference())
    return _replace(updated, TOC_BEGIN, TOC_END, generate_toc(updated))


def update_readme(
    path: Path, *, check: bool = False, render: Callable[[str], str] = rendered_readme
) -> bool:
    original = path.read_text(encoding="utf-8")
    updated = render(original)
    changed = updated != original
    if changed and not check:
        path.write_text(updated, encoding="utf-8")
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Fail instead of updating drift.")
    parser.add_argument("--readme", type=Path, default=Path("README.md"), help=argparse.SUPPRESS)
    parser.add_argument(
        "--policies", type=Path, default=Path("docs/policies.md"), help=argparse.SUPPRESS
    )
    args = parser.parse_args()
    status = 0
    for path, render in ((args.readme, rendered_readme), (args.policies, rendered_policy_docs)):
        try:
            changed = update_readme(path, check=args.check, render=render)
        except (OSError, ReadmeError) as error:
            print(f"error: {error}", file=sys.stderr)
            return 1
        if args.check and changed:
            print(f"error: {path} is out of date", file=sys.stderr)
            status = 1
        else:
            print(f"{'Would update' if changed else 'Current'}: {path}")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
