"""Vehicle-model priors: mass, battery, CdA, Crr, efficiency, with parameter uncertainty.

Three GENERIC classes only (small e-car, compact e-SUV, e-scooter). Every number below is
approximate - verify. None of them is an official specification of any real vehicle, and the prior
widths are modelling assumptions, not measurements.

Calibrated parameters live in an unconstrained vector ``u`` (6 entries, see ``THETA_NAMES``) so a
Gaussian prior/posterior on ``u`` is a valid, bounded distribution in physical space:

    cda_mult = exp(u0)   crr_mult = exp(u1)   aux_mult = exp(u3)   k = exp(u5)
    eta_drive   = ETA_LO + (ETA_HI - ETA_LO) * sigmoid(u2)
    usable_frac = UF_LO  + (UF_HI  - UF_LO)  * sigmoid(u4)         (E_usable = usable_frac * nominal)
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np
from scipy.special import expit, logit

from .physics import VehicleParams

Array = np.ndarray

THETA_NAMES: tuple[str, ...] = ("cda_mult", "crr_mult", "eta_drive", "aux_mult", "usable_frac", "k")
N_THETA = len(THETA_NAMES)

# Physical bounds of the bounded parameters (modelling assumptions).
ETA_LO, ETA_HI = 0.60, 0.98
UF_LO, UF_HI = 0.50, 1.05


def _u_from_physical(median: float, sd: float, lo: float, hi: float) -> tuple[float, float]:
    """Mean and sd of u so that the physical value has roughly this median and sd (delta method)."""
    s = (median - lo) / (hi - lo)
    slope = (hi - lo) * s * (1.0 - s)
    return float(logit(s)), float(sd / slope)


def to_physical(u: Array) -> dict[str, Array]:
    """Map unconstrained ``u`` (..., 6) to the named physical calibration parameters."""
    u = np.asarray(u, dtype=float)
    return {
        "cda_mult": np.exp(u[..., 0]),
        "crr_mult": np.exp(u[..., 1]),
        "eta_drive": ETA_LO + (ETA_HI - ETA_LO) * expit(u[..., 2]),
        "aux_mult": np.exp(u[..., 3]),
        "usable_frac": UF_LO + (UF_HI - UF_LO) * expit(u[..., 4]),
        "k": np.exp(u[..., 5]),
    }


def to_params(base: VehicleParams, nominal_kwh: float, u: Array) -> VehicleParams:
    """Apply calibration parameters ``u`` to a class's base parameters.

    ``u`` of shape (6,) gives scalar parameters; shape (N, 6) gives (N, 1) arrays for N Monte Carlo runs
    (one parameter set shared by the whole trip); shape (N, S, 6) gives (N, S) arrays, i.e. a separate
    parameter draw for every segment (used only to test the independent-segments strawman in E2).
    """
    u = np.asarray(u, dtype=float)
    phys = to_physical(u)

    def col(x: Array) -> Array:
        return x[..., None] if u.ndim == 2 else x

    return replace(
        base,
        cda_m2=base.cda_m2 * col(phys["cda_mult"]),
        crr=base.crr * col(phys["crr_mult"]),
        eta_drive=col(phys["eta_drive"]),
        aux_w_per_k=base.aux_w_per_k * col(phys["aux_mult"]),
        usable_kwh=nominal_kwh * col(phys["usable_frac"]),
        dyn_k=col(phys["k"]),
    )


@dataclass(frozen=True, eq=False)
class VehiclePrior:
    """A generic vehicle class: base parameters plus a Gaussian prior on ``u``."""

    key: str
    label: str
    vtype: str  # "car" | "2w" (matches verity.trip.v1 vehicle.type; 3w is not modelled yet)
    nominal_kwh: float
    claimed_range_km: float  # brochure-style number, used only by the flat-discount baseline
    base: VehicleParams
    u_mean: Array
    u_sd: Array

    @property
    def u_cov(self) -> Array:
        return np.diag(self.u_sd**2)

    def nominal_params(self, nominal_kwh: float | None = None) -> VehicleParams:
        """The 'nominal-spec' model: parameters at the prior median, with no vehicle-specific learning."""
        return to_params(self.base, nominal_kwh or self.nominal_kwh, self.u_mean)


def _make_prior(
    key: str,
    label: str,
    vtype: str,
    *,
    nominal_kwh: float,
    claimed_range_km: float,
    curb_mass_kg: float,
    cda_m2: float,
    crr: float,
    eta_drive: float,
    eta_regen: float,
    max_regen_kw: float,
    base_w: float,
    aux_w_per_k: float,
    usable_frac: float = 0.93,
    sd_cda: float = 0.12,
    sd_crr: float = 0.25,
    sd_eta: float = 0.035,
    sd_aux: float = 0.40,
    sd_usable: float = 0.06,
    sd_k: float = 0.25,
) -> VehiclePrior:
    u_eta, s_eta = _u_from_physical(eta_drive, sd_eta, ETA_LO, ETA_HI)
    u_uf, s_uf = _u_from_physical(usable_frac, sd_usable, UF_LO, UF_HI)
    u_mean = np.array([0.0, 0.0, u_eta, 0.0, u_uf, 0.0])
    u_sd = np.array([sd_cda, sd_crr, s_eta, sd_aux, s_uf, sd_k])
    base = VehicleParams(
        curb_mass_kg=curb_mass_kg,
        usable_kwh=nominal_kwh * usable_frac,
        cda_m2=cda_m2,
        crr=crr,
        eta_drive=eta_drive,
        eta_regen=eta_regen,
        max_regen_kw=max_regen_kw,
        base_w=base_w,
        aux_w_per_k=aux_w_per_k,
    )
    return VehiclePrior(key, label, vtype, nominal_kwh, claimed_range_km, base, u_mean, u_sd)


# All numbers: approximate - verify. Generic classes, not real models.
VEHICLE_CLASSES: dict[str, VehiclePrior] = {
    "small_ecar": _make_prior(
        "small_ecar", "Small e-car (~25 kWh class)", "car",
        nominal_kwh=25.0,  # approximate - verify
        claimed_range_km=250.0,  # approximate - verify; brochure-style, lab-cycle number
        curb_mass_kg=1150.0,  # approximate - verify
        cda_m2=0.65,  # approximate - verify (Cd ~0.30 x frontal area ~2.1 m^2)
        crr=0.0105,  # approximate - verify
        eta_drive=0.90,  # approximate - verify; battery-to-wheel
        eta_regen=0.65,  # approximate - verify; wheel-to-battery
        max_regen_kw=35.0,  # approximate - verify
        base_w=350.0,  # approximate - verify
        aux_w_per_k=60.0,  # approximate - verify; climate watts per kelvin from 22 C
    ),
    "compact_esuv": _make_prior(
        "compact_esuv", "Compact e-SUV (~45 kWh class)", "car",
        nominal_kwh=45.0,  # approximate - verify
        claimed_range_km=350.0,  # approximate - verify
        curb_mass_kg=1600.0,  # approximate - verify
        cda_m2=0.80,  # approximate - verify (Cd ~0.33 x frontal area ~2.4 m^2)
        crr=0.0100,  # approximate - verify
        eta_drive=0.90,  # approximate - verify
        eta_regen=0.65,  # approximate - verify
        max_regen_kw=60.0,  # approximate - verify
        base_w=450.0,  # approximate - verify
        aux_w_per_k=85.0,  # approximate - verify
    ),
    "e_scooter": _make_prior(
        "e_scooter", "E-scooter (~3 kWh class)", "2w",
        nominal_kwh=3.0,  # approximate - verify
        claimed_range_km=100.0,  # approximate - verify
        curb_mass_kg=110.0,  # approximate - verify; the rider is counted via passengers
        cda_m2=0.45,  # approximate - verify (upright rider)
        crr=0.0140,  # approximate - verify
        eta_drive=0.85,  # approximate - verify
        eta_regen=0.45,  # approximate - verify; many scooters recover little
        max_regen_kw=2.0,  # approximate - verify
        base_w=25.0,  # approximate - verify
        aux_w_per_k=0.0,  # no climate control
        sd_aux=0.05,
    ),
}


def get_prior(key: str) -> VehiclePrior:
    try:
        return VEHICLE_CLASSES[key]
    except KeyError as exc:
        raise KeyError(f"unknown vehicle class {key!r}; known: {sorted(VEHICLE_CLASSES)}") from exc


def classify(vtype: str, nominal_kwh: float) -> VehiclePrior:
    """Pick the generic class for a logged vehicle: scooters by type, cars by nearest battery size."""
    if vtype == "2w":
        return VEHICLE_CLASSES["e_scooter"]
    cars = [p for p in VEHICLE_CLASSES.values() if p.vtype == "car"]
    return min(cars, key=lambda p: abs(p.nominal_kwh - nominal_kwh))
