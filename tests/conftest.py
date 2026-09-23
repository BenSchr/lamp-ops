from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def bundle(tmp_path: Path) -> Path:
    (tmp_path / "src/dashboards/dev").mkdir(parents=True)
    (tmp_path / "src/genie/dev").mkdir(parents=True)
    (tmp_path / "src/dashboards/dev/sales.lvdash.json").write_text(
        '{"my_catalog_dev": "key", "query": "my_catalog_dev.sales"}',
        encoding="utf-8",
    )
    (tmp_path / "src/genie/dev/sales.geniespace.json").write_text(
        '{"version": 2, "data_sources": {"tables": []}}', encoding="utf-8"
    )
    (tmp_path / "lamp_config.yaml").write_text(
        """schema_version: 2
root_path: src
settings:
  mode: env
  source_target: dev
  targets:
    - name: test
      replacements:
        my_catalog_dev: my_catalog_test
    - name: prod
      replacements:
        my_catalog_dev: my_catalog_prod
assets:
  dashboards: {}
  genie: {}
lint:
  genie: {}
""",
        encoding="utf-8",
    )
    return tmp_path
