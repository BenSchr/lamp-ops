from __future__ import annotations

import typer

from lamp_ops.commands.promote_all import Assets, DryRun, Force, run_promotion


def promote_genie(
    ctx: typer.Context, assets: Assets = None, dry_run: DryRun = False, force: Force = False
) -> None:
    """Promote configured Genie assets."""
    run_promotion(ctx, ("genie",), assets, dry_run=dry_run, force=force)
