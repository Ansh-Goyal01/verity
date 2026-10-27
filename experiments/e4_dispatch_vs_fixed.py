"""E4: do backup plans matter? A fixed shortest-path plan vs the dispatch policy (SIMULATED).

* fixed    - the single fastest plan chosen by a reliability-blind planner and driven with no alternates:
             if a planned charger is dead the driver carries on and is stranded if the next stop is out of reach.
* dispatch - the trust-aware plan with reachable alternates and diversion when a planned charger fails.

Hundreds of simulated 190 km trips per failure-rate scenario (energy from the truth simulator, charger
failures drawn at the scenario rate). Rates: 0 % is the control (any stranding there is energy-forecast error,
not charger failure), 10 %, 20 % ~ the 18 % non-operational share of PLAN s3, and 40 % ~ the DC fast-charger
downtime of PLAN s3 (both approximate - verify). Also reports compute time per plan on this laptop.
"""

from __future__ import annotations

import argparse
import json
import time

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from _world import rate, run_trials, setup_vehicle  # noqa: E402

from verity.dispatch import dispatch  # noqa: E402
from verity.paths import RESULTS_DIR  # noqa: E402

RATES = (0.0, 0.1, 0.2, 0.4)


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return float(centre - half), float(centre + half)


def summarise(trials: list[dict]) -> dict:
    n = len(trials)
    k_fixed = int(sum(t["blind_fixed_stranded"] for t in trials))
    k_disp = int(sum(t["aware_alts_stranded"] for t in trials))
    both_ok = [t for t in trials if not t["blind_fixed_stranded"] and not t["aware_alts_stranded"]]
    return {
        "trips": n, "stranded_fixed": k_fixed, "stranded_dispatch": k_disp,
        "stranding_rate_fixed": round(k_fixed / n, 3), "stranding_rate_dispatch": round(k_disp / n, 3),
        "ci95_fixed": [round(v, 3) for v in wilson(k_fixed, n)], "ci95_dispatch": [round(v, 3) for v in wilson(k_disp, n)],
        "stranded_only_fixed": int(sum(t["blind_fixed_stranded"] and not t["aware_alts_stranded"] for t in trials)),
        "stranded_only_dispatch": int(sum(t["aware_alts_stranded"] and not t["blind_fixed_stranded"] for t in trials)),
        "mean_minutes_fixed_not_stranded": round(float(np.mean([t["blind_fixed_minutes"] for t in both_ok])), 1) if both_ok else None,
        "mean_minutes_dispatch_not_stranded": round(float(np.mean([t["aware_alts_minutes"] for t in both_ok])), 1) if both_ok else None,
        "plan_ms_fixed_mean": round(rate(trials, "plan_ms_blind"), 0), "plan_ms_dispatch_mean": round(rate(trials, "plan_ms_aware"), 0),
    }


def full_dispatch_timing(fx, n: int = 5) -> dict:
    """Wall time of a COMPLETE dispatch (plan + Bingo Line + point of no return + levers) on the real route."""
    from verity.chargers import chargers_near_route, load_chargers
    from verity.routing import load_planned_route
    from verity.trust.engine import TrustEngine
    from verity.trust.reports_sim import NOW

    route, snap = load_planned_route()
    chargers, _ = load_chargers()
    near = chargers_near_route(chargers, np.array(snap["polyline"]), np.array(snap["polyline_km"]), 3.0)
    trust = TrustEngine([rc.charger for rc in near]).estimate([], NOW).chargers
    ms = [dispatch(fx.vehicle, route, near, trust, start_soc=70.0, seed=i).compute_ms for i in range(n)]
    return {"real_route_km": snap["distance_km"], "chargers_near_route": len(near), "runs_per_forecast": 2000,
            "full_dispatch_ms_mean": round(float(np.mean(ms)), 0), "full_dispatch_ms_max": int(max(ms))}


def run_e4(n: int) -> dict:
    t0 = time.time()
    fx = setup_vehicle("main")
    scenarios = {f"{r:.0%} chargers down": summarise(run_trials(fx, r, n)) for r in RATES}
    return {"experiment": "E4 fixed shortest-path plan vs dispatch policy", "is_simulated": True, "trips_per_scenario": n,
            "total_simulated_trips": n * len(RATES), "scenarios": scenarios, "compute": full_dispatch_timing(fx),
            "runtime_s": round(time.time() - t0, 1),
            "notes": ["The 0 % row is the control: stranding there comes from energy-forecast error, not chargers.",
                      "Chargers, reports and failures are SIMULATED; energy truth comes from verity.sim.",
                      "800 trips here, not thousands: runtime limited tonight; confidence intervals are Wilson 95 %."]}


def plot(result: dict, path) -> None:
    names = list(result["scenarios"])
    x = np.arange(len(names))
    fig, ax = plt.subplots(figsize=(8, 4.8))
    for off, key, label, colour in ((-0.18, "fixed", "Fixed shortest-path plan (no backups)", "tab:red"),
                                    (0.18, "dispatch", "Dispatch policy (trust-aware + alternates)", "tab:green")):
        vals = [result["scenarios"][s][f"stranding_rate_{key}"] for s in names]
        ci = np.array([result["scenarios"][s][f"ci95_{key}"] for s in names])
        ax.bar(x + off, vals, 0.36, label=label, color=colour,
               yerr=[np.array(vals) - ci[:, 0], ci[:, 1] - np.array(vals)], capsize=3)
    ax.set_xticks(x, names)
    ax.set(ylabel="share of trips stranded", title="E4: fixed shortest-path plan vs dispatch policy (simulated)")
    ax.legend(fontsize=8)
    fig.text(0.5, 0.005, f"SIMULATED: {result['trips_per_scenario']} trips per scenario, whiskers = Wilson 95% CI. "
             "0% row = control (forecast error only).", ha="center", fontsize=7)
    fig.tight_layout(rect=(0, 0.02, 1, 1))
    fig.savefig(path, dpi=140)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description="E4 fixed plan vs dispatch (simulated)")
    parser.add_argument("--trips", type=int, default=200)
    parser.add_argument("--replot", action="store_true")
    args = parser.parse_args()
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stem = "e4_dispatch_vs_fixed_simulated"
    if args.replot:
        plot(json.loads((RESULTS_DIR / f"{stem}.json").read_text(encoding="utf-8")), RESULTS_DIR / f"{stem}.png")
        return
    result = run_e4(args.trips)
    (RESULTS_DIR / f"{stem}.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    plot(result, RESULTS_DIR / f"{stem}.png")
    print(f"E4 (SIMULATED) {result['total_simulated_trips']} trips, runtime {result['runtime_s']} s; compute: {result['compute']}")
    for name, s in result["scenarios"].items():
        print(f"{name}: stranded fixed {s['stranding_rate_fixed']:.1%} {s['ci95_fixed']} vs dispatch {s['stranding_rate_dispatch']:.1%} "
              f"{s['ci95_dispatch']} (only-fixed {s['stranded_only_fixed']}, only-dispatch {s['stranded_only_dispatch']}) | "
              f"minutes {s['mean_minutes_fixed_not_stranded']} vs {s['mean_minutes_dispatch_not_stranded']}")


if __name__ == "__main__":
    main()
