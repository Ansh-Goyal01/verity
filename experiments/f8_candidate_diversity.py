"""F8d: do diverse candidates (reliability-ranked and spatially distinct chains) improve decisions? (SIMULATED)

The same simulated 190 km trips as E3/E4 (experiments/_world.py): each trip is planned by the trust-aware
dispatcher twice with the same seed, once with only the 6 cheapest chains (before F8) and once with the added
diverse chains, and both plans are driven through the same simulated world. Reports stranding (Wilson 95 % CI),
minutes when both arrive, how often the plan changes, and planning time on this laptop.

Run: uv run python experiments/f8_candidate_diversity.py [--trips 100]
"""

from __future__ import annotations

import argparse
import json
import time

import numpy as np
from _world import one_trial, setup_vehicle  # noqa: E402

from verity.paths import RESULTS_DIR  # noqa: E402

RATES = (0.1, 0.2, 0.4)
STEM = "f8_candidate_diversity_simulated"


def wilson(k: int, n: int, z: float = 1.96) -> list[float]:
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return [round(float(centre - half), 3), round(float(centre + half), 3)]


def _mean(rows: list[dict], key: str) -> float | None:
    return round(float(np.mean([r[key] for r in rows])), 1) if rows else None


def compare(fx, rate: float, n: int, seed0: int = 9000) -> dict:
    pairs = [(one_trial(fx, rate, seed0 + i, diversity=False), one_trial(fx, rate, seed0 + i, diversity=True))
             for i in range(n)]
    old = [o for o, _ in pairs]
    new = [w for _, w in pairs]
    k_old = int(sum(o["aware_alts_stranded"] for o in old))
    k_new = int(sum(w["aware_alts_stranded"] for w in new))
    both = [(o, w) for o, w in pairs if not o["aware_alts_stranded"] and not w["aware_alts_stranded"]]
    return {
        "trips": n, "stranded_cheapest_only": k_old, "stranded_diverse": k_new,
        "ci95_cheapest_only": wilson(k_old, n), "ci95_diverse": wilson(k_new, n),
        "plan_changed": int(sum(o["stops_aware"] != w["stops_aware"] for o, w in pairs)),
        "mean_minutes_both_arrived_cheapest_only": _mean([o for o, _ in both], "aware_alts_minutes"),
        "mean_minutes_both_arrived_diverse": _mean([w for _, w in both], "aware_alts_minutes"),
        "claimed_confidence_mean_cheapest_only": round(float(np.mean([o["aware_claimed_confidence"] for o in old])), 3),
        "claimed_confidence_mean_diverse": round(float(np.mean([w["aware_claimed_confidence"] for w in new])), 3),
        "plan_ms_mean_cheapest_only": _mean(old, "plan_ms_aware"), "plan_ms_mean_diverse": _mean(new, "plan_ms_aware"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="F8d candidate diversity (simulated)")
    parser.add_argument("--trips", type=int, default=100)
    args = parser.parse_args()
    t0 = time.time()
    fx = setup_vehicle("main")
    scenarios = {f"{r:.0%} chargers down": compare(fx, r, args.trips) for r in RATES}
    result = {"experiment": "F8d candidate diversity", "is_simulated": True, "trips_per_scenario": args.trips,
              "scenarios": scenarios, "runtime_s": round(time.time() - t0, 1),
              "notes": ["SIMULATED world, chargers and reports (experiments/_world.py); same seeds for both planners.",
                        "cheapest_only = the 6 lowest expected-minute chains (before F8); diverse = plus 3 "
                        "reliability-ranked and 2 spatially distinct chains, all scored by the same Monte Carlo."]}
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / f"{STEM}.json"
    out.write_text(json.dumps(result, indent=1), encoding="utf-8")
    for name, s in scenarios.items():
        print(f"{name}: stranded {s['stranded_cheapest_only']} -> {s['stranded_diverse']} of {s['trips']}, plan changed "
              f"{s['plan_changed']}, ms {s['plan_ms_mean_cheapest_only']} -> {s['plan_ms_mean_diverse']}")
    print(f"-> {out} ({result['runtime_s']} s)")


if __name__ == "__main__":
    main()
