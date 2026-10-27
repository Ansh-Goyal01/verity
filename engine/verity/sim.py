"""Truth simulator: trips that are deliberately RICHER than the planning model.

Everything produced here is SIMULATED and labelled ``"is_simulated": true``. The simulator integrates
its own physics at 1 s resolution with effects the planner (``physics.py``) does not have:

* wind gusts (AR(1) noise around a trip-mean wind) and per-trip wind the forecast only roughly knows
* driver variability (per-trip aggressiveness changes cruise speed, acceleration limits, speed wobble)
* battery ageing (state of health, slow capacity fade across trips, I^2R loss growing with age)
* stop-and-go traffic blocks, rough-surface blocks, arterial / highway regimes
* AC duty cycling (compressor on/off) instead of a smooth linear load
* payload variation (occupants' real masses and cargo differ from what the log says)
* power-dependent drive efficiency (worse at low power), unlike the planner's constant eta

Three kinds of files are written per dataset (see DECISIONS.md): ``trips/`` (what a phone logger would
export, ``verity.trip.v1``), ``context/`` (elevation profile + weather forecast a planner could look
up) and ``truth/`` (hidden ground truth, read only by scoring code and tests). Seeded: the same seed
reproduces byte-identical files.
"""

from __future__ import annotations

import argparse
import json
import math
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
from scipy.signal import lfilter

from .paths import SIM_DIR
from .physics import G, J_PER_KWH, PASSENGER_MASS_KG, T_COMFORT_C, air_density
from .schemas import TRIP_SCHEMA_V1, TripLog
from .vehicles import VEHICLE_CLASSES, VehiclePrior, get_prior

IST = timezone(timedelta(hours=5, minutes=30))
EARTH_R_M = 6_371_000.0
ORIGIN_LATLON = (28.5355, 77.3910)  # Noida, as in docs/API_CONTRACT.md
SIM_EPOCH = datetime(2026, 9, 1, 6, 0, tzinfo=IST)
STEP_S = 1.0  # truth integration step
GPS_INTERVAL_S = 10  # what the phone logger records
SOC_REMINDER_S = 600  # a battery reading roughly every 10 minutes (logger spec)
NAMESPACE = uuid.UUID("5d7c1a26-2f8e-4b0a-9c55-6f1b3a9e0d11")  # fixed, so trip ids are reproducible

# (highway cruise range, arterial cruise range, congested peak range) in m/s per vehicle type.
# Simulation settings, not claims about real traffic.
SPEED_TABLE = {
    "car": ((21.0, 31.0), (11.0, 17.0), (8.0, 15.0)),
    "2w": ((9.0, 13.0), (6.0, 10.0), (4.0, 8.0)),
}
ACC_LIMITS = {"car": (1.3, 2.0), "2w": (0.9, 1.6)}  # (accelerate, brake) m/s^2 before driver scaling


@dataclass(frozen=True)
class VehicleTruth:
    """Hidden ground truth of one simulated vehicle."""

    vehicle_id: str
    class_key: str
    nominal_kwh: float
    curb_mass_kg: float
    cda_m2: float
    crr: float
    eta_peak: float
    eta_regen: float
    max_regen_kw: float
    base_w: float
    aux_w_per_k: float
    soh: float  # battery state of health
    usable_kwh_true: float  # at trip index 0
    rot_delta: float
    p_ref_kw: float  # power scale for the I^2R-like loss
    loss_beta: float  # I^2R loss coefficient, grows with age
    eta_knee_kw: float  # drive efficiency falls off below this power
    driver_g_mean: float  # driver aggressiveness (1 = average)
    driver_g_sd: float


@dataclass(frozen=True)
class TripSpec:
    """Everything that defines one simulated trip before randomness in the dynamics."""

    distance_km: float
    bearing_deg: float
    origin_lat: float
    origin_lon: float
    passengers: int
    cargo_logged_kg: float
    ac_on: bool
    temp_c: float
    wind_mean_mps: float
    wind_from_deg: float
    terrain_amp_m: float
    highway_frac: float
    congested_frac: float
    start_soc_pct: float
    driver_g: float
    started_at: datetime
    trip_index: int  # index of the trip within its vehicle's life (drives capacity fade)


