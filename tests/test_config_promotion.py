from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import yaml

from lamp_ops.config import LampError, find_config, load_config
from lamp_ops.config_schema import generate_schema, write_schema
from lamp_ops.promotion import discover_assets, execute_plan, plan_promotion


def test_loads_config_relative_to_its_file(bundle: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(bundle.parent)
    config = load_config(bundle / "lamp_config.yaml")
    assert config.root == bundle / "src"
    assert discover_assets(config, "dashboards")[0].source.name == "sales.lvdash.json"


@pytest.mark.parametrize(
    "name", ["lamp_config.yaml", "lamp_config.yml", ".lamp_config.yaml", ".lamp_config.yml"]
)
def test_discovers_config_variants_in_ancestors(tmp_path: Path, name: str) -> None:
    path = tmp_path / name
    path.write_text("schema_version: 2\n", encoding="utf-8")
    child = tmp_path / "nested"
    child.mkdir()
    assert find_config(child) == path
    assert load_config(find_config(child)).config_path == path


@pytest.mark.parametrize("root_path", ["/tmp", "../outside"])
def test_rejects_unsafe_root(bundle: Path, root_path: str) -> None:
    config_path = bundle / "lamp_config.yaml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            "root_path: src", f"root_path: {root_path}"
        ),
        encoding="utf-8",
    )
    with pytest.raises(LampError, match="root_path"):
        load_config(config_path)


def test_schema_is_complete_and_safe_to_write(bundle: Path) -> None:
    schema = generate_schema()
    assert schema["title"] == "Lamp Configuration"
    assert schema["x-lamp-schema-version"] == "2.0"
    assert "assets" in schema["properties"]
    assert "dab_resource_path" in schema["properties"]
    assert "dab_resource_path" not in schema["$defs"]["GenieLintConfig"]["properties"]
    output = bundle / "schema/lamp.json"
    write_schema(output)
    assert json.loads(output.read_text(encoding="utf-8"))["title"] == "Lamp Configuration"
    with pytest.raises(LampError, match="overwrite"):
        write_schema(output)
    write_schema(output, force=True)


def test_rejects_old_nested_bundle_path(bundle: Path) -> None:
    config_path = bundle / "lamp_config.yaml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            "  genie: {}\n", "  genie:\n    dab_resource_path: resources\n", 1
        ),
        encoding="utf-8",
    )
    with pytest.raises(LampError, match="dab_resource_path"):
        load_config(config_path)


def test_plan_executes_value_only_replacements_and_stale_skip(bundle: Path) -> None:
    config = load_config(bundle / "lamp_config.yaml")
    plan = plan_promotion(config, ("dashboards",))
    assert plan.totals == {"create": 2, "update": 0, "skip": 0}
    execute_plan(plan)

    test_data = json.loads((bundle / "src/dashboards/test/sales.lvdash.json").read_text())
    assert "my_catalog_dev" in test_data
    assert test_data["query"] == "my_catalog_test.sales"

    skipped = plan_promotion(config, ("dashboards",))
    assert skipped.totals == {"create": 0, "update": 0, "skip": 2}

    forced = plan_promotion(config, ("dashboards",), force=True)
    assert forced.totals == {"create": 0, "update": 2, "skip": 0}


def test_newer_source_updates_and_dry_run_does_not_write(bundle: Path) -> None:
    config = load_config(bundle / "lamp_config.yaml")
    execute_plan(plan_promotion(config, ("dashboards",)))
    source = bundle / "src/dashboards/dev/sales.lvdash.json"
    future = source.stat().st_mtime + 10
    os.utime(source, (future, future))
    plan = plan_promotion(config, ("dashboards",))
    before = (bundle / "src/dashboards/test/sales.lvdash.json").read_text()
    execute_plan(plan, dry_run=True)
    assert plan.totals["update"] == 2
    assert (bundle / "src/dashboards/test/sales.lvdash.json").read_text() == before


def test_exclusions_apply_before_tasks(bundle: Path) -> None:
    config_path = bundle / "lamp_config.yaml"
    config_path.write_text(
        config_path.read_text().replace("dashboards: {}", "dashboards:\n    exclusions: [sales]"),
        encoding="utf-8",
    )
    config = load_config(config_path)
    with pytest.raises(LampError, match="No non-excluded"):
        plan_promotion(config, ("dashboards",))


