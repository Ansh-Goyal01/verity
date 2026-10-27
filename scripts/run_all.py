"""Run ALL tests and ALL experiments from a clean state, then summarise.

    uv run python scripts/run_all.py            # full run (~20 min: E2 and E4 dominate)
    uv run python scripts/run_all.py --fast     # same steps, much smaller experiment sizes (a few minutes)
    uv run python scripts/run_all.py --only tests,E5

"Clean state" means the files this script produces (charts, result JSON, API examples, logs) are deleted
first and regenerated; it never touches data/, the fixed held-out split, the cache, or anything outside
the repository. Every step runs in its own process; its output goes to results/logs/<step>.log. The exit
code is non-zero if any step fails or does not produce its expected files.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RESULTS = REPO / "results"
LOGS = RESULTS / "logs"
PY = sys.executable

# name -> (command tail, files it must produce (relative to the repo), fast-mode extra args)
STEPS: dict[str, tuple[list[str], list[str], list[str]]] = {
    "tests": (["-m", "pytest", "-W", "ignore"], [], []),
    "E1": (["experiments/e1_learning_curve.py", "--dataset", "main"],
           ["results/e1_learning_curve_simulated.png", "results/e1_learning_curve_simulated.json"], ["--resamples", "1"]),
    "E2": (["experiments/e2_calibration.py", "--dataset", "main"],
           ["results/e2_calibration_simulated.png", "results/e2_calibration_simulated.json"], ["--trips-per-vehicle", "8"]),
    "E3": (["experiments/e3_reliability_planning.py"],
           ["results/e3_reliability_planning_simulated.png", "results/e3_reliability_planning_simulated.json"], ["--trips", "20"]),
    "E4": (["experiments/e4_dispatch_vs_fixed.py"],
           ["results/e4_dispatch_vs_fixed_simulated.png", "results/e4_dispatch_vs_fixed_simulated.json"], ["--trips", "20"]),
    "E5": (["experiments/e5_trust_recovery.py"],
           ["results/e5_trust_recovery_simulated.png", "results/e5_trust_recovery_simulated.json"], []),
    "neo4j_smoke": (["scripts/neo4j_smoke_test.py"], [], []),
    "api_examples": (["scripts/export_api_examples.py"], ["docs/api_examples/index.json"], []),
}


def clean(selected: list[str]) -> None:
    """Delete only this script's own outputs for the selected steps."""
    for name in selected:
        for rel in STEPS[name][1]:
            (REPO / rel).unlink(missing_ok=True)
    if "api_examples" in selected:
        for f in (REPO / "docs" / "api_examples").glob("*.json"):
            f.unlink()
    LOGS.mkdir(parents=True, exist_ok=True)
    for f in LOGS.glob("*.log"):
        f.unlink()


def run_step(name: str, fast: bool) -> dict:
    tail, produced, fast_args = STEPS[name]
    cmd = [PY, *tail, *(fast_args if fast else [])]
    t0 = time.time()
    proc = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, encoding="utf-8", errors="replace")
    seconds = round(time.time() - t0, 1)
    LOGS.mkdir(parents=True, exist_ok=True)
    (LOGS / f"{name}.log").write_text(proc.stdout + "\n--- stderr ---\n" + proc.stderr, encoding="utf-8")
    missing = [p for p in produced if not (REPO / p).exists()]
    ok = proc.returncode == 0 and not missing
    last = [ln for ln in proc.stdout.strip().splitlines() if ln.strip()][-1:] or [""]
    return {"step": name, "ok": ok, "seconds": seconds, "returncode": proc.returncode, "missing_outputs": missing,
            "last_line": last[0][:200]}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run all tests and experiments from a clean state")
    parser.add_argument("--fast", action="store_true", help="tiny experiment sizes: checks the plumbing, NOT the results")
    parser.add_argument("--only", default="", help="comma-separated steps: " + ",".join(STEPS))
    parser.add_argument("--keep", action="store_true", help="do not delete previous outputs first")
    args = parser.parse_args()
    selected = [s.strip() for s in args.only.split(",") if s.strip()] or list(STEPS)
    unknown = [s for s in selected if s not in STEPS]
    if unknown:
        print(f"unknown step(s): {unknown}; choose from {list(STEPS)}")
        return 2
    if not (REPO / "data" / "splits" / "sim_main_split.json").exists():
        print("ERROR: the fixed held-out split data/splits/sim_main_split.json is missing; it is never regenerated here")
        return 2
    if args.fast:
        print("FAST MODE: experiment sizes are tiny, so the numbers are NOT the reported results.")
        print("           Experiment outputs are NOT cleaned or kept: they are overwritten with fast-mode numbers.")
        print("           Do not commit them; re-run without --fast (or git checkout results/) to restore the real ones.")
        selected_clean = [s for s in selected if s in ("tests", "neo4j_smoke", "api_examples")]
    else:
        selected_clean = selected
    if not args.keep:
        clean(selected_clean)
    summary = []
    for name in selected:
        print(f"[{name}] running ...", flush=True)
        res = run_step(name, args.fast)
        summary.append(res)
        status = "OK  " if res["ok"] else "FAIL"
        print(f"[{name}] {status} {res['seconds']:>7.1f} s  {res['last_line']}", flush=True)
        if not res["ok"]:
            print(f"        see results/logs/{name}.log" + (f"; missing outputs: {res['missing_outputs']}" if res["missing_outputs"] else ""))
    RESULTS.mkdir(parents=True, exist_ok=True)
    out = {"fast_mode": args.fast, "all_ok": all(s["ok"] for s in summary), "steps": summary}
    (RESULTS / "run_all_summary.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    print("\nALL STEPS OK" if out["all_ok"] else "\nSOME STEPS FAILED")
    return 0 if out["all_ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
