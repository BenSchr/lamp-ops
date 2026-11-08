"""Concise terminal and stable JSON reporting."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import typer
from rich.console import Console
from rich.table import Table
from rich.text import Text

from lamp_ops.errors import LampError
from lamp_ops.linting.results import LintReport
from lamp_ops.promotion import PromotionPlan

if TYPE_CHECKING:
    from lamp_ops.policy.runner import PolicyReport

REPORT_SCHEMA_VERSION = "3.0"
OUTCOME_STYLES = {
    "pass": "bold green",
    "warn": "bold yellow",
    "error": "bold red",
    "skipped": "bold cyan",
}


def render_promotion(plan: PromotionPlan, root: Path, *, dry_run: bool) -> None:
    prefix = "DRY-RUN " if dry_run else ""
    for task in plan.tasks:
        typer.echo(
            f"{prefix}{task.action.upper()} {task.kind}:{task.identifier}:{task.target_name} "
            f"-> {task.target.relative_to(root)} ({task.reason})"
        )
    totals = plan.totals
    typer.echo(
        f"Totals: {totals['create']} created, {totals['update']} updated, {totals['skip']} skipped"
    )


def render_lint(report: LintReport, *, verbose: bool) -> None:
    console = Console(highlight=False)
    for asset in report.assets:
        counts = asset.counts
        table = Table(
            title=Text.assemble(
                (f"{asset.kind.title()} Agent: {asset.identifier}", "bold cyan"),
                (f"\nSource target: {asset.source_target} | File: {asset.path}", "dim"),
            ),
            show_lines=False,
            # padding=(0, 1,1,1),
        )
        table.add_column("#", justify="right", style="dim", no_wrap=True)
        table.add_column("Result", no_wrap=True)
        table.add_column("Rule")
        table.add_column("Details")
        table.add_row(
            "",
            f"[{OUTCOME_STYLES[asset.outcome]}]{asset.outcome.upper()}[/]",
            "Summary",
            f"{counts['pass']} pass  {counts['warn']} warn  {counts['error']} error  "
            f"{counts['skipped']} skipped\n",
        )
        for check in asset.checks:
            if not verbose and check.status == "pass" and not check.advisory:
                continue
            message = check.message
            if check.advisory:
                message = f"{message}\n[bold yellow]Advisory: {check.advisory}[/]"
            if check.remediation:
                message = f"{message}\n[dim]Fix: {check.remediation}[/]"
            table.add_row(
                f"{check.id:02d}",
                f"[{OUTCOME_STYLES[check.status]}]{check.status.upper()}[/]",
                check.name,
                message + "\n",
            )
        console.print(table)


def report_json(report: LintReport | PolicyReport, command: str) -> str:
    payload = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "command": command,
        "generated_at": datetime.now(UTC).isoformat(),
        **report.to_dict(),
    }
    return json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def write_output(path: Path, text: str) -> None:
    """Write a generated file, replacing an existing file but never a directory."""
    if path.is_dir():
        raise LampError(f"Output path is a directory: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def render_policy(report: PolicyReport, *, verbose: bool) -> None:
    console = Console(highlight=False)
    for check in report.checks:
        if check.status in {"pass", "skipped"} and not verbose:
            continue
        detail = (
            check.reason if check.status == "skipped" else (f"{check.violation_count} violation(s)")
        )
        console.print(
            Text(
                f"{check.status.upper()} {check.name}: {detail}", style=OUTCOME_STYLES[check.status]
            )
        )
        if check.description:
            console.print(Text(check.description))
        if check.rows:
            table = Table()
            for column in check.rows[0]:
                table.add_column(Text(column))
            for row in check.rows:
                table.add_row(*(Text(str(value)) for value in row.values()))
            console.print(table)
    summary = report.summary
    typer.echo(
        f"Policy checks: {summary['pass']} pass, {summary['warn']} warn, "
        f"{summary['error']} error, {summary['skipped']} skipped; "
        f"{summary['violations']} violation(s)"
    )
