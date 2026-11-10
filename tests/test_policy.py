"""Multi-step CLI workflows using real packs, JSON files and DuckDB databases."""

import json
import os
import subprocess
import sys
from contextlib import chdir
from pathlib import Path
from typing import Any

import duckdb
import yaml
from typer.testing import CliRunner

from lamp_ops.cli import app
from lamp_ops.config import load_config

runner = CliRunner()


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def write_pack(path: Path, checks: list[dict]) -> None:
    path.write_text(yaml.safe_dump({"lamp_policies": checks}), encoding="utf-8")


def run(summary: Path, checks: Path | None, *args: str, env: dict[str, str] | None = None):
    return runner.invoke(
        app,
        [
            "policy",
            "--input",
            str(summary),
            *(["--policy-path", str(checks)] if checks is not None else []),
            *args,
        ],
        env=env,
    )


def test_external_policy_pack_workflow(tmp_path: Path) -> None:
    summary = tmp_path / "summary.json"
    pack = tmp_path / "central-policies"
    pack.mkdir()
    properties = pack / "_checks.yaml"
    definitions: list[dict[str, Any]] = [
        {
            "name": "no_schemas",
            "preset": "deny_resource",
            "parameters": {"resource_types": ["schemas", "catalogs"]},
        },
        {
            "name": "job_tags",
            "preset": "require_tags",
            "parameters": {
                "resource_types": ["jobs"],
                "keys": ["owner"],
                "key_values": {"environment": "prod"},
            },
        },
        {
            "name": "job_access",
            "preset": "require_permissions",
            "parameters": {
                "resource_types": ["jobs"],
                "permissions": [{"group_name": "platform", "level": "CAN_MANAGE"}],
            },
        },
        {
            "name": "global_access",
            "preset": "require_bundle_permissions",
            "parameters": {"permissions": [{"group_name": "platform", "level": "CAN_MANAGE"}]},
        },
        {
            "name": "run_as",
            "preset": "require_fields",
            "parameters": {
                "resource_types": ["jobs"],
                "fields": ["$.run_as.service_principal_name"],
            },
        },
        {
            "name": "no_users",
            "preset": "forbid_fields",
            "parameters": {"resource_types": ["jobs"], "fields": ["$.run_as.user_name"]},
        },
        {
            "name": "concurrency",
            "preset": "allowed_field_values",
            "parameters": {
                "resource_types": ["jobs"],
                "field": "$.max_concurrent_runs",
                "values": [1, 2],
            },
        },
        {
            "name": "small",
            "sql": "limits.sql",
            "parameters": {"maximum": 2},
            "config": {"severity": "warn"},
        },
        {
            "name": "dev_only",
            "sql": "limits.sql",
            "parameters": {"maximum": 0},
            "config": {"targets": ["dev"]},
        },
    ]
    # Directory packs may spread properties and SQL across nested YAML files.
    limits, definitions = definitions[7:], definitions[:7]
    limits_properties = pack / "jobs" / "limits" / "_limits.yml"
    limits_properties.parent.mkdir(parents=True)
    write_pack(properties, definitions)
    write_pack(limits_properties, limits)
    (limits_properties.parent / "limits.sql").write_text(
        "SELECT resource_key, 'Too much concurrency' AS message FROM resource('jobs') "
        "WHERE field(config, '$.max_concurrent_runs')::INTEGER > $maximum",
        encoding="utf-8",
    )
    (pack / "bundle_defaults.sql").write_text(
        """
        SELECT 'bundle' AS resource_key, 'Bad bundle defaults' AS message
        FROM summary
        WHERE field(config, '$.workspace.host') <> 'https://example.invalid'
          OR NOT EXISTS (
              SELECT 1 FROM variables
              WHERE name = 'catalog' AND field("default", '$') = 'prod'
          )
          OR NOT EXISTS (
              SELECT 1 FROM presets
              WHERE name = 'tags' AND field(value, '$.environment') = 'prod'
          )
        """,
        encoding="utf-8",
    )
    (pack / "future.sql").write_text(
        "SELECT resource_key FROM resource('future_assets') "
        "WHERE field(config, '$.governed') IS DISTINCT FROM 'true'",
        encoding="utf-8",
    )
    document = {
        "bundle": {"name": "example", "target": "prod"},
        "workspace": {"host": "https://example.invalid"},
        "variables": {"catalog": {"default": "prod"}},
        "presets": {"tags": {"environment": "prod"}},
        "permissions": [],
        "resources": {
            "schemas": {"analytics": {"name": "analytics"}},
            "jobs": {
                "etl": {
                    "name": "ETL",
                    "tags": {"owner": "", "environment": "dev"},
                    # The group and level must belong to the same entry.
                    "permissions": [
                        {"group_name": "platform", "level": "CAN_VIEW"},
                        {"group_name": "other", "level": "CAN_MANAGE"},
                    ],
                    "run_as": {"user_name": "dev@example.invalid"},
                    "max_concurrent_runs": 3,
                }
            },
            "future_assets": {"new": {"governed": False}},
        },
    }
    write_json(summary, document)
    output = tmp_path / "reports" / "policy.json"
    failed_store = tmp_path / "violations.duckdb"
    failed = run(
        summary,
        pack,
        "--output",
        "json",
        "--output-path",
        str(output),
        "--store",
        str(failed_store),
    )
    assert failed.exit_code == 1, failed.output
    assert failed.stdout.startswith("{"), failed.output
    report = json.loads(failed.stdout)
    assert report == json.loads(output.read_text())
    assert report["summary"] == {
        "checks": 11,
        "violations": 10,
        "pass": 1,
        "warn": 1,
        "error": 8,
        "skipped": 1,
    }
    checks = {check["name"]: check for check in report["checks"]}
    assert checks["no_schemas"]["rows"][0]["resource_key"] == "resources.schemas.analytics"
    assert checks["job_tags"]["violation_count"] == 2
    assert checks["job_access"]["violation_count"] == 1
    assert checks["dev_only"]["status"] == "skipped"
    assert checks["dev_only"]["reason"] == "target prod is not in the check's targets: dev"
    assert checks["job_tags"]["reason"] is None
    assert len(report["input_sha256"]) == 64
    with duckdb.connect(str(failed_store), read_only=True) as connection:
        assert connection.execute("SELECT resource_key FROM resource('schemas')").fetchall() == [
            ("resources.schemas.analytics",)
        ]
        assert connection.execute(
            "SELECT field(config, '$.max_concurrent_runs') FROM resource('jobs')"
        ).fetchone() == ("3",)
    text = run(summary, pack)
    assert "analytics" in text.stdout and "job_tags" in text.stdout
    assert "small" in text.stdout and "dev_only" not in text.stdout
    assert "1 skipped" in text.stdout
    verbose = run(summary, pack, "--verbose")
    assert "SKIPPED dev_only: target prod is not in the check's targets: dev" in verbose.stdout

    del document["resources"]["schemas"]
    permission = {"group_name": "platform", "level": "CAN_MANAGE"}
    document["permissions"] = [permission]
    job = document["resources"]["jobs"]["etl"]
    job.update(
        tags={"owner": "team", "environment": "prod"},
        permissions=[permission],
        run_as={"service_principal_name": "deployment-sp"},
        max_concurrent_runs=2,
    )
    document["resources"]["future_assets"]["new"]["governed"] = True
    write_json(summary, document)
    passed = run(summary, pack, "--output", "json")
    assert passed.exit_code == 0, passed.output
    assert json.loads(passed.stdout)["summary"]["violations"] == 0
    store = tmp_path / "exploration" / "summary.duckdb"
    from_env = run(
        summary,
        None,
        "--store",
        str(store),
        "--output",
        "json",
        env={"LAMP_POLICY_PATH": str(pack)},
    )
    assert from_env.exit_code == 0, from_env.output
    assert json.loads(from_env.stdout)["summary"] == json.loads(passed.stdout)["summary"]
    with duckdb.connect(str(store), read_only=True) as connection:
        assert connection.execute("SELECT target FROM summary").fetchone() == ("prod",)
        assert connection.execute(
            "SELECT field(value, '$') FROM bundle WHERE name = 'target'"
        ).fetchone() == ("prod",)
        assert connection.execute(
            "SELECT key, value FROM resource_tags ORDER BY key"
        ).fetchall() == [
            ("environment", "prod"),
            ("owner", "team"),
        ]
        assert connection.execute("SELECT group_name, level FROM permissions").fetchall() == [
            ("platform", "CAN_MANAGE")
        ]
        assert connection.execute(
            "SELECT group_name, level FROM resource_permissions"
        ).fetchall() == [("platform", "CAN_MANAGE")]
        assert connection.execute(
            "SELECT name, field(\"default\", '$'), value, sensitive FROM variables"
        ).fetchall() == [("catalog", "prod", None, None)]
        assert connection.execute(
            "SELECT name, field(value, '$.environment') FROM presets"
        ).fetchall() == [("tags", "prod")]
    # Rerunning replaces the exploration store instead of failing on existing tables.
    assert run(summary, pack, "--store", str(store)).exit_code == 0, store
    explicit = run(summary, pack, env={"LAMP_POLICY_PATH": str(tmp_path / "missing-checks")})
    assert explicit.exit_code == 0, explicit.output
    missing_env = run(summary, None, env={"LAMP_POLICY_PATH": str(tmp_path / "missing-checks")})
    assert missing_env.exit_code == 1 and "Policy path does not exist" in missing_env.output
    extra = tmp_path / "extra checks.sql"
    extra.write_text("SELECT 1 WHERE false", encoding="utf-8")
    combined = run(
        summary,
        None,
        "--output",
        "json",
        env={"LAMP_POLICY_PATH": os.pathsep.join([str(pack), str(extra)])},
    )
    assert combined.exit_code == 0, combined.output
    assert json.loads(combined.stdout)["summary"]["checks"] == 12
    with chdir(tmp_path):
        Path("policies").mkdir()
        Path("policies/empty.sql").write_text("SELECT 1 WHERE false", encoding="utf-8")
        default = run(summary, None, env={"LAMP_POLICY_PATH": ""})
        assert default.exit_code == 0, default.output
        # A discovered Lamp config may point at the pack; env and --policy-path still win.
        config = Path("lamp_config.yaml")
        config.write_text(
            f"policy_path: {pack.name}\n"
            "lamp_policies: [{name: inline_sql, sql: policies/empty.sql}]\n",
            encoding="utf-8",
        )
        configured = run(summary, None, "--output", "json", env={"LAMP_POLICY_PATH": ""})
        assert json.loads(configured.stdout)["summary"]["checks"] == 12, configured.output
        explicit_only = run(summary, Path("policies"), "--output", "json")
        assert json.loads(explicit_only.stdout)["summary"]["checks"] == 1, explicit_only.output
        # Without policy_path, policies/ beside the config adds to inline checks from any cwd.
        config.write_text("lamp_policies: [{name: inline_sql, sql: policies/empty.sql}]\n")
        Path("nested").mkdir()
        with chdir("nested"):
            beside = run(summary, None, "--output", "json", env={"LAMP_POLICY_PATH": ""})
        assert json.loads(beside.stdout)["summary"]["checks"] == 2, beside.output
        # Inline policies are validated by `lamp policy` only, not whenever config loads.
        config.write_text("lamp_policies: [{name: bad, preset: unknown}]\n", encoding="utf-8")
        assert load_config(config).lamp_policies == [{"name": "bad", "preset": "unknown"}]
        invalid = run(summary, None, env={"LAMP_POLICY_PATH": ""})
        assert invalid.exit_code == 1
        assert "Invalid lamp_policies" in invalid.output
        assert "Unknown policy preset" in invalid.output
        config.write_text("policy_path: ../outside\n", encoding="utf-8")
        escaped = run(summary, None, env={"LAMP_POLICY_PATH": ""})
        assert escaped.exit_code == 1 and "escapes" in escaped.output
        config.unlink()
    job["tags"] = [
        {"key": "owner", "value": "team"},
        {"key": "environment", "value": "prod"},
    ]
    document["variables"]["catalog"]["default"] = "dev"
    write_json(summary, document)
    wrong_defaults = run(summary, pack, "--output", "json")
    assert wrong_defaults.exit_code == 1, wrong_defaults.output
    findings = {
        check["name"]: check["violation_count"]
        for check in json.loads(wrong_defaults.stdout)["checks"]
    }
    assert findings["bundle_defaults"] == 1 and findings["job_tags"] == 0
    document["variables"]["catalog"]["default"] = "prod"
    write_json(summary, document)

    # One SQL template can be instantiated repeatedly, and CLI parameters override YAML.
    custom = limits_properties.parent / "limits.sql"
    override = run(summary, custom, "--param", "maximum=1", "--output", "json")
    assert override.exit_code == 1, override.output
    assert (
        json.loads(override.stdout)["checks"][0]["rows"][0]["resource_key"] == "resources.jobs.etl"
    )
    assert run(summary, custom, "--param", "maximum=3").exit_code == 0
    limits[0]["parameters"]["maximum"] = 1
    write_pack(limits_properties, limits)
    warned = run(summary, pack, "--output", "json")
    assert warned.exit_code == 0, warned.output
    assert json.loads(warned.stdout)["summary"]["warn"] == 1
    mixed_override = run(summary, pack, "--param", "maximum=5", "--output", "json")
    assert mixed_override.exit_code == 0, mixed_override.output
    assert json.loads(mixed_override.stdout)["summary"]["warn"] == 0

    # An absent type remains queryable without a schema or a hard-coded resource registry.
    write_json(summary, {"bundle": {"target": "prod"}, "resources": {}})
    empty = run(summary, custom, "--param", "maximum=1", "--output", "json")
    assert empty.exit_code == 0, empty.output
    assert json.loads(empty.stdout)["summary"]["violations"] == 0
    assert not (tmp_path / "lamp_config.yaml").exists()

    principal = "deploy@example.invalid"
    demo_job = {
        "name": "demo setup",
        "max_concurrent_runs": 1,
        "permissions": [{"user_name": principal, "level": "IS_OWNER"}],
        "tags": {"test_tag": "bla"},
        "tasks": [
            {
                "task_key": "create_demo_data",
                "notebook_task": {"notebook_path": "/Workspace/demo/files/setup"},
            }
        ],
    }
    demo_summary = {
        "bundle": {"target": "dev", "environment": "dev"},
        "python": {
            "resources": ["resources.pydabs_job:load_resources"],
            "mutators": ["lamp_ops.bundles:mutate_dashboards"],
        },
        "experimental": {"python": {"mutators": ["platform.legacy:govern"]}},
        "permissions": [{"user_name": principal, "level": "CAN_MANAGE"}],
        "presets": {"tags": {"test_tag": "bla"}},
        "variables": {
            "catalog": {"default": "demo_dev", "value": "demo_dev"},
            "lamp_replacements": {
                "type": "complex",
                "default": {"demo_dev": "demo_prod"},
                "value": {"demo_dev": "demo_prod"},
            },
            "warehouse_id": {"lookup": {"warehouse": "Example Warehouse"}, "value": "123"},
        },
        "resources": {
            "jobs": {
                "setup_demo": {**demo_job, "id": "123"},
                "setup_demo_pydabs": {**demo_job, "modified_status": "created"},
            }
        },
    }
    demo_checks = [
        {
            "name": "no_schemas",
            "preset": "deny_resource",
            "parameters": {"resource_types": ["schemas"]},
        },
        {
            "name": "job_tags",
            "preset": "require_tags",
            "parameters": {"resource_types": ["jobs"], "key_values": {"test_tag": "bla"}},
        },
        {
            "name": "bundle_access",
            "preset": "require_bundle_permissions",
            "parameters": {"permissions": [{"user_name": principal, "level": "CAN_MANAGE"}]},
        },
        {
            "name": "preset_tags",
            "preset": "require_preset_tags",
            "parameters": {"keys": ["test_tag"], "key_values": {"test_tag": "bla"}},
        },
        {
            "name": "job_access",
            "preset": "require_permissions",
            "parameters": {
                "resource_types": ["jobs"],
                "permissions": [{"user_name": principal, "level": "CAN_MANAGE"}],
            },
        },
        {
            "name": "mutators",
            "preset": "require_mutators",
            "parameters": {
                "mutators": ["lamp_ops.bundles:mutate_dashboards", "platform.legacy:govern"]
            },
        },
        {
            "name": "platform_mutator",
            "preset": "require_mutators",
            "parameters": {"mutators": ["platform.policies:govern"]},
        },
        {
            "name": "summary_values",
            "sql": "summary_values.sql",
            "parameters": {"catalog": "demo_dev"},
        },
    ]
    demo_pack = tmp_path / "demo_checks.yaml"
    write_pack(demo_pack, demo_checks)
    (tmp_path / "summary_values.sql").write_text(
        """
        SELECT 'bundle' AS resource_key, 'Unexpected summary values' AS message
        FROM summary
        WHERE target IS DISTINCT FROM 'dev'
          OR (SELECT count(*) FROM resource('jobs')) <> 2
          OR (SELECT count(*) FROM resource_tags WHERE key = 'test_tag' AND value = 'bla') <> 2
          OR (SELECT count(*) FROM resource_permissions WHERE level = 'IS_OWNER') <> 2
          OR (SELECT count(*) FROM permissions WHERE level = 'CAN_MANAGE') <> 1
          OR field(config, '$.python.resources[0]')
             IS DISTINCT FROM 'resources.pydabs_job:load_resources'
          OR NOT EXISTS (
              SELECT 1 FROM variables
              WHERE name = 'catalog' AND field(value, '$') = $catalog
          )
          OR NOT EXISTS (
              SELECT 1 FROM variables
              WHERE name = 'lamp_replacements'
                AND field(value, '$.demo_dev') = 'demo_prod' AND type = 'complex'
          )
          OR NOT EXISTS (
              SELECT 1 FROM variables
              WHERE name = 'warehouse_id'
                AND field(lookup, '$.warehouse') = 'Example Warehouse'
          )
          OR NOT EXISTS (
              SELECT 1 FROM presets
              WHERE name = 'tags' AND field(value, '$.test_tag') = 'bla'
          )
        """,
        encoding="utf-8",
    )
    write_json(summary, demo_summary)
    demo_result = run(summary, demo_pack, "--output", "json")
    assert demo_result.exit_code == 1, demo_result.output
    demo_reports = {check["name"]: check for check in json.loads(demo_result.stdout)["checks"]}
    assert {name: check["status"] for name, check in demo_reports.items()} == {
        "no_schemas": "pass",
        "job_tags": "pass",
        "bundle_access": "pass",
        "preset_tags": "pass",
        "job_access": "error",
        "mutators": "pass",
        "platform_mutator": "error",
        "summary_values": "pass",
    }
    assert demo_reports["platform_mutator"]["rows"][0]["mutator"] == "platform.policies:govern"
    assert {row["resource_key"] for row in demo_reports["job_access"]["rows"]} == {
        "resources.jobs.setup_demo",
        "resources.jobs.setup_demo_pydabs",
    }

    demo_summary["presets"] = {"tags": {"test_tag": " "}}
    write_json(summary, demo_summary)
    preset_tags = run(summary, demo_pack, "--output", "json")
    preset_reports = {check["name"]: check for check in json.loads(preset_tags.stdout)["checks"]}
    assert [row["tag_key"] for row in preset_reports["preset_tags"]["rows"]] == [
        "test_tag",
        "test_tag",
    ]
    demo_summary["presets"] = {"tags": {"test_tag": "bla"}}
    demo_summary["python"]["mutators"].append("platform.policies:govern")
    write_json(summary, demo_summary)

    demo_checks[4]["parameters"]["permissions"][0]["level"] = "IS_OWNER"
    write_pack(demo_pack, demo_checks)
    assert run(summary, demo_pack).exit_code == 0
    override = run(summary, demo_pack, "--param", 'catalog="demo_prod"', "--output", "json")
    assert override.exit_code == 1, override.output
    override_reports = {check["name"]: check for check in json.loads(override.stdout)["checks"]}
    assert override_reports["summary_values"]["violation_count"] == 1
    demo_summary["variables"]["catalog"]["value"] = "demo_prod"
    write_json(summary, demo_summary)
    assert run(summary, demo_pack, "--param", 'catalog="demo_prod"').exit_code == 0

    demo_summary["resources"]["schemas"] = {"forbidden": {"name": "forbidden"}}
    write_json(summary, demo_summary)
    denied = run(summary, demo_pack, "--param", 'catalog="demo_prod"', "--output", "json")
    assert denied.exit_code == 1, denied.output
    denied_reports = {check["name"]: check for check in json.loads(denied.stdout)["checks"]}
    assert denied_reports["no_schemas"]["rows"][0]["resource_key"] == (
        "resources.schemas.forbidden"
    )


