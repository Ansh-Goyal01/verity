"""F11: the one-command REAL-data runner (pure parts only; the steps themselves have their own tests)."""

from __future__ import annotations

import csv
from pathlib import Path

import scripts.run_real_data as runner
from verity import paths
from verity.corpus import SPECS


def test_steps_cover_validation_and_f3_to_f6_and_f9() -> None:
    assert list(runner.STEPS) == ["validate", "F3", "F4", "F5", "F6", "F9"]
    for name, (tail, _, _, _) in runner.STEPS.items():
        assert (runner.REPO / tail[0]).exists(), f"{name} points to a missing script"


def test_no_paid_switches_only_f6_to_the_keyword_baseline() -> None:
    assert runner.step_command("F6", no_paid=True)[-2:] == ["--method", "keyword"]
    assert "--method" not in runner.step_command("F6", no_paid=False)
    assert "--method" not in runner.step_command("F4", no_paid=True)


def test_corpus_sizes_count_rows_usable_and_errors_on_the_committed_corpus() -> None:
    sizes = runner.corpus_sizes(paths.CORPUS_DIR)
    assert set(sizes) == {s.filename for s in SPECS}
    trips = sizes["trips_india.csv"]
    assert trips["rows"] >= 6 and trips["usable"] <= trips["rows"]


def test_missing_corpus_file_is_an_error_with_zero_rows(tmp_path: Path) -> None:
    sizes = runner.corpus_sizes(tmp_path)
    assert all(s == {"rows": 0, "usable": 0, "errors": 1} for s in sizes.values())


def test_private_review_texts_counts_non_empty_texts_only(tmp_path: Path) -> None:
    assert runner.private_review_texts(tmp_path) == 0
    with (tmp_path / "app_reviews_text.csv").open("w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows([["review_id", "text"], ["R1", "fictional text"], ["R2", "  "]])
    assert runner.private_review_texts(tmp_path) == 1


def test_unknown_step_is_rejected_without_running_anything() -> None:
    assert runner.main(["--only", "nope"]) == 2


def test_headlines_tolerate_missing_result_files() -> None:
    for name, (_, _, result_file, headline) in runner.STEPS.items():
        if headline:
            assert isinstance(headline({}), dict), name
