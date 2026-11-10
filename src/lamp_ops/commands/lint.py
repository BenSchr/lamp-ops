from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

import typer

from lamp_ops.cli import guarded, state
from lamp_ops.linting import lint_assets
from lamp_ops.reporting import render_lint, report_json, write_output

OutputFormat = Literal["text", "json"]


def lint(
    ctx: typer.Context,
    assets: Annotated[
        list[str] | None,
        typer.Argument(help="Optional asset names, or root-relative paths of configured assets."),
    ] = None,
    paths: Annotated[
        list[Path] | None,
        typer.Option(
            "--path",
            envvar="LAMP_LINT_PATH",
            help=(
                "Genie JSON file, or directory searched recursively for *.geniespace.json; "
                "repeatable. Replaces lint.path; needs no Lamp config."
            ),
        ),
    ] = None,
    dab_resource_path: Annotated[
        Path | None,
        typer.Option(
            "--dab-resource-path",
            help="Bundle resource YAML directory for agent descriptions; overrides the config.",
        ),
    ] = None,
    target: Annotated[
        str | None,
        typer.Option(
            "--target",
            "-t",
            envvar="LAMP_LINT_TARGET",
            help=(
                "Target to lint: promoted copies of configured assets, and target-scoped "
                "bundle descriptions. Defaults to lint.target, then the source target."
            ),
        ),
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
    """Lint configured assets, or Genie JSON from --path or lint.path."""
    command = f"lamp {ctx.info_name}"

    def operation() -> None:
        report = lint_assets(
            state(ctx).config_path,
            assets,
            paths=paths,
            dab_resource_path=dab_resource_path,
            target=target,
            profile=profile,
            offline=offline,
        )
        payload = report_json(report, command)
        if output == "json":
            typer.echo(payload, nl=False)
        else:
            render_lint(report, verbose=verbose)
        if output_path:
            write_output(output_path, payload)
        if report.has_errors:
            raise typer.Exit(1)

    guarded(ctx, operation)
