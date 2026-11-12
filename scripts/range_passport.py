"""F9 Range Passport: certificates (JSON + markdown) and their validation.

* Demo certificate for fleet vehicle V07 (SIMULATED trips; its hidden simulator truth checks the estimate).
* SIMULATED validation: every simulated car of the ``main`` dataset, passport from its first k TRAINING trips vs the
  simulator's true usable capacity.
* REAL validation (when data/raw/bmw_i3/ is present): BMW i3 60 Ah, TRAINING-split trips only (the held-out test
  trips are never opened here); passport from k random trips vs the ground truth from integrated measured battery
  voltage x current (kWh per 100 BMS SoC points over all usable training trips). German driving: REAL, not Indian.

Range in Indian conditions is always the engine's simulation on two real Indian reference routes (real router,
elevation and forecast weather): Noida -> Agra (highway) and Noida -> IGI Airport T3 in the evening (urban +
expressway).

Run: uv run python scripts/range_passport.py [--subsets 20]
"""

from __future__ import annotations

import argparse
import json
import time

import numpy as np

from verity.datasets import bmw_i3
from verity.forecast import ForecastConfig
from verity.passport import ReferenceRoute, capacity, certificate, fit, markdown
from verity.paths import PROCESSED_DIR, RESULTS_DIR
from verity.routing import load_planned_route, snapshot_to_route
from verity.sim import load_manifest, load_truth
from verity.splits import load_eval_dataset
from verity.trips import load_sim_observation
from verity.vehicles import get_prior

CFG = ForecastConfig(n_runs=2000)
K_SIM = (1, 3, 5, 10)
K_BMW = (1, 3, 5, 10, 20, 40)
SEED = 9


def reference_routes() -> list[ReferenceRoute]:
    hw, snap = load_planned_route()
    ride = json.loads((PROCESSED_DIR / "ride_demo_noida_t3.json").read_text(encoding="utf-8"))
    return [ReferenceRoute("noida_agra", "Noida -> Agra highway, afternoon", hw,
                           f"{snap['source']} route, Open-Meteo elevation, forecast for {snap['departure_time']}"),
            ReferenceRoute("noida_igi_evening", "Noida -> IGI Airport T3, evening", snapshot_to_route(ride),
                           f"{ride['source']} route, Open-Meteo elevation, forecast for {ride['weather_points'][-1]['hour']}")]


def interval_check(cap: dict, truth: float) -> dict:
    return {"estimate_kwh": cap["usable_kwh_p50"], "truth_kwh": round(truth, 2),
            "error_pct": round(100.0 * (cap["usable_kwh_p50"] - truth) / truth, 1),
            "in_ci80": cap["ci80_kwh"][0] <= truth <= cap["ci80_kwh"][1],
            "in_ci95": cap["ci95_kwh"][0] <= truth <= cap["ci95_kwh"][1]}


def summarise(rows: list[dict]) -> dict:
    err = np.abs([r["error_pct"] for r in rows])
    return {"n": len(rows), "median_abs_error_pct": round(float(np.median(err)), 1),
            "mean_error_pct": round(float(np.mean([r["error_pct"] for r in rows])), 1),
            "ci80_coverage": round(float(np.mean([r["in_ci80"] for r in rows])), 2),
            "ci95_coverage": round(float(np.mean([r["in_ci95"] for r in rows])), 2)}


def simulated_validation() -> dict:
    manifest, split = load_eval_dataset("main")
    out: dict[str, list] = {str(k): [] for k in K_SIM}
    for veh in manifest["vehicles"]:
        if veh["class_key"] == "e_scooter":
            continue
        prior = get_prior(veh["class_key"])
        train = split["vehicles"][veh["vehicle_id"]]["train"]
        truth = float(load_truth("main", train[0])["vehicle"]["usable_kwh_true"])
        obs = [load_sim_observation("main", t) for t in train]
        for k in K_SIM:
            out[str(k)].append({"vehicle_id": veh["vehicle_id"], **interval_check(capacity(fit(prior, obs[:k])), truth)})
    return {"label": "SIMULATED: simulator cars, truth = the simulator's hidden usable capacity",
            "by_k": {k: {**summarise(rows), "rows": rows} for k, rows in out.items()}}


def bmw_validation(subsets: int) -> tuple[dict, list, object]:
    if not bmw_i3.available():
        return {"label": "REAL BMW i3 validation NOT RUN: data/raw/bmw_i3/ is missing"}, [], None
    from verity.datasets.bmw_passport import i3_prior, measured, observation, truth_kwh_per_100, usable_for_passport

    trips = [t for t in bmw_i3.load_split_part("train") if usable_for_passport(t)]
    prior = i3_prior()
    obs = [observation(t, prior) for t in trips]
    meas = [measured(t) for t in trips]
    truth = truth_kwh_per_100(meas)
    rng = np.random.default_rng(SEED)
    by_k = {}
    for k in K_BMW:
        if k > len(obs):
            continue
        draws = 1 if k == len(obs) else subsets
        rows = [interval_check(capacity(fit(prior, [obs[i] for i in rng.choice(len(obs), k, replace=False)])), truth)
                for _ in range(draws)]
        by_k[str(k)] = summarise(rows)
    ah = 100.0 * sum(m.charge_ah for m in meas) / sum(m.soc_drop_pct for m in meas)
    result = {"label": "REAL: BMW i3 60 Ah, Germany (TU Munich, DOI 10.21227/6jr9-5235), TRAINING-split trips only; "
                       "not Indian conditions",
              "trips_usable": len(trips), "km": round(float(sum(o.distance_km for o in obs)), 1),
              "truth": {"kwh_per_100_soc_points": round(truth, 2), "ah_per_100_soc_points": round(ah, 1),
                        "method": "integrated measured battery voltage x current over each trip's valid-SoC window, "
                                  "pooled over all usable training trips"},
              "prior_kwh_p50": capacity(fit(prior, []))["usable_kwh_p50"],
              "model_ceiling_kwh": round(prior.nominal_kwh * 1.05, 2), "by_k": by_k}
    return result, obs, prior


