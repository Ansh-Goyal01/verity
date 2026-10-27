"""Convert a logged trip plus planner-visible context into physics inputs.

The planner only ever sees what a phone logger and a lookup service provide: GPS points, whole-percent
SoC readings, an elevation profile and a weather forecast. Hidden simulator truth never enters here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .paths import SIM_DIR
from .physics import Segments, total_mass_kg
from .schemas import TripLog
from .vehicles import VehiclePrior, classify

EARTH_R_M = 6_371_000.0
MAX_ABS_GRADE = 0.20  # clip absurd grades from noisy elevation (11 degrees)
MIN_MOVE_M = 5.0  # below this a segment is treated as standing still (no grade / bearing)
STATIONARY_MPS = 0.5  # reported speed below which GPS displacement is treated as jitter


def haversine_m(lat1: np.ndarray, lon1: np.ndarray, lat2: np.ndarray, lon2: np.ndarray) -> np.ndarray:
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dphi, dlmb = p2 - p1, np.radians(lon2 - lon1)
    a = np.sin(dphi / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dlmb / 2) ** 2
    return 2 * EARTH_R_M * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))


def bearing_deg(lat1: np.ndarray, lon1: np.ndarray, lat2: np.ndarray, lon2: np.ndarray) -> np.ndarray:
    p1, p2, dl = np.radians(lat1), np.radians(lat2), np.radians(lon2 - lon1)
    y = np.sin(dl) * np.cos(p2)
    x = np.cos(p1) * np.sin(p2) - np.sin(p1) * np.cos(p2) * np.cos(dl)
    return (np.degrees(np.arctan2(y, x)) + 360.0) % 360.0


@dataclass(frozen=True, eq=False)
class TripObservation:
    """One trip as the planner sees it, ready for the energy model and for calibration."""

    trip_id: str
    segments: Segments
    mass_kg: float
    ac_on: bool
    soc_t_s: np.ndarray  # seconds after the first GPS point
    soc_pct: np.ndarray
    window_weights: np.ndarray  # (S,) fraction of each segment inside [first, last] SoC reading
    distance_km: float
    duration_s: float
    ascent_m: float
    passengers: int
    cargo_kg: float
    temp_c: float

    @property
    def delta_soc_pct(self) -> float:
        """Observed SoC drop between the first and last reading, in percentage points."""
        return float(self.soc_pct[0] - self.soc_pct[-1])


def _smooth3(z: np.ndarray) -> np.ndarray:
    if len(z) < 3:
        return z
    padded = np.concatenate([[z[0]], z, [z[-1]]])
    return (padded[:-2] + padded[1:-1] + padded[2:]) / 3.0


def build_observation(trip: TripLog, context: dict, prior: VehiclePrior | None = None) -> TripObservation:
    """Build physics segments (one per GPS interval) from a trip log and its lookup context."""
    prior = prior or classify(trip.vehicle.type, trip.vehicle.nominal_kwh)
    pts = sorted(trip.points, key=lambda p: p.t)
    t_all = np.array([(p.t - pts[0].t).total_seconds() for p in pts])
    keep = np.concatenate([[True], np.diff(t_all) > 0])  # drop duplicate timestamps
    pts = [p for p, k in zip(pts, keep) if k]
    t = t_all[keep]
    lat = np.array([p.lat for p in pts])
    lon = np.array([p.lon for p in pts])
    d = haversine_m(lat[:-1], lon[:-1], lat[1:], lon[1:])
    dt = np.diff(t)

    reported = np.array([np.nan if p.speed_mps is None else p.speed_mps for p in pts])
    if np.isfinite(reported).any():
        good = np.isfinite(reported)
        point_v = np.interp(t, t[good], reported[good])
        # GPS jitter adds phantom distance while parked; trust the Doppler speed there instead.
        d = np.where(0.5 * (point_v[:-1] + point_v[1:]) < STATIONARY_MPS, 0.0, d)
    else:
        mid = np.concatenate([[d[0] / dt[0]], d / dt, [d[-1] / dt[-1]]])
        point_v = 0.5 * (mid[:-1] + mid[1:])
    v_seg = d / dt
    accel = np.diff(point_v) / dt

    s_cum = np.concatenate([[0.0], np.cumsum(d)])
    elev = context["elevation"]
    grid = np.arange(len(elev["z_m"])) * float(elev["step_m"])
    z = _smooth3(np.interp(s_cum, grid, np.asarray(elev["z_m"], dtype=float)))
    moving = d >= MIN_MOVE_M
    grade = np.where(moving, np.clip(np.diff(z) / np.maximum(d, 1.0), -MAX_ABS_GRADE, MAX_ABS_GRADE), 0.0)

    wx = context["weather"]
    brg = bearing_deg(lat[:-1], lon[:-1], lat[1:], lon[1:])
    headwind = np.where(moving, wx["wind_speed_mps"] * np.cos(np.radians(wx["wind_from_deg"] - brg)), 0.0)

    seg = Segments.build(
        d, v_seg, accel_mps2=accel, grade_rad=np.arctan(grade), temp_c=wx["temperature_c"],
        surface=1.0, headwind_mps=headwind, dt_s=dt,
    )
    reads = sorted(trip.soc, key=lambda r: r.t)
    soc_t = np.array([(r.t - pts[0].t).total_seconds() for r in reads])
    soc_pct = np.array([r.pct for r in reads], dtype=float)
    seg_start, seg_end = t[:-1], t[1:]
    overlap = np.clip(np.minimum(seg_end, soc_t[-1]) - np.maximum(seg_start, soc_t[0]), 0.0, None)
    weights = overlap / dt
    ascent = float(np.clip(np.diff(z), 0.0, None).sum())
    return TripObservation(
        trip_id=trip.trip_id, segments=seg,
        mass_kg=total_mass_kg(prior.base.curb_mass_kg, trip.passengers, trip.cargo_kg),
        ac_on=trip.ac_on, soc_t_s=soc_t, soc_pct=soc_pct, window_weights=weights,
        distance_km=float(d.sum() / 1000.0), duration_s=float(t[-1]), ascent_m=ascent,
        passengers=trip.passengers, cargo_kg=trip.cargo_kg, temp_c=float(wx["temperature_c"]),
    )


def load_sim_observation(name: str, trip_id: str, root: Path = SIM_DIR) -> TripObservation:
    """Planner view of one simulated trip (trip log + context). Never reads the hidden truth."""
    base = Path(root) / name
    trip = TripLog.model_validate(json.loads((base / "trips" / f"{trip_id}.json").read_text(encoding="utf-8")))
    context = json.loads((base / "context" / f"{trip_id}.json").read_text(encoding="utf-8"))
    return build_observation(trip, context)


__all__ = ["TripObservation", "build_observation", "load_sim_observation", "haversine_m", "bearing_deg"]
