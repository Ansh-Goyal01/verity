"""Monte Carlo arrival forecast with shared trip-level factors, plus live mid-trip updates.

CLAUDE.md rules implemented here:

* Shared trip-level factors are sampled ONCE per simulated run: the calibration draw (driver k, usable
  capacity, CdA, Crr, eta, aux coefficient), the aux multiplier, payload error, wind error, a day-to-day
  capacity error and the traffic regime. Only a small segment noise is drawn per segment.
* ``shared=False`` re-draws every factor independently for every segment (same per-segment marginals).
  It exists only as the strawman of experiment E2: it makes long trips falsely confident.
* All randomness comes from the caller's seeded ``numpy`` generator; everything is vectorised.

The planner never sees the truth: the route only carries what OSM, an elevation service and a weather
forecast would give (length, road class, grade, forecast temperature and wind).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .calibrate import Posterior
from .physics import J_PER_KWH, Segments, energy_terms
from .vehicles import to_params

Array = np.ndarray

RESERVE_PCT = 10.0  # arrival reserve a plan must keep (assumption, approximate - verify)
SEGMENT_KM = 1.0

# Planning assumptions, all approximate - verify: typical speed on (highway, arterial, congestion-prone)
# road classes in m/s, the stop-and-go speed peak and number of stop-go cycles per km in congestion-prone
# stretches. Chosen on the DEV simulator seed; in production they would be learned from fleet GPS logs.
FREE_SPEED_MPS = {"car": (26.0, 14.0, 6.5), "2w": (11.0, 8.0, 4.0)}
CONG_PEAK_MPS = {"car": 10.0, "2w": 5.0}
CONG_CYCLES_PER_KM = {"car": 2.5, "2w": 3.0}
DRIVER_SPEED_SLOPE = 0.30  # cruise speed changes by 30 % of the (k - 1) driver-style deviation


@dataclass(frozen=True, eq=False)
class PlannedRoute:
    """A route cut into ~1 km segments with what the planner can know before driving."""

    vtype: str
    length_m: Array
    road_class: Array  # 0 highway, 1 arterial, 2 congestion-prone
    grade_rad: Array
    temp_c: Array
    headwind_mps: Array

    @property
    def n(self) -> int:
        return int(self.length_m.shape[0])

    @property
    def distance_km(self) -> float:
        return float(self.length_m.sum() / 1000.0)

    @property
    def km_end(self) -> Array:
        return np.cumsum(self.length_m) / 1000.0


def build_route(
    vtype: str,
    distance_km: float,
    block_end_m: Array,
    block_kind: Array,
    elevation: dict,
    weather: dict,
    bearing_deg: float,
    segment_km: float = SEGMENT_KM,
) -> PlannedRoute:
    """Cut a route description (planner view) into planning segments."""
    n = max(1, int(np.ceil(distance_km / segment_km)))
    edges_m = np.minimum(np.arange(n + 1) * segment_km * 1000.0, distance_km * 1000.0)
    length = np.diff(edges_m)
    mid = 0.5 * (edges_m[:-1] + edges_m[1:])
    block_end_m = np.asarray(block_end_m, dtype=float)
    kind = np.asarray(block_kind)[np.clip(np.searchsorted(block_end_m, mid), 0, len(block_end_m) - 1)]
    grid = np.arange(len(elevation["z_m"])) * float(elevation["step_m"])
    z = np.interp(edges_m, grid, np.asarray(elevation["z_m"], dtype=float))
    padded = np.concatenate([[z[0]], z, [z[-1]]])
    z = (padded[:-2] + padded[1:-1] + padded[2:]) / 3.0
    grade = np.arctan(np.diff(z) / np.maximum(length, 1.0))
    wind = weather["wind_speed_mps"] * np.cos(np.radians(weather["wind_from_deg"] - bearing_deg))
    return PlannedRoute(vtype, length, kind.astype(int), grade, np.full(n, float(weather["temperature_c"])),
                        np.full(n, float(wind)))


@dataclass(frozen=True)
class ForecastConfig:
    """Spread of the shared trip-level factors (lognormal sd unless stated). Assumptions - verify."""

    n_runs: int = 2000
    sigma_wind_mps: float = 1.5  # forecast wind error (normal)
    sigma_payload_kg: float = 12.0  # occupant/cargo mass error (normal)
    sigma_aux: float = 0.20
    sigma_capacity: float = 0.02  # day-to-day usable-capacity error beyond the calibration posterior
    sigma_traffic: float = 0.12
    sigma_segment: float = 0.04  # the small independent per-segment noise


@dataclass(frozen=True, eq=False)
class ForecastRuns:
    """N simulated runs of one route: cumulative SoC used, and per-term totals (positive = used)."""

    km_end: Array  # (S,)
    soc_used_cum: Array  # (N, S): SoC points used up to the end of each segment
    terms_soc: dict[str, Array]  # (N,) per term over the whole route; regen is negative
    shared: bool

    @property
    def n_runs(self) -> int:
        return int(self.soc_used_cum.shape[0])

    @property
    def total_used(self) -> Array:
        return self.soc_used_cum[:, -1]

    def used_at_km(self, km: float) -> Array:
        """SoC points used by each run at route position ``km`` (linear inside a segment)."""
        km = float(np.clip(km, 0.0, self.km_end[-1]))
        idx = int(min(np.searchsorted(self.km_end, km), len(self.km_end) - 1))
        km0 = 0.0 if idx == 0 else float(self.km_end[idx - 1])
        prev = np.zeros(self.n_runs) if idx == 0 else self.soc_used_cum[:, idx - 1]
        frac = 0.0 if self.km_end[idx] == km0 else (km - km0) / (self.km_end[idx] - km0)
        return prev + frac * (self.soc_used_cum[:, idx] - prev)

    def arrival_soc(self, start_soc_pct: float) -> Array:
        return start_soc_pct - self.total_used

    def confidence(self, start_soc_pct: float, reserve_pct: float = RESERVE_PCT) -> float:
        return float(np.mean(self.arrival_soc(start_soc_pct) >= reserve_pct))

    def arrival_quantiles(self, start_soc_pct: float, qs: tuple[float, ...] = (0.1, 0.5, 0.9)) -> Array:
        return np.quantile(self.arrival_soc(start_soc_pct), qs)


def simulate_runs(
    post: Posterior,
    route: PlannedRoute,
    *,
    mass_kg: float,
    ac_on: bool,
    rng: np.random.Generator,
    cfg: ForecastConfig = ForecastConfig(),
    shared: bool = True,
    highway_speed_mps: float | None = None,
) -> ForecastRuns:
    """Monte Carlo of the energy used along ``route`` for a calibrated vehicle.

    ``highway_speed_mps`` caps/sets the cruise speed on highway segments (the "cruise at 90" lever).
    """
    n_runs, n_seg = cfg.n_runs, route.n
    shape = (n_runs, 1) if shared else (n_runs, n_seg)
    if shared:
        u = post.sample_u(rng, n_runs)
    else:
        u = post.sample_u(rng, n_runs * n_seg).reshape(n_runs, n_seg, -1)
    params = to_params(post.base, post.nominal_kwh, u)
    k = np.asarray(params.dyn_k)

    def lognormal(sd: float) -> Array:
        return np.exp(rng.normal(0.0, sd, size=shape))

    traffic, aux_scale, cap_err = lognormal(cfg.sigma_traffic), lognormal(cfg.sigma_aux), lognormal(cfg.sigma_capacity)
    payload = rng.normal(0.0, cfg.sigma_payload_kg, size=shape)
    wind = rng.normal(0.0, cfg.sigma_wind_mps, size=shape)

    speeds = np.asarray(FREE_SPEED_MPS[route.vtype])[route.road_class]
    if highway_speed_mps is not None:
        speeds = np.where(route.road_class == 0, min(highway_speed_mps, speeds.max()), speeds)
    v = np.maximum(speeds * traffic * (1.0 + DRIVER_SPEED_SLOPE * (k - 1.0)), 0.5)
    seg = Segments(route.length_m, v, 0.0, route.grade_rad, route.length_m / v, route.temp_c, 1.0,
                   route.headwind_mps)
    mass_run = mass_kg + payload
    terms = dict(energy_terms(params, seg, mass_kg=mass_run, ac_on=ac_on, wind_offset_mps=wind, aux_scale=aux_scale))

    # stop-and-go in congestion-prone stretches: kinetic energy cycled through drive and regen losses
    in_jam = (route.road_class == 2).astype(float)
    cycles = CONG_CYCLES_PER_KM[route.vtype] * (route.length_m / 1000.0) * in_jam / traffic
    kinetic_j = 0.5 * mass_run * (1.0 + params.rot_inertia_delta) * CONG_PEAK_MPS[route.vtype] ** 2
    loss_ratio = np.maximum(1.0 / np.asarray(params.eta_drive) - params.eta_regen, 0.0)
    terms["inertia"] = terms["inertia"] + k * cycles * kinetic_j * loss_ratio

    noise = np.exp(rng.normal(0.0, cfg.sigma_segment, size=(n_runs, n_seg)))
    usable_j = np.asarray(params.usable_kwh) * cap_err * J_PER_KWH
    soc_terms = {name: 100.0 * t * noise / usable_j for name, t in terms.items()}
    cum = np.cumsum(sum(soc_terms.values()), axis=1)
    return ForecastRuns(route.km_end, cum, {name: t.sum(axis=1) for name, t in soc_terms.items()}, shared)


def round_prob(p: float) -> float:
    """Every probability shown to a user is rounded to the nearest 5 % (CLAUDE.md rule 5)."""
    return round(round(float(p) * 20.0) / 20.0, 2) + 0.0


def weighted_quantiles(values: Array, weights: Array, qs: tuple[float, ...]) -> Array:
    order = np.argsort(values)
    v, w = values[order], weights[order]
    cdf = (np.cumsum(w) - 0.5 * w) / w.sum()
    return np.interp(qs, cdf, v)


@dataclass(frozen=True)
class LiveForecast:
    km_now: float
    confidence: float
    arrival_p10: float
    arrival_p50: float
    arrival_p90: float
    ess: float  # effective number of runs that agree with the new reading
    expected_soc_now_p50: float  # what the model expected at this km, for the "draining faster" message


def live_update(
    runs: ForecastRuns,
    start_soc_pct: float,
    km_now: float,
    soc_now_pct: float,
    *,
    reserve_pct: float = RESERVE_PCT,
    sigma_obs: float = 0.8,
    min_ess: float = 40.0,
) -> LiveForecast:
    """Condition the runs on a new battery reading: runs whose SoC at ``km_now`` matches the reading keep
    a high weight. Because trip-level factors are shared, a reading mid-trip is informative about the
    rest of the trip. The reading noise is widened until at least ``min_ess`` runs still count."""
    expected_now = start_soc_pct - runs.used_at_km(km_now)
    resid = soc_now_pct - expected_now
    sigma = sigma_obs
    for _ in range(8):
        w = np.exp(-0.5 * (resid / sigma) ** 2)
        if w.sum() > 0 and (w.sum() ** 2) / (w**2).sum() >= min_ess:
            break
        sigma *= 1.6
    w = w / w.sum()
    arrival = runs.arrival_soc(start_soc_pct)
    q = weighted_quantiles(arrival, w, (0.1, 0.5, 0.9))
    return LiveForecast(
        km_now=float(km_now), confidence=float(np.sum(w * (arrival >= reserve_pct))),
        arrival_p10=float(q[0]), arrival_p50=float(q[1]), arrival_p90=float(q[2]),
        ess=float(1.0 / np.sum(w**2)), expected_soc_now_p50=float(np.median(expected_now)),
    )