@dataclass
class TripRun:
    """Result of one truth simulation (arrays are per 1 s step)."""

    t_s: np.ndarray
    s_m: np.ndarray
    v_mps: np.ndarray
    soc_true_pct: np.ndarray
    distance_km: float
    duration_s: float
    energy_terms_soc_pct: dict[str, float]
    mass_true_kg: float
    capacity_kwh: float
    z_fn_params: tuple
    gust_rms_mps: float
    mean_temp_c: float


# --- sampling ---------------------------------------------------------------------------------


def sample_vehicle_truth(
    rng: np.random.Generator, vehicle_id: str, prior: VehiclePrior, *, soh: float | None = None
) -> VehicleTruth:
    b = prior.base
    health = float(rng.uniform(0.82, 1.0)) if soh is None else float(soh)
    usable_frac0 = float(np.clip(rng.normal(0.93, 0.03), 0.8, 1.0))
    p_ref = 1.3 * prior.nominal_kwh
    return VehicleTruth(
        vehicle_id=vehicle_id,
        class_key=prior.key,
        nominal_kwh=prior.nominal_kwh,
        curb_mass_kg=float(b.curb_mass_kg * rng.normal(1.0, 0.02)),
        cda_m2=float(b.cda_m2 * math.exp(rng.normal(0, 0.10))),
        crr=float(b.crr * math.exp(rng.normal(0, 0.18))),
        eta_peak=float(np.clip(rng.normal(b.eta_drive + 0.025, 0.015), 0.78, 0.97)),
        eta_regen=float(np.clip(rng.normal(b.eta_regen, 0.05), 0.25, 0.85)),
        max_regen_kw=float(b.max_regen_kw),
        base_w=float(b.base_w * math.exp(rng.normal(0, 0.10))),
        aux_w_per_k=float(b.aux_w_per_k * math.exp(rng.normal(0, 0.30))),
        soh=health,
        usable_kwh_true=float(prior.nominal_kwh * usable_frac0 * health),
        rot_delta=float(0.05 * rng.normal(1.0, 0.10)),
        p_ref_kw=float(p_ref),
        loss_beta=float(0.04 * (1.0 + 3.0 * (1.0 - health))),
        eta_knee_kw=float(0.06 * p_ref),
        driver_g_mean=float(math.exp(rng.normal(0, 0.12))),
        driver_g_sd=0.08,
    )


def sample_trip_spec(
    rng: np.random.Generator,
    vt: VehicleTruth,
    trip_index: int,
    *,
    distance_range_km: tuple[float, float] | None = None,
    start_soc_pct: float | None = None,
) -> TripSpec:
    vtype = VEHICLE_CLASSES[vt.class_key].vtype
    lo, hi = distance_range_km or ((20.0, 110.0) if vtype == "car" else (6.0, 35.0))
    hw = float(rng.uniform(0.1, 0.8)) if vtype == "car" else float(rng.uniform(0.0, 0.3))
    cf = float(rng.uniform(0.0, min(0.5, 1.0 - hw)))
    passengers = int(rng.integers(1, 5)) if vtype == "car" else 1
    return TripSpec(
        distance_km=float(rng.uniform(lo, hi)),
        bearing_deg=float(rng.uniform(0, 360)),
        origin_lat=float(ORIGIN_LATLON[0] + rng.uniform(-0.15, 0.15)),
        origin_lon=float(ORIGIN_LATLON[1] + rng.uniform(-0.15, 0.15)),
        passengers=passengers,
        cargo_logged_kg=float(rng.choice([0.0, 0.0, 10.0, 25.0, 40.0])),
        ac_on=bool(rng.random() < 0.7) if vtype == "car" else False,
        temp_c=float(rng.uniform(16.0, 41.0)),
        wind_mean_mps=float(rng.uniform(0.0, 6.0)),
        wind_from_deg=float(rng.uniform(0, 360)),
        terrain_amp_m=float(rng.uniform(2.0, 40.0)),
        highway_frac=hw,
        congested_frac=cf,
        start_soc_pct=float(rng.uniform(55, 100)) if start_soc_pct is None else start_soc_pct,
        driver_g=float(np.clip(vt.driver_g_mean * math.exp(rng.normal(0, vt.driver_g_sd)), 0.7, 1.5)),
        started_at=SIM_EPOCH + timedelta(days=trip_index, hours=float(rng.uniform(0, 10))),
        trip_index=trip_index,
    )


