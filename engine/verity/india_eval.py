"""F4 E1-India: predict held-out public Indian trip reports through the normal planner path.

Pure functions (no network): the enriched records come from data/processed/trips_india_enriched.json.
Observable per record type: soc_trip -> SoC points used; consumption -> Wh/km; range_test -> km driven
between the reported start and end SoC. Unknown context (AC, passengers, speed band, temperature) is
integrated over as Monte Carlo scenarios rather than guessed, so thin reports get wider bands.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import numpy as np

from verity.calibrate import DEFAULT_RHO_REL, SIGMA_Q2, Posterior, calibrate
from verity.corpus import Row
from verity.forecast import FREE_SPEED_MPS, ForecastConfig, ForecastRuns, PlannedRoute, simulate_runs
from verity.physics import Segments, total_mass_kg
from verity.trips import TripObservation
from verity.vehicles import get_prior

Array = np.ndarray

# --- split (fixed rule, assignments frozen in data/splits/india_trips_split.json) ---
SPLIT_SALT = "verity-india-e1-2026-11-12"
TEST_FRACTION = 0.30
SPLIT_SCHEMA = "verity.india_split.v1"

# --- assumptions (named, documented in docs/DECISIONS.md; fixed before any held-out data exists) ---
SELF_REPORT_SD_PP = 1.0  # extra self-report error on a SoC difference beyond integer rounding (approximate - verify)
WHKM_REPORT_REL_SD = 0.03  # relative error of a reported Wh/km figure (approximate - verify)
UNKNOWN_TEMP_C = (15.0, 40.0)  # plausible Indian daytime ambient range when nothing is known (approximate - verify)
UNKNOWN_PASSENGERS = (1, 3)  # inclusive range of occupants when not reported (approximate - verify)
CALIBRATION_TEMP_C = 27.5  # point temperature for calibration when unknown: middle of UNKNOWN_TEMP_C
HIGHWAY_80_100_CAP_MPS = 25.0  # 90 km/h cruise for the highway_80_100 band
FLAT_RULE_FACTOR = 0.75  # "expect 25 % less than claimed" rule-of-thumb baseline
RANGE_ROUTE_FACTOR = 1.6  # flat route length for range tests, as a multiple of the claimed range
DEFAULT_RANGE_ROUTE_KM = 800.0
CONSUMPTION_ROUTE_KM = 50.0
N_SCENARIOS = 8
MIN_CALIBRATION_RECORDS = 3
LEVELS = (0.50, 0.80, 0.90)
BANDS = ("city", "mixed", "highway_80_100", "highway_100_plus")
UNITS = {"soc_trip": "SoC points used", "consumption": "Wh/km", "range_test": "km"}


# ---------------------------------------------------------------- split
def source_group(url: str) -> str:
    """One group per source page: host without www + path, lower case, no query, fragment or trailing slash."""
    parts = urlsplit(url.strip().lower())
    host = parts.netloc[4:] if parts.netloc.startswith("www.") else parts.netloc
    return f"{host}{parts.path.rstrip('/')}"


def assign_group(group: str) -> str:
    digest = hashlib.sha256(f"{SPLIT_SALT}|{group}".encode("utf-8")).hexdigest()
    return "test" if int(digest[:8], 16) / 2**32 < TEST_FRACTION else "train"


def update_split(path: Path, groups: set[str]) -> dict:
    """Add unseen groups by the fixed rule; existing assignments are never changed."""
    if path.exists():
        doc = json.loads(path.read_text(encoding="utf-8"))
    else:
        doc = {"schema": SPLIT_SCHEMA, "salt": SPLIT_SALT, "test_fraction": TEST_FRACTION,
               "rule": "test iff sha256(salt|group)[:8] / 2^32 < test_fraction; group = source page",
               "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "assignments": {}}
    if doc["salt"] != SPLIT_SALT or doc["test_fraction"] != TEST_FRACTION:
        raise ValueError("split rule in the file differs from the code; the split is fixed and must not change")
    assignments = dict(doc["assignments"])
    new = {g: assign_group(g) for g in sorted(groups) if g not in assignments}
    if new or not path.exists():
        doc = {**doc, "assignments": {**assignments, **new}}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc, indent=1, sort_keys=True), encoding="utf-8")
    return doc


# ---------------------------------------------------------------- cases
@dataclass(frozen=True)
class Case:
    record_id: str
    record_type: str
    group: str
    vehicle_id: str
    observed: float  # in UNITS[record_type]
    start_soc: float | None
    end_soc: float | None
    distance_km: float | None
    nominal_kwh: float
    curb_mass_kg: float
    claimed_range_km: float | None
    prior_key: str
    speed_band: str | None
    ac: bool | None
    passengers: int | None
    temp_c: float | None
    segments: dict | None
    distance_flag: bool

    @property
    def delta_soc(self) -> float | None:
        return None if self.start_soc is None or self.end_soc is None else self.start_soc - self.end_soc


def _reported(record: dict, name: str) -> object:
    return record["reported"].get(name, {}).get("value")


def _observed(record_type: str, record: dict) -> float | None:
    if record_type == "soc_trip":
        start, end = _reported(record, "start_soc_pct"), _reported(record, "end_soc_pct")
        return None if start is None or end is None else float(start) - float(end)
    if record_type == "consumption":
        return _reported(record, "energy_wh_per_km")
    if _reported(record, "start_soc_pct") is None or _reported(record, "end_soc_pct") is None:
        return None  # a range test needs both SoC ends
    return _reported(record, "distance_km")


def _curb_mass(vehicle: Row, prior_key: str) -> float:
    lo, hi = vehicle.get("curb_mass_kg_low", ""), vehicle.get("curb_mass_kg_high", "")
    if lo and hi:
        return 0.5 * (float(lo) + float(hi))
    return float(get_prior(prior_key).base.curb_mass_kg)


def _optional_float(v: object) -> float | None:
    return None if v is None else float(v)


def case_from_record(record: dict, vehicles: dict[str, Row]) -> tuple[Case | None, str]:
    """(case, "") or (None, reason the record cannot be evaluated)."""
    vehicle_id = record["vehicle_id"]["value"]
    if vehicle_id is None or vehicle_id not in vehicles:
        return None, "no unique vehicle spec"
    observed = _observed(record["record_type"], record)
    if observed is None:
        return None, "observation incomplete"
    vehicle = vehicles[vehicle_id]
    weather = (record.get("weather") or {}).get("value") or {}
    band, passengers = _reported(record, "speed_band"), _reported(record, "passengers")
    case = Case(
        record_id=record["record_id"], record_type=record["record_type"], group=source_group(record["source_url"]),
        vehicle_id=vehicle_id, observed=float(observed),
        start_soc=_optional_float(_reported(record, "start_soc_pct")),
        end_soc=_optional_float(_reported(record, "end_soc_pct")),
        distance_km=_optional_float(_reported(record, "distance_km")),
        nominal_kwh=float(vehicle["battery_nominal_kwh"]),
        curb_mass_kg=_curb_mass(vehicle, vehicle["engine_prior_class"]),
        claimed_range_km=_optional_float(vehicle.get("claimed_range_km") or None),
        prior_key=vehicle["engine_prior_class"], speed_band=band if band in BANDS else None,
        ac={"on": True, "off": False}.get(_reported(record, "ac")),
        passengers=None if passengers is None else int(passengers),
        temp_c=weather.get("temperature_c"), segments=(record.get("segments") or {}).get("value"),
        distance_flag=bool((record.get("distance_check") or {}).get("flag")))
    return case, ""


# ---------------------------------------------------------------- planner path
def planned_route(case: Case, band: str | None, temp_c: float) -> tuple[PlannedRoute, float | None]:
    """The case's routed 1 km segments (scaled to the reported distance), or a flat route for unrouted reports."""
    cap = HIGHWAY_80_100_CAP_MPS if band == "highway_80_100" else None
    if case.segments:
        km_end = np.asarray(case.segments["km_end"], dtype=float)
        length = np.diff(np.concatenate([[0.0], km_end])) * 1000.0
        if case.distance_km:
            length = length * (case.distance_km * 1000.0 / length.sum())
        temps = np.array([temp_c if t is None else t for t in case.segments["temp_c"]], dtype=float)
        route = PlannedRoute("car", length, np.asarray(case.segments["road_class"], dtype=int),
                             np.arctan(np.asarray(case.segments["grade_pct"], dtype=float) / 100.0), temps,
                             np.asarray(case.segments["headwind_mps"], dtype=float))
        return route, cap
    if case.record_type == "range_test":
        km = RANGE_ROUTE_FACTOR * case.claimed_range_km if case.claimed_range_km else DEFAULT_RANGE_ROUTE_KM
    else:
        km = case.distance_km or CONSUMPTION_ROUTE_KM
    n = max(1, math.ceil(km))
    length = np.full(n, km * 1000.0 / n)
    road_class = {"city": np.full(n, 2), "mixed": np.arange(n) % 2 + 1}.get(band, np.zeros(n, dtype=int))
    return PlannedRoute("car", length, road_class.astype(int), np.zeros(n), np.full(n, temp_c), np.zeros(n)), cap


