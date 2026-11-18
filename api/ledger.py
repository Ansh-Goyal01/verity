"""The evidence ledger (W1.5): how much REAL evidence and how much SIMULATION stand behind the current answers.

Everything here is read from the saved result files on every call, so the counts move when the files do (a new corpus
row, a new OCM fetch, a new experiment run) with no code change. A missing file is reported as missing, never as zero
evidence of something else, and nothing is estimated or invented.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SCHEMA = "verity.evidence_ledger.v1"
DRIVER_REAL_MIN_REPORTS = 3  # guide W1.3: real dated reports needed before a real-only demo scenario is built

SIMULATED_EXPERIMENTS = (  # (result file, label, key holding how many trips/worlds stand behind it)
    ("e3_reliability_planning_simulated.json", "E3 reliability-blind vs trust-aware planning", "trips_per_scenario"),
    ("e4_dispatch_vs_fixed_simulated.json", "E4 dispatch vs fixed stops", "trips_per_scenario"),
    ("e5_trust_recovery_simulated.json", "E5 trust recovery", "worlds_per_scenario"),
    ("e5b_ring_overlap_simulated.json", "E5b ring detection without a time window", "worlds_per_scenario"),
    ("e6_operator_correlation_simulated.json", "E6 operator-correlated outages", "trips_per_scenario"),
)


def _read(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _corpus(summary: dict | None, name: str) -> dict:
    row = ((summary or {}).get("corpus") or {}).get(name)
    return {"rows": row["rows"], "usable": row["usable"]} if row else {"rows": None, "usable": None}


def _voice_notes(voice: dict | None) -> dict:
    """Venue voice measurement (guide W3.5): NOT_RUN without the file, PRELIMINARY below the 30-sample / 10-speaker target."""
    if voice is None:
        return {"count": 0, "speakers": 0, "status": "NOT_RUN",
                "note": "venue measurement on real speakers (guide W3.5) has not happened"}
    preliminary = bool(voice.get("preliminary", True))  # a file that does not say is not trusted as complete
    return {"count": int(voice.get("n_samples", 0)), "speakers": int(voice.get("n_speakers", 0)),
            "status": "PRELIMINARY" if preliminary else "MEASURED",
            "note": voice.get("preliminary_reason") or "venue measurement on real speakers (guide W3.5)"}


def _bmw(results: Path) -> dict:
    data = _read(results / "bmw_i3_heldout.json")
    if data is None:
        return {"status": "NOT_RUN", "reason": "the held-out split has not been opened"}
    v2 = data["results"]["v2_frozen_prior"]
    return {"status": "DONE", "label": data["label"], "trips_scored": data["n_trips_scored"], "km_scored": data["km_scored"],
            "preliminary": data["preliminary"],
            "capacity_error_pct": v2["capacity"]["error_pct"], "capacity_truth_in_ci95": v2["capacity"]["truth_in_ci95"],
            "capacity_truth_in_ci80": v2["capacity"]["truth_in_ci80"],
            "soc_drop_median_abs_error_pp": v2["per_trip_soc_drop"]["median_abs_error_pp"],
            "soc_drop_band95_coverage": v2["per_trip_soc_drop"]["band95_coverage"]}


def _simulated(results: Path) -> list[dict]:
    out = []
    for name, label, key in SIMULATED_EXPERIMENTS:
        data = _read(results / name)
        out.append({"experiment": label, "present": data is not None, "is_simulated": True,
                    "units": (data or {}).get(key) if key else None, "file": f"results/{name}"})
    return out


def build_ledger(results_dir: Path, *, n_runs: int | None = None) -> dict[str, Any]:
    summary = _read(results_dir / "real_data_summary.json")
    ocm = _read(results_dir / "ocm_checkins.json")
    voice = _read(results_dir / "voice_real.json")
    trips, evidence = _corpus(summary, "trips_india.csv"), _corpus(summary, "charger_evidence.csv")
    snapshots, reviews = _corpus(summary, "app_status_snapshots.csv"), _corpus(summary, "app_reviews.csv")
    n_ocm = int(ocm["n_checkins"]) if ocm and "n_checkins" in ocm else None
    real_reports = (evidence["usable"] or 0) + (snapshots["usable"] or 0) + (n_ocm or 0)
    ready = real_reports >= DRIVER_REAL_MIN_REPORTS
    return {
        "schema": SCHEMA,
        "real": {
            "trips": {**trips, "note": "hand-collected public Indian trips, usable = verified and enrichable"},
            "charger_reports": {"corpus_evidence_usable": evidence["usable"], "app_snapshot_usable": snapshots["usable"],
                                "ocm_checkins": n_ocm, "total_real_reports": real_reports,
                                "ocm_status": "FETCHED" if n_ocm is not None else "NOT_FETCHED"},
            "voice_notes": _voice_notes(voice),
            "app_reviews": {**reviews, "text_rows_private": (summary or {}).get("private_review_texts")},
            "bmw_i3_held_out": _bmw(results_dir),
        },
        "simulated": {"monte_carlo_runs_per_answer": n_runs, "experiments": _simulated(results_dir),
                      "note": "every simulated number is labelled (simulated) where it is shown"},
        "driver_real": {"needed_reports": DRIVER_REAL_MIN_REPORTS, "real_reports_available": real_reports,
                        "status": "READY_TO_CHECK" if ready else "WAITING_FOR_DATA",
                        "reason": ("enough real dated reports exist in total; whether >= 3 lie on chargers near the drop "
                                   "still has to be checked before a real-only scenario is built") if ready else
                                  (f"{real_reports} real report(s); a real-only scenario needs at least "
                                   f"{DRIVER_REAL_MIN_REPORTS} dated reports near the drop")},
        "sources": {"real_data_summary": summary is not None, "ocm_checkins": ocm is not None,
                    "bmw_heldout": (results_dir / "bmw_i3_heldout.json").exists()},
    }