# --- truth dynamics ---------------------------------------------------------------------------


def _terrain(s_m: np.ndarray, params: tuple) -> np.ndarray:
    """Synthetic elevation (m) along the route: two sinusoids (km-scale rolling + short ripples)."""
    a1, l1, p1, a2, l2, p2 = params
    return a1 * np.sin(2 * np.pi * s_m / l1 + p1) + a2 * np.sin(2 * np.pi * s_m / l2 + p2)


def _terrain_slope(s_m: np.ndarray, params: tuple) -> np.ndarray:
    """Exact dz/ds of ``_terrain`` (rise over run, dimensionless)."""
    a1, l1, p1, a2, l2, p2 = params
    return (a1 * 2 * np.pi / l1) * np.cos(2 * np.pi * s_m / l1 + p1) + (
        a2 * 2 * np.pi / l2
    ) * np.cos(2 * np.pi * s_m / l2 + p2)


def _sample_terrain_params(rng: np.random.Generator, amp: float) -> tuple:
    return (amp, float(rng.uniform(3000, 12000)), float(rng.uniform(0, 6.28)),
            0.3 * amp, float(rng.uniform(500, 2000)), float(rng.uniform(0, 6.28)))


def _build_blocks(rng: np.random.Generator, spec: TripSpec, vtype: str) -> dict[str, np.ndarray]:
    hwy, art, cong = SPEED_TABLE[vtype]
    dist_m = spec.distance_km * 1000.0
    ends, kinds, speeds, periods, surf = [], [], [], [], []
    pos = 0.0
    probs = [spec.highway_frac, max(0.0, 1.0 - spec.highway_frac - spec.congested_frac), spec.congested_frac]
    probs = np.array(probs) / sum(probs)
    while pos < dist_m + 1000.0:
        kind = int(rng.choice(3, p=probs))
        pos += float(rng.uniform(500, 2500) if kind == 2 else rng.uniform(1500, 8000))
        lo, hi = (hwy, art, cong)[kind]
        ends.append(pos)
        kinds.append(kind)
        speeds.append(float(rng.uniform(lo, hi)))
        periods.append(float(rng.uniform(35, 90)))
        surf.append(float(rng.choice([1.0, 1.12, 1.30], p=[0.7, 0.2, 0.1])))
    return {k: np.array(v) for k, v in
            {"end": ends, "kind": kinds, "speed": speeds, "period": periods, "surface": surf}.items()}


