"""Physics energy model: pure functions in SI units, vectorised with numpy.

Per route segment (speed v, acceleration a, grade angle theta, length d, duration dt):

    F_roll    = Crr * s_surface * m * g * cos(theta)
    F_grade   = m * g * sin(theta)
    F_aero    = 0.5 * rho_air(T) * CdA * (v + v_headwind)^2
    F_inertia = k * m * (1 + delta) * a          (k = driver/dynamics multiplier, see DECISIONS.md)
    P_wheel   = (F_roll + F_grade + F_aero + F_inertia) * v
    P_batt    = P_wheel / eta_drive            if P_wheel >= 0
              = max(P_wheel * eta_regen, -P_regen_max)   otherwise
    P_total   = P_batt + P_aux(T, ac_on) + P_base

Energy attribution (so the UI breakdown is exact): in a driving step (P_wheel >= 0) the four motion
terms are ``p_i / eta_drive``; in a regeneration step the whole battery power is credited to the
single ``regen`` term (negative = energy recovered). Aux and base are ``P * dt``. The terms therefore
sum exactly to ``sum(P_total * dt)``.

All functions broadcast: segment arrays have shape (S,), while vehicle parameters and trip-level
factors may be arrays of shape (N, 1) so that N Monte Carlo runs evaluate in one call -> (N, S).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Mapping

import numpy as np

Array = np.ndarray

G = 9.80665  # m/s^2, standard gravity
J_PER_KWH = 3.6e6

# ASSUMPTION (named, per CLAUDE.md): average body mass per occupant including clothing and a small
# personal bag. Approximate - verify. "passengers" in a trip log counts every occupant incl. the driver.
PASSENGER_MASS_KG = 70.0

# Rotational inertia factor delta (wheels, rotor). Approximate - verify; not calibrated here.
ROT_INERTIA_DELTA = 0.05

# Ambient temperature at which climate control draws nothing (assumption, approximate - verify).
T_COMFORT_C = 22.0

# Specific gas constant of dry air, J/(kg K), and sea-level pressure in Pa. Altitude effects on air
# density over the Delhi-Agra plain (a few hundred metres) are ignored.
R_AIR = 287.05
P_ATM_PA = 101325.0

TERMS: tuple[str, ...] = ("rolling", "grade", "aero", "inertia", "regen", "aux", "base")


@dataclass(frozen=True)
class VehicleParams:
    """Physical parameters of one vehicle. Fields may be floats or (N, 1) arrays (Monte Carlo)."""

    curb_mass_kg: float | Array
    usable_kwh: float | Array  # E_usable: true usable battery energy today
    cda_m2: float | Array
    crr: float | Array
    eta_drive: float | Array
    eta_regen: float | Array
    max_regen_kw: float | Array
    base_w: float | Array  # always-on load (electronics, pumps, lights)
    aux_w_per_k: float | Array  # climate load per kelvin away from T_COMFORT_C when ac_on
    rot_inertia_delta: float | Array = ROT_INERTIA_DELTA
    dyn_k: float | Array = 1.0  # driver aggressiveness / dynamics multiplier k


@dataclass(frozen=True)
class Segments:
    """Route segments as parallel float arrays of shape (S,)."""

    length_m: Array
    v_mps: Array
    accel_mps2: Array
    grade_rad: Array
    dt_s: Array
    temp_c: Array
    surface: Array  # s_surface >= 1 (rough roads cost more rolling resistance)
    headwind_mps: Array  # positive = wind against the direction of travel

    def __post_init__(self) -> None:
        names = ("length_m", "v_mps", "accel_mps2", "grade_rad", "dt_s", "temp_c", "surface", "headwind_mps")
        try:
            arrays = np.broadcast_arrays(*[np.asarray(getattr(self, n), dtype=float) for n in names])
        except ValueError as exc:
            raise ValueError(f"segment arrays must share one shape (1-D, or 2-D (runs, segments)): {exc}") from exc
        if arrays[0].ndim not in (1, 2):
            raise ValueError(f"segment arrays must be 1-D or 2-D, got {arrays[0].ndim}-D")
        for name, arr in zip(names, arrays):
            if not np.all(np.isfinite(arr)):
                raise ValueError(f"segment field {name} contains non-finite values")
            object.__setattr__(self, name, np.array(arr))
        if np.any(self.length_m < 0) or np.any(self.dt_s < 0) or np.any(self.v_mps < 0):
            raise ValueError("length, duration and speed must be non-negative")
        if np.any(self.surface < 1.0):
            raise ValueError("surface factor must be >= 1")

    @classmethod
    def build(
        cls,
        length_m: Array,
        v_mps: Array,
        *,
        accel_mps2: Array | float = 0.0,
        grade_rad: Array | float = 0.0,
        temp_c: Array | float = 25.0,
        surface: Array | float = 1.0,
        headwind_mps: Array | float = 0.0,
        dt_s: Array | None = None,
    ) -> Segments:
        """Convenience constructor: scalars broadcast; ``dt_s`` defaults to length / speed."""
        length = np.asarray(length_m, dtype=float)
        v = np.asarray(v_mps, dtype=float)
        if dt_s is None:
            with np.errstate(divide="ignore", invalid="ignore"):
                dt = np.where(v > 0, length / np.where(v > 0, v, 1.0), 0.0)
        else:
            dt = np.asarray(dt_s, dtype=float)

        def full(x: Array | float) -> Array:
            return np.broadcast_to(np.asarray(x, dtype=float), length.shape).copy()

        return cls(length, v, full(accel_mps2), full(grade_rad), dt, full(temp_c), full(surface), full(headwind_mps))

    @property
    def n(self) -> int:
        return int(self.length_m.shape[-1])

    @property
    def distance_km(self) -> float:
        return float(np.atleast_2d(self.length_m)[0].sum() / 1000.0)

    def cumulative_km(self) -> Array:
        """Distance at the END of each segment, in km."""
        return np.cumsum(self.length_m) / 1000.0

    def replace(self, **changes: Array) -> Segments:
        return replace(self, **changes)


def air_density(temp_c: Array | float) -> Array | float:
    """Dry-air density in kg/m^3 at sea-level pressure (ideal gas law)."""
    return P_ATM_PA / (R_AIR * (np.asarray(temp_c, dtype=float) + 273.15))


def total_mass_kg(curb_mass_kg: float, passengers: float, cargo_kg: float) -> float:
    """Curb mass + passengers x assumed body mass + cargo."""
    return float(curb_mass_kg + passengers * PASSENGER_MASS_KG + cargo_kg)


def aux_power_w(params: VehicleParams, temp_c: Array | float, ac_on: bool, aux_scale: Array | float = 1.0) -> Array:
    """Climate/auxiliary power: coefficient x |T - T_comfort| when climate control is on."""
    if not ac_on:
        return np.zeros_like(np.asarray(temp_c, dtype=float)) * np.asarray(params.aux_w_per_k, dtype=float)
    return aux_scale * params.aux_w_per_k * np.abs(np.asarray(temp_c, dtype=float) - T_COMFORT_C)


def energy_terms(
    params: VehicleParams,
    seg: Segments,
    *,
    mass_kg: float | Array,
    ac_on: bool,
    wind_offset_mps: float | Array = 0.0,
    aux_scale: float | Array = 1.0,
) -> dict[str, Array]:
    """Battery energy per segment in joules, separately for each term in ``TERMS``.

    Positive = energy drawn from the battery; ``regen`` is negative (energy recovered). Result arrays
    broadcast to (N, S) when parameters/factors are (N, 1) arrays, else (S,).
    """
    cos_t, sin_t = np.cos(seg.grade_rad), np.sin(seg.grade_rad)
    rho = air_density(seg.temp_c)
    v = seg.v_mps

    f_roll = params.crr * seg.surface * mass_kg * G * cos_t
    f_grade = mass_kg * G * sin_t
    f_aero = 0.5 * rho * params.cda_m2 * (v + seg.headwind_mps + wind_offset_mps) ** 2
    f_inertia = params.dyn_k * mass_kg * (1.0 + params.rot_inertia_delta) * seg.accel_mps2

    p_roll, p_grade, p_aero, p_inertia = f_roll * v, f_grade * v, f_aero * v, f_inertia * v
    p_wheel = p_roll + p_grade + p_aero + p_inertia
    driving = p_wheel >= 0.0

    cap_w = np.asarray(params.max_regen_kw, dtype=float) * 1000.0
    p_regen = np.maximum(p_wheel * params.eta_regen, -cap_w)

    dt = seg.dt_s
    zero = 0.0
    terms = {
        "rolling": np.where(driving, p_roll / params.eta_drive, zero) * dt,
        "grade": np.where(driving, p_grade / params.eta_drive, zero) * dt,
        "aero": np.where(driving, p_aero / params.eta_drive, zero) * dt,
        "inertia": np.where(driving, p_inertia / params.eta_drive, zero) * dt,
        "regen": np.where(driving, zero, p_regen) * dt,
        "aux": aux_power_w(params, seg.temp_c, ac_on, aux_scale) * dt,
        "base": np.asarray(params.base_w, dtype=float) * dt,
    }
    shape = np.broadcast_shapes(*(t.shape for t in terms.values()))
    return {name: np.broadcast_to(t, shape) for name, t in terms.items()}


def total_energy_j(terms: Mapping[str, Array], axis: int | None = -1) -> Array:
    """Sum of all terms (and over segments when ``axis`` is given)."""
    stacked = sum(terms.values())
    return stacked if axis is None else stacked.sum(axis=axis)


def joules_to_soc_pct(energy_j: Array | float, usable_kwh: Array | float) -> Array | float:
    """State-of-charge percentage points for an energy in joules: 100 * E / E_usable."""
    return 100.0 * np.asarray(energy_j, dtype=float) / (np.asarray(usable_kwh, dtype=float) * J_PER_KWH)


def soc_drop_by_term_pct(terms: Mapping[str, Array], usable_kwh: Array | float) -> dict[str, Array]:
    """Per-term SoC percentage points used over the whole trip (sum over segments).

    ``keepdims`` keeps a per-run (N, 1) energy aligned with a per-run (N, 1) capacity; summing to (N,)
    first would broadcast against (N, 1) into an (N, N) matrix and silently mix runs.
    """
    return {
        name: np.squeeze(joules_to_soc_pct(t.sum(axis=-1, keepdims=True), usable_kwh), axis=-1)
        for name, t in terms.items()
    }


def cumulative_soc_drop_pct(terms: Mapping[str, Array], usable_kwh: Array | float) -> Array:
    """SoC percentage points used up to the END of each segment, shape (..., S)."""
    return joules_to_soc_pct(np.cumsum(total_energy_j(terms, axis=None), axis=-1), usable_kwh)


def trip_soc_drop_pct(
    params: VehicleParams,
    seg: Segments,
    *,
    mass_kg: float | Array,
    ac_on: bool,
    wind_offset_mps: float | Array = 0.0,
    aux_scale: float | Array = 1.0,
) -> Array:
    """Total SoC percentage points used over the trip (shape () or (N,))."""
    terms = energy_terms(
        params, seg, mass_kg=mass_kg, ac_on=ac_on, wind_offset_mps=wind_offset_mps, aux_scale=aux_scale
    )
    energy_j = total_energy_j(terms, axis=None).sum(axis=-1, keepdims=True)  # (..., 1), aligned with usable_kwh
    return np.squeeze(joules_to_soc_pct(energy_j, params.usable_kwh), axis=-1)
