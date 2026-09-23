#!/usr/bin/env python3
"""Update generated README sections from headings and the real Typer app."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml
from typer.testing import CliRunner

from lamp_ops.cli import app
from lamp_ops.config import GenieLintConfig, GenieRulesConfig
from lamp_ops.linting.genie.rules import RULES

TOC_BEGIN = "<!-- BEGIN GENERATED TOC -->"
TOC_END = "<!-- END GENERATED TOC -->"
CLI_BEGIN = "<!-- BEGIN GENERATED CLI REFERENCE -->"
CLI_END = "<!-- END GENERATED CLI REFERENCE -->"
LINT_BEGIN = "<!-- BEGIN GENERATED GENIE LINT REFERENCE -->"
LINT_END = "<!-- END GENERATED GENIE LINT REFERENCE -->"
COMMANDS = (
    (),
    ("promote",),
    ("promote", "dashboards"),
    ("promote", "genie"),
    ("lint",),
    ("rub",),
    ("config", "schema"),
)


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


def generate_toc(text: str) -> str:
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
        lines.append(f"{'  ' * (level - 2)}- [{title}](#{_anchor(title, seen)})")
    return "\n".join(lines)


def generate_cli_reference() -> str:
    runner = CliRunner()
    sections = []
    for command in COMMANDS:
        result = runner.invoke(
            app, [*command, "--help"], prog_name="lamp", color=False, env={"COLUMNS": "209"}
        )
        if result.exit_code != 0:
            raise ReadmeError(f"Could not render help for {' '.join(command) or 'lamp'}")
        name = "lamp" if not command else f"lamp {' '.join(command)}"
        sections.append(f"### `{name}`\n\n```text\n{result.output.rstrip()}\n```")
    return "\n\n".join(sections)


def generate_lint_reference() -> str:
    keys = [rule.key for rule in RULES]
    configured = list(GenieRulesConfig.model_fields)
    if len(keys) != len(set(keys)) or len({rule.id for rule in RULES}) != len(RULES):
        raise ReadmeError("Genie lint rule keys and IDs must be unique")
    if set(keys) != set(configured):
        raise ReadmeError("Genie lint registry and configuration fields do not match")

    defaults_model = GenieRulesConfig()
    lines = [
        "| ID | Rule | Configuration key | Lower limit / pass condition | "
        "Upper limit / fail condition | What it checks | Non-blocking warning | Remediation |",
        "|---:|---|---|---|---|---|---|---|",
    ]
    for rule in RULES:
        fields = getattr(defaults_model, rule.key).model_dump(mode="python")
        values = [
            rule.label,
            rule.key,
            rule.lower_limit.format(**fields),
            rule.upper_limit.format(**fields),
            rule.checks,
            rule.advisory.format(**fields),
            rule.remediation,
        ]
        label, key, lower, upper, checks, advisory, remediation = (
            value.replace("|", "\\|") for value in values
        )
        lines.append(
            f"| {rule.id} | {label} | `{key}` | {lower} | {upper} | {checks} | {advisory} | "
            f"{remediation} |"
        )

    defaults = {"lint": {"genie": GenieLintConfig().model_dump(mode="json")}}
    yaml_defaults = yaml.safe_dump(defaults, sort_keys=False, allow_unicode=True).rstrip()
    lines.extend(("", "Default configuration:", "", "```yaml", yaml_defaults, "```"))
    return "\n".join(lines)


def rendered_readme(text: str) -> str:
    updated = _replace(text, LINT_BEGIN, LINT_END, generate_lint_reference())
    updated = _replace(updated, CLI_BEGIN, CLI_END, generate_cli_reference())
    return _replace(updated, TOC_BEGIN, TOC_END, generate_toc(updated))


def update_readme(path: Path, *, check: bool = False) -> bool:
    original = path.read_text(encoding="utf-8")
    updated = rendered_readme(original)
    changed = updated != original
    if changed and not check:
        path.write_text(updated, encoding="utf-8")
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Fail instead of updating drift.")
    parser.add_argument("--readme", type=Path, default=Path("README.md"), help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        changed = update_readme(args.readme, check=args.check)
    except (OSError, ReadmeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    if args.check and changed:
        print(f"error: {args.readme} is out of date", file=sys.stderr)
        return 1
    print(f"{'Would update' if changed else 'Current'}: {args.readme}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
