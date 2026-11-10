"""Check bundle summaries against independent, externally supplied policies."""

from pathlib import Path
from typing import Annotated, Literal

import typer

from lamp_ops.cli import guarded, state
from lamp_ops.errors import LampError
from lamp_ops.reporting import render_policy, report_json, write_output


def policy(
    ctx: typer.Context,
    input_path: Annotated[
        Path, typer.Option("--input", help="JSON from databricks bundle summary -o json.")
    ],
    policy_path: Annotated[
        list[Path] | None,
        typer.Option(
            "--policy-path",
            envvar="LAMP_POLICY_PATH",
            help=(
                "Policy directory, YAML or SQL file; repeatable. "
                "Defaults to the Lamp config policies, or ./policies without a config."
            ),
        ),
    ] = None,
    schema: Annotated[
        Path | None,
        typer.Option("--schema", help="Optional bundle JSON Schema for typed resource tables."),
    ] = None,
    param: Annotated[
        list[str] | None,
        typer.Option("--param", help="Override a bound SQL parameter as NAME=JSON; repeatable."),
    ] = None,
    target: Annotated[
        str | None,
        typer.Option(
            "--target", "-t", help="Target if absent from summary; must match if present."
        ),
    ] = None,
    output: Annotated[
        Literal["text", "json"], typer.Option("--output", "-o", help="Console report format.")
    ] = "text",
    output_path: Annotated[
        Path | None, typer.Option("--output-path", help="Also write a JSON report to this path.")
    ] = None,
    store: Annotated[
        Path | None,
        typer.Option("--store", help="Save query tables to a DuckDB file for exploration."),
    ] = None,
    verbose: Annotated[
        bool, typer.Option("--verbose", "-v", help="Also show passing and skipped checks.")
    ] = False,
) -> None:
    """Run SQL checks; returned rows are violations. No Lamp config is required."""

    def operation() -> None:
        # Loaded here so other commands start without the policy models and DuckDB.
        from lamp_ops.policy.loader import config_policy_source, parse_parameters
        from lamp_ops.policy.runner import check_policies

        if (
            store is not None
            and output_path is not None
            and store.resolve() == output_path.resolve()
        ):
            raise LampError("--store and --output-path must use different paths")
        # Explicit or CI-injected paths replace every repository-controlled policy source.
        paths, config = (
            (policy_path, None) if policy_path else config_policy_source(state(ctx).config_path)
        )
        report = check_policies(
            input_path,
            paths,
            config=config,
            schema_path=schema,
            store_path=store,
            parameters=parse_parameters(param or []),
            target=target,
        )
        payload = report_json(report, "lamp policy")
        if output == "json":
            typer.echo(payload, nl=False)
        else:
            render_policy(report, verbose=verbose)
        if output_path is not None:
            write_output(output_path, payload)
        if report.has_errors:
            raise typer.Exit(1)

    guarded(ctx, operation)
