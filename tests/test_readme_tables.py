"""The README's generated tables (W5.1) must match the saved result files, and stay honest."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from api.readme_tables import END, START, render_tables, splice

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"


def test_readme_tables_match_the_saved_results() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert splice(readme, render_tables(RESULTS)) == readme, (
        "README tables are stale: run uv run python scripts/export_readme_tables.py")


def test_splice_refuses_a_readme_without_markers() -> None:
    with pytest.raises(ValueError):
        splice("# no markers here", "table")


def test_splice_replaces_only_what_sits_between_the_markers() -> None:
    text = f"before\n{START}\nold\n{END}\nafter\n"
    assert splice(text, "new") == f"before\n{START}\nnew\n{END}\nafter\n"


def test_tables_name_simulated_data_and_hold_no_certainty() -> None:
    tables = render_tables(RESULTS)
    assert "simulated" in tables.lower()
    assert "100%" not in tables  # golden rule 5: never promise certainty


def test_missing_result_files_are_reported_not_invented(tmp_path: Path) -> None:
    tables = render_tables(tmp_path)  # an empty results folder
    assert "result file missing" in tables
    assert "n/a" in tables
    assert "BMW i3 held-out evaluation | not run" in tables


def test_ledger_counts_come_from_the_summary_file(tmp_path: Path) -> None:
    summary = {"corpus": {"trips_india.csv": {"rows": 7, "usable": 3, "errors": 0}}}
    (tmp_path / "real_data_summary.json").write_text(json.dumps(summary), encoding="utf-8")
    assert "3 usable of 7 rows" in render_tables(tmp_path)
