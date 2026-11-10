"""Run every supported asset linter."""

from collections.abc import Sequence
from pathlib import Path

from lamp_ops.config import find_config, load_config, load_optional_config
from lamp_ops.errors import LampError
from lamp_ops.linting.genie import lint_genie
from lamp_ops.linting.results import LintReport


def lint_assets(
    config_path: Path | None,
    selected: list[str] | None = None,
    *,
    paths: Sequence[Path] | None = None,
    dab_resource_path: Path | None = None,
    target: str | None = None,
    profile: str | None = None,
    offline: bool = False,
) -> LintReport:
    """Run all supported linters on configured assets, or on explicit paths.

    Explicit `paths` need no Lamp configuration; one that is passed or discovered
    still tunes rules, exclusions, bundle resources, and the default bundle target.
    """
    if paths:
        config = load_optional_config(config_path)
    else:
        if config_path is None:
            try:
                config_path = find_config()
            except LampError as error:
                raise LampError(f"{error}; pass --path to lint Genie JSON without one") from error
        config = load_config(config_path)
    return LintReport(
        lint_genie(
            config,
            selected,
            paths=paths,
            dab_resource_path=dab_resource_path,
            target=target,
            profile=profile,
            offline=offline,
        )
    )


__all__ = ["lint_assets"]
