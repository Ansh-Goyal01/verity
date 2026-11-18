"""W1.5: the evidence ledger reads its counts from the saved result files, honestly, with no code change."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from api.ledger import DRIVER_REAL_MIN_REPORTS, build_ledger
from api.main import app


def write(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def summary(evidence_usable: int = 0, snapshots: int = 0) -> dict:
    row = lambda rows, usable: {"rows": rows, "usable": usable, "errors": 0}  # noqa: E731
    return {"corpus": {"trips_india.csv": row(6, 2), "charger_evidence.csv": row(evidence_usable, evidence_usable),
                       "app_status_snapshots.csv": row(snapshots, snapshots), "app_reviews.csv": row(0, 0)},
            "private_review_texts": 0}


def test_an_empty_results_folder_is_reported_as_missing_never_as_data(tmp_path: Path) -> None:
    ledger = build_ledger(tmp_path, n_runs=1500)
    assert ledger["real"]["trips"] == {"rows": None, "usable": None, "note": ledger["real"]["trips"]["note"]}
    assert ledger["real"]["charger_reports"]["total_real_reports"] == 0
    assert ledger["real"]["charger_reports"]["ocm_status"] == "NOT_FETCHED"
    assert ledger["real"]["voice_notes"] == {"count": 0, "speakers": 0, "status": "NOT_RUN",
                                             "note": ledger["real"]["voice_notes"]["note"]}
    assert ledger["real"]["bmw_i3_held_out"]["status"] == "NOT_RUN"
    assert all(not e["present"] for e in ledger["simulated"]["experiments"])
    assert ledger["driver_real"]["status"] == "WAITING_FOR_DATA"
    assert ledger["simulated"]["monte_carlo_runs_per_answer"] == 1500


def test_counts_follow_the_files_without_a_code_change(tmp_path: Path) -> None:
    write(tmp_path / "real_data_summary.json", summary(evidence_usable=2))
    assert build_ledger(tmp_path)["real"]["charger_reports"]["total_real_reports"] == 2
    write(tmp_path / "real_data_summary.json", summary(evidence_usable=2, snapshots=1))
    write(tmp_path / "ocm_checkins.json", {"n_checkins": 4})
    ledger = build_ledger(tmp_path)
    assert ledger["real"]["charger_reports"]["total_real_reports"] == 2 + 1 + 4
    assert ledger["real"]["charger_reports"]["ocm_status"] == "FETCHED"
    assert ledger["driver_real"]["status"] == "READY_TO_CHECK" and ledger["driver_real"]["real_reports_available"] >= DRIVER_REAL_MIN_REPORTS
    assert ledger["real"]["trips"]["rows"] == 6 and ledger["real"]["trips"]["usable"] == 2


def test_the_bmw_held_out_result_is_summarised_from_its_file(tmp_path: Path) -> None:
    v2 = {"capacity": {"error_pct": -10.5, "truth_in_ci95": True, "truth_in_ci80": False},
          "per_trip_soc_drop": {"median_abs_error_pp": 0.95, "band95_coverage": 0.47}}
    write(tmp_path / "bmw_i3_heldout.json", {"label": "REAL: x", "n_trips_scored": 19, "km_scored": 398.8, "preliminary": False,
                                             "results": {"v2_frozen_prior": v2}})
    bmw = build_ledger(tmp_path)["real"]["bmw_i3_held_out"]
    assert (bmw["status"], bmw["trips_scored"], bmw["capacity_error_pct"], bmw["capacity_truth_in_ci95"]) == ("DONE", 19, -10.5, True)


def test_a_corrupt_file_counts_as_missing(tmp_path: Path) -> None:
    (tmp_path / "real_data_summary.json").write_text("{not json", encoding="utf-8")
    assert build_ledger(tmp_path)["real"]["trips"]["rows"] is None


def test_the_route_serves_the_saved_results() -> None:
    with TestClient(app) as client:
        body = client.get("/api/evidence/ledger").json()
    assert body["schema"] == "verity.evidence_ledger.v1"
    assert body["simulated"]["monte_carlo_runs_per_answer"] > 0
    assert {e["experiment"] for e in body["simulated"]["experiments"]} >= {"E3 reliability-blind vs trust-aware planning"}