def _scenario(case: Case, rng: np.random.Generator) -> tuple[str | None, bool, int, float]:
    band = case.speed_band if case.speed_band or case.segments else str(rng.choice(BANDS))
    ac = case.ac if case.ac is not None else bool(rng.random() < 0.5)
    pax = case.passengers if case.passengers is not None else int(
        rng.integers(UNKNOWN_PASSENGERS[0], UNKNOWN_PASSENGERS[1] + 1))
    temp = case.temp_c if case.temp_c is not None else float(rng.uniform(*UNKNOWN_TEMP_C))
    return band, ac, pax, temp


def _report_noise_sd() -> float:
    return math.sqrt(SIGMA_Q2 + SELF_REPORT_SD_PP**2)


def _observable(case: Case, runs: ForecastRuns, rng: np.random.Generator) -> Array:
    n = runs.n_runs
    if case.record_type == "soc_trip":
        return runs.total_used + rng.normal(0.0, _report_noise_sd(), n)
    if case.record_type == "consumption":
        wh_per_km = runs.energy_kwh * 1000.0 / runs.km_end[-1]
        return wh_per_km * (1.0 + rng.normal(0.0, WHKM_REPORT_REL_SD, n))
    target = case.delta_soc + rng.normal(0.0, _report_noise_sd(), n)
    cum = np.maximum.accumulate(runs.soc_used_cum, axis=1)
    # a run that never reaches the target is censored at the route end
    return np.array([np.interp(t, c, runs.km_end) for t, c in zip(target, cum)])


