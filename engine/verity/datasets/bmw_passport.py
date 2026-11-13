"""BMW i3 trips as Range Passport input, and the ground truth they carry (F9 validation, REAL data).

* ``observation`` turns one loader trip into what a driver's logger would give the planner: speed and elevation
  traces (1 Hz), ambient temperature, and two dashboard SoC readings (first and last valid BMS SoC, rounded to whole
  percent). It never uses the measured current, voltage or heater power.
* ``measured`` integrates the measured battery voltage x current over the same SoC window: the GROUND TRUTH energy
  per SoC point the passport's capacity estimate is checked against.

German driving with heater (winter) or A/C (summer): REAL data, not Indian conditions. Only TRAINING-split trips are
used (the held-out test trips stay sacred for the final E1 evaluation). Trip selection is a fixed flag rule, applied
before any estimate is looked at.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..physics import Segments, total_mass_kg
from ..trips import TripObservation
from ..passport import model_prior
from ..vehicles import VehiclePrior
from .bmw_i3 import BmwTrip

# BMW i3 60 Ah (2013-2017), EV Database https://ev-database.org/car/1004/BMW-i3-60-Ah (read 2026-11-13):
# "Nominal Capacity 21.6 kWh", "Useable Capacity 18.8 kWh", "Weight Unladen (EU) 1270 kg". EU unladen mass includes
# a 75 kg driver, so the curb mass here is 1270 - 75 = 1195 kg and the driver is counted as the one passenger.
# CdA, rolling resistance, efficiencies and climate load stay at the small_ecar class prior (no cited i3 value fetched).
# All approximate - verify.
I3_NOMINAL_KWH = 21.6
I3_USABLE_KWH_SPEC = 18.8
I3_CURB_KG = 1270.0 - 75.0
EXCLUDE_FLAGS = ("soc_missing_tail", "soc_jump", "charging_session", "heating_spike", "soc_copy_of")
MIN_SOC_DROP_PCT = 5.0  # dashboard readings are whole percent; shorter drops are mostly rounding
DOWNSAMPLE = 10  # 0.1 s rows -> 1 s
MAX_ABS_GRADE = 0.15
SMOOTH_S = 31  # elevation is smoothed over this many seconds (GPS-like altitude noise)


def i3_prior() -> VehiclePrior:
    return model_prior("small_ecar", "bmw_i3_60ah", "BMW i3 60 Ah (cited battery and mass, class prior otherwise)",
                       nominal_kwh=I3_NOMINAL_KWH, curb_mass_kg=I3_CURB_KG,
                       usable_frac=I3_USABLE_KWH_SPEC / I3_NOMINAL_KWH)


def usable_for_passport(trip: BmwTrip) -> bool:
    """Fixed rule: no known data problem and a SoC drop of at least MIN_SOC_DROP_PCT."""
    if any(f.startswith(x) for f in trip.flags for x in EXCLUDE_FLAGS):
        return False
    soc = trip.data["soc_pct"].dropna().to_numpy()
    return soc.size >= 2 and float(soc[0] - soc[-1]) >= MIN_SOC_DROP_PCT


def _window(trip: BmwTrip) -> tuple[int, int]:
    valid = np.flatnonzero(trip.data["soc_pct"].notna().to_numpy())
    return int(valid[0]), int(valid[-1])


def observation(trip: BmwTrip, prior: VehiclePrior) -> TripObservation:
    i0, i1 = _window(trip)
    df = trip.data.iloc[i0:i1 + 1:DOWNSAMPLE]
    t = df["time_s"].to_numpy() - float(df["time_s"].iloc[0])
    v = np.nan_to_num(df["speed_mps"].to_numpy(), nan=0.0)
    z = df["elevation_m"].interpolate(limit_direction="both").to_numpy()
    z = np.convolve(np.pad(z, SMOOTH_S // 2, mode="edge"), np.ones(SMOOTH_S) / SMOOTH_S, mode="valid")
    dt = np.diff(t)
    d = 0.5 * (v[:-1] + v[1:]) * dt
    grade = np.where(d >= 1.0, np.clip(np.diff(z) / np.maximum(d, 1.0), -MAX_ABS_GRADE, MAX_ABS_GRADE), 0.0)
    temp = float(np.nanmean(df["ambient_c"].to_numpy()))
    seg = Segments.build(d, np.where(dt > 0, d / dt, 0.0), accel_mps2=np.diff(v) / dt, grade_rad=np.arctan(grade),
                         temp_c=temp, dt_s=dt)
    soc = trip.data["soc_pct"].to_numpy()
    readings = np.round(np.array([soc[i0], soc[i1]]))  # what a driver reads off the dashboard
    return TripObservation(
        trip_id=trip.trip_id, segments=seg, mass_kg=total_mass_kg(prior.base.curb_mass_kg, 1, 0.0), ac_on=True,
        soc_t_s=np.array([0.0, float(t[-1])]), soc_pct=readings, window_weights=np.ones(len(d)),
        distance_km=float(d.sum() / 1000.0), duration_s=float(t[-1]),
        ascent_m=float(np.clip(np.diff(z), 0.0, None).sum()), passengers=1, cargo_kg=0.0, temp_c=temp)


@dataclass(frozen=True)
class Measured:
    trip_id: str
    energy_kwh: float  # net battery energy out over the SoC window (regen included)
    charge_ah: float
    soc_drop_pct: float  # unrounded BMS SoC


def measured(trip: BmwTrip) -> Measured:
    """GROUND TRUTH from the measured battery signals (sign: current is negative while discharging)."""
    i0, i1 = _window(trip)
    df = trip.data.iloc[i0:i1 + 1]
    dt = np.diff(df["time_s"].to_numpy())
    p = (df["voltage_v"] * df["current_a"]).to_numpy()
    i = df["current_a"].to_numpy()
    energy = -float(np.nansum(0.5 * (p[:-1] + p[1:]) * dt)) / 3.6e6
    ah = -float(np.nansum(0.5 * (i[:-1] + i[1:]) * dt)) / 3600.0
    soc = df["soc_pct"].to_numpy()
    return Measured(trip.trip_id, energy, ah, float(soc[0] - soc[-1]))


def truth_kwh_per_100(items: list[Measured]) -> float:
    """Ground-truth usable capacity as the gauge counts it: measured kWh per 100 BMS SoC points (pooled)."""
    return 100.0 * sum(m.energy_kwh for m in items) / sum(m.soc_drop_pct for m in items)
