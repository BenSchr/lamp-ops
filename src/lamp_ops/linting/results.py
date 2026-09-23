"""Generic lint result models."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

Outcome = Literal["pass", "warn", "error"]
OUTCOMES: tuple[Outcome, ...] = ("pass", "warn", "error")
CheckStatus = Literal["pass", "warn", "error", "skipped"]
CHECK_STATUSES: tuple[CheckStatus, ...] = (*OUTCOMES, "skipped")


@dataclass(frozen=True)
class CheckResult:
    id: int
    name: str
    status: CheckStatus
    message: str
    remediation: str | None = None
    advisory: str | None = None


@dataclass(frozen=True)
class AssetReport:
    kind: str
    identifier: str
    path: Path
    checks: tuple[CheckResult, ...]
    source_target: str | None = None

    @property
    def counts(self) -> dict[str, int]:
        return {
            status: sum(check.status == status for check in self.checks)
            for status in CHECK_STATUSES
        }

    @property
    def outcome(self) -> Outcome:
        if self.counts["error"]:
            return "error"
        if self.counts["warn"]:
            return "warn"
        return "pass"


@dataclass(frozen=True)
class LintReport:
    assets: tuple[AssetReport, ...]

    @property
    def has_errors(self) -> bool:
        return any(asset.outcome == "error" for asset in self.assets)

    @property
    def summary(self) -> dict[str, Any]:
        asset_outcomes = {
            status: sum(asset.outcome == status for asset in self.assets) for status in OUTCOMES
        }
        check_outcomes = {
            status: sum(asset.counts[status] for asset in self.assets) for status in CHECK_STATUSES
        }
        return {
            "assets": len(self.assets),
            "outcomes": asset_outcomes,
            "checks": check_outcomes,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "assets": [
                {
                    "kind": asset.kind,
                    "identifier": asset.identifier,
                    "path": str(asset.path),
                    "source_target": asset.source_target,
                    "outcome": asset.outcome,
                    "counts": asset.counts,
                    "checks": [asdict(check) for check in asset.checks],
                }
                for asset in self.assets
            ],
            "summary": self.summary,
        }
