"""Regenerate the evidence and results tables inside README.md from the saved result files.

    uv run python scripts/export_readme_tables.py           # rewrite README.md
    uv run python scripts/export_readme_tables.py --check   # exit 1 if README.md is stale, change nothing
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "engine")]

from api.readme_tables import render_tables, splice  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="only report whether README.md is current")
    args = parser.parse_args()
    readme = ROOT / "README.md"
    current = readme.read_text(encoding="utf-8")
    updated = splice(current, render_tables(ROOT / "results"))
    if updated == current:
        print("README tables are current.")
        return 0
    if args.check:
        print("README tables are stale: run uv run python scripts/export_readme_tables.py")
        return 1
    readme.write_text(updated, encoding="utf-8", newline="\n")
    print("README tables rewritten.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
