"""E6: do operator-correlated outages change the plan, and does stranding fall? (SIMULATED, W2.2)

Same trips, same vehicle posterior, same trust estimates, same candidate generator and Monte Carlo scorer. The ONLY
difference between the two planners: the Site-only planner (before W2.2) treats chargers of one operator on different
Sites as independent; the operator-aware planner adds the operator-wide common cause (``BETA_OPERATOR``, an
ASSUMPTION of 0.2, see DECISIONS). Each plan is then driven through a simulated world in which operators really do go
down as a whole.

The world's mechanism is deliberately NOT the planner's: an operator is either down (every one of its chargers fails)
or up (each charger then works independently), with P(down) = ``operator_share`` x the scenario's failure rate and
each charger's marginal failure rate kept at the scenario's value. ``operator_share = 0`` is the control: no operator
effect exists, so the operator-aware planner is wrong about the world and any cost shows here. Operators, outages,
reports and trips are all SIMULATED; energy truth comes from verity.sim. The result is published whatever it shows.

    uv run python experiments/e6_operator_correlation.py [--trips 500]
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import math
import time

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from _world import CFG, DISTANCE_KM, N_CHARGERS, START_SOC, Fixture, _reports, setup_vehicle  # noqa: E402

from verity.chargers import RouteCharger  # noqa: E402
from verity.dispatch import execute_trip, plan_only  # noqa: E402
from verity.forecast import build_route  # noqa: E402
from verity.paths import RESULTS_DIR  # noqa: E402
from verity.schemas import ChargerRecord  # noqa: E402
from verity.sim import planner_view, run_trip, sample_trip_spec  # noqa: E402
from verity.sites import BETA_OPERATOR  # noqa: E402
from verity.trust.engine import TrustEngine  # noqa: E402
from verity.trust.reports_sim import NOW  # noqa: E402

FAILURE_RATES = (0.2, 0.4)
OPERATOR_SHARES = (0.0, 0.3, 0.6)  # share of failures that are operator-wide in the simulated world
N_OPERATORS = 3
MAX_UP_RELIABILITY = 0.99  # a charger of an operator that is up never works more often than this
STEM = "e6_operator_correlation_simulated"


def wilson(k: int, n: int, z: float = 1.96) -> list[float]:
    """95 % Wilson interval for a share (k of n)."""
    if n == 0:
        return [0.0, 1.0]
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return [round(max(centre - half, 0.0), 3), round(min(centre + half, 1.0), 3)]


def one_trial(fx: Fixture, failure_rate: float, operator_share: float, seed: int) -> dict:
    rng = np.random.default_rng([seed, int(round(failure_rate * 100)), int(round(operator_share * 100))])
    spec = sample_trip_spec(rng, fx.truth, 30, distance_range_km=DISTANCE_KM, start_soc_pct=100.0)
    run = run_trip(rng, fx.truth, spec)
    view = planner_view(rng, spec, run)
    route = build_route("car", view["distance_km"], np.array(view["block_end_m"]), np.array(view["block_kind"]),
                        view["elevation"], view["weather"], view["bearing_deg"])
    vehicle = dataclasses.replace(fx.vehicle, passengers=view["passengers"], ac_on=view["ac_on"], cargo_kg=view["cargo_kg"])

    kms = np.sort(rng.uniform(20.0, route.distance_km - 10.0, N_CHARGERS))
    op_of = rng.integers(N_OPERATORS, size=N_CHARGERS)
    chargers = [ChargerRecord(id=f"W{i}", name=f"Simulated charger {i}", lat=28.0 + float(k) / 111.0, lon=77.5,
                              operator=f"SimOp{int(op_of[i])}", site_type="highway",
                              connectors=[{"type": "CCS2", "kw": 50.0}], source_ids=["synthetic"])
                for i, k in enumerate(kms)]
    mean = 1.0 - failure_rate
    true_r = np.clip(rng.beta(mean * 5.0 + 0.3, (1.0 - mean) * 5.0 + 0.3, N_CHARGERS), 0.02, 0.99)
    trust = TrustEngine(chargers).estimate(_reports(chargers, true_r, rng), NOW).chargers
    route_chargers = [RouteCharger(c, float(k), 0.1) for c, k in zip(chargers, kms)]

    plans = {}
    for label, link in (("site_only", False), ("operator_aware", True)):
        stops, ev, ctx = plan_only(vehicle, route, route_chargers, trust, start_soc=START_SOC, seed=seed, cfg=CFG,
                                   trust_aware=True, link_operators=link)
        plans[label] = (stops, ev, ctx)

    # The simulated day: whole operators go down; the rest of the chargers fail on their own, marginals preserved.
    outage_p = operator_share * failure_rate
    down = rng.random(N_OPERATORS) < outage_p
    r_up = np.clip(true_r / (1.0 - outage_p), 0.0, MAX_UP_RELIABILITY)
    works = {c.id: bool((not down[op_of[i]]) and rng.random() < r_up[i]) for i, c in enumerate(chargers)}
    used = lambda km: float(np.interp(km * 1000.0, run.s_m, 100.0 - run.soc_true_pct))  # noqa: E731 - REAL use
    ctx = plans["site_only"][2]
    base_min = ctx.minutes_at(ctx.total_km)
    out = {"seed": seed, "any_operator_down": bool(down.any())}
    for label, (stops, ev, _) in plans.items():
        o = execute_trip(stops, used, START_SOC, works, base_min, ctx.min_per_pp, ctx.total_km, use_alts=True)
        out[label] = {"stops": [s.node.id for s in stops], "claimed": float(ev.confidence),
                      "stranded": bool(o.stranded), "minutes": float(o.minutes)}
    return out


def summarise(trials: list[dict]) -> dict:
    n = len(trials)
    site, aware = [t["site_only"] for t in trials], [t["operator_aware"] for t in trials]
    k_site, k_aware = sum(x["stranded"] for x in site), sum(x["stranded"] for x in aware)
    both_ok = [(x, y) for x, y in zip(site, aware) if not x["stranded"] and not y["stranded"]]
    claimed = lambda xs: float(np.mean([x["claimed"] for x in xs]))  # noqa: E731
    return {
        "trips": n,
        "plan_changes": round(float(np.mean([x["stops"] != y["stops"] for x, y in zip(site, aware)])), 3),
        "first_stop_changes": round(float(np.mean([(x["stops"][:1] != y["stops"][:1]) for x, y in zip(site, aware)])), 3),
        "stranding_rate_site_only": round(k_site / n, 3), "stranding_ci95_site_only": wilson(k_site, n),
        "stranding_rate_operator_aware": round(k_aware / n, 3), "stranding_ci95_operator_aware": wilson(k_aware, n),
        "stranded_only_when_site_only": int(sum(x["stranded"] and not y["stranded"] for x, y in zip(site, aware))),
        "stranded_only_when_operator_aware": int(sum(y["stranded"] and not x["stranded"] for x, y in zip(site, aware))),
        "trips_with_an_operator_down": int(sum(t["any_operator_down"] for t in trials)),
        "mean_extra_minutes_of_operator_aware": round(float(np.mean([y["minutes"] - x["minutes"] for x, y in both_ok])), 1)
        if both_ok else None,
        "claimed_confidence_site_only": round(claimed(site), 3), "realised_success_site_only": round(1.0 - k_site / n, 3),
        "claimed_confidence_operator_aware": round(claimed(aware), 3),
        "realised_success_operator_aware": round(1.0 - k_aware / n, 3),
    }


def run_e6(n: int) -> dict:
    t0 = time.time()
    fx = setup_vehicle("main")
    scenarios = {}
    for rate in FAILURE_RATES:
        for share in OPERATOR_SHARES:
            name = f"{rate:.0%} chargers down, {share:.0%} of failures operator-wide"
            scenarios[name] = {"failure_rate": rate, "operator_share": share,
                               **summarise([one_trial(fx, rate, share, 9000 + i) for i in range(n)])}
    return {"experiment": "E6 operator-correlated outages: Site-only vs operator-aware planning", "is_simulated": True,
            "trips_per_scenario": n, "vehicle_calibrated_trips": fx.vehicle.trips_calibrated,
            "assumed_beta_operator": BETA_OPERATOR, "operators_in_world": N_OPERATORS, "scenarios": scenarios,
            "runtime_s": round(time.time() - t0, 1),
            "notes": ["Chargers, operators, reports and outages are SIMULATED; energy truth comes from verity.sim.",
                      "BETA_OPERATOR = 0.2 is an assumption; the world's outage mechanism differs from the planner's model.",
                      "operator_share 0 is the control: the world has no operator effect, so the operator-aware "
                      "planner pays for a correlation that does not exist.",
                      "Stranding is rare in this world (see E3), so many differences are inside the 95 % intervals."]}


def plot(result: dict, path) -> None:
    names = list(result["scenarios"])
    x = np.arange(len(names))
    site = [result["scenarios"][s]["stranding_rate_site_only"] for s in names]
    aware = [result["scenarios"][s]["stranding_rate_operator_aware"] for s in names]
    fig, ax = plt.subplots(figsize=(9.5, 4.8))
    ax.bar(x - 0.18, site, 0.36, label="Site-only planner", color="tab:orange")
    ax.bar(x + 0.18, aware, 0.36, label="Operator-aware planner", color="tab:blue")
    for i, s in enumerate(names):
        ax.text(i, max(site[i], aware[i]) + 0.004, f"plan changed {result['scenarios'][s]['plan_changes']:.0%}",
                ha="center", fontsize=7)
    ax.set_xticks(x, [n.replace(", ", "\n") for n in names], fontsize=7)
    ax.set(ylabel="share of trips stranded", ylim=(0, max(site + aware) * 1.35 + 0.01),
           title="E6: operator-correlated outages, Site-only vs operator-aware planning (simulated)")
    ax.legend(fontsize=8)
    fig.text(0.5, 0.005, f"SIMULATED trips, operators and outages: {result['trips_per_scenario']} trips per scenario; "
             f"BETA_OPERATOR = {result['assumed_beta_operator']} is an assumption.", ha="center", fontsize=7)
    fig.tight_layout(rect=(0, 0.02, 1, 1))
    fig.savefig(path, dpi=140)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description="E6 operator-correlated outages (simulated)")
    parser.add_argument("--trips", type=int, default=500)
    parser.add_argument("--replot", action="store_true")
    args = parser.parse_args()
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    if args.replot:
        plot(json.loads((RESULTS_DIR / f"{STEM}.json").read_text(encoding="utf-8")), RESULTS_DIR / f"{STEM}.png")
        return
    result = run_e6(args.trips)
    (RESULTS_DIR / f"{STEM}.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    plot(result, RESULTS_DIR / f"{STEM}.png")
    print(f"E6 (SIMULATED) runtime {result['runtime_s']} s")
    for name, s in result["scenarios"].items():
        print(f"{name}: plan changed {s['plan_changes']:.0%} | stranded site-only {s['stranding_rate_site_only']:.1%} "
              f"vs operator-aware {s['stranding_rate_operator_aware']:.1%} (only-site {s['stranded_only_when_site_only']}, "
              f"only-aware {s['stranded_only_when_operator_aware']}) | claimed/realised site-only "
              f"{s['claimed_confidence_site_only']:.2f}/{s['realised_success_site_only']:.2f}, operator-aware "
              f"{s['claimed_confidence_operator_aware']:.2f}/{s['realised_success_operator_aware']:.2f}")


if __name__ == "__main__":
    main()
