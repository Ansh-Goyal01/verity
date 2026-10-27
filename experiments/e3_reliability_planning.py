"""E3: does charger reliability change decisions? Reliability-blind vs trust-aware planning (SIMULATED).

Same trips, same vehicle posterior, same candidate generator and Monte Carlo scorer, same driving policy
(divert to a working alternate if the planned charger is dead). The ONLY difference: the blind planner
assumes every charger works; the trust-aware planner uses the trust engine's reliability estimates.
Each plan is then driven through a simulated world in which chargers really fail at the scenario's rate.

Failure-rate scenarios come from PLAN s3: ~18 % of stations non-operational nationally (Parliament data,
Mar 2026) and DC fast-charger downtime near 40 % (fleet playbook). Both approximate - verify.
Metrics: how often the first stop changes, stranding rate, minutes lost. Energy truth comes from the
truth simulator, so a baseline stranding rate exists even without charger failures (see E4's 0 % row).
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

from verity.paths import RESULTS_DIR  # noqa: E402

RATES = (0.2, 0.4)


def summarise(trials: list[dict]) -> dict:
    n = len(trials)
    both_ok = [t for t in trials if not t["blind_alts_stranded"] and not t["aware_alts_stranded"]]
    blind_only = sum(t["blind_alts_stranded"] and not t["aware_alts_stranded"] for t in trials)
    aware_only = sum(t["aware_alts_stranded"] and not t["blind_alts_stranded"] for t in trials)
    changed = [t for t in trials if t["first_stop_blind"] != t["first_stop_aware"]]
    return {
        "trips": n,
        "first_stop_changes": round(len(changed) / n, 3),
        "stop_set_changes": round(float(np.mean([t["stops_blind"] != t["stops_aware"] for t in trials])), 3),
        "stranding_rate_blind": round(rate(trials, "blind_alts_stranded"), 3),
        "stranding_rate_trust_aware": round(rate(trials, "aware_alts_stranded"), 3),
        "stranded_only_when_blind": int(blind_only), "stranded_only_when_aware": int(aware_only),
        "mean_minutes_blind_not_stranded": round(float(np.mean([t["blind_alts_minutes"] for t in both_ok])), 1) if both_ok else None,
        "mean_minutes_aware_not_stranded": round(float(np.mean([t["aware_alts_minutes"] for t in both_ok])), 1) if both_ok else None,
        "mean_minutes_lost_by_being_aware": round(float(np.mean([t["aware_alts_minutes"] - t["blind_alts_minutes"] for t in both_ok])), 1) if both_ok else None,
        "plan_ms_blind_mean": round(rate(trials, "plan_ms_blind"), 0), "plan_ms_aware_mean": round(rate(trials, "plan_ms_aware"), 0),
    }


def run_e3(n: int) -> dict:
    t0 = time.time()
    fx = setup_vehicle("main")
    out = {f"{r:.0%} chargers down": summarise(run_trials(fx, r, n)) for r in RATES}
    return {"experiment": "E3 reliability-blind vs trust-aware planning", "is_simulated": True, "trips_per_scenario": n,
            "vehicle_calibrated_trips": fx.vehicle.trips_calibrated, "scenarios": out, "runtime_s": round(time.time() - t0, 1),
            "notes": ["Chargers, reports and failures are SIMULATED; energy truth comes from verity.sim.",
                      "Failure rates 20 % / 40 % follow PLAN s3 figures (approximate - verify)."]}


def plot(result: dict, path) -> None:
    names = list(result["scenarios"])
    x = np.arange(len(names))
    fig, ax = plt.subplots(figsize=(7, 4.6))
    blind = [result["scenarios"][s]["stranding_rate_blind"] for s in names]
    aware = [result["scenarios"][s]["stranding_rate_trust_aware"] for s in names]
    ax.bar(x - 0.18, blind, 0.36, label="Reliability-blind planner", color="tab:red")
    ax.bar(x + 0.18, aware, 0.36, label="Trust-aware planner", color="tab:green")
    for i, s in enumerate(names):
        ax.text(i, max(blind[i], aware[i]) + 0.01, f"first stop changed in {result['scenarios'][s]['first_stop_changes']:.0%} of trips",
                ha="center", fontsize=8)
    ax.set_xticks(x, names)
    ax.set(ylabel="share of trips stranded", title="E3: reliability-blind vs trust-aware planning (simulated)",
           ylim=(0, max(blind + aware) * 1.35 + 0.02))
    ax.legend(fontsize=8)
    fig.text(0.5, 0.005, f"SIMULATED trips and charger failures: {result['trips_per_scenario']} trips per scenario.",
             ha="center", fontsize=7)
    fig.tight_layout(rect=(0, 0.02, 1, 1))
    fig.savefig(path, dpi=140)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description="E3 reliability-blind vs trust-aware (simulated)")
    parser.add_argument("--trips", type=int, default=200)
    parser.add_argument("--replot", action="store_true")
    args = parser.parse_args()
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stem = "e3_reliability_planning_simulated"
    if args.replot:
        plot(json.loads((RESULTS_DIR / f"{stem}.json").read_text(encoding="utf-8")), RESULTS_DIR / f"{stem}.png")
        return
    result = run_e3(args.trips)
    (RESULTS_DIR / f"{stem}.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    plot(result, RESULTS_DIR / f"{stem}.png")
    print(f"E3 (SIMULATED) runtime {result['runtime_s']} s")
    for name, s in result["scenarios"].items():
        print(f"{name}: first stop changed {s['first_stop_changes']:.0%} | stranded blind {s['stranding_rate_blind']:.1%} "
              f"vs trust-aware {s['stranding_rate_trust_aware']:.1%} (only-blind {s['stranded_only_when_blind']}, "
              f"only-aware {s['stranded_only_when_aware']}) | minutes lost {s['mean_minutes_lost_by_being_aware']} "
              f"| plan ms {s['plan_ms_blind_mean']}/{s['plan_ms_aware_mean']}")


if __name__ == "__main__":
    main()