def _kinematics(rng: np.random.Generator, spec: TripSpec, vtype: str, blocks: dict[str, np.ndarray]):
    """Time-stepped speed profile. Returns per-step arrays t, s, v, a and the block index."""
    dist_m = spec.distance_km * 1000.0
    acc, dec = ACC_LIMITS[vtype]
    a_acc, a_dec, tau = acc * spec.driver_g, dec * spec.driver_g, 5.0 / spec.driver_g
    cruise_scale = 1.0 + 0.30 * (spec.driver_g - 1.0)
    vcap = SPEED_TABLE[vtype][0][1] * 1.08
    max_steps = int(6 * 3600 / STEP_S)
    noise = rng.normal(0.0, 0.01, size=max_steps)
    phases = rng.uniform(0, 6.28, size=len(blocks["end"]))
    ends, kinds, speeds, periods = blocks["end"], blocks["kind"], blocks["speed"], blocks["period"]

    v = s = wob = 0.0
    b = 0
    t_list, s_list, v_list, b_list = [0.0], [0.0], [0.0], [0]
    for step in range(max_steps):
        while s >= ends[b] and b < len(ends) - 1:
            b += 1
        wob = 0.97 * wob + noise[step]
        remaining = dist_m - s
        if kinds[b] == 2:
            phase = 2 * math.pi * (step * STEP_S) / periods[b] + phases[b]
            target = speeds[b] * min(1.0, max(0.0, 1.3 * math.sin(phase) + 0.45))
        else:
            target = speeds[b] * cruise_scale * (1.0 + wob)
        if remaining < 150.0:
            target = 0.0
        a = min(a_acc, max(-a_dec, (target - v) / tau))
        v_new = min(vcap, max(0.0, v + a * STEP_S))
        s += 0.5 * (v + v_new) * STEP_S
        v = v_new
        t_list.append((step + 1) * STEP_S)
        s_list.append(s)
        v_list.append(v)
        b_list.append(b)
        if remaining <= 1.0 or (remaining < 150.0 and v < 0.5):
            break
    t, sarr, varr = np.array(t_list), np.array(s_list), np.array(v_list)
    acc_arr = np.gradient(varr, STEP_S)
    return t, sarr, varr, acc_arr, np.array(b_list)


def run_trip(rng: np.random.Generator, vt: VehicleTruth, spec: TripSpec) -> TripRun:
    """Integrate one trip with the RICH truth physics and return per-step arrays."""
    vtype = VEHICLE_CLASSES[vt.class_key].vtype
    blocks = _build_blocks(rng, spec, vtype)
    t, s, v, a, bidx = _kinematics(rng, spec, vtype, blocks)
    n = len(t)

    terr = _sample_terrain_params(rng, spec.terrain_amp_m)
    grade = np.arctan(_terrain_slope(s, terr))
    surface = blocks["surface"][bidx]

    # temperature drifts slowly; wind = trip mean + AR(1) gusts (correlation time ~ 20 s)
    temp = spec.temp_c + 0.8 * np.sin(2 * np.pi * t / 3600.0) + rng.normal(0, 0.1, size=n)
    sigma_g = 0.35 * spec.wind_mean_mps + 0.4
    phi = math.exp(-STEP_S / 20.0)
    gust = lfilter([sigma_g * math.sqrt(1 - phi**2)], [1.0, -phi], rng.normal(size=n))
    wind = np.maximum(spec.wind_mean_mps + gust, 0.0)
    headwind = wind * math.cos(math.radians(spec.wind_from_deg - spec.bearing_deg))

    occupants = np.clip(rng.normal(PASSENGER_MASS_KG + 2.0, 12.0, size=spec.passengers), 40.0, None)
    mass = vt.curb_mass_kg + float(occupants.sum()) + max(0.0, spec.cargo_logged_kg + rng.normal(0, 6.0))
    capacity_kwh = vt.usable_kwh_true * (1.0 - 0.0004 * spec.trip_index)  # slow capacity fade

    rho = air_density(temp)
    f_roll = vt.crr * surface * mass * G * np.cos(grade)
    f_grade = mass * G * np.sin(grade)
    f_aero = 0.5 * rho * vt.cda_m2 * (v + headwind) * np.abs(v + headwind)
    f_inertia = mass * (1.0 + vt.rot_delta) * a
    p_roll, p_grade, p_aero, p_inertia = f_roll * v, f_grade * v, f_aero * v, f_inertia * v
    p_wheel = p_roll + p_grade + p_aero + p_inertia
    driving = p_wheel >= 0.0

    knee_w = vt.eta_knee_kw * 1000.0
    eta = vt.eta_peak * (1.0 - 0.10 * np.exp(-np.maximum(p_wheel, 0.0) / knee_w))
    p_ref_w = vt.p_ref_kw * 1000.0
    p_drive = p_wheel / eta
    p_drive = p_drive * (1.0 + vt.loss_beta * p_drive / p_ref_w)  # I^2R-like loss, worse with age
    p_regen = np.maximum(p_wheel * vt.eta_regen, -vt.max_regen_kw * 1000.0)
    p_motion = np.where(driving, p_drive, p_regen)

    delta_t = np.abs(temp - T_COMFORT_C)
    duty = float(np.clip(delta_t.mean() / 18.0, 0.35, 1.0))
    period = float(rng.uniform(90, 180))
    compressor_on = ((t + rng.uniform(0, period)) % period) < duty * period
    p_aux = (vt.aux_w_per_k * 1.15 * delta_t * compressor_on) if spec.ac_on else np.zeros(n)
    p_base = np.full(n, vt.base_w)

    e_step = np.concatenate([[0.0], (p_motion[1:] + p_aux[1:] + p_base[1:]) * STEP_S])
    e_cum = np.cumsum(e_step)
    soc = spec.start_soc_pct - 100.0 * e_cum / (capacity_kwh * J_PER_KWH)

    def soc_pts(power: np.ndarray, mask: np.ndarray | None = None) -> float:
        p = power if mask is None else np.where(mask, power, 0.0)
        return float(100.0 * (p[1:] * STEP_S).sum() / (capacity_kwh * J_PER_KWH))

    terms = {
        "motion_driving": soc_pts(p_motion, driving),
        "regen": soc_pts(p_motion, ~driving),
        "aux": soc_pts(p_aux),
        "base": soc_pts(p_base),
    }
    return TripRun(
        t_s=t, s_m=s, v_mps=v, soc_true_pct=soc, distance_km=float(s[-1] / 1000.0), duration_s=float(t[-1]),
        energy_terms_soc_pct=terms, mass_true_kg=float(mass), capacity_kwh=float(capacity_kwh),
        z_fn_params=terr, gust_rms_mps=float(np.sqrt(np.mean(gust**2))), mean_temp_c=float(temp.mean()),
    )


