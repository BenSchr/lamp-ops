from __future__ import annotations

import typer

from lamp_ops.commands.promote_all import Assets, DryRun, Force, run_promotion


def promote_dashboards(
    ctx: typer.Context, assets: Assets = None, dry_run: DryRun = False, force: Force = False
) -> None:
    """Promote configured dashboard assets."""
    run_promotion(ctx, ("dashboards",), assets, dry_run=dry_run, force=force)
