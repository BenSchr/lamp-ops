from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from lamp_ops.cli import app

runner = CliRunner()


def invoke(bundle: Path, *args: str):
    return runner.invoke(app, ["--config", str(bundle / "lamp_config.yaml"), *args])


def test_cli_import_does_not_load_databricks_sdk_or_bundles() -> None:
    code = (
        "import sys, lamp_ops.cli; "
        "print('databricks.sdk' in sys.modules or 'databricks.bundles' in sys.modules)"
    )
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


@pytest.mark.parametrize("command", [("promote",), ("promote", "genie")])
@pytest.mark.parametrize("with_replacements", [False, True])
def test_genie_promotion_sorts_sources_after_replacement(
    bundle: Path, command: tuple[str, ...], with_replacements: bool
) -> None:
    config_path = bundle / "lamp_config.yaml"
    config = yaml.safe_load(config_path.read_text())
    replacements = {
        "dev.s.a": "prod.s.z",
        "dev.s.b": "prod.s.a",
        "/Volumes/dev/s/a": "/Volumes/prod/s/z",
        "/Volumes/dev/s/b": "/Volumes/prod/s/a",
    }
    for target in config["settings"]["targets"]:
        target["replacements"] = replacements if with_replacements else {}
    config_path.write_text(yaml.safe_dump(config))
    data = {
        "data_sources": {
            "tables": [{"identifier": "dev.s.a"}, {"identifier": "dev.s.b"}],
            "volumes": [{"path": "/Volumes/dev/s/a"}, {"path": "/Volumes/dev/s/b"}],
            "future_source": ["z", "a"],
        },
        "instructions": {"text_instructions": ["z", "a"]},
    }
    if not with_replacements:
        data["data_sources"]["tables"].reverse()
        data["data_sources"]["volumes"].reverse()
    source = bundle / "src/genie/dev/sales.geniespace.json"
    source.write_text(json.dumps(data))
    before = source.read_bytes()
    dry_run = invoke(bundle, *command, "--dry-run")
    assert dry_run.exit_code == 0, dry_run.output
    assert not (bundle / "src/genie/test").exists()

    promoted = invoke(bundle, *command)
    assert promoted.exit_code == 0, promoted.output
    catalog = "prod" if with_replacements else "dev"
    names = ["a", "z"] if with_replacements else ["a", "b"]
    for target in ("test", "prod"):
        output = json.loads((bundle / f"src/genie/{target}/sales.geniespace.json").read_text())
        assert output == {
            "data_sources": {
                "tables": [{"identifier": f"{catalog}.s.{name}"} for name in names],
                "volumes": [{"path": f"/Volumes/{catalog}/s/{name}"} for name in names],
                "future_source": ["z", "a"],
            },
            "instructions": {"text_instructions": ["z", "a"]},
        }
    assert source.read_bytes() == before


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ([], "Expected a JSON object"),
        ({"data_sources": []}, "Genie data_sources must be an object"),
        ({"data_sources": {"tables": [{}]}}, "objects with identifier"),
        ({"data_sources": {"volumes": [{"path": 1}]}}, "objects with path"),
    ],
)
def test_genie_promotion_rejects_invalid_source_ordering_fields(
    bundle: Path, data: object, message: str
) -> None:
    source = bundle / "src/genie/dev/sales.geniespace.json"
    source.write_text(json.dumps(data))
    result = invoke(bundle, "promote", "genie")
    assert result.exit_code == 1
    assert message in result.output
    assert not (bundle / "src/genie/test").exists()


def test_dashboard_promotion_preserves_source_array_order(bundle: Path) -> None:
    data = {"data_sources": {"tables": [{"identifier": "z"}, {"identifier": "a"}]}}
    source = bundle / "src/dashboards/dev/sales.lvdash.json"
    source.write_text(json.dumps(data))
    result = invoke(bundle, "promote", "dashboards")
    assert result.exit_code == 0, result.output
    output = bundle / "src/dashboards/test/sales.lvdash.json"
    assert json.loads(output.read_text()) == data


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
        assert "Target: dev | File: genie/dev/sales.geniespace.json" in result.output
        assert result.output.index("File: genie/dev/sales.geniespace.json") < result.output.index(
            "Summary"
        )
        assert "genie/test/sales.geniespace.json" not in result.output
        payload = json.loads(invoke(bundle, command, "--offline", "--output", "json").output)
        assert payload["command"] == f"lamp {command}"
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


