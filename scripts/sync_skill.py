#!/usr/bin/env python3
"""Copy the Claude plugin's lamp-ops skill into the installable Python package."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "skills/lamp-ops/SKILL.md"
DESTINATION = ROOT / "src/lamp_ops/.agents/skills/lamp-ops/SKILL.md"


def sync_skill(source: Path, destination: Path, *, check: bool = False) -> bool:
    """Return whether the package copy differs; update it unless checking."""
    content = source.read_bytes()
    if destination.is_file() and destination.read_bytes() == content:
        return False
    if not check:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Fail if the package copy differs.")
    args = parser.parse_args()
    try:
        changed = sync_skill(SOURCE, DESTINATION, check=args.check)
    except OSError as error:
        parser.exit(1, f"error: {error}\n")
    if args.check and changed:
        parser.exit(1, f"error: {DESTINATION} is out of date\n")
    print(f"{'Updated' if changed else 'Current'}: {DESTINATION}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
