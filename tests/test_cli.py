from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import yaml
from typer.testing import CliRunner

from lamp_ops.cli import app

runner = CliRunner()


def invoke(bundle: Path, *args: str):
    return runner.invoke(app, ["--config", str(bundle / "lamp_config.yaml"), *args])


def test_cli_import_does_not_load_databricks_sdk() -> None:
    code = "import sys, lamp_ops.cli; print('databricks.sdk' in sys.modules)"
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "False"


def ready_space() -> dict:
    return {
        "version": 2,
        "description": (
            "This sales analytics Genie agent supports finance teams with governed revenue "
            "and customer analysis across their core reporting workflows."
        ),
        "data_sources": {
            "tables": [
                {
                    "identifier": "catalog.schema.sales",
                    "description": ["Curated sales facts"],
                    "column_configs": [
                        {
                            "column_name": "customer",
                            "description": ["Customer name"],
                            "enable_entity_matching": True,
                            "enable_format_assistance": True,
                        }
                    ],
                }
            ]
        },
        "instructions": {
            "text_instructions": [
                {
                    "id": "1" * 32,
                    "content": [
                        "Use fiscal dates and explain revenue results with the relevant "
                        "customer and reporting context."
                    ],
                }
            ],
            "example_question_sqls": [
                {
                    "id": "2" * 32,
                    "question": ["Sales?"],
                    "sql": ["SELECT 1"],
                    "usage_guidance": ["Use for total revenue questions."],
                }
            ],
            "sql_snippets": {"filters": [{"id": "3" * 32, "expression": ["region = 'US'"]}]},
        },
        "benchmarks": {
            "questions": [
                {
                    "id": f"{index:032d}",
                    "question": [f"Sales question {index}?"],
                    "answer": [{"format": "SQL", "content": ["SELECT 1"]}],
                }
                for index in range(20)
            ]
        },
    }


def test_help_has_no_promotion_side_effects(bundle: Path) -> None:
    result = invoke(bundle, "promote", "dashboards", "--help")
    assert result.exit_code == 0
    assert "Promote configured dashboard assets" in result.output
    assert not (bundle / "src/dashboards/test/sales.lvdash.json").exists()
    assert not (bundle / "src/genie/test/sales.geniespace.json").exists()


def test_promote_dry_run_stale_and_force(bundle: Path) -> None:
    dry_run = invoke(bundle, "promote", "dashboards", "--dry-run")
    assert dry_run.exit_code == 0
    assert "DRY-RUN CREATE" in dry_run.output
    assert not (bundle / "src/dashboards/test/sales.lvdash.json").exists()

    created = invoke(bundle, "promote", "dashboards")
    assert created.exit_code == 0
    assert "2 created" in created.output
    skipped = invoke(bundle, "promote", "dashboards")
    assert skipped.exit_code == 0
    assert "2 skipped" in skipped.output
    forced = invoke(bundle, "promote", "dashboards", "--force")
    assert forced.exit_code == 0
    assert "2 updated" in forced.output


def test_lint_shows_warnings_without_verbose_and_has_only_ten_rules(bundle: Path) -> None:
    result = invoke(bundle, "lint", "--offline")
    assert result.exit_code == 0
    assert "WARN" in result.output
    assert "Fix:" in result.output


def test_lint_displays_source_target_and_file_above_table(bundle: Path) -> None:
    target = bundle / "src/genie/test/sales.geniespace.json"
    target.parent.mkdir(parents=True)
    target.write_text("{}", encoding="utf-8")
    for command in ("lint", "rub"):
        result = invoke(bundle, command, "--offline")
        assert result.exit_code == 0
        assert "Source target: dev | File: genie/dev/sales.geniespace.json" in result.output
        assert result.output.index("File: genie/dev/sales.geniespace.json") < result.output.index(
            "Summary"
        )
        assert "genie/test/sales.geniespace.json" not in result.output
        payload = json.loads(invoke(bundle, command, "--offline", "--output", "json").output)
        assert payload["summary"]["assets"] == 1
        assert payload["assets"][0]["source_target"] == "dev"