def predictive_samples(case: Case, post: Posterior, rng: np.random.Generator, n_runs: int = 2000) -> Array:
    """Posterior-predictive samples of the reported quantity, unknown context integrated as scenarios."""
    per = max(1, n_runs // N_SCENARIOS)
    out = []
    for _ in range(N_SCENARIOS):
        band, ac, pax, temp = _scenario(case, rng)
        route, cap = planned_route(case, band, temp)
        runs = simulate_runs(post, route, mass_kg=total_mass_kg(case.curb_mass_kg, pax, 0.0), ac_on=ac, rng=rng,
                             cfg=ForecastConfig(n_runs=per), highway_speed_mps=cap)
        out.append(_observable(case, runs, rng))
    return np.concatenate(out)


def prior_posterior(case: Case) -> Posterior:
    prior = get_prior(case.prior_key)
    return Posterior(prior.u_mean.copy(), prior.u_cov, 0, DEFAULT_RHO_REL, prior.base, case.nominal_kwh)


def _observation(case: Case) -> TripObservation:
    """A training report as a calibration observation (point values where context is unknown)."""
    temp = case.temp_c if case.temp_c is not None else CALIBRATION_TEMP_C
    route, cap = planned_route(case, case.speed_band, temp)
    if case.record_type == "range_test":
        keep = np.cumsum(route.length_m) <= case.observed * 1000.0 + 1e-6
        keep[0] = True
        route = PlannedRoute("car", route.length_m[keep], route.road_class[keep], route.grade_rad[keep],
                             route.temp_c[keep], route.headwind_mps[keep])
    v = np.asarray(FREE_SPEED_MPS["car"], dtype=float)[route.road_class]
    v = np.minimum(v, cap) if cap else v
    seg = Segments(route.length_m, v, 0.0, route.grade_rad, route.length_m / v, route.temp_c, 1.0, route.headwind_mps)
    pax = case.passengers if case.passengers is not None else 1
    duration = float(np.sum(route.length_m / v))
    return TripObservation(case.record_id, seg, total_mass_kg(case.curb_mass_kg, pax, 0.0), bool(case.ac),
                           np.array([0.0, duration]), np.array([case.start_soc, case.end_soc]), np.ones(route.n),
                           route.distance_km, duration, 0.0, pax, 0.0, temp)


def calibrated_posterior(cases: list[Case]) -> Posterior | None:
    """Bayesian calibration on one vehicle's training SoC reports; None below MIN_CALIBRATION_RECORDS."""
    usable = [c for c in cases if c.delta_soc is not None and c.record_type in ("soc_trip", "range_test")]
    if len(usable) < MIN_CALIBRATION_RECORDS:
        return None
    prior = get_prior(usable[0].prior_key)
    return calibrate(prior.u_mean, prior.u_cov, [_observation(c) for c in usable], prior.base, usable[0].nominal_kwh)


# ---------------------------------------------------------------- baselines
def claimed_point(case: Case, factor: float) -> float | None:
    """Claimed range scaled linearly (factor 1) or the flat minus-25 % rule (factor 0.75)."""
    if not case.claimed_range_km:
        return None
    effective = case.claimed_range_km * factor
    if case.record_type == "soc_trip":
        return 100.0 * case.distance_km / effective if case.distance_km else None
    if case.record_type == "consumption":
        return case.nominal_kwh * 1000.0 / effective
    return effective * case.delta_soc / 100.0 if case.delta_soc is not None else None


def _wh_per_km(case: Case) -> float | None:
    if case.record_type == "consumption":
        return case.observed
    km = case.distance_km if case.record_type == "soc_trip" else case.observed
    if case.delta_soc is None or not km:
        return None
    return case.delta_soc / 100.0 * case.nominal_kwh * 1000.0 / km


def _features(case: Case) -> Array:
    highway = 1.0 if (case.speed_band or "").startswith("highway") else 0.0
    ac = 0.5 if case.ac is None else float(case.ac)
    pax = float(np.mean(UNKNOWN_PASSENGERS)) if case.passengers is None else float(case.passengers)
    return np.array([1.0, highway, ac, pax, case.nominal_kwh])


N_FEATURES = 5


def fit_regression(train: list[Case]) -> tuple[Array, float] | None:
    """OLS of Wh/km on trip features; None when there are too few training records to fit honestly."""
    pairs = [(_features(c), _wh_per_km(c)) for c in train]
    pairs = [(x, y) for x, y in pairs if y is not None]
    if len(pairs) < N_FEATURES + 2:
        return None
    x = np.vstack([p[0] for p in pairs])
    y = np.array([p[1] for p in pairs])
    beta, *_ = np.linalg.lstsq(x, y, rcond=None)
    sd = float(np.sqrt(np.sum((y - x @ beta) ** 2) / (len(y) - N_FEATURES)))
    return beta, sd


def regression_samples(case: Case, model: tuple[Array, float], rng: np.random.Generator,
                       n: int = 2000) -> Array | None:
    beta, sd = model
    wh = np.maximum(float(_features(case) @ beta) + rng.normal(0.0, sd, n), 1.0)
    if case.record_type == "consumption":
        return wh
    if case.record_type == "soc_trip":
        return wh * case.distance_km / (case.nominal_kwh * 10.0) if case.distance_km else None
    return case.delta_soc * case.nominal_kwh * 10.0 / wh if case.delta_soc is not None else None


# ---------------------------------------------------------------- metrics
def wilson(k: int, n: int) -> list[float] | None:
    """Wilson 95 % interval for a proportion, over unique records."""
    if n <= 0:
        return None
    z2 = 1.96**2
    centre = (k / n + z2 / (2 * n)) / (1 + z2 / n)
    half = 1.96 * math.sqrt((k / n) * (1 - k / n) / n + z2 / (4 * n**2)) / (1 + z2 / n)
    return [round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4)]


