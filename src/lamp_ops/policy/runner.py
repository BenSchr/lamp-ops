"""Execute external checks with zero rows meaning no policy violations."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from lamp_ops.config import LampConfig
from lamp_ops.errors import LampError
from lamp_ops.linting.results import CHECK_STATUSES, CheckStatus
from lamp_ops.policy.database import create_database, read_object
from lamp_ops.policy.loader import load_checks


@dataclass(frozen=True)
class CheckReport:
    name: str
    description: str
    source: str
    status: CheckStatus
    violation_count: int
    rows: tuple[dict[str, Any], ...]
    # Why the check did not run; set only for skipped checks.
    reason: str | None = None


@dataclass(frozen=True)
class PolicyReport:
    input: str
    input_sha256: str
    target: str | None
    checks: tuple[CheckReport, ...]

    @property
    def has_errors(self) -> bool:
        return any(check.status == "error" for check in self.checks)

    @property
    def summary(self) -> dict[str, int]:
        return {
            "checks": len(self.checks),
            "violations": sum(check.violation_count for check in self.checks),
            **{
                status: sum(check.status == status for check in self.checks)
                for status in CHECK_STATUSES
            },
        }

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "summary": self.summary}


def check_policies(
    input_path: Path,
    checks_paths: list[Path],
    *,
    schema_path: Path | None = None,
    store_path: Path | None = None,
    parameters: dict[str, Any] | None = None,
    target: str | None = None,
    config: LampConfig | None = None,
) -> PolicyReport:
    # Imported here so commands other than `lamp policy` start without loading DuckDB.
    import duckdb

    items = load_checks(checks_paths, parameters or {}, config)
    document = read_object(input_path)
    schema = read_object(schema_path) if schema_path is not None else None
    if store_path is not None:
        if store_path.is_dir():
            raise LampError(f"Policy store is a directory: {store_path}")
        store_path.parent.mkdir(parents=True, exist_ok=True)
        # A stale store would already contain the query tables.
        store_path.unlink(missing_ok=True)
    reports: list[CheckReport] = []
    used_parameters: set[str] = set()
    active_check = "loading bundle summary/schema"
    try:
        with duckdb.connect(
            str(store_path.resolve()) if store_path is not None else ":memory:",
            config={
                "enable_external_access": False,
                "autoinstall_known_extensions": False,
                "autoload_known_extensions": False,
            },
        ) as connection:
            summary_target = create_database(connection, document, schema)
            if target is not None and summary_target is not None and target != summary_target:
                raise LampError(
                    f"Target {target!r} does not match summary target {summary_target!r}"
                )
            target = target or summary_target
            connection.execute("UPDATE summary SET target = ?", [target])
            connection.execute("SET lock_configuration = true")
            for item in items:
                active_check = item.name
                statements = connection.extract_statements(item.sql)
                if len(statements) != 1 or statements[0].type != duckdb.StatementType.SELECT:
                    raise LampError(f"{item.name}: a check must contain exactly one SELECT query")
                names = statements[0].named_parameters
                used_parameters.update(names)
                missing = names - item.parameters.keys()
                if missing:
                    raise LampError(
                        f"{item.name}: missing parameters: {', '.join(sorted(missing))}"
                    )
                if item.config.targets and target is None:
                    raise LampError(
                        f"{item.name}: target filter requires bundle.target or --target"
                    )
                if item.config.targets and target not in item.config.targets:
                    reason = f"target {target} is not in the check's targets: " + ", ".join(
                        item.config.targets
                    )
                    reports.append(
                        CheckReport(
                            item.name, item.description, str(item.path), "skipped", 0, (), reason
                        )
                    )
                    continue
                cursor = connection.execute(
                    item.sql, {name: item.parameters[name] for name in names}
                )
                columns = [column[0] for column in cursor.description]
                if len(columns) != len(set(columns)):
                    raise LampError(f"{item.name}: result column names must be unique")
                rows = tuple(dict(zip(columns, row, strict=True)) for row in cursor.fetchall())
                # Normalize native dates, decimals, and nested values for both report formats.
                rows = tuple(json.loads(json.dumps(row, default=str)) for row in rows)
                reports.append(
                    CheckReport(
                        item.name,
                        item.description,
                        str(item.path),
                        item.config.severity if rows else "pass",
                        len(rows),
                        rows,
                    )
                )
    except duckdb.Error as error:
        raise LampError(f"Policy {active_check}: {error}") from error
    unused = (parameters or {}).keys() - used_parameters
    if unused:
        raise LampError(f"Unused CLI parameters: {', '.join(sorted(unused))}")
    if not any(report.status != "skipped" for report in reports):
        raise LampError("No policy checks apply to this target")
    return PolicyReport(
        str(input_path),
        hashlib.sha256(input_path.read_bytes()).hexdigest(),
        target,
        tuple(reports),
    )