def end_soc_for_spec(rng_seed: tuple, vt: VehicleTruth, spec: TripSpec) -> float:
    """Truth end-of-trip SoC for a spec (used by E2 and by the start-SoC fitting below)."""
    return float(run_trip(np.random.default_rng(rng_seed), vt, spec).soc_true_pct[-1])


# --- exporting what a logger / lookup service would see -----------------------------------------


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _latlon_at(spec: TripSpec, s_m: np.ndarray, rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    b = math.radians(spec.bearing_deg)
    north = s_m * math.cos(b) + rng.normal(0, 4.0, size=s_m.shape)
    east = s_m * math.sin(b) + rng.normal(0, 4.0, size=s_m.shape)
    lat = spec.origin_lat + np.degrees(north / EARTH_R_M)
    lon = spec.origin_lon + np.degrees(east / (EARTH_R_M * math.cos(math.radians(spec.origin_lat))))
    return lat, lon


def export_trip(
    rng: np.random.Generator, vt: VehicleTruth, spec: TripSpec, run: TripRun, trip_id: str
) -> tuple[dict, dict, dict]:
    """Build (trip.v1 dict, planner context dict, hidden truth dict) from a truth run."""
    idx = np.arange(0, len(run.t_s), GPS_INTERVAL_S)
    if idx[-1] != len(run.t_s) - 1:
        idx = np.append(idx, len(run.t_s) - 1)
    lat, lon = _latlon_at(spec, run.s_m[idx], rng)
    has_speed = rng.random(len(idx)) < 0.9
    points = [
        {
            "t": _iso(spec.started_at + timedelta(seconds=float(run.t_s[i]))),
            "lat": round(float(la), 6), "lon": round(float(lo), 6),
            "acc_m": round(float(np.clip(rng.normal(5, 1.5), 3, 12)), 1),
            "speed_mps": round(float(max(0.0, run.v_mps[i] + rng.normal(0, 0.2))), 2) if hs else None,
        }
        for i, la, lo, hs in zip(idx, lat, lon, has_speed)
    ]
    read_t, tcur = [0.0], 0.0
    while tcur + SOC_REMINDER_S + 120 < run.duration_s:
        tcur += SOC_REMINDER_S + float(rng.uniform(-120, 120))
        read_t.append(tcur)
    read_t.append(run.duration_s)
    soc = [
        {"t": _iso(spec.started_at + timedelta(seconds=rt)),
         "pct": int(np.clip(round(float(np.interp(rt, run.t_s, run.soc_true_pct))), 0, 100)),
         "source": "tap"}  # v1 allows only tap|voice; trip-level is_simulated carries the label
        for rt in read_t
    ]
    vehicle = VEHICLE_CLASSES[vt.class_key]
    trip = {
        "schema": TRIP_SCHEMA_V1, "trip_id": trip_id,
        "vehicle": {"make": "Generic (simulated)", "model": vehicle.label, "variant": vt.vehicle_id,
                    "nominal_kwh": vt.nominal_kwh, "type": vehicle.vtype},
        "driver_id": f"anon-{vt.vehicle_id}", "passengers": spec.passengers, "cargo_kg": spec.cargo_logged_kg,
        "ac_on": spec.ac_on, "started_at": _iso(spec.started_at),
        "ended_at": _iso(spec.started_at + timedelta(seconds=run.duration_s)),
        "points": points, "soc": soc, "notes": "SIMULATED trip from verity.sim (not real data)",
        "is_simulated": True,
    }
    grid = np.arange(0.0, run.s_m[-1] + 300.0, 100.0)
    elevation = _terrain(grid, run.z_fn_params) + rng.normal(0, 0.8, size=grid.shape)
    context = {
        "is_simulated": True, "trip_id": trip_id,
        "elevation": {"step_m": 100.0, "z_m": [round(float(z), 1) for z in elevation]},
        "weather": {"temperature_c": round(float(spec.temp_c + rng.normal(0, 1.0)), 1),
                    "wind_speed_mps": round(float(max(0.0, spec.wind_mean_mps + rng.normal(0, 1.0))), 1),
                    "wind_from_deg": round(float((spec.wind_from_deg + rng.normal(0, 20.0)) % 360.0), 0)},
    }
    truth = {
        "is_simulated": True, "trip_id": trip_id, "vehicle": asdict(vt),
        "trip": {**{k: v for k, v in asdict(spec).items() if k != "started_at"},
                 "mass_true_kg": run.mass_true_kg, "capacity_kwh": run.capacity_kwh,
                 "soc_start_true": float(run.soc_true_pct[0]), "soc_end_true": float(run.soc_true_pct[-1]),
                 "gust_rms_mps": run.gust_rms_mps, "mean_temp_c": run.mean_temp_c,
                 "energy_terms_soc_pct": run.energy_terms_soc_pct,
                 "distance_km": run.distance_km, "duration_s": run.duration_s},
    }
    return trip, context, truth


# --- datasets ---------------------------------------------------------------------------------

DEFAULT_FLEET: tuple[tuple[str, str], ...] = tuple(
    (f"{key}-{i}", key) for key in ("small_ecar", "compact_esuv", "e_scooter") for i in (1, 2, 3)
)


def _write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, separators=(",", ":"), sort_keys=True), encoding="utf-8")


