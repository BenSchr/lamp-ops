from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from lamp_ops.cli import guarded
from lamp_ops.config_schema import schema_json, write_schema


def config_schema(
    ctx: typer.Context,
    output_path: Annotated[
        Path | None, typer.Option("--output-path", help="Write schema to this path.")
    ] = None,
    force: Annotated[bool, typer.Option(help="Overwrite an existing output file.")] = False,
) -> None:
    """Print or write the complete lamp_config.yaml JSON Schema."""

    def operation() -> None:
        if output_path is None:
            typer.echo(schema_json(), nl=False)
        else:
            write_schema(output_path, force=force)
            typer.echo(f"Wrote {output_path}")

    guarded(ctx, operation)
