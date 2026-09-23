from pathlib import Path

import pytest

from scripts.update_readme import (
    CLI_BEGIN,
    CLI_END,
    LINT_BEGIN,
    LINT_END,
    ReadmeError,
    rendered_readme,
    update_readme,
)


def test_readme_generation_preserves_handwritten_text_and_ignores_fences(tmp_path: Path) -> None:
    readme = tmp_path / "Readme.md"
    readme.write_text(
        """# Project

Handwritten.

## Contents
<!-- BEGIN GENERATED TOC -->
old
<!-- END GENERATED TOC -->

## Real heading
```markdown
## Not a heading
```

## Linting
<!-- BEGIN GENERATED GENIE LINT REFERENCE -->
old
<!-- END GENERATED GENIE LINT REFERENCE -->

## CLI reference
<!-- BEGIN GENERATED CLI REFERENCE -->
old
<!-- END GENERATED CLI REFERENCE -->
""",
        encoding="utf-8",
    )
    assert update_readme(readme)
    rendered = readme.read_text(encoding="utf-8")
    assert "Handwritten." in rendered
    assert "[Real heading](#real-heading)" in rendered
    assert "Not a heading](" not in rendered
    assert "### `lamp promote dashboards`" in rendered
    assert "### `lamp lint`" in rendered
    assert "--profile      -p" in rendered
    assert "### `lamp lint genie`" not in rendered
    assert "| 6 | Data source count | `source_count` |" in rendered
    assert "maximum_sources: 12" in rendered
    assert "threshold: 0" in rendered
    assert not update_readme(readme, check=True)


def test_readme_check_detects_drift_without_writing(tmp_path: Path) -> None:
    readme = tmp_path / "Readme.md"
    original = (
        "# Project\n\n## Contents\n<!-- BEGIN GENERATED TOC -->\nold\n"
        "<!-- END GENERATED TOC -->\n\n## Linting\n"
        f"{LINT_BEGIN}\nold\n{LINT_END}\n\n## CLI reference\n"
        f"{CLI_BEGIN}\nold\n{CLI_END}\n"
    )
    readme.write_text(original, encoding="utf-8")
    assert update_readme(readme, check=True)
    assert readme.read_text(encoding="utf-8") == original


def test_readme_rejects_missing_markers() -> None:
    with pytest.raises(ReadmeError, match="Expected exactly one"):
        rendered_readme("# Missing markers\n")
