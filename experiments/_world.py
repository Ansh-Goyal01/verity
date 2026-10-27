"""Shared SIMULATED world for E3 and E4: a truth trip, charger failures, noisy reports, and executed plans.

One trial = one 190 km trip of a calibrated compact e-SUV (energy from the truth simulator, which differs
from the planner's physics), 12 chargers at random km whose real reliabilities follow a failure-rate
scenario, and noisy reports about them. The planner only sees the forecast route, the vehicle posterior
(calibrated on TRAINING-split trips) and the trust engine's estimates; the world then draws which chargers
actually work on the day and the plan is driven through it with ``dispatch.execute_trip``.
"""

from __future__ import annotations

import dataclasses
import time
from dataclasses import dataclass
from datetime import timedelta

import numpy as np

from verity.calibrate import calibrate
from verity.chargers import RouteCharger
from verity.dispatch import VehicleContext, execute_trip, plan_only
from verity.forecast import ForecastConfig, build_route
from verity.schemas import ChargerRecord, Report
from verity.sim import VehicleTruth, load_truth, planner_view, run_trip, sample_trip_spec
from verity.splits import load_eval_dataset
from verity.trips import load_sim_observation
from verity.trust.engine import TrustEngine
from verity.trust.reports_sim import NOW
from verity.vehicles import get_prior

CFG = ForecastConfig(n_runs=800)
N_CHARGERS = 12
START_SOC = 85.0
DISTANCE_KM = (185.0, 195.0)
N_REPORT_USERS = 20


@dataclass(frozen=True)
class Fixture:
    vehicle: VehicleContext
    truth: VehicleTruth


def setup_vehicle(dataset: str = "main") -> Fixture:
    """The first compact e-SUV of the dataset, calibrated on its TRAINING trips only."""
    manifest, split = load_eval_dataset(dataset)
    veh = next(v for v in manifest["vehicles"] if v["class_key"] == "compact_esuv")
    truth = VehicleTruth(**load_truth(dataset, veh["trip_ids"][0])["vehicle"])
    prior = get_prior("compact_esuv")
    train = [load_sim_observation(dataset, t) for t in split["vehicles"][veh["vehicle_id"]]["train"]]
    post = calibrate(prior.u_mean, prior.u_cov, train, prior.base, prior.nominal_kwh)
    return Fixture(VehicleContext(veh["vehicle_id"], "compact_esuv", post, 2, True, len(train)), truth)


def _reports(chargers: list[ChargerRecord], true_r: np.ndarray, rng: np.random.Generator) -> list[Report]:
    out = []
    for u in range(N_REPORT_USERS):
        sens, spec = rng.uniform(0.80, 0.97), rng.uniform(0.70, 0.95)
        for _ in range(int(rng.integers(3, 8))):
            c = int(rng.integers(len(chargers)))
            working = rng.random() < true_r[c]
            label = rng.random() < sens if working else rng.random() >= spec
            out.append(Report(charger_id=chargers[c].id, source_id=f"user-{u}", status="working" if label else "broken",
                              t=NOW - timedelta(days=float(rng.uniform(0, 60)))))
    return out


def one_trial(fx: Fixture, failure_rate: float, seed: int) -> dict:
    rng = np.random.default_rng([seed, int(round(failure_rate * 100))])
    spec = sample_trip_spec(rng, fx.truth, 30, distance_range_km=DISTANCE_KM, start_soc_pct=100.0)
    run = run_trip(rng, fx.truth, spec)
    view = planner_view(rng, spec, run)
    route = build_route("car", view["distance_km"], np.array(view["block_end_m"]), np.array(view["block_kind"]),
                        view["elevation"], view["weather"], view["bearing_deg"])
    vehicle = dataclasses.replace(fx.vehicle, passengers=view["passengers"], ac_on=view["ac_on"], cargo_kg=view["cargo_kg"])

    kms = np.sort(rng.uniform(20.0, route.distance_km - 10.0, N_CHARGERS))
    chargers = [ChargerRecord(id=f"W{i}", name=f"Simulated charger {i}", lat=28.0, lon=77.5, operator="SimOp",
                              site_type="highway", connectors=[{"type": "CCS2", "kw": 50.0}], source_ids=["synthetic"])
                for i in range(N_CHARGERS)]
    mean = 1.0 - failure_rate
    true_r = np.clip(rng.beta(mean * 5.0 + 0.3, (1.0 - mean) * 5.0 + 0.3, N_CHARGERS), 0.02, 0.99)
    trust = TrustEngine(chargers).estimate(_reports(chargers, true_r, rng), NOW).chargers
    route_chargers = [RouteCharger(c, float(k), 0.1) for c, k in zip(chargers, kms)]

    t0 = time.perf_counter()
    blind_stops, _, ctx = plan_only(vehicle, route, route_chargers, trust, start_soc=START_SOC, seed=seed, cfg=CFG,
                                    trust_aware=False)
    t1 = time.perf_counter()
    aware_stops, aware_ev, _ = plan_only(vehicle, route, route_chargers, trust, start_soc=START_SOC, seed=seed, cfg=CFG,
                                         trust_aware=True)
    t2 = time.perf_counter()

    works = {c.id: bool(rng.random() < r) for c, r in zip(chargers, true_r)}
    used = lambda km: float(np.interp(km * 1000.0, run.s_m, 100.0 - run.soc_true_pct))  # noqa: E731 - REAL use
    base_min = ctx.minutes_at(ctx.total_km)

    def drive(stops, alts: bool):
        return execute_trip(stops, used, START_SOC, works, base_min, ctx.min_per_pp, ctx.total_km, use_alts=alts)

    first = lambda stops: stops[0].node.id if stops else None  # noqa: E731
    outcomes = {"blind_alts": drive(blind_stops, True), "blind_fixed": drive(blind_stops, False),
                "aware_alts": drive(aware_stops, True)}
    return {
        "seed": seed, "failure_rate": failure_rate, "distance_km": route.distance_km,
        "true_energy_pp": float(100.0 - run.soc_true_pct[-1]),
        "first_stop_blind": first(blind_stops), "first_stop_aware": first(aware_stops),
        "stops_blind": [s.node.id for s in blind_stops], "stops_aware": [s.node.id for s in aware_stops],
        "aware_claimed_confidence": aware_ev.confidence,
        "plan_ms_blind": 1000 * (t1 - t0), "plan_ms_aware": 1000 * (t2 - t1),
        **{f"{k}_{f}": getattr(o, f) for k, o in outcomes.items() for f in ("stranded", "minutes", "stops_used")},
    }


def run_trials(fx: Fixture, failure_rate: float, n: int, seed0: int = 9000) -> list[dict]:
    return [one_trial(fx, failure_rate, seed0 + i) for i in range(n)]


def rate(trials: list[dict], key: str) -> float:
    return float(np.mean([t[key] for t in trials]))
