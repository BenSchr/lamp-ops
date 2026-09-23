"""Run every supported asset linter."""

from lamp_ops.config import LampConfig
from lamp_ops.linting.genie import lint_genie
from lamp_ops.linting.results import LintReport


def lint_assets(
    config: LampConfig,
    selected: list[str] | None = None,
    *,
    profile: str | None = None,
    offline: bool = False,
) -> LintReport:
    """Run all currently supported asset linters."""
    return LintReport(lint_genie(config, selected, profile=profile, offline=offline))


__all__ = ["lint_assets"]
