from __future__ import annotations

import typer

from lamp_ops.commands.promote_all import promote_all
from lamp_ops.commands.promote_dashboards import promote_dashboards
from lamp_ops.commands.promote_genie import promote_genie

app = typer.Typer(
    invoke_without_command=True,
    help="Plan and promote configured assets.",
    context_settings={"help_option_names": ["-h", "--help"]},
)
app.callback()(promote_all)
app.command(name="dashboards")(promote_dashboards)
app.command(name="genie")(promote_genie)
