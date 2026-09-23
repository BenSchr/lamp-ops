from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
import yaml
from databricks.sdk import WorkspaceClient

from lamp_ops.config import LampError, SourceCountRuleConfig, load_config
from lamp_ops.linting.genie.rules import source_count
from lamp_ops.linting.genie.scanner import lint_genie


def sources(count: int) -> dict:
    return {"data_sources": {"tables": [{"name": f"table_{index}"} for index in range(count)]}}


def test_source_count_matches_workbench_boundaries() -> None:
    config = SourceCountRuleConfig(maximum_sources=12, warning_sources=9)
    assert source_count(sources(0), config)[0] == "fail"
    assert source_count(sources(1), config)[0] == "pass"
    assert source_count(sources(8), config)[0] == "pass"
    outcome, _, advisory = source_count(sources(9), config)
    assert outcome == "pass"
    assert advisory and "Approaching the maximum" in advisory
    outcome, _, advisory = source_count(sources(12), config)
    assert outcome == "pass"
    assert advisory and "Approaching the maximum" in advisory
    assert source_count(sources(13), config)[0] == "fail"


def test_threshold_counts_failing_agents_before_error(tmp_path: Path) -> None:
    source_dir = tmp_path / "genie/dev"
    source_dir.mkdir(parents=True)
    for name in ("one", "two"):
        (source_dir / f"{name}.geniespace.json").write_text(
            json.dumps(sources(1)), encoding="utf-8"
        )
    resource_dir = tmp_path / "resources/genie_agents"
    resource_dir.mkdir(parents=True)
    (resource_dir / "spaces.yml").write_text(
        yaml.safe_dump(
            {"resources": {"genie_spaces": {name: {"description": ""} for name in ("one", "two")}}}
        ),
        encoding="utf-8",
    )
    config_path = tmp_path / "lamp_config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "schema_version": 2,
                "dab_resource_path": "resources/genie_agents",
                "assets": {"genie": {"path": "genie"}},
                "lint": {
                    "genie": {
                        "rules": {
                            "agent_description": {
                                "severity": "error",
                                "threshold": 1,
                            }
                        },
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    reports = lint_genie(load_config(config_path), offline=True)
    description_checks = [check for report in reports for check in report.checks if check.id == 1]
    assert len(description_checks) == 2
    assert {check.status for check in description_checks} == {"error"}


def test_bundle_description_uses_file_path_then_key_and_indexes_once(
    tmp_path: Path, monkeypatch
) -> None:
    source_dir = tmp_path / "src/genie/dev"
    source_dir.mkdir(parents=True)
    for name in ("one", "two"):
        (source_dir / f"{name}.geniespace.json").write_text("{}", encoding="utf-8")
    resource_dir = tmp_path / "resources/genie_agents"
    resource_dir.mkdir(parents=True)
    (resource_dir / "spaces.yaml").write_text(
        yaml.safe_dump(
            {
                "resources": {
                    "genie_spaces": {
                        "different_key": {
                            "file_path": "../../src/genie/dev/one.geniespace.json",
                            "description": "This bundle description covers the first Genie agent "
                            "and its audience.",
                        },
                        "one": {"description": ""},
                        "two": {
                            "file_path": "../../src/other/two.geniespace.json",
                            "description": "This bundle description covers the second Genie agent "
                            "and its audience.",
                        },
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    config_path = tmp_path / "lamp_config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "root_path": "src",
                "dab_resource_path": "resources/genie_agents",
                "assets": {"genie": {"path": "genie"}},
            }
        ),
        encoding="utf-8",
    )
    config = load_config(config_path)
    original = yaml.safe_load
    reads = []

    def tracked_load(stream):
        reads.append(stream)
        return original(stream)

    monkeypatch.setattr("lamp_ops.linting.genie.scanner.yaml.safe_load", tracked_load)
    reports = lint_genie(config, offline=True)
    assert len(reads) == 1
    assert [report.checks[0].status for report in reports] == ["pass", "pass"]


def test_description_skips_without_bundle_or_match_and_fails_when_empty(tmp_path: Path) -> None:
    source_dir = tmp_path / "genie/dev"
    source_dir.mkdir(parents=True)
    (source_dir / "one.geniespace.json").write_text("{}", encoding="utf-8")
    config_path = tmp_path / "lamp_config.yaml"
    config_path.write_text("assets:\n  genie:\n    path: genie\n", encoding="utf-8")
    assert lint_genie(load_config(config_path), offline=True)[0].checks[0].status == "skipped"

    config_path.write_text(
        "dab_resource_path: missing\nassets:\n  genie:\n    path: genie\n",
        encoding="utf-8",
    )
    assert lint_genie(load_config(config_path), offline=True)[0].checks[0].status == "skipped"

    resource_dir = tmp_path / "resources"
    resource_dir.mkdir()
    (resource_dir / "spaces.yml").write_text(
        "resources:\n  genie_spaces:\n    other:\n      description: Some other space\n",
        encoding="utf-8",
    )
    config_path.write_text(
        "dab_resource_path: resources\nassets:\n  genie:\n    path: genie\n",
        encoding="utf-8",
    )
    assert lint_genie(load_config(config_path), offline=True)[0].checks[0].status == "skipped"
    (resource_dir / "spaces.yml").write_text(
        "resources:\n  genie_spaces:\n    one:\n      description: ''\n", encoding="utf-8"
    )
    assert lint_genie(load_config(config_path), offline=True)[0].checks[0].status == "warn"


def test_bundle_resource_directory_cannot_escape_config(tmp_path: Path) -> None:
    source_dir = tmp_path / "genie/dev"
    source_dir.mkdir(parents=True)
    (source_dir / "one.geniespace.json").write_text("{}", encoding="utf-8")
    config_path = tmp_path / "lamp_config.yaml"
    config_path.write_text(
        "dab_resource_path: ../outside\nassets:\n  genie:\n    path: genie\n",
        encoding="utf-8",
    )
    with pytest.raises(LampError, match="escapes configuration directory"):
        lint_genie(load_config(config_path), offline=True)


class FakeTables:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.calls: list[str] = []
        self.error = error

    def get(self, *, full_name: str):
        self.calls.append(full_name)
        if self.error:
            raise self.error
        return SimpleNamespace(
            comment="Remote source description",
            columns=[SimpleNamespace(name="customer", comment="Remote customer description")],
        )


def shared_source_config(tmp_path: Path) -> Path:
    source_dir = tmp_path / "genie/dev"
    source_dir.mkdir(parents=True)
    data = {
        "data_sources": {
            "tables": [
                {
                    "identifier": "catalog.schema.sales",
                    "column_configs": [{"column_name": "customer"}],
                }
            ],
            "metric_views": [{"identifier": "catalog.schema.sales_metrics"}],
        }
    }
    for name in ("one", "two"):
        (source_dir / f"{name}.geniespace.json").write_text(json.dumps(data), encoding="utf-8")
    config_path = tmp_path / "lamp_config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "schema_version": 2,
                "assets": {"genie": {"path": "genie"}},
                "lint": {"genie": {}},
            }
        ),
        encoding="utf-8",
    )
    return config_path


def test_remote_metadata_is_enriched_and_cached(tmp_path: Path) -> None:
    tables = FakeTables()
    reports = lint_genie(
        load_config(shared_source_config(tmp_path)),
        client=cast(WorkspaceClient, SimpleNamespace(tables=tables)),
    )
    assert sorted(tables.calls) == ["catalog.schema.sales", "catalog.schema.sales_metrics"]
    for report in reports:
        checks = {check.id: check for check in report.checks}
        assert checks[2].status == "pass"
        assert checks[3].status == "pass"


def test_offline_skips_remote_metadata_rules(tmp_path: Path) -> None:
    tables = FakeTables(error=AssertionError("offline lookup attempted"))
    reports = lint_genie(
        load_config(shared_source_config(tmp_path)),
        offline=True,
        client=cast(WorkspaceClient, SimpleNamespace(tables=tables)),
    )
    assert tables.calls == []
    for report in reports:
        checks = {check.id: check for check in report.checks}
        assert checks[2].status == checks[3].status == "skipped"
        assert "--offline" in checks[2].message


def test_remote_metadata_error_is_cached_and_skips_rules(tmp_path: Path) -> None:
    from databricks.sdk.errors import NotFound

    tables = FakeTables(error=NotFound("missing"))
    reports = lint_genie(
        load_config(shared_source_config(tmp_path)),
        client=cast(WorkspaceClient, SimpleNamespace(tables=tables)),
    )
    assert sorted(tables.calls) == ["catalog.schema.sales", "catalog.schema.sales_metrics"]
    assert all(
        check.status == "skipped"
        for report in reports
        for check in report.checks
        if check.id in {2, 3}
    )
    assert all(
        "table not found in Unity Catalog" in check.message and "missing" not in check.message
        for report in reports
        for check in report.checks
        if check.id in {2, 3}
    )


def test_lint_only_checks_configured_source_target(tmp_path: Path) -> None:
    config_path = shared_source_config(tmp_path)
    target = tmp_path / "genie/test/one.geniespace.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(sources(0)), encoding="utf-8")
    reports = lint_genie(load_config(config_path), offline=True)
    assert {report.path.as_posix() for report in reports} == {
        "genie/dev/one.geniespace.json",
        "genie/dev/two.geniespace.json",
    }
    assert {report.source_target for report in reports} == {"dev"}


def test_lint_reports_per_asset_source_target_with_explicit_source(tmp_path: Path) -> None:
    config_path = shared_source_config(tmp_path)
    values = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    values["assets"]["genie"]["items"] = {
        "one": {"source_target": "test", "source": "custom/one.geniespace.json"}
    }
    config_path.write_text(yaml.safe_dump(values), encoding="utf-8")
    source = tmp_path / "custom/one.geniespace.json"
    source.parent.mkdir()
    source.write_text("{}", encoding="utf-8")
    reports = lint_genie(load_config(config_path), offline=True)
    assert [
        (report.identifier, report.source_target, report.path.as_posix()) for report in reports
    ] == [
        ("one", "test", "custom/one.geniespace.json"),
        ("two", "dev", "genie/dev/two.geniespace.json"),
    ]


def test_authentication_error_is_short_and_distinct(tmp_path: Path, monkeypatch) -> None:
    def failed_client():
        raise ValueError("default auth: confidential configuration details")

    monkeypatch.setattr("databricks.sdk.WorkspaceClient", failed_client)
    reports = lint_genie(load_config(shared_source_config(tmp_path)))
    assert all(
        check.message == "Databricks authentication unavailable; check --profile or log in"
        for report in reports
        for check in report.checks
        if check.id in {2, 3}
    )


def test_profile_is_forwarded_to_workspace_client(tmp_path: Path, monkeypatch) -> None:
    tables = FakeTables()
    profiles = []

    def client_factory(*, profile: str):
        profiles.append(profile)
        return SimpleNamespace(tables=tables)

    monkeypatch.setattr("databricks.sdk.WorkspaceClient", client_factory)
    lint_genie(load_config(shared_source_config(tmp_path)), profile="development")
    assert profiles == ["development"]
