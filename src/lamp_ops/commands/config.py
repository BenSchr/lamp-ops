import typer

from lamp_ops.commands.config_schema import config_schema

app = typer.Typer(
    no_args_is_help=True,
    help="Inspect Lamp configuration support.",
    context_settings={"help_option_names": ["-h", "--help"]},
)
app.command(name="schema")(config_schema)
