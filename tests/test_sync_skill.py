import json
from pathlib import Path

import yaml

from scripts.sync_skill import DESTINATION, ROOT, SOURCE, sync_skill


def test_package_skill_matches_plugin() -> None:
    assert SOURCE.read_bytes() == DESTINATION.read_bytes()


def test_plugin_metadata_matches_skill() -> None:
    assert SOURCE.parents[2] == ROOT
    manifest = json.loads((ROOT / ".claude-plugin/plugin.json").read_text())
    skill = SOURCE.read_text(encoding="utf-8")
    metadata = yaml.safe_load(skill.split("---", 2)[1])
    assert manifest["name"] == metadata["name"] == SOURCE.parent.name
    assert manifest["description"] and metadata["description"]


def test_sync_skill_checks_and_updates_real_files(tmp_path: Path) -> None:
    source = tmp_path / "plugin/SKILL.md"
    destination = tmp_path / "package/skills/lamp-ops/SKILL.md"
    source.parent.mkdir()
    source.write_text("first\n", encoding="utf-8")

    assert sync_skill(source, destination, check=True)
    assert not destination.exists()
    assert sync_skill(source, destination)
    assert destination.read_text(encoding="utf-8") == "first\n"
    assert not sync_skill(source, destination, check=True)
    assert not sync_skill(source, destination)

    source.write_text("updated\n", encoding="utf-8")
    assert sync_skill(source, destination, check=True)
    assert destination.read_text(encoding="utf-8") == "first\n"
    assert sync_skill(source, destination)
    assert destination.read_text(encoding="utf-8") == "updated\n"