def generate_dataset(
    name: str,
    seed: int,
    out_root: Path = SIM_DIR,
    fleet: tuple[tuple[str, str], ...] | tuple[tuple[str, str, float], ...] = DEFAULT_FLEET,
    trips_per_vehicle: int = 14,
) -> dict:
    """Write a simulated dataset to ``out_root/name`` and return its manifest.

    ``fleet`` entries are ``(vehicle_id, class_key)`` or ``(vehicle_id, class_key, soh)`` to force an aged
    (or healthy) battery. Deterministic for a given ``seed``.
    """
    root = Path(out_root) / name
    vehicles, trips_meta = [], []
    for vi, entry in enumerate(fleet):
        vid, key = entry[0], entry[1]
        soh = entry[2] if len(entry) > 2 else None
        vt = sample_vehicle_truth(np.random.default_rng([seed, vi, 0]), vid, get_prior(key), soh=soh)
        ids = []
        for ti in range(trips_per_vehicle):
            rng = np.random.default_rng([seed, vi, ti + 1])
            spec = sample_trip_spec(rng, vt, ti)
            run = run_trip(rng, vt, spec)
            for _ in range(6):  # shorten until the trip fits in the battery with a safety margin
                used = float(run.soc_true_pct[0] - run.soc_true_pct[-1])
                if used <= 85.0:
                    break
                spec = TripSpec(**{**asdict(spec), "distance_km": spec.distance_km * 0.6,
                                   "started_at": spec.started_at})
                run = run_trip(rng, vt, spec)
            used = float(run.soc_true_pct[0] - run.soc_true_pct[-1])
            start = float(np.clip(used + rng.uniform(8.0, 45.0), 25.0, 100.0))
            spec = TripSpec(**{**asdict(spec), "start_soc_pct": start, "started_at": spec.started_at})
            run.soc_true_pct = run.soc_true_pct - run.soc_true_pct[0] + start
            trip_id = str(uuid.uuid5(NAMESPACE, f"{name}/{seed}/{vid}/{ti}"))
            trip, context, truth = export_trip(rng, vt, spec, run, trip_id)
            TripLog.model_validate(trip)  # the simulator must honour the data contract
            _write_json(root / "trips" / f"{trip_id}.json", trip)
            _write_json(root / "context" / f"{trip_id}.json", context)
            _write_json(root / "truth" / f"{trip_id}.json", truth)
            ids.append(trip_id)
            trips_meta.append({"trip_id": trip_id, "vehicle_id": vid, "trip_index": ti,
                               "distance_km": round(run.distance_km, 2)})
        vehicles.append({"vehicle_id": vid, "class_key": key, "nominal_kwh": vt.nominal_kwh, "trip_ids": ids})
    manifest = {"is_simulated": True, "name": name, "seed": seed, "trips_per_vehicle": trips_per_vehicle,
                "vehicles": vehicles, "trips": trips_meta}
    _write_json(root / "manifest.json", manifest)
    return manifest


