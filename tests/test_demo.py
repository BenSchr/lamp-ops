from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest
import yaml
from databricks.bundles.core import Bundle
from databricks.bundles.dashboards import Dashboard
from databricks.bundles.genie_spaces import GenieSpace
from typer.testing import CliRunner

from lamp_ops.bundles import mutate_dashboards, mutate_genie_spaces
from lamp_ops.cli import app
from lamp_ops.policy.models import PolicyPack

DEMO = Path(__file__).resolve().parents[1] / "demo"
runner = CliRunner()
CATALOGS = {"dev_catalog": "lamp_ops_dev", "prod_catalog": "lamp_ops_prod"}


NOTEBOOK = DEMO / "notebooks/setup_demo.py"


@pytest.mark.parametrize("target", ["setup", "dev", "prod"])
def test_demo_targets_separate_data_from_consumers(target: str) -> None:
    config = yaml.safe_load((DEMO / "databricks.yml").read_text())
    assert config["workspace"]["profile"] == "private"
    assert "mode" not in (config["targets"][target] or {})
    assert config["variables"]["dev_catalog"]["default"] == "lamp_ops_dev"
    assert config["variables"]["prod_catalog"]["default"] == "lamp_ops_prod"

    resources = {}
    for pattern in config["include"]:
        for path in DEMO.glob(pattern):
            data = yaml.safe_load(path.read_text())
            assert "resources" not in data
            for kind, values in (
                data.get("targets", {}).get(target, {}).get("resources", {}).items()
            ):
                resources.setdefault(kind, {}).update(values)
    if target == "setup":
        assert set(resources) == {"jobs"}
        job = resources["jobs"]["setup_demo"]
        assert job["performance_target"] == "PERFORMANCE_OPTIMIZED"
        assert "schedule" not in job
        assert job["parameters"] == [
            {"name": "catalogs", "default": "${var.dev_catalog},${var.prod_catalog}"},
            {"name": "schema", "default": "base"},
        ]
        assert job["tasks"] == [
            {
                "task_key": "create_demo_data",
                "notebook_task": {"notebook_path": "../notebooks/setup_demo.py"},
            }
        ]
    else:
        assert config["targets"][target]["variables"]["catalog"] == f"lamp_ops_{target}"
        assert set(resources) == {"dashboards", "genie_spaces"}
        assert resources["dashboards"]["demo"]["dataset_catalog"] == "${var.catalog}"


# The demo pack requires tag values the bundle does not set, to show failing checks.
DELIBERATE_FAILURES = {
    "setup": {"bundle_presets_tag_resources", "jobs_have_demo_tag"},
    "dev": {"bundle_presets_tag_resources"},
    "prod": {"bundle_presets_tag_resources"},
}


def test_demo_policy_pack_checks_each_target(tmp_path: Path) -> None:
    PolicyPack.model_validate(
        yaml.safe_load((DEMO / "policies/_checks.yaml").read_text()), strict=False
    )
    config = yaml.safe_load((DEMO / "databricks.yml").read_text())
    for target in ("setup", "dev", "prod"):
        resources = {}
        for pattern in config["include"]:
            for path in DEMO.glob(pattern):
                data = yaml.safe_load(path.read_text())
                for kind, values in (
                    data.get("targets", {}).get(target, {}).get("resources", {}).items()
                ):
                    resources.setdefault(kind, {}).update(values)
        if target == "setup":
            spec = importlib.util.spec_from_file_location(
                "pydabs_job", DEMO / "resources/pydabs_job.py"
            )
            assert spec and spec.loader
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            pydabs = module.load_resources(Bundle(target=target, variables=CATALOGS))
            resources.setdefault("jobs", {}).update(
                {name: job.as_dict() for name, job in pydabs.jobs.items()}
            )
            # Databricks applies bundle preset tags to jobs in the rendered summary.
            for job in resources["jobs"].values():
                job.setdefault("tags", {}).update(config["presets"]["tags"])

        summary_path = tmp_path / f"{target}.json"
        summary_path.write_text(
            json.dumps(
                {
                    "bundle": {"name": "lamp-ops-demo", "target": target},
                    "permissions": [{"group_name": "demo-managers", "level": "CAN_MANAGE"}],
                    "presets": config["presets"],
                    "python": config["python"],
                    "resources": resources,
                }
            ),
            encoding="utf-8",
        )
        result = runner.invoke(
            app,
            [
                "policy",
                "--input",
                str(summary_path),
                "--policy-path",
                str(DEMO / "policies"),
                "--output",
                "json",
            ],
        )
        assert result.exit_code == 1, f"{target}: {result.output}"
        report = json.loads(result.stdout)
        failing = {check["name"] for check in report["checks"] if check["status"] == "error"}
        assert failing == DELIBERATE_FAILURES[target], target
        assert report["summary"]["pass"] > 0