def score(cases: list[Case], predictions: dict[str, Array | float | None]) -> dict:
    """MAE per record type (point = predictive median) and pooled band coverage with Wilson intervals."""
    errors: dict[str, list[float]] = {}
    hits = {level: 0 for level in LEVELS}
    n_band = 0
    for case in cases:
        pred = predictions.get(case.record_id)
        if pred is None:
            continue
        samples = np.atleast_1d(np.asarray(pred, dtype=float))
        errors.setdefault(case.record_type, []).append(abs(float(np.median(samples)) - case.observed))
        if samples.size > 1:
            n_band += 1
            for level in LEVELS:
                lo, hi = np.quantile(samples, [(1 - level) / 2, (1 + level) / 2])
                hits[level] += int(lo <= case.observed <= hi)
    mae = {t: {"n": len(e), "mae": round(float(np.mean(e)), 3), "unit": UNITS[t]} for t, e in errors.items()}
    if not n_band:
        return {"mae_by_type": mae, "coverage": None}
    coverage = {f"{int(level * 100)}%": {"k": hits[level], "n": n_band,
                                          "coverage": round(hits[level] / n_band, 4),
                                          "wilson95": wilson(hits[level], n_band)} for level in LEVELS}
    return {"mae_by_type": mae, "coverage": coverage}
