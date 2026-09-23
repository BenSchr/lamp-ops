"""Lamp command-line application and shared command state."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import typer

from lamp_ops.config import LampError


@dataclass(frozen=True)
class CliState:
    config_path: Path | None
    debug: bool


app = typer.Typer(
    no_args_is_help=True,
    help="Promote and lint Databricks assets.",
    context_settings={"help_option_names": ["-h", "--help"]},
)


@app.callback()
def main(
    ctx: typer.Context,
    config: Annotated[
        Path | None,
        typer.Option("--config", "-c", help="Path to lamp_config.yaml."),
    ] = None,
    debug: Annotated[bool, typer.Option(help="Show full tracebacks on errors.")] = False,
) -> None:
    """Promote and lint Databricks dashboard and Genie assets."""
    ctx.obj = CliState(config, debug)


def state(ctx: typer.Context) -> CliState:
    return ctx.ensure_object(CliState)


def guarded[T](ctx: typer.Context, operation: Callable[[], T]) -> T:
    try:
        return operation()
    except (LampError, OSError, ValueError) as error:
        if state(ctx).debug:
            raise
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(1) from error


from lamp_ops.commands import config as config_commands
from lamp_ops.commands import lint as lint_commands
from lamp_ops.commands import promote as promote_commands
from lamp_ops.commands.rub import rub

app.add_typer(promote_commands.app, name="promote")
app.add_typer(config_commands.app, name="config")
app.command(name="lint")(lint_commands.lint)
app.command()(rub)


if __name__ == "__main__":
    app()
