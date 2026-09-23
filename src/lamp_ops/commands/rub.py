from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from lamp_ops.commands.lint import OutputFormat, run_lint


def rub(
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
    """Polish assets with the same checks as `lint`."""
    run_lint(ctx, assets, verbose, output, output_path, "lamp rub", profile, offline)
