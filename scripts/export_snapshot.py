"""One bundle of every saved number a screen may show -> results/snapshot.json (W0.3, F4).

The frontend's Mission Control page opens from disk (file://) and cannot call the API, so it reads a snapshot. A
snapshot is only honest if it cannot go stale unnoticed: F4 found one that still showed E4 stranding 33 % vs 6 %
(the result file said 37.5 % vs 3.5 %). So:

- every value is COPIED from a saved file (docs/api_examples/, results/); nothing is computed or rounded here;
- ``sources`` records which file each section came from;
- there is no timestamp, so the bundle is a pure function of those files, and
  tests/test_snapshot.py fails whenever the committed results/snapshot.json differs from what the files say now.

Run: uv run python scripts/export_snapshot.py [--check]      (stdlib only; --check changes nothing, exit 1 if stale)
Then Ansh runs scripts/export_web_snapshot.py, which wraps THIS bundle for web/js/snapshot.js (engine work never
writes into web/).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_FILE = Path("results") / "snapshot.json"
VEHICLES = ("V07", "V12", "V19")
SOURCE_NOTE = ("Saved engine outputs (docs/api_examples) and experiment results (results/), copied unchanged by "
               "scripts/export_snapshot.py. api.* and results.e1-e5 are SIMULATED (see each file's is_simulated); "
               "results.real_data carries its own labels. `sources` names the file behind every section.")


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def e1_summary(raw: dict) -> dict:
    """E1 condensed to what the learning-curve panel plots: per method and k, MAE and the 90 % band's coverage."""
    methods = {}
    for name, by_k in raw["methods"].items():
        rows = []
        for k, row in by_k.items():
            band90 = row.get("bands", {}).get("90", {})
            rows.append({
                "k": int(k),
                "mae_pp": row.get("mae_pp"),
                "coverage90": band90.get("coverage"),
                "covered90": band90.get("covered"),
                "n90": band90.get("n"),
                "wilson90": band90.get("wilson95"),
            })
        methods[name] = sorted(rows, key=lambda r: r["k"])
    return {
        "experiment": raw["experiment"],
        "is_simulated": raw["is_simulated"],
        "test_trips": raw.get("test_trips"),
        "vehicles": raw.get("vehicles"),
        "resamples": raw.get("resamples"),
        "methods": methods,
        "notes": raw.get("notes", []),
    }


def e2_summary(raw: dict) -> dict:
    keep = ("ece", "brier", "coverage_90_arrival_band", "mean_90_band_width_pp",
            "share_extreme_predictions", "when_forecast_ge_0.9", "bins")
    return {
        "experiment": raw["experiment"],
        "is_simulated": raw["is_simulated"],
        "trips": raw.get("trips"),
        "n_runs": raw.get("n_runs"),
        "methods": {m: {k: v for k, v in body.items() if k in keep} for m, body in raw["methods"].items()},
    }


# Sections that copy only some keys of their file (the rest are per-trip detail the screens never show).
E3_KEYS = ("experiment", "is_simulated", "trips_per_scenario", "scenarios", "notes")
E4_KEYS = ("experiment", "is_simulated", "trips_per_scenario", "total_simulated_trips", "scenarios", "compute", "notes")
E5_KEYS = ("experiment", "is_simulated", "worlds_per_scenario", "chargers_per_world", "scenarios")


def build_snapshot(root: Path = ROOT) -> dict:
    ex, res = root / "docs" / "api_examples", root / "results"
    sources: dict[str, str] = {}

    def read(section: str, path: Path) -> dict:
        sources[section] = path.relative_to(root).as_posix()
        return load(path)

    api = {
        "health": read("api.health", ex / "health.json"),
        "scenario": read("api.scenario", ex / "demo_scenario.json"),
        "dispatch": {v: read(f"api.dispatch.{v}", ex / f"dispatch_{v}.json") for v in VEHICLES},
        "replay": {v: read(f"api.replay.{v}", ex / f"replay_{v}.json") for v in VEHICLES},
        "chargers": read("api.chargers", ex / "chargers.json"),
        "forecastUpdate": read("api.forecastUpdate", ex / "forecast_update.json"),
        "rideCheck": read("api.rideCheck", ex / "ride_check.json"),
        "driverDemo": read("api.driverDemo", ex / "driver_demo.json"),
    }
    e3 = read("results.e3", res / "e3_reliability_planning_simulated.json")
    e4 = read("results.e4", res / "e4_dispatch_vs_fixed_simulated.json")
    e5 = read("results.e5", res / "e5_trust_recovery_simulated.json")
    results = {
        "e1": e1_summary(read("results.e1", res / "e1_learning_curve_simulated.json")),
        "e2": e2_summary(read("results.e2", res / "e2_calibration_simulated.json")),
        "e3": {k: e3[k] for k in E3_KEYS},
        "e4": {k: e4[k] for k in E4_KEYS},
        "e5": {k: e5[k] for k in E5_KEYS if k in e5},
        "run_all": read("results.run_all", res / "run_all_summary.json"),
        "real_data": read("results.real_data", res / "real_data_summary.json"),
    }
    return {"source": SOURCE_NOTE, "is_simulated": True, "api": api, "results": results, "sources": sources}


def render(snapshot: dict) -> str:
    return json.dumps(snapshot, indent=1, ensure_ascii=False) + "\n"


def is_current(root: Path = ROOT) -> bool:
    path = root / SNAPSHOT_FILE
    return path.is_file() and path.read_text(encoding="utf-8") == render(build_snapshot(root))


def main(argv: list[str]) -> int:
    if "--check" in argv:
        current = is_current()
        print("results/snapshot.json is current" if current else
              "results/snapshot.json is STALE: run  uv run python scripts/export_snapshot.py")
        return 0 if current else 1
    path = ROOT / SNAPSHOT_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:  # LF everywhere, as .gitattributes says
        handle.write(render(build_snapshot()))
    print(f"wrote {SNAPSHOT_FILE.as_posix()} ({path.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
