from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from databricks.bundles.core import Bundle, Variable
from databricks.bundles.dashboards import Dashboard
from databricks.bundles.genie_spaces import GenieSpace

from lamp_ops.bundles import mutate_dashboards, mutate_genie_spaces
from lamp_ops.errors import LampError
from lamp_ops.replacements import replace_strings

RESOURCE_TYPES = [
    ("dashboards", Dashboard, mutate_dashboards, "serialized_dashboard"),
    ("genie", GenieSpace, mutate_genie_spaces, "serialized_space"),
]


def test_shared_replacements_preserve_legacy_order_and_json_types() -> None:
    source = {"dev.sales": ["dev.sales", "dev.other", 1, True, None, {"x": "dev.sales"}]}
    result = replace_strings(
        source, (("dev", "test"), ("dev.sales", "stage.sales"), ("stage", "prod"))
    )
    assert result == {"dev.sales": ["prod.sales", "test.other", 1, True, None, {"x": "prod.sales"}]}
    assert source["dev.sales"][0] == "dev.sales"


@pytest.mark.parametrize("already_inlined", [False, True])
def test_native_bundle_build_mutates_both_types_without_writes(
    tmp_path: Path, already_inlined: bool
) -> None:
    sources = {}
    resources = {}
    for kind, suffix in (("dashboards", "lvdash"), ("genie_spaces", "geniespace")):
        source = tmp_path / f"sales.{suffix}.json"
        source.write_text('{"dev": "dev.sales", "future": {"query": ["dev.orders"]}}')
        sources[kind] = source
        resource = {"file_path": source.name, "warehouse_id": "original"}
        if already_inlined:
            field = "serialized_dashboard" if kind == "dashboards" else "serialized_space"
            resource[field] = source.read_text()
        resources[kind] = {"sales": resource}
    input_path = tmp_path / "input.json"
    output_path = tmp_path / "output.json"
    diagnostics = tmp_path / "diagnostics.json"
    input_path.write_text(
        json.dumps(
            {
                "bundle": {"target": "test"},
                "python": {
                    "mutators": [
                        "lamp_ops.bundles:mutate_genie_spaces",
                        "lamp_ops.bundles:mutate_dashboards",
                    ]
                },
                "variables": {"lamp_replacements": {"value": {"dev.": "test."}}},
                "resources": resources,
            }
        )
    )
    before = {source: source.read_bytes() for source in sources.values()}
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "databricks.bundles.build",
            "--phase",
            "apply_mutators",
            "--input",
            str(input_path),
            "--output",
            str(output_path),
            "--diagnostics",
            str(diagnostics),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert diagnostics.read_text() == ""
    resources = json.loads(output_path.read_text())["resources"]
    for kind, field in (
        ("dashboards", "serialized_dashboard"),
        ("genie_spaces", "serialized_space"),
    ):
        resource = resources[kind]["sales"]
        assert "file_path" not in resource
        assert resource["warehouse_id"] == "original"
        assert json.loads(resource[field]) == {
            "dev": "test.sales",
            "future": {"query": ["test.orders"]},
        }
    assert all(source.read_bytes() == contents for source, contents in before.items())
    assert not (tmp_path / ".databricks").exists()


@pytest.mark.parametrize(("kind", "resource_type", "mutator", "field"), RESOURCE_TYPES)
def test_mutators_ignore_lamp_configuration(
    bundle: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind,
    resource_type,
    mutator,
    field,
) -> None:
    monkeypatch.chdir(bundle)
    config_path = bundle / "lamp_config.yaml"
    config = yaml.safe_load(config_path.read_text())
    config["assets"][kind]["items"] = {
        "sales": {
            "targets": [{"name": "test", "replacements": {"my_catalog_dev": "asset_catalog"}}]
        }
    }
    config["assets"][kind]["exclusions"] = ["sales"]
    config_path.write_text(yaml.safe_dump(config))
    suffix = "lvdash" if kind == "dashboards" else "geniespace"
    source = bundle / f"src/{kind}/dev/sales.{suffix}.json"
    source.write_text('{"my_catalog_dev": "my_catalog_dev.sales"}')
    original = resource_type(file_path=str(source), warehouse_id="untouched")
    variables = {"lamp_replacements": {"my_catalog_dev": "bundle_catalog"}}
    # Lamp targets, exclusions, and source targets do not apply: only the variable does.
    for target in ("test", "dev", "unlisted"):
        result = mutator.function(Bundle(target=target, variables=variables), original)
        assert json.loads(getattr(result, field)) == {"my_catalog_dev": "bundle_catalog.sales"}
        assert result.file_path is None
        assert result.warehouse_id == "untouched"
    assert original.file_path == str(source)
    assert "my_catalog_dev.sales" in source.read_text()
    empty = Bundle(target="test", variables={"lamp_replacements": {}})
    assert mutator.function(empty, original) is original
    with pytest.raises(LampError, match="need a lamp_replacements bundle variable"):
        mutator.function(Bundle(target="test"), original)


@pytest.mark.parametrize(("kind", "resource_type", "mutator", "field"), RESOURCE_TYPES)
@pytest.mark.parametrize("as_string", [False, True])
def test_inline_content_uses_literal_bundle_replacements(
    bundle: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind,
    resource_type,
    mutator,
    field,
    as_string: bool,
) -> None:
    monkeypatch.chdir(bundle)
    data = {"key": ["my_catalog_dev.sales", "quoted", 1, False, None]}
    original = resource_type(**{field: json.dumps(data) if as_string else data})
    replacements = {"my_catalog_dev": "my_catalog_test", "quoted": 'a "quote"\nline'}
    result = mutator.function(
        Bundle(target="test", variables={"lamp_replacements": replacements}), original
    )
    assert json.loads(getattr(result, field)) == {
        "key": ["my_catalog_test.sales", 'a "quote"\nline', 1, False, None]
    }
    assert data["key"][0] == "my_catalog_dev.sales"
    assert (
        mutator.function(Bundle(target="dev", variables={"lamp_replacements": {}}), original)
        is original
    )


