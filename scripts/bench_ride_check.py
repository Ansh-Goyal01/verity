"""F8f: ride-check latency on this laptop with the in-memory graph (target: p95 under 5 s).

Times complete POST /api/ride-check service calls (route from the committed bundle, trust from the graph, Monte
Carlo, Bingo Line, levers) at the API's default run count, varying battery and earning rate so no two calls are the
same request. Writes results/f8_ride_check_latency.json.

Run: uv run python scripts/bench_ride_check.py [--calls 30]
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import time

import numpy as np

from api.service import EngineService
from verity.paths import RESULTS_DIR

RIDE = {"vehicle_id": "V12", "current": {"lat": 28.6280, "lon": 77.3649}, "pickup": {"lat": 28.5708, "lon": 77.3261},
        "drop": {"lat": 28.5562, "lon": 77.0999}, "departure_time": "2026-11-13T19:00:00+05:30", "passengers": 1,
        "ac_on": True, "target_confidence": 0.9}
TARGET_P95_S = 5.0


def main() -> None:
    parser = argparse.ArgumentParser(description="ride-check latency (in-memory graph)")
    parser.add_argument("--calls", type=int, default=30)
    args = parser.parse_args()
    svc = EngineService(graph_backend="memory")
    svc.ride_check({**RIDE, "soc_pct": 50.0, "earning_rs_per_hour": None})  # warm-up (imports, caches)
    times, decisions = [], []
    for i in range(args.calls):
        req = {**RIDE, "soc_pct": 20.0 + i % 40, "earning_rs_per_hour": 40.0 + 5 * i}
        t0 = time.perf_counter()
        out = svc.ride_check(req)
        times.append(time.perf_counter() - t0)
        decisions.append(out["decision"])
    svc.close()
    t = np.array(times)
    result = {"what": "POST /api/ride-check service call, in-memory graph, committed route bundle",
              "calls": args.calls, "n_runs": svc.cfg.n_runs, "p50_s": round(float(np.percentile(t, 50)), 3),
              "p95_s": round(float(np.percentile(t, 95)), 3), "max_s": round(float(t.max()), 3),
              "target_p95_s": TARGET_P95_S, "meets_target": bool(np.percentile(t, 95) < TARGET_P95_S),
              "decisions": {d: decisions.count(d) for d in sorted(set(decisions))},
              "machine": {"platform": platform.platform(), "python": platform.python_version(),
                          "cpu_count": os.cpu_count()},
              "is_simulated": True, "note": "Vehicle is a SIMULATED demo vehicle; timings are real on this machine."}
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    (RESULTS_DIR / "f8_ride_check_latency.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()
