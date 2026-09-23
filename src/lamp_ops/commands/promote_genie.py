from __future__ import annotations

from typing import Annotated

import typer

from lamp_ops.cli import guarded, state
from lamp_ops.config import load_config
from lamp_ops.promotion import execute_plan, plan_promotion
from lamp_ops.reporting import render_promotion


def promote_genie(
    ctx: typer.Context,
    assets: Annotated[
        list[str] | None, typer.Argument(help="Optional asset names or paths.")
    ] = None,
    dry_run: Annotated[bool, typer.Option(help="Print the plan without writing files.")] = False,
    force: Annotated[bool, typer.Option(help="Update regardless of modification time.")] = False,
) -> None:
    """Promote configured Genie assets."""

    def operation() -> None:
        config = load_config(state(ctx).config_path)
        plan = plan_promotion(config, ("genie",), assets, force=force)
        execute_plan(plan, dry_run=dry_run)
        render_promotion(plan, config.root, dry_run=dry_run)

    guarded(ctx, operation)