def test_resource_presets_skip_deleted_resources(tmp_path: Path) -> None:
    summary, pack = tmp_path / "summary.json", tmp_path / "checks.yaml"
    write_json(
        summary,
        {
            "bundle": {"target": "dev"},
            "resources": {
                "jobs": {
                    "kept": {"name": "kept", "max_concurrent_runs": 1, "tags": {"owner": "x"}},
                    "new": {
                        "name": "new",
                        "max_concurrent_runs": 1,
                        "tags": {"owner": "x"},
                        "modified_status": "created",
                    },
                    "removed": {"id": "123", "url": "https://x", "modified_status": "deleted"},
                }
            },
        },
    )
    jobs = {"resource_types": ["*"]}
    write_pack(
        pack,
        [
            {"name": "deny", "preset": "deny_resource", "parameters": {"resource_types": ["jobs"]}},
            {"name": "tags", "preset": "require_tags", "parameters": {**jobs, "keys": ["owner"]}},
            {
                "name": "access",
                "preset": "require_permissions",
                "parameters": {**jobs, "permissions": [{"group_name": "g", "level": "CAN_VIEW"}]},
            },
            {
                "name": "fields",
                "preset": "require_fields",
                "parameters": {**jobs, "fields": ["$.name"]},
            },
            {
                "name": "forbid",
                "preset": "forbid_fields",
                "parameters": {**jobs, "fields": ["$.id"]},
            },
            {
                "name": "values",
                "preset": "allowed_field_values",
                "parameters": {**jobs, "field": "$.max_concurrent_runs", "values": [1]},
            },
            {"name": "deletions", "sql": "deletions.sql"},
        ],
    )
    (tmp_path / "deletions.sql").write_text(
        "SELECT resource_key, modified_status AS message FROM resources "
        "WHERE modified_status IS NOT NULL ORDER BY resource_key",
        encoding="utf-8",
    )

    result = run(summary, pack, "--output", "json")

    assert result.exit_code == 1, result.output
    reports = {check["name"]: check for check in json.loads(result.stdout)["checks"]}
    assert {name: check["status"] for name, check in reports.items()} == {
        "deny": "error",
        "tags": "pass",
        "access": "error",
        "fields": "pass",
        "forbid": "pass",
        "values": "pass",
        "deletions": "error",
    }
    for name in ("deny", "access"):
        assert {row["resource_key"] for row in reports[name]["rows"]} == {
            "resources.jobs.kept",
            "resources.jobs.new",
        }
    assert [(row["resource_key"], row["message"]) for row in reports["deletions"]["rows"]] == [
        ("resources.jobs.new", "created"),
        ("resources.jobs.removed", "deleted"),
    ]

    write_json(summary, {"resources": {"jobs": {"bad": {"modified_status": 1}}}})
    invalid = run(summary, pack)
    assert invalid.exit_code == 1
    assert "resources.jobs.bad.modified_status must be a string" in invalid.output

    write_json(summary, {"bundle": {}, "variables": {"secret": {"sensitive": "yes"}}})
    invalid = run(summary, pack)
    assert invalid.exit_code == 1
    assert "variables.secret.sensitive must be a boolean" in invalid.output