def test_lint_report_and_rub_share_success_path(bundle: Path) -> None:
    source = bundle / "src/genie/dev/sales.geniespace.json"
    source.write_text(json.dumps(ready_space()), encoding="utf-8")
    output = bundle / "reports/genie.json"

    linted = invoke(bundle, "lint", "--offline", "--output-path", str(output))
    rubbed = invoke(bundle, "rub", "--offline")
    assert linted.exit_code == rubbed.exit_code == 0
    assert "Genie Agent: sales" in linted.output
    assert "PASS" in linted.output
    assert "Genie Agent: sales" in rubbed.output
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "3.0"
    assert payload["command"] == "lamp lint"
    assert payload["summary"] == {
        "assets": 1,
        "outcomes": {"error": 0, "pass": 1, "warn": 0},
        "checks": {"error": 0, "pass": 8, "skipped": 2, "warn": 0},
    }
    assert len(payload["assets"][0]["checks"]) == 10
    assert {check["status"] for check in payload["assets"][0]["checks"]} == {
        "pass",
        "skipped",
    }
    assert "score" not in payload["assets"][0]
    assert "maturity" not in payload["assets"][0]


def test_error_severity_is_blocking(bundle: Path) -> None:
    config = bundle / "lamp_config.yaml"
    values = yaml.safe_load(config.read_text(encoding="utf-8"))
    values["dab_resource_path"] = "resources"
    values["lint"]["genie"] = {
        "rules": {"agent_description": {"enabled": True, "severity": "error", "threshold": 0}},
    }
    config.write_text(yaml.safe_dump(values), encoding="utf-8")
    resources = bundle / "resources"
    resources.mkdir()
    (resources / "sales.yml").write_text(
        "resources:\n  genie_spaces:\n    sales:\n      description: ''\n", encoding="utf-8"
    )
    result = invoke(bundle, "lint", "--offline")
    assert result.exit_code == 1
    assert "ERROR" in result.output


def test_warning_is_non_blocking(bundle: Path) -> None:
    source = bundle / "src/genie/dev/sales.geniespace.json"
    source.write_text(json.dumps(ready_space()), encoding="utf-8")
    config = bundle / "lamp_config.yaml"
    values = yaml.safe_load(config.read_text(encoding="utf-8"))
    values["lint"]["genie"] = {
        "rules": {
            "benchmarks": {
                "enabled": True,
                "severity": "warn",
                "threshold": 0,
                "minimum_questions": 21,
                "recommended_questions": 25,
            }
        }
    }
    config.write_text(yaml.safe_dump(values), encoding="utf-8")
    result = invoke(bundle, "lint", "--offline")
    assert result.exit_code == 0
    assert "1 warn" in result.output


def test_lint_exclusion_fails_clearly(bundle: Path) -> None:
    config = bundle / "lamp_config.yaml"
    values = yaml.safe_load(config.read_text(encoding="utf-8"))
    values["lint"]["genie"] = {"exclusions": ["sales"]}
    config.write_text(yaml.safe_dump(values), encoding="utf-8")
    result = invoke(bundle, "lint", "--offline")
    assert result.exit_code == 1
    assert "No non-excluded genie assets" in result.output


def test_lint_and_rub_expose_databricks_options(bundle: Path) -> None:
    for command in ("lint", "rub"):
        result = invoke(bundle, command, "--help")
        assert result.exit_code == 0
        assert "--profile" in result.output
        assert "-p" in result.output
        assert "--offline" in result.output


def test_short_profile_option_is_forwarded(bundle: Path, monkeypatch) -> None:
    source = bundle / "src/genie/dev/sales.geniespace.json"
    source.write_text(json.dumps(ready_space()), encoding="utf-8")
    profiles = []

    def client_factory(*, profile: str):
        profiles.append(profile)
        raise ValueError("confidential auth error")

    monkeypatch.setattr("databricks.sdk.WorkspaceClient", client_factory)
    for command in ("lint", "rub"):
        result = invoke(bundle, command, "-p", "development", "--output", "json")
        assert result.exit_code == 0
        assert "confidential" not in result.output
        assert "Databricks authentication unavailable" in result.output
    assert profiles == ["development", "development"]


def test_schema_stdout_and_overwrite_protection(bundle: Path) -> None:
    stdout = invoke(bundle, "config", "schema")
    assert stdout.exit_code == 0
    assert json.loads(stdout.output)["title"] == "Lamp Configuration"
    output = bundle / "schema/lamp.json"
    first = invoke(bundle, "config", "schema", "--output-path", str(output))
    second = invoke(bundle, "config", "schema", "--output-path", str(output))
    forced = invoke(bundle, "config", "schema", "--output-path", str(output), "--force")
    assert first.exit_code == forced.exit_code == 0
    assert second.exit_code == 1
    assert "Refusing to overwrite" in second.output