def test_asset_mode_and_per_asset_settings_override_globals(tmp_path: Path) -> None:
    source = tmp_path / "src/dashboards/sales/dev/sales.lvdash.json"
    source.parent.mkdir(parents=True)
    source.write_text('{"value": "global asset"}', encoding="utf-8")
    config_path = tmp_path / "lamp_config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "schema_version": 2,
                "root_path": "src",
                "settings": {
                    "mode": "env",
                    "source_target": "source",
                    "targets": [
                        {
                            "name": "test",
                            "replacements": {"global": "shared", "asset": "shared"},
                        },
                        {"name": "prod"},
                    ],
                },
                "assets": {
                    "dashboards": {
                        "items": {
                            "sales": {
                                "mode": "asset",
                                "source_target": "dev",
                                "targets": [
                                    {
                                        "name": "test",
                                        "path": "custom/sales.json",
                                        "replacements": {"asset": "specific"},
                                    }
                                ],
                            }
                        }
                    },
                    "genie": {},
                },
            }
        ),
        encoding="utf-8",
    )
    config = load_config(config_path)
    plan = plan_promotion(config, ("dashboards",))
    assert len(plan.tasks) == 1
    task = plan.tasks[0]
    assert task.source == source
    assert task.target == tmp_path / "src/custom/sales.json"
    assert dict(task.substitutions) == {"global": "shared", "asset": "specific"}


def test_env_mode_is_default_and_asset_mode_adds_name_directory(tmp_path: Path) -> None:
    for path in (
        tmp_path / "src/dashboards/dev/flat.lvdash.json",
        tmp_path / "src/genie/nested/dev/nested.geniespace.json",
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")
    config_path = tmp_path / "lamp_config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "schema_version": 2,
                "root_path": "src",
                "assets": {
                    "dashboards": {},
                    "genie": {"items": {"nested": {"mode": "asset"}}},
                },
            }
        ),
        encoding="utf-8",
    )
    config = load_config(config_path)
    dashboard = plan_promotion(config, ("dashboards",)).tasks[0]
    genie = plan_promotion(config, ("genie",)).tasks[0]
    assert dashboard.source == tmp_path / "src/dashboards/dev/flat.lvdash.json"
    assert dashboard.target == tmp_path / "src/dashboards/test/flat.lvdash.json"
    assert genie.source == tmp_path / "src/genie/nested/dev/nested.geniespace.json"
    assert genie.target == tmp_path / "src/genie/nested/test/nested.geniespace.json"


@pytest.mark.parametrize("mode", ["env", "asset"])
def test_omitted_items_auto_discover_from_custom_group_paths(tmp_path: Path, mode: str) -> None:
    dashboard = (
        tmp_path / "dashxy/dev/sales.lvdash.json"
        if mode == "env"
        else tmp_path / "dashxy/sales/dev/sales.lvdash.json"
    )
    genie = (
        tmp_path / "geniexy/dev/support.geniespace.json"
        if mode == "env"
        else tmp_path / "geniexy/support/dev/support.geniespace.json"
    )
    for source in (dashboard, genie):
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("{}", encoding="utf-8")
    config_path = tmp_path / "lamp_config.yaml"
    config_path.write_text(
        f"""schema_version: 2
settings:
  mode: {mode}
assets:
  dashboards:
    path: dashxy
  genie:
    path: geniexy
""",
        encoding="utf-8",
    )
    config = load_config(config_path)
    dashboard_task = plan_promotion(config, ("dashboards",)).tasks[0]
    genie_task = plan_promotion(config, ("genie",)).tasks[0]
    assert dashboard_task.identifier == "sales"
    assert genie_task.identifier == "support"
    if mode == "env":
        assert dashboard_task.target == tmp_path / "dashxy/test/sales.lvdash.json"
        assert genie_task.target == tmp_path / "geniexy/test/support.geniespace.json"
    else:
        assert dashboard_task.target == tmp_path / "dashxy/sales/test/sales.lvdash.json"
        assert genie_task.target == tmp_path / "geniexy/support/test/support.geniespace.json"
