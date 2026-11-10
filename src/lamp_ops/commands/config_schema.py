from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from lamp_ops.cli import guarded
from lamp_ops.reporting import write_output


def config_schema(
    ctx: typer.Context,
    output_path: Annotated[
        Path | None, typer.Option("--output-path", help="Write schema to this path.")
    ] = None,
) -> None:
    """Print or write the complete lamp_config.yaml JSON Schema."""

    def operation() -> None:
        # Loaded here because the schema includes every policy preset model.
        from lamp_ops.config_schema import schema_json

        if output_path is None:
            typer.echo(schema_json(), nl=False)
        else:
            write_output(output_path, schema_json())
            typer.echo(f"Wrote {output_path}")

    guarded(ctx, operation)