def test_lint_finds_target_scoped_bundle_genie_space(bundle: Path) -> None:
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
        "resources:\n  genie_spaces:\n    sales:\n      description: Top-level sales agent\n"
        "targets:\n"
        "  dev:\n    resources:\n      genie_spaces:\n        sales:\n          description: ''\n"
        "  prod:\n    resources:\n      genie_spaces:\n"
        "        sales:\n          description: Prod sales agent\n",
        encoding="utf-8",
    )
    result = invoke(bundle, "lint", "--offline")
    assert result.exit_code == 1
    assert "not found in dab_resource_path" not in result.output


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


def test_schema_stdout_and_output_path(bundle: Path) -> None:
    stdout = invoke(bundle, "config", "schema")
    assert stdout.exit_code == 0
    schema = json.loads(stdout.output)
    assert schema["title"] == "Lamp Configuration"
    check_refs = schema["properties"]["lamp_policies"]["items"]["oneOf"]
    presets = {
        schema["$defs"][ref["$ref"].rsplit("/", 1)[1]]["properties"].get("preset", {}).get("const")
        for ref in check_refs
    }
    assert {"deny_resource", "require_mutators", "require_preset_tags", None} <= presets
    output = bundle / "schema/lamp.json"
    first = invoke(bundle, "config", "schema", "--output-path", str(output))
    second = invoke(bundle, "config", "schema", "--output-path", str(output))
    directory = invoke(bundle, "config", "schema", "--output-path", str(output.parent))
    assert first.exit_code == second.exit_code == 0
    assert json.loads(output.read_text(encoding="utf-8")) == schema
    assert directory.exit_code == 1 and "is a directory" in directory.output


def lint_json(*args: str) -> tuple[int, dict]:
    result = runner.invoke(app, ["lint", "--offline", "--output", "json", *args])
    return result.exit_code, json.loads(result.output) if result.exit_code == 0 else {}


def write_target_scoped_space(resources: Path, description: str) -> None:
    resources.mkdir(parents=True)
    (resources / "agents.yml").write_text(
        yaml.safe_dump(
            {
                "targets": {
                    name: {
                        "resources": {
                            "genie_spaces": {
                                "sales": {
                                    "file_path": "../genie/${var.asset_dir}/sales.geniespace.json",
                                    "description": description,
                                }
                            }
                        }
                    }
                    for name in ("dev", "prod")
                }
            }
        ),
        encoding="utf-8",
    )


def test_lint_path_needs_no_config_and_searches_directories(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    for relative in (
        "genie/dev/sales.geniespace.json",
        "genie/nested/deeper/ops.geniespace.json",
        "genie/.databricks/cached.geniespace.json",
    ):
        (tmp_path / relative).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / relative).write_text(json.dumps(ready_space()), encoding="utf-8")
    (tmp_path / "genie/dev/sales.lvdash.json").write_text("{}", encoding="utf-8")
    (tmp_path / "loose.json").write_text(json.dumps(ready_space()), encoding="utf-8")

    exit_code, payload = lint_json("--path", "genie", "--path", "loose.json", "--path", "genie")
    assert exit_code == 0
    assert [(asset["identifier"], asset["path"]) for asset in payload["assets"]] == [
        ("sales", "genie/dev/sales.geniespace.json"),
        ("ops", "genie/nested/deeper/ops.geniespace.json"),
        ("loose", "loose.json"),
    ]
    assert {asset["source_target"] for asset in payload["assets"]} == {None}
    # Without bundle resources the JSON description is evaluated; all ten rules run.
    assert {len(asset["checks"]) for asset in payload["assets"]} == {10}
    assert payload["assets"][0]["checks"][0]["status"] == "pass"

    text = runner.invoke(app, ["rub", "--offline", "--path", "loose.json"])
    assert text.exit_code == 0
    assert "File: loose.json" in text.output
    assert "Target:" not in text.output


