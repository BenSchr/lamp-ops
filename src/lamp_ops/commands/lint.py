from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

import typer

from lamp_ops.cli import guarded, state
from lamp_ops.config import load_config
from lamp_ops.linting import lint_assets
from lamp_ops.reporting import render_lint, render_lint_json, write_lint_report

OutputFormat = Literal["text", "json"]


def run_lint(
    ctx: typer.Context,
    assets: list[str] | None,
    verbose: bool,
    output_format: OutputFormat,
    output_path: Path | None,
    command: str,
    profile: str | None,
    offline: bool,
) -> None:
    def operation() -> None:
        config = load_config(state(ctx).config_path)
        report = lint_assets(config, assets, profile=profile, offline=offline)
        if output_format == "json":
            render_lint_json(report, command)
        else:
            render_lint(report, verbose=verbose)
        if output_path:
            write_lint_report(report, output_path, command)
        if report.has_errors:
            raise typer.Exit(1)

    guarded(ctx, operation)


def lint(
    ctx: typer.Context,
    assets: Annotated[
        list[str] | None, typer.Argument(help="Optional asset names or paths.")
    ] = None,
    verbose: Annotated[
        bool, typer.Option("--verbose", "-v", help="Also show passing checks.")
    ] = False,
    output: Annotated[
        OutputFormat,
        typer.Option("--output", "-o", help="Console report format: text or json."),
    ] = "text",
    output_path: Annotated[
        Path | None,
        typer.Option("--output-path", help="Write a structured report to this path."),
    ] = None,
    profile: Annotated[
        str | None,
        typer.Option("--profile", "-p", help="Databricks CLI profile for Unity Catalog metadata."),
    ] = None,
    offline: Annotated[
        bool,
        typer.Option("--offline", help="Skip Unity Catalog metadata checks."),
    ] = False,
) -> None:
    """Lint all configured assets."""
    run_lint(ctx, assets, verbose, output, output_path, "lamp lint", profile, offline)