def test_typed_schema_and_failure_workflow(tmp_path: Path) -> None:
    summary, schema, pack = (tmp_path / name for name in ("summary.json", "schema.json", "checks"))
    pack.mkdir()
    query = pack / "typed.sql"
    query.write_text(
        """
        SELECT _resource_key, 'Bad typed values' AS message
        FROM bundle_resources.jobs
        WHERE tags['owner'] <> 'platform'
           OR tasks[1].timeout_seconds > 60
           OR tasks[1].notebook_task.notebook_path
              IS DISTINCT FROM '/Workspace/demo/files/setup'
           OR permissions[1].level IS DISTINCT FROM 'IS_OWNER'
           OR _modified_status IS DISTINCT FROM 'created'
           OR max_concurrent_runs IS NOT NULL
        UNION ALL
        SELECT _resource_key, 'Future type is not allowed'
        FROM bundle_resources.future_resources
        """,
        encoding="utf-8",
    )
    schema_document = {
        "$defs": {
            "job": {
                "oneOf": [
                    {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "tags": {"type": "object", "additionalProperties": {"type": "string"}},
                            "max_concurrent_runs": {"$ref": "#/$defs/int"},
                            "tasks": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "task_key": {"type": "string"},
                                        "timeout_seconds": {"type": "integer"},
                                        "notebook_task": {
                                            "type": "object",
                                            "properties": {"notebook_path": {"type": "string"}},
                                        },
                                    },
                                },
                            },
                            "permissions": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "user_name": {"type": "string"},
                                        "level": {"type": "string"},
                                    },
                                },
                            },
                        },
                    },
                    {"type": "string", "pattern": "substitution"},
                ]
            },
            "int": {
                "oneOf": [
                    {"type": "integer"},
                    {"type": "string", "pattern": "substitution"},
                ]
            },
        },
        "properties": {
            "resources": {
                "type": "object",
                "properties": {
                    "jobs": {"type": "object", "additionalProperties": {"$ref": "#/$defs/job"}},
                    "future_resources": {
                        "type": "object",
                        "additionalProperties": {
                            "type": "object",
                            "properties": {"description": {"type": "string"}},
                        },
                    },
                },
            }
        },
    }
    write_json(schema, schema_document)
    document = {
        "bundle": {"target": "prod"},
        "resources": {
            "jobs": {
                "etl": {
                    "name": "ETL",
                    "tags": {"owner": "platform"},
                    "tasks": [
                        {
                            "task_key": "extract",
                            "timeout_seconds": 30,
                            "notebook_task": {"notebook_path": "/Workspace/demo/files/setup"},
                        }
                    ],
                    "permissions": [{"user_name": "deploy@example.invalid", "level": "IS_OWNER"}],
                    "modified_status": "created",
                    "unknown_future_field": {"still": "available in _config"},
                }
            }
        },
    }
    write_json(summary, document)
    typed_store = tmp_path / "typed.duckdb"
    passed = run(summary, pack, "--schema", str(schema), "--store", str(typed_store), "--verbose")
    assert passed.exit_code == 0, passed.output
    assert "PASS typed" in passed.stdout
    with duckdb.connect(str(typed_store), read_only=True) as connection:
        assert connection.execute(
            "SELECT _resource_name, tasks[1].notebook_task.notebook_path, "
            "permissions[1].level FROM bundle_resources.jobs"
        ).fetchall() == [("etl", "/Workspace/demo/files/setup", "IS_OWNER")]
        assert connection.execute(
            "SELECT count(*) FROM bundle_resources.future_resources"
        ).fetchone() == (0,)
    document["resources"]["jobs"]["etl"]["tasks"][0]["timeout_seconds"] = 90
    write_json(summary, document)
    failed = run(summary, pack, "--schema", str(schema), "--output", "json")
    assert failed.exit_code == 1, failed.output
    assert json.loads(failed.stdout)["checks"][0]["violation_count"] == 1
    document["resources"]["jobs"]["etl"]["tasks"][0]["timeout_seconds"] = 30
    document["resources"]["future_resources"] = {"audit": {}}
    write_json(summary, document)
    future = run(summary, pack, "--schema", str(schema), "--output", "json")
    assert future.exit_code == 1, future.output
    assert json.loads(future.stdout)["checks"][0]["rows"][0]["_resource_key"] == (
        "resources.future_resources.audit"
    )

    # Malformed values must not silently become SQL NULLs in typed mode.
    document["resources"]["jobs"]["etl"]["max_concurrent_runs"] = "${resources.jobs.other.id}"
    write_json(summary, document)
    invalid_type = run(summary, pack, "--schema", str(schema))
    assert invalid_type.exit_code == 1 and "loading bundle summary/schema" in invalid_type.output

    query.write_text("SELECT resource_key FROM resource('missing_future_type')", encoding="utf-8")
    assert run(summary, pack).exit_code == 0
    for sql, message in [
        ("SELECT $missing", "missing parameters"),
        ("SELECT 1; SELECT 2", "exactly one SELECT"),
        ("DELETE FROM resources", "exactly one SELECT"),
        ("SELECT * FROM absent_table", "absent_table"),
        ("SELECT * FROM read_csv('/etc/passwd')", "disabled"),
    ]:
        query.write_text(sql, encoding="utf-8")
        result = run(summary, pack)
        assert result.exit_code == 1 and message in result.output, result.output

    query.write_text("SELECT 1 WHERE false", encoding="utf-8")
    collision = tmp_path / "collision.duckdb"
    same_path = run(summary, pack, "--store", str(collision), "--output-path", str(collision))
    assert same_path.exit_code == 1 and "different paths" in same_path.output
    assert not collision.exists()
    assert run(summary, pack, "--target", "dev").exit_code == 1
    assert run(summary, pack, "--param", "limit=not-json").exit_code == 1
    assert run(summary, pack, "--param", "x=1", "--param", "x=2").exit_code == 1
    assert "Unused CLI parameters" in run(summary, pack, "--param", "typo=1").output
    for content, message in [
        ("lamp_policies: [", "Invalid policy pack"),
        ("checks: []", "Extra inputs are not permitted"),
        ("lamp_policies: [{name: bad, preset: unknown}]", "Unknown policy preset"),
        (
            "lamp_policies: [{name: bad, preset: deny_resource, parameters: {resource_types: []}}]",
            "resource_types",
        ),
        (
            "lamp_policies: [{name: bad, preset: require_fields, "
            "parameters: {resource_types: [jobs], fields: ['$.tasks[*].name']}}]",
            "scalar paths",
        ),
        ("lamp_policies: [{name: bad, sql: ../outside.sql}]", "must stay inside"),
        (
            "lamp_policies: [{name: typed, config: {targets: [dev]}}]",
            "No policy checks apply",
        ),
    ]:
        (pack / "_checks.yml").write_text(content, encoding="utf-8")
        result = run(summary, pack)
        assert result.exit_code == 1 and message in result.output, result.output
    (pack / "_checks.yml").unlink()
    for bad_document in ([], {"resources": []}, {"resources": {"jobs": []}}):
        write_json(summary, bad_document)
        result = run(summary, pack)
        assert result.exit_code == 1 and "error:" in result.output
    query.unlink()
    write_json(summary, {})
    assert "No policy checks found" in run(summary, pack).output

    # Importing/help must not make DuckDB mandatory for existing Lamp users.
    code = "import sys, lamp_ops.cli; print('duckdb' in sys.modules)"
    imported = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert imported.stdout.strip() == "False"
    assert runner.invoke(app, ["policy", "--help"]).exit_code == 0