def load_manifest(name: str, root: Path = SIM_DIR) -> dict:
    return json.loads((Path(root) / name / "manifest.json").read_text(encoding="utf-8"))


def load_trip_files(name: str, trip_id: str, root: Path = SIM_DIR) -> tuple[TripLog, dict]:
    """Load the logger-visible trip and the planner-visible context (NOT the hidden truth)."""
    base = Path(root) / name
    trip = TripLog.model_validate(json.loads((base / "trips" / f"{trip_id}.json").read_text(encoding="utf-8")))
    context = json.loads((base / "context" / f"{trip_id}.json").read_text(encoding="utf-8"))
    return trip, context


def load_truth(name: str, trip_id: str, root: Path = SIM_DIR) -> dict:
    """Hidden ground truth. Only scoring code and tests may call this."""
    return json.loads((Path(root) / name / "truth" / f"{trip_id}.json").read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a SIMULATED trip dataset under data/sim/")
    parser.add_argument("--name", default="main")
    parser.add_argument("--seed", type=int, default=20261027)
    parser.add_argument("--trips-per-vehicle", type=int, default=14)
    args = parser.parse_args()
    manifest = generate_dataset(args.name, args.seed, trips_per_vehicle=args.trips_per_vehicle)
    print(f"wrote {len(manifest['trips'])} SIMULATED trips for {len(manifest['vehicles'])} vehicles "
          f"to {SIM_DIR / args.name}")


if __name__ == "__main__":
    main()
