"""Range Passport (F9): what a used EV's battery really holds and how far it really goes, from its logged trips.

From k logged trips of ONE vehicle the physics calibration (calibrate.py) gives a posterior over the vehicle's
parameters; the passport reads off it

* usable capacity: kWh per 100 SoC points, with an 80 % and a 95 % credible interval;
* real-world range in Indian conditions: the Monte Carlo (forecast.py, shared trip-level factors) on fixed
  reference routes (real Indian routes with real elevation and forecast weather), AC on, stated occupants:
  km from 100 % to 0 % of the usable battery, p10 / p50 / p90, and the Wh/km behind it.

A certificate is a JSON document (schema ``verity.range_passport.v1``) and a markdown page. Pure: no network, no
file access; scripts/range_passport.py loads the data and writes the files.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np

from .calibrate import Posterior, calibrate
from .forecast import ForecastConfig, PlannedRoute, simulate_runs
from .physics import total_mass_kg
from .trips import TripObservation
from .vehicles import VehiclePrior

SCHEMA = "verity.range_passport.v1"
MIN_TRIPS = 3  # below this the passport is marked PRELIMINARY (approximate - verify)


@dataclass(frozen=True, eq=False)
class ReferenceRoute:
    key: str
    label: str
    route: PlannedRoute
    source: str  # where the route, elevation and weather come from
    passengers: int = 2
    ac_on: bool = True


def fit(prior: VehiclePrior, observations: Sequence[TripObservation]) -> Posterior:
    return calibrate(prior.u_mean, prior.u_cov, list(observations), prior.base, prior.nominal_kwh)


def capacity(post: Posterior, n: int = 4000, seed: int = 0) -> dict:
    q = post.usable_kwh_quantiles((0.025, 0.1, 0.5, 0.9, 0.975), n=n, seed=seed)
    return {"usable_kwh_p50": round(float(q[2]), 2), "ci80_kwh": [round(float(q[1]), 2), round(float(q[3]), 2)],
            "ci95_kwh": [round(float(q[0]), 2), round(float(q[4]), 2)],
            "share_of_nominal_p50": round(float(q[2]) / post.nominal_kwh, 3)}


def real_world_range(post: Posterior, prior: VehiclePrior, ref: ReferenceRoute, cfg: ForecastConfig,
                     seed: int = 0) -> dict:
    mass = total_mass_kg(prior.base.curb_mass_kg, ref.passengers, 0.0)
    runs = simulate_runs(post, ref.route, mass_kg=mass, ac_on=ref.ac_on, rng=np.random.default_rng(seed), cfg=cfg)
    km = ref.route.distance_km
    range_km = 100.0 * km / np.maximum(runs.total_used, 1e-9)
    wh_km = 1000.0 * np.asarray(runs.energy_kwh) / km
    r = np.quantile(range_km, [0.1, 0.5, 0.9])
    w = np.quantile(wh_km, [0.1, 0.5, 0.9])
    return {"route": ref.key, "label": ref.label, "route_km": round(km, 1), "source": ref.source,
            "passengers": ref.passengers, "ac_on": ref.ac_on,
            "range_km": {"p10": int(round(r[0])), "p50": int(round(r[1])), "p90": int(round(r[2]))},
            "wh_per_km": {"p10": int(round(w[0])), "p50": int(round(w[1])), "p90": int(round(w[2]))}}


def certificate(vehicle: dict, prior: VehiclePrior, observations: Sequence[TripObservation],
                refs: Sequence[ReferenceRoute], *, label: str, cfg: ForecastConfig = ForecastConfig(),
                seed: int = 0, validation: dict | None = None) -> dict:
    """The passport for one vehicle. ``label`` says what the trips are (REAL ... or SIMULATED ...)."""
    post = fit(prior, observations)
    k = len(observations)
    return {
        "schema": SCHEMA, "label": label, "is_simulated": label.upper().startswith("SIMULATED"),
        "status": "PRELIMINARY" if k < MIN_TRIPS else "OK",
        "vehicle": vehicle, "prior_class": prior.key, "nominal_kwh": prior.nominal_kwh,
        "evidence": {"trips": k, "km_logged": round(float(sum(o.distance_km for o in observations)), 1),
                     "soc_points_observed": round(float(sum(o.delta_soc_pct for o in observations)), 1),
                     # DATA, not model: km driven per 100 SoC points on the logged trips themselves
                     "observed_km_per_100_soc_points": round(100.0 * float(sum(o.distance_km for o in observations))
                                                             / float(sum(o.delta_soc_pct for o in observations)), 1)
                     if k and sum(o.delta_soc_pct for o in observations) > 0 else None,
                     "temp_range_c": [round(float(min(o.temp_c for o in observations)), 1),
                                      round(float(max(o.temp_c for o in observations)), 1)] if k else None},
        "capacity": capacity(post, seed=seed),
        "range_in_indian_conditions": [real_world_range(post, prior, r, cfg, seed) for r in refs],
        "method": ("Bayesian calibration of the physics energy model on the logged trips' SoC drops (Laplace "
                   "posterior), then the Monte Carlo on fixed reference routes. Usable capacity = kWh per 100 "
                   "SoC points as the vehicle's own gauge counts them."),
        "validation": validation,
    }


def markdown(cert: dict) -> str:
    cap = cert["capacity"]
    ev = cert["evidence"]
    name = cert["vehicle"].get("label", cert["vehicle"].get("id", "vehicle"))
    lines = [f"# Range Passport - {name}", "",
             f"**{cert['label']}** - status: **{cert['status']}** (schema `{cert['schema']}`)", "",
             f"Evidence: {ev['trips']} logged trips, {ev['km_logged']} km, {ev['soc_points_observed']} SoC points used "
             f"(observed on those trips: {ev['observed_km_per_100_soc_points']} km per 100 SoC points; data, not model).",
             "", "## Usable battery", "",
             f"- Usable capacity: **{cap['usable_kwh_p50']} kWh** (80 % credible interval {cap['ci80_kwh'][0]}-"
             f"{cap['ci80_kwh'][1]}; 95 % {cap['ci95_kwh'][0]}-{cap['ci95_kwh'][1]})",
             f"- That is {cap['share_of_nominal_p50']:.0%} of the {cert['nominal_kwh']} kWh nominal pack.", "",
             "## Real-world range in Indian conditions", "",
             "| Reference route | Range 100->0 % (p10 / p50 / p90) | Wh/km (p50) | Conditions |", "|---|---|---|---|"]
    for r in cert["range_in_indian_conditions"]:
        rk = r["range_km"]
        lines.append(f"| {r['label']} ({r['route_km']} km) | {rk['p10']} / **{rk['p50']}** / {rk['p90']} km | "
                     f"{r['wh_per_km']['p50']} | AC {'on' if r['ac_on'] else 'off'}, {r['passengers']} people |")
    lines += ["", "## Method", "", cert["method"], ""]
    if cert.get("validation"):
        v = cert["validation"]
        lines += ["## Validation", "", f"**{v['label']}**", "", v["summary"], ""]
    lines += ["Intervals come from the model; the reference routes and their sources are listed in the JSON. "
              "Not a warranty."]
    return "\n".join(lines) + "\n"