def test_genie_sources_are_sorted_after_replacement(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    data = {
        "data_sources": {
            "tables": [{"identifier": "dev.s.a"}, {"identifier": "dev.s.b"}],
            "volumes": [{"path": "/Volumes/dev/s/a"}, {"path": "/Volumes/dev/s/b"}],
            "future_source": {"value": "dev.s.a"},
        }
    }
    result = mutate_genie_spaces.function(
        Bundle(
            target="test",
            variables={
                "lamp_replacements": {
                    "dev.s.a": "prod.s.z",
                    "dev.s.b": "prod.s.a",
                    "/Volumes/dev/s/a": "/Volumes/prod/s/z",
                    "/Volumes/dev/s/b": "/Volumes/prod/s/a",
                }
            },
        ),
        GenieSpace(serialized_space=data),
    )
    sources = json.loads(result.serialized_space)["data_sources"]
    assert [entry["identifier"] for entry in sources["tables"]] == ["prod.s.a", "prod.s.z"]
    assert [entry["path"] for entry in sources["volumes"]] == [
        "/Volumes/prod/s/a",
        "/Volumes/prod/s/z",
    ]
    assert sources["future_source"] == {"value": "prod.s.z"}
    assert data["data_sources"]["tables"][0]["identifier"] == "dev.s.a"


@pytest.mark.parametrize("overrides", [None, [], {"x": 1}, {"": "x"}])
def test_invalid_bundle_mapping_fails(tmp_path: Path, monkeypatch, overrides) -> None:
    monkeypatch.chdir(tmp_path)
    with pytest.raises(LampError, match="lamp_replacements"):
        mutate_dashboards.function(
            Bundle(target="test", variables={"lamp_replacements": overrides}),
            Dashboard(serialized_dashboard={}),
        )


@pytest.mark.parametrize("serialized", ["{", "[]", "null", 42, []])
def test_invalid_serialized_content_fails(tmp_path: Path, monkeypatch, serialized) -> None:
    monkeypatch.chdir(tmp_path)
    with pytest.raises(LampError, match="Invalid JSON|Expected a JSON object"):
        mutate_dashboards.function(
            Bundle(target="test", variables={"lamp_replacements": {"dev": "test"}}),
            Dashboard(serialized_dashboard=serialized),
        )


def test_normalized_content_is_authoritative_and_missing_files_fail(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    bundle = Bundle(target="test", variables={"lamp_replacements": {"dev": "test"}})
    result = mutate_genie_spaces.function(
        bundle, GenieSpace(file_path="missing.json", serialized_space={"query": "dev.sales"})
    )
    assert json.loads(result.serialized_space) == {"query": "test.sales"}
    with pytest.raises(LampError, match="Could not read.*missing.json"):
        mutate_genie_spaces.function(bundle, GenieSpace(file_path="missing.json"))


def test_noop_and_missing_variable_are_distinct(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    original = Dashboard(file_path="not-read.json")
    assert (
        mutate_dashboards.function(
            Bundle(target="test", variables={"lamp_replacements": {}}), original
        )
        is original
    )
    with pytest.raises(LampError, match="need a lamp_replacements bundle variable"):
        mutate_dashboards.function(Bundle(target="test"), original)
    assert mutate_dashboards.function(Bundle(target="test"), Dashboard()).file_path is None
    # An invalid Lamp configuration is never read by the mutators.
    (tmp_path / "lamp_config.yaml").write_text("settings: [invalid]")
    (tmp_path / "dashboard.json").write_text('{"query": "dev.sales"}')
    result = mutate_dashboards.function(
        Bundle(target="test", variables={"lamp_replacements": {"dev": "test"}}),
        Dashboard(file_path="dashboard.json"),
    )
    assert json.loads(result.serialized_dashboard) == {"query": "test.sales"}


@pytest.mark.parametrize(
    "sources",
    [[], {"tables": {}}, {"tables": [{}]}, {"volumes": [{"path": 1}]}],
)
def test_invalid_genie_sources_fail(tmp_path: Path, monkeypatch, sources) -> None:
    monkeypatch.chdir(tmp_path)
    with pytest.raises(LampError, match="Genie data_sources"):
        mutate_genie_spaces.function(
            Bundle(target="test", variables={"lamp_replacements": {"dev": "test"}}),
            GenieSpace(serialized_space={"data_sources": sources, "query": "dev.sales"}),
        )


def test_unmatched_strings_preserve_resource_and_file_path(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    original = Dashboard(file_path="original.json", serialized_dashboard={"query": "prod.sales"})
    assert (
        mutate_dashboards.function(
            Bundle(target="test", variables={"lamp_replacements": {"dev": "test"}}), original
        )
        is original
    )


def test_bundle_variable_file_path_resolves_from_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "src/custom.json"
    source.parent.mkdir()
    source.write_text('{"query": "my_catalog_dev.sales"}')
    monkeypatch.chdir(tmp_path)
    result = mutate_dashboards.function(
        Bundle(
            target="test",
            variables={
                "source": "src/custom.json",
                "lamp_replacements": {"my_catalog_dev": "custom_catalog"},
            },
        ),
        Dashboard(file_path=Variable(path="var.source", type=str)),
    )
    assert json.loads(result.serialized_dashboard) == {"query": "custom_catalog.sales"}