@pytest.mark.parametrize("target", ["dev", "prod"])
def test_demo_mutators_use_target_catalog_without_source_writes(
    target: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(DEMO)
    catalog = f"lamp_ops_{target}"
    bundle_config = yaml.safe_load((DEMO / "databricks.yml").read_text())
    replacements = bundle_config["variables"]["lamp_replacements"]["default"]
    target_variables = bundle_config["targets"][target].get("variables") or {}
    variables = {"lamp_replacements": target_variables.get("lamp_replacements", replacements)}
    for kind, name, resource_type, mutator, field in (
        ("dashboards", "demo.lvdash.json", Dashboard, mutate_dashboards, "serialized_dashboard"),
        (
            "genie",
            "test_agent.geniespace.json",
            GenieSpace,
            mutate_genie_spaces,
            "serialized_space",
        ),
    ):
        source = DEMO / kind / "dev" / name
        before = source.read_bytes()
        original = resource_type(file_path=str(source), warehouse_id="unchanged")
        rendered = mutator.function(Bundle(target=target, variables=variables), original)
        definition = (
            json.loads(getattr(rendered, field))
            if getattr(rendered, field) is not None
            else json.loads(source.read_text())
        )
        identifiers = (
            [dataset["asset_name"] for dataset in definition["datasets"]]
            if kind == "dashboards"
            else [table["identifier"] for table in definition["data_sources"]["tables"]]
        )
        assert identifiers
        assert all(identifier.startswith(f"{catalog}.base.") for identifier in identifiers)
        assert rendered.warehouse_id == "unchanged"
        assert source.read_bytes() == before


def test_demo_dashboard_queries_match_notebook_metric_definitions() -> None:
    notebook = NOTEBOOK.read_text()
    assert 'dbutils.widgets.text("catalogs", "lamp_ops_dev,lamp_ops_prod")' in notebook
    views = {
        name: yaml.safe_load(body)
        for name, body in re.findall(
            r"CREATE OR REPLACE VIEW (\w+)\s+WITH METRICS\s+LANGUAGE YAML\s+AS \$\$(.*?)\$\$",
            notebook,
            flags=re.DOTALL,
        )
    }
    assert set(views) == {"sales_metrics", "product_metrics"}
    assert all(view["source"] == "orders" for view in views.values())
    dashboard = json.loads((DEMO / "dashboards/dev/demo.lvdash.json").read_text())
    datasets = {
        dataset["name"]: views[dataset["asset_name"].split(".")[-1]]
        for dataset in dashboard["datasets"]
    }
    for page in dashboard["pages"]:
        assert page["layoutVersion"] == "GRID_V1"
        for layout in page["layout"]:
            widget = layout["widget"]
            for query in widget.get("queries", []):
                view = datasets[query["query"]["datasetName"]]
                measures = {measure["name"] for measure in view["measures"]}
                dimensions = {dimension["name"] for dimension in view["dimensions"]}
                for field in query["query"]["fields"]:
                    expression = field["expression"]
                    if expression.startswith("MEASURE("):
                        assert expression.removeprefix("MEASURE(`").removesuffix("`)") in measures
                    else:
                        assert expression.strip("`") in dimensions
                field_names = {field["name"] for field in query["query"]["fields"]}
                for encoding in widget["spec"]["encodings"].values():
                    if isinstance(encoding, dict):
                        assert encoding["fieldName"] in field_names


@pytest.mark.parametrize(
    ("target", "jobs"), [("setup", {"setup_demo_pydabs"}), ("dev", set()), ("prod", set())]
)
def test_demo_pydabs_job_reuses_setup_notebook(target: str, jobs: set[str]) -> None:
    spec = importlib.util.spec_from_file_location("pydabs_job", DEMO / "resources/pydabs_job.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    resources = module.load_resources(Bundle(target=target, variables=CATALOGS))

    assert set(resources.jobs) == jobs
    for job in resources.jobs.values():
        assert job.as_dict()["performance_target"] == "PERFORMANCE_OPTIMIZED"
        assert job.as_dict()["parameters"] == [
            {"name": "catalogs", "default": "lamp_ops_dev,lamp_ops_prod"},
            {"name": "schema", "default": "base"},
        ]
        assert [task["notebook_task"] for task in job.as_dict()["tasks"]] == [
            {"notebook_path": "notebooks/setup_demo.py"}
        ]