def write(stem: str, cert: dict) -> None:
    (RESULTS_DIR / f"{stem}.json").write_text(json.dumps(cert, indent=1), encoding="utf-8")
    (RESULTS_DIR / f"{stem}.md").write_text(markdown(cert), encoding="utf-8")


def demo_certificate(refs: list[ReferenceRoute]) -> dict:
    demo = next(v for v in load_manifest("demo")["vehicles"] if v["vehicle_id"] == "V07")
    obs = [load_sim_observation("demo", t) for t in demo["trip_ids"][:6]]  # the 6 trips the fleet demo calibrates on
    truth = float(load_truth("demo", demo["trip_ids"][0])["vehicle"]["usable_kwh_true"])
    cert = certificate({"id": "V07", "label": "Demo vehicle V07 (compact e-SUV, aged battery)"},
                       get_prior("compact_esuv"), obs, refs, cfg=CFG, seed=SEED,
                       label="SIMULATED: demo fleet trips (verity.sim)")
    v = {"label": "SIMULATED: checked against the simulator's hidden usable capacity",
         **interval_check(cert["capacity"], truth)}
    v["summary"] = (f"Estimate {v['estimate_kwh']} kWh vs simulator truth {v['truth_kwh']} kWh ({v['error_pct']:+.1f} %); "
                    f"truth inside the 80 % interval: {v['in_ci80']}.")
    cert["validation"] = v
    return cert


def bmw_certificate(bmw: dict, obs: list, prior, refs: list[ReferenceRoute]) -> dict:
    cert = certificate({"id": "bmw_i3_tum", "label": "BMW i3 60 Ah (TU Munich test car)"}, prior, obs, refs, cfg=CFG,
                       seed=SEED, label="REAL trips (BMW i3, Germany); range on Indian routes is the engine's SIMULATION")
    truth = bmw["truth"]["kwh_per_100_soc_points"]
    v = {"label": "REAL: BMW i3 ground truth from integrated measured current", **interval_check(cert["capacity"], truth),
         "learning_curve": bmw["by_k"]}
    v["summary"] = (f"Passport estimate {v['estimate_kwh']} kWh (80 % CI {cert['capacity']['ci80_kwh'][0]}-"
                    f"{cert['capacity']['ci80_kwh'][1]}) vs measured {v['truth_kwh']} kWh per 100 SoC points "
                    f"({v['error_pct']:+.1f} %); truth inside the 95 % interval: {v['in_ci95']}. The model caps usable "
                    f"capacity at 1.05 x nominal = {bmw['model_ceiling_kwh']} kWh. On its own logged trips this car drove "
                    f"{cert['evidence']['observed_km_per_100_soc_points']} km per 100 SoC points (measured, German "
                    "conditions incl. winter heating); the Indian-route ranges above are model outputs NOT validated "
                    "against measured Indian driving, and the capacity bias suggests the model under-predicts this "
                    "car's consumption.")
    cert["validation"] = v
    return cert


def main() -> None:
    parser = argparse.ArgumentParser(description="F9 Range Passport certificates and validation")
    parser.add_argument("--subsets", type=int, default=20)
    args = parser.parse_args()
    t0 = time.time()
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    refs = reference_routes()
    sim = simulated_validation()
    bmw, bmw_obs, bmw_prior = bmw_validation(args.subsets)
    demo_cert = demo_certificate(refs)
    write("range_passport_demo_V07", demo_cert)
    if bmw_obs:
        write("range_passport_bmw_i3", bmw_certificate(bmw, bmw_obs, bmw_prior, refs))
    result = {"experiment": "F9 Range Passport validation", "simulated": sim, "bmw_i3": bmw,
              "demo_V07": demo_cert["validation"], "runtime_s": round(time.time() - t0, 1)}
    (RESULTS_DIR / "f9_passport_validation.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    for k, s in sim["by_k"].items():
        print(f"SIMULATED k={k}: median |error| {s['median_abs_error_pct']} %, 80 % coverage {s['ci80_coverage']} "
              f"(n={s['n']})")
    for k, s in (bmw.get("by_k") or {}).items():
        print(f"REAL BMW k={k}: median |error| {s['median_abs_error_pct']} %, mean error {s['mean_error_pct']} %, "
              f"95 % coverage {s['ci95_coverage']} (draws={s['n']})")
    print(f"demo V07: {demo_cert['validation']['summary']}  ({result['runtime_s']} s)")


if __name__ == "__main__":
    main()