def test_lint_path_reads_target_scoped_bundle_descriptions(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "genie/dev/sales.geniespace.json"
    source.parent.mkdir(parents=True)
    space = ready_space()
    del space["description"]
    source.write_text(json.dumps(space), encoding="utf-8")
    write_target_scoped_space(
        tmp_path / "resources", "Sales analytics agent for finance teams and their reporting."
    )

    args = ("--path", "genie", "--dab-resource-path", "resources")
    _, untargeted = lint_json(*args)
    check = untargeted["assets"][0]["checks"][0]
    assert check["status"] == "skipped"
    assert "pass --target" in check["message"]

    _, targeted = lint_json(*args, "-t", "dev")
    assert targeted["assets"][0]["source_target"] == "dev"
    assert targeted["assets"][0]["checks"][0]["status"] == "pass"

    _, missing = lint_json(*args, "--target", "staging")
    assert "not found in dab_resource_path" in missing["assets"][0]["checks"][0]["message"]


def test_lint_path_uses_discovered_config_to_tune_rules(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    for name in ("sales", "legacy"):
        path = tmp_path / f"agents/{name}.geniespace.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(ready_space()), encoding="utf-8")
    write_target_scoped_space(tmp_path / "bundle", "")
    (tmp_path / "lamp_config.yaml").write_text(
        yaml.safe_dump(
            {
                "dab_resource_path": "bundle",
                "lint": {
                    "target": "dev",
                    "genie": {
                        "exclusions": ["legacy"],
                        "rules": {
                            "benchmarks": {"enabled": False},
                            "agent_description": {"severity": "error"},
                        },
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    # Not the env/asset layout, so configured discovery finds nothing here.
    result = runner.invoke(app, ["lint", "--offline", "--path", "agents"])
    assert result.exit_code == 1, result.output
    assert "Genie Agent: sales" in result.output
    assert "legacy" not in result.output
    assert "Target: dev" in result.output
    assert "Benchmark questions" not in result.output
    assert "ERROR" in result.output


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (("other", "--path", "genie"), "Genie asset not found in lint paths: other"),
        (("--path", "missing"), "Genie path not found"),
        (("--path", "empty"), "No *.geniespace.json files found below empty"),
        (("--path", "notes.txt"), "Expected a Genie JSON file"),
        (("--path", "genie", "--dab-resource-path", "nope"), "Bundle resource directory not found"),
        ((), "pass --path to lint Genie JSON"),
        (("-t", "dev"), "pass --path to lint Genie JSON"),
    ],
)
def test_lint_path_errors_are_concise(
    tmp_path: Path, monkeypatch, args: tuple[str, ...], message: str
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "genie").mkdir()
    (tmp_path / "genie/sales.geniespace.json").write_text("{}", encoding="utf-8")
    (tmp_path / "empty").mkdir()
    (tmp_path / "notes.txt").write_text("", encoding="utf-8")
    result = runner.invoke(app, ["lint", "--offline", *args])
    assert result.exit_code == 1
    assert message in result.output


def test_lint_target_checks_promoted_copies_of_configured_assets(bundle: Path) -> None:
    config = bundle / "lamp_config.yaml"
    values = yaml.safe_load(config.read_text(encoding="utf-8"))
    values["assets"]["genie"] = {
        "items": {"sales": {"targets": [{"name": "prod", "path": "custom/sales.json"}]}}
    }
    config.write_text(yaml.safe_dump(values), encoding="utf-8")
    missing = invoke(bundle, "lint", "--offline", "--target", "prod")
    assert missing.exit_code == 1
    assert "for target prod not found" in missing.output
    assert "run lamp promote first" in missing.output

    assert invoke(bundle, "promote", "genie").exit_code == 0
    result = invoke(bundle, "lint", "--offline", "-t", "prod", "--output", "json")
    assert result.exit_code == 0, result.output
    asset = json.loads(result.output)["assets"][0]
    assert (asset["path"], asset["source_target"]) == ("custom/sales.json", "prod")

    values["lint"] = {"target": "prod"}
    config.write_text(yaml.safe_dump(values), encoding="utf-8")
    default = json.loads(invoke(bundle, "lint", "--offline", "-o", "json").output)
    assert default["assets"][0]["path"] == "custom/sales.json"
    source = json.loads(invoke(bundle, "lint", "--offline", "-t", "dev", "-o", "json").output)
    assert source["assets"][0]["path"] == "genie/dev/sales.geniespace.json"

    unpromoted = invoke(bundle, "lint", "--offline", "-t", "test")
    assert unpromoted.exit_code == 1
    assert "No configured genie assets are promoted to target test" in unpromoted.output


def test_lint_path_config_replaces_asset_layout(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "project"
    for name in ("sales", "ops"):
        path = project / f"agents/{name}.geniespace.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(ready_space()), encoding="utf-8")
    (project / "extra.json").write_text(json.dumps(ready_space()), encoding="utf-8")
    write_target_scoped_space(project / "bundle", "")
    config = project / "lamp_config.yaml"
    config.write_text(
        "lint:\n  path: agents\n  target: dev\ndab_resource_path: bundle\n", encoding="utf-8"
    )
    # Config-relative paths work from any directory that discovers the config.
    (project / "nested").mkdir()
    monkeypatch.chdir(project / "nested")

    _, payload = lint_json()
    assert [asset["identifier"] for asset in payload["assets"]] == ["ops", "sales"]
    assert {asset["source_target"] for asset in payload["assets"]} == {"dev"}
    sales = payload["assets"][1]["checks"][0]
    assert (sales["status"], "characters" in sales["message"]) == ("warn", True)

    _, selected = lint_json("sales", "-t", "prod")
    assert [asset["identifier"] for asset in selected["assets"]] == ["sales"]
    assert selected["assets"][0]["source_target"] == "prod"

    config.write_text("lint:\n  path: [agents/ops.geniespace.json, extra.json]\n", encoding="utf-8")
    _, listed = lint_json()
    assert [asset["identifier"] for asset in listed["assets"]] == ["ops", "extra"]
    _, replaced = lint_json("--path", "../agents/sales.geniespace.json")
    assert [asset["identifier"] for asset in replaced["assets"]] == ["sales"]

    config.write_text("lint:\n  path: ../outside\n", encoding="utf-8")
    escaped = runner.invoke(app, ["lint", "--offline"])
    assert escaped.exit_code == 1
    assert "escapes configuration directory" in escaped.output


def test_lint_path_and_target_environment_variables(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    for name in ("one", "two"):
        path = tmp_path / f"{name}/{name}.geniespace.json"
        path.parent.mkdir()
        path.write_text(json.dumps(ready_space()), encoding="utf-8")
    (tmp_path / "lamp_config.yaml").write_text(
        "lint:\n  path: two\n  target: dev\n", encoding="utf-8"
    )

    def identifiers(env: dict[str, str], *args: str) -> list[tuple[str, str | None]]:
        result = runner.invoke(app, ["lint", "--offline", "-o", "json", *args], env=env)
        assert result.exit_code == 0, result.output
        return [
            (asset["identifier"], asset["source_target"])
            for asset in json.loads(result.output)["assets"]
        ]

    # Environment beats the configuration; empty values count as unset; flags beat both.
    assert identifiers({"LAMP_LINT_PATH": "", "LAMP_LINT_TARGET": ""}) == [("two", "dev")]
    assert identifiers(
        {"LAMP_LINT_PATH": os.pathsep.join(["one", "two"]), "LAMP_LINT_TARGET": "prod"}
    ) == [("one", "prod"), ("two", "prod")]
    assert identifiers(
        {"LAMP_LINT_PATH": "one", "LAMP_LINT_TARGET": "prod"}, "--path", "two", "-t", "test"
    ) == [("two", "test")]


DESCRIPTION = "Sales analytics agent for finance teams and their reporting workflows."


def write_resources(directory: Path, spaces: dict) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "agents.yml").write_text(yaml.safe_dump(spaces), encoding="utf-8")


def write_space(path: Path, *, description: bool = False) -> None:
    space = ready_space()
    if not description:
        del space["description"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(space), encoding="utf-8")


def top_level(key: str, description: str = DESCRIPTION) -> dict:
    return {"resources": {"genie_spaces": {key: {"description": description}}}}


def description_check(*args: str) -> dict[str, tuple[str, str]]:
    _, payload = lint_json(*args)
    return {
        asset["identifier"]: (asset["checks"][0]["status"], asset["checks"][0]["message"])
        for asset in payload["assets"]
    }


def test_lint_discovers_resources_above_the_genie_file(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    write_space(tmp_path / "genie/dev/sales.geniespace.json")
    write_resources(
        tmp_path / "resources/genie_agents",
        {
            "targets": {
                "dev": {
                    "resources": {
                        "genie_spaces": {
                            "anything": {
                                "file_path": "../../genie/dev/sales.geniespace.json",
                                "description": DESCRIPTION,
                            }
                        }
                    }
                }
            }
        },
    )
    write_resources(tmp_path / ".hidden/resources", top_level("sales", ""))
    assert "pass --target" in description_check("--path", "genie")["sales"][1]
    assert description_check("--path", "genie", "-t", "dev")["sales"][0] == "pass"


def test_lint_discovers_resources_beside_and_below_ancestors(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    # project/src/genie/sales.geniespace.json -> project/bundle/resources (two levels down).
    write_space(tmp_path / "project/src/genie/sales.geniespace.json")
    write_resources(tmp_path / "project/bundle/resources", top_level("sales"))
    assert description_check("--path", "project")["sales"][0] == "pass"

    # The nearest level wins over a farther one.
    write_resources(tmp_path / "project/src/resources", top_level("sales", ""))
    assert description_check("--path", "project")["sales"][0] == "warn"


def test_discovery_stops_at_the_boundary_and_search_limits(tmp_path: Path, monkeypatch) -> None:
    write_space(tmp_path / "repo/a/b/c/d/sales.geniespace.json", description=True)
    write_resources(tmp_path / "repo/resources", top_level("sales", ""))
    monkeypatch.chdir(tmp_path / "repo")
    # repo/resources is four levels above d/, beyond the search.
    assert description_check("--path", "a")["sales"][0] == "pass"

    write_space(tmp_path / "repo/genie/sales.geniespace.json")
    write_resources(tmp_path / "resources", top_level("sales"))
    (tmp_path / "repo/resources/agents.yml").unlink()
    (tmp_path / "repo/resources").rmdir()
    # The working directory bounds the upward search, so tmp_path/resources is not used.
    message = description_check("--path", "genie")["sales"][1]
    assert message.startswith("No bundle resources")
    # A git root replaces the working directory as the bound, so a deeper working
    # directory still reaches resources beside the repository root.
    write_resources(tmp_path / "repo/resources", top_level("sales"))
    monkeypatch.chdir(tmp_path / "repo/genie")
    assert description_check("--path", ".")["sales"][1].startswith("No bundle resources")
    (tmp_path / "repo/.git").mkdir()
    assert description_check("--path", ".")["sales"][0] == "pass"


def test_discovered_resources_fall_back_to_json_description(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    write_space(tmp_path / "genie/sales.geniespace.json", description=True)
    write_resources(tmp_path / "resources", top_level("other", ""))
    assert description_check("--path", "genie")["sales"][0] == "pass"

    write_space(tmp_path / "genie/sales.geniespace.json")
    assert description_check("--path", "genie")["sales"] == (
        "skipped",
        "Genie bundle resource not found in discovered resources and JSON has no description",
    )
    # Explicit directories keep skipping unmatched spaces, even with a JSON description.
    write_space(tmp_path / "genie/sales.geniespace.json", description=True)
    assert description_check("--path", "genie", "--dab-resource-path", "resources")["sales"] == (
        "skipped",
        "Genie bundle resource not found in dab_resource_path",
    )


def test_each_file_uses_its_nearest_resources(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    for name, description in (("good", DESCRIPTION), ("bad", "")):
        write_space(tmp_path / f"{name}/genie/sales.geniespace.json")
        write_resources(tmp_path / f"{name}/resources", top_level("sales", description))
    _, payload = lint_json("--path", "good", "--path", "bad")
    assert [asset["checks"][0]["status"] for asset in payload["assets"]] == ["pass", "warn"]


def test_configured_resource_path_beats_discovery(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    write_space(tmp_path / "genie/sales.geniespace.json")
    write_resources(tmp_path / "resources", top_level("sales"))
    write_resources(tmp_path / "bundle", top_level("sales", ""))
    (tmp_path / "lamp_config.yaml").write_text("dab_resource_path: bundle\n", encoding="utf-8")
    assert description_check("--path", "genie")["sales"][0] == "warn"
