"""Ride check (F8b): can a cab driver take this ride, and where should they charge after the drop?

The ride (current position -> pickup -> drop) and the after-drop leg to a charger share ONE Monte Carlo with
shared trip-level factors: the ride is followed by a flat urban "tail" route whose slices stand in for the drive
from the drop to a charger and between chargers (Delhi NCR is flat; grade on these short legs is taken as 0,
approximate - verify). A plan is a primary charger plus backups; each run tries them in order, and chargers at the
same Site fail together (sites.py). The plan minimises expected lost earning minutes (F8a):

    drive to the charger + backup driving + failed stops + expected queue wait + charging (heat-derated power)
    + STRANDING_PENALTY_MIN x P(stranded)

subject to the driver's confidence target; minutes become rupees at the driver's earning rate (an input).

Decision: GO when P(complete the ride AND reach a working charger after the drop) >= target (shown, 5 % steps),
NO_GO otherwise, INSUFFICIENT_EVIDENCE when the vehicle is uncalibrated or the GO depends on chargers with stale
or missing evidence. Pure: no network; the service fetches and caches routes (routing.py) and passes them in.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, replace
from typing import Sequence

import numpy as np

from .dispatch import (
    FAILED_STOP_MIN,
    LEVER_MIN_GAIN,
    MIN_CALIBRATED_TRIPS,
    RES_STOP,
    STOP_OVERHEAD_MIN,
    STRANDING_PENALTY_MIN,
    Ctx,
    Node,
    VehicleContext,
    _evidence,
    _stale,
    drive_cum_minutes,
)
from .forecast import ForecastConfig, PlannedRoute, round_prob, simulate_runs
from .physics import total_mass_kg
from .sites import WorksSampler, p_all_fail
from .trips import haversine_m
from .vehicles import get_prior

Array = np.ndarray

CHARGE_TO_PCT = 80.0  # a cab driver tops up to this SoC after the drop (approximate - verify)
URBAN_KMH = 22.0  # evening urban speed between chargers (approximate - verify)
ROAD_FACTOR = 1.3  # road km per straight-line km between nearby chargers (approximate - verify)
MAX_BACKUPS = 2
RECENT_FAILURE_DAYS = 7.0  # a failure report this recent is called out to the driver
TAIL_MARGIN_KM = 2.0
PART_LABELS = {"drive": "Drive to the charger", "backup_drive": "Driving on to a backup", "failed_stops":
               "Stops at broken chargers", "waiting": "Expected queue", "charging": f"Charging to {CHARGE_TO_PCT:.0f} %",
               "stranding": "Stranding risk (penalty x probability)"}


@dataclass(frozen=True, eq=False)
class AfterDropOption:
    """A charger the driver could go to after the drop, with the road leg from the drop."""

    node: Node
    leg_km: float
    leg_min: float
    recent_failure_days: float | None = None  # days since its latest broken / no_power report


@dataclass(frozen=True, eq=False)
class RideInput:
    vehicle: VehicleContext
    ride: PlannedRoute  # current position -> pickup -> drop
    pickup_km: float
    start_soc: float
    target: float
    earning_rs_per_hour: float
    options: tuple[AfterDropOption, ...]


@dataclass(frozen=True, eq=False)
class ChainEval:
    chain: tuple[int, ...]  # option indices: primary first, then backups
    success: Array  # ride completed AND a working charger reached
    lost: Array  # lost earning minutes after the drop, stranding penalty included
    parts: dict[str, Array]

    @property
    def confidence(self) -> float:
        return float(self.success.mean())

    @property
    def expected_lost(self) -> float:
        return float(self.lost.mean())


def pair_km(a: Node, b: Node) -> float:
    """Road km between two chargers, from their straight-line distance."""
    if a.charger is None or b.charger is None:
        return 0.0
    d = haversine_m(np.array([a.charger.lat]), np.array([a.charger.lon]), np.array([b.charger.lat]),
                    np.array([b.charger.lon]))[0]
    return float(d) / 1000.0 * ROAD_FACTOR


def composite_route(ride: PlannedRoute, tail_km: float) -> PlannedRoute:
    """The ride followed by a flat urban tail of ``tail_km`` (1 km segments) for the after-drop legs."""
    n = max(1, int(math.ceil(tail_km)))
    temp = float(ride.temp_c[-1])
    return PlannedRoute(ride.vtype, np.concatenate([ride.length_m, np.full(n, 1000.0)]),
                        np.concatenate([ride.road_class, np.ones(n, dtype=int)]),
                        np.concatenate([ride.grade_rad, np.zeros(n)]), np.concatenate([ride.temp_c, np.full(n, temp)]),
                        np.concatenate([ride.headwind_mps, np.zeros(n)]))


def build_ctx(inp: RideInput, rng: np.random.Generator, cfg: ForecastConfig) -> Ctx:
    opts = inp.options
    longest_pair = max((pair_km(a.node, b.node) for a in opts for b in opts), default=0.0)
    tail = max((o.leg_km for o in opts), default=0.0) + MAX_BACKUPS * longest_pair + TAIL_MARGIN_KM
    route = composite_route(inp.ride, tail)
    prior = get_prior(inp.vehicle.class_key)
    mass = total_mass_kg(prior.base.curb_mass_kg, inp.vehicle.passengers, inp.vehicle.cargo_kg)
    runs = simulate_runs(inp.vehicle.post, route, mass_kg=mass, ac_on=inp.vehicle.ac_on, rng=rng, cfg=cfg)
    usable = float(inp.vehicle.post.nominal_kwh * inp.vehicle.post.summary(n=1000)["usable_frac"][0])
    return Ctx(runs, route, inp.vehicle, float(inp.start_soc), float(inp.target), usable, drive_cum_minutes(route))


def backups_for(i: int, opts: Sequence[AfterDropOption]) -> tuple[int, ...]:
    others = sorted((j for j in range(len(opts)) if j != i), key=lambda j: pair_km(opts[i].node, opts[j].node))
    return (i, *others[:MAX_BACKUPS])


def evaluate_chain(ctx: Ctx, inp: RideInput, chain: Sequence[int], rng: np.random.Generator,
                   p_override: dict[str, float] | None = None) -> ChainEval:
    runs, n = ctx.runs, ctx.runs.n_runs
    ride_km = inp.ride.distance_km
    soc = ctx.start_soc - runs.used_at_km(ride_km)
    pending = soc >= RES_STOP  # the ride itself was completed
    ride_ok = pending.copy()
    success = np.zeros(n, dtype=bool)
    parts = {k: np.zeros(n) for k in PART_LABELS}
    sampler = WorksSampler(rng, n)
    off, prev = 0.0, None
    for k, i in enumerate(chain):
        opt = inp.options[i]
        node = opt.node
        dist = opt.leg_km if prev is None else pair_km(prev, node)
        drive_min = opt.leg_min if prev is None else dist / URBAN_KMH * 60.0
        arr = soc - (runs.used_at_km(ride_km + off + dist) - runs.used_at_km(ride_km + off))
        reach = pending & (arr >= RES_STOP)
        works = sampler.works(node.site_id, (p_override or {}).get(node.id, node.p_works))
        ok = reach & works
        parts["drive" if k == 0 else "backup_drive"] += np.where(reach, drive_min, 0.0)
        parts["failed_stops"] += np.where(reach & ~works, FAILED_STOP_MIN, 0.0)
        parts["waiting"] += np.where(ok, node.wait_min, 0.0)
        gain = np.clip(CHARGE_TO_PCT - arr, 0.0, None)
        parts["charging"] += np.where(ok, STOP_OVERHEAD_MIN + gain * ctx.min_per_pp(node), 0.0)
        success |= ok
        pending = reach & ~works
        soc = np.where(reach, arr, soc)
        off, prev = off + dist, node
    parts["stranding"] = np.where(ride_ok & success, 0.0, STRANDING_PENALTY_MIN)
    return ChainEval(tuple(chain), ride_ok & success, sum(parts.values()), parts)


def choose_chain(ctx: Ctx, inp: RideInput, seed: int) -> tuple[ChainEval, list[ChainEval]]:
    """Every option as primary (with its nearest backups); among chains meeting the target, fewest lost minutes,
    otherwise the most reliable (compared at the Monte Carlo's 2-point resolution)."""
    evals = [evaluate_chain(ctx, inp, backups_for(i, inp.options), np.random.default_rng(seed + 10 + i))
             for i in range(len(inp.options))]
    meeting = [e for e in evals if e.confidence >= ctx.target]
    best = (min(meeting, key=lambda e: e.expected_lost) if meeting
            else max(evals, key=lambda e: (round(e.confidence / 0.02), -e.expected_lost)))
    return best, evals


def ride_bingo_line(ctx: Ctx, inp: RideInput, rng: np.random.Generator, n_sub: int = 1000) -> list[dict]:
    """Per ride segment end: the lowest SoC from which some after-drop charger is reachable and working with
    probability >= target (Site-correlated), plus the expected SoC there."""
    runs, ride_km = ctx.runs, inp.ride.distance_km
    sub = rng.choice(runs.n_runs, size=min(n_sub, runs.n_runs), replace=False)
    grid = np.arange(0.0, 100.5, 1.0)
    used_drop = runs.used_at_km(ride_km)[sub]
    leg_used = [(runs.used_at_km(ride_km + o.leg_km) - runs.used_at_km(ride_km))[sub] for o in inp.options]
    p = [o.node.p_works for o in inp.options]
    sites = [o.node.site_id for o in inp.options]
    out = []
    for si, km in enumerate(runs.km_end):
        if km > ride_km + 1e-6:
            break
        used_x = runs.soc_used_cum[sub, si]
        energy = np.stack([used_drop - used_x + lu for lu in leg_used])  # (C, n)
        reach = (grid[None, None, :] - energy[:, :, None]) >= RES_STOP
        prob = 1.0 - p_all_fail(p, sites, reach).mean(axis=0)
        ok = np.nonzero(prob >= ctx.target)[0]
        out.append({"km": round(float(km), 1), "bingo_soc_pct": int(grid[ok[0]]) if len(ok) else 100,
                    "expected_soc_pct": int(round(ctx.start_soc - float(np.median(runs.soc_used_cum[:, si]))))})
    first = out[0]["bingo_soc_pct"] if out else 100
    return [{"km": 0.0, "bingo_soc_pct": first, "expected_soc_pct": int(round(ctx.start_soc))}, *out]


def rupees(minutes: float, rate_rs_per_hour: float) -> int:
    """Rupees to the nearest 10: the inputs do not support more precision."""
    return int(round(minutes * rate_rs_per_hour / 60.0 / 10.0) * 10)


def _name(node: Node) -> str:
    return node.charger.name if node.charger else node.id


def _option_row(inp: RideInput, i: int, ev: ChainEval, chosen: bool) -> dict:
    opt = inp.options[i]
    node = opt.node
    note = "Chosen: fewest expected lost minutes at the target confidence." if chosen else ""
    if not chosen and opt.recent_failure_days is not None and opt.recent_failure_days <= RECENT_FAILURE_DAYS:
        days = int(round(opt.recent_failure_days))
        note = f"Avoided: a failure was reported {days} day{'' if days == 1 else 's'} ago."
    elif not chosen and _stale(node):
        note = "Not chosen: few or no recent reports."
    elif not chosen:
        note = "Not chosen: more expected lost minutes."
    return {"charger_id": node.id, "name": _name(node), "lat": node.charger.lat if node.charger else None,
            "lon": node.charger.lon if node.charger else None, "site_id": node.site_id,
            "road_km": round(opt.leg_km, 1), "drive_min": int(round(opt.leg_min)), "p_works": round_prob(node.p_works),
            "evidence": _evidence(node), "recent_failure_days": None if opt.recent_failure_days is None
            else int(round(opt.recent_failure_days)), "confidence_as_primary": round_prob(ev.confidence),
            "expected_lost_min": int(round(ev.expected_lost)),
            "expected_lost_rs": rupees(ev.expected_lost, inp.earning_rs_per_hour), "chosen": chosen, "note": note}


def _decide(inp: RideInput, ctx: Ctx, best: ChainEval, seed: int) -> tuple[str, str]:
    """(decision, reason): the reason is built from the engine's own numbers."""
    target, shown = inp.target, round_prob(best.confidence)
    stale = [inp.options[i].node for i in best.chain if _stale(inp.options[i].node)]
    if inp.vehicle.trips_calibrated < MIN_CALIBRATED_TRIPS:
        return "INSUFFICIENT_EVIDENCE", ("This vehicle has no calibrated trips yet, so its real range is unknown. "
                                         "Log one or two trips before relying on a ride check.")
    if shown < target - 1e-9:
        return "NO_GO", (f"Only {shown:.0%} confidence of completing the ride and reaching a working charger after "
                         f"the drop (target {target:.0%}). Charge first, or decline this ride.")
    if stale:
        pess = evaluate_chain(ctx, inp, best.chain, np.random.default_rng(seed + 1), {n.id: n.p_lo for n in stale})
        if round_prob(pess.confidence) < target - 1e-9:
            names = ", ".join(sorted({_name(n) for n in stale}))
            return "INSUFFICIENT_EVIDENCE", (
                f"The plan only meets the {target:.0%} target if chargers with stale or missing reports ({names}) work; "
                f"read pessimistically, confidence falls to about {round_prob(pess.confidence):.0%}.")
    return "GO", (f"{shown:.0%} confidence of completing the ride and reaching a working charger after the drop "
                  f"(target {target:.0%}).")


def ride_check(inp: RideInput, *, seed: int = 0, cfg: ForecastConfig = ForecastConfig(),
               compute_levers: bool = True) -> dict:
    t0 = time.perf_counter()
    rng = np.random.default_rng(seed)
    ctx = build_ctx(inp, rng, cfg)
    runs, ride_km = ctx.runs, inp.ride.distance_km
    drop_soc = np.clip(ctx.start_soc - runs.used_at_km(ride_km), 0.0, 100.0)
    q = [int(round(float(v))) for v in np.quantile(drop_soc, [0.1, 0.5, 0.9])]
    ride = {"distance_km": round(ride_km, 1), "pickup_km": round(inp.pickup_km, 1),
            "duration_min": int(round(ctx.minutes_at(ride_km))),
            "p_complete": round_prob(float(np.mean(drop_soc >= RES_STOP))),
            "drop_soc": {"p10": q[0], "p50": q[1], "p90": q[2]}}
    if not inp.options:
        return _no_charger(inp, ride, t0)
    best, evals = choose_chain(ctx, inp, seed)
    decision, reason = _decide(inp, ctx, best, seed)
    primary = {e.chain[0]: e for e in evals}
    lost = best.expected_lost
    out = {
        "decision": decision, "confidence": round_prob(best.confidence), "reason": reason, "ride": ride,
        "after_drop": {
            "charger": _option_row(inp, best.chain[0], best, True),
            "backups": [{"charger_id": inp.options[i].node.id, "name": _name(inp.options[i].node),
                         "same_site_as_primary": inp.options[i].node.site_id == inp.options[best.chain[0]].node.site_id}
                        for i in best.chain[1:]],
            "expected_lost_min": int(round(lost)), "expected_lost_rs": rupees(lost, inp.earning_rs_per_hour),
            "minutes": [{"part": k, "label": PART_LABELS[k], "minutes": round(float(v.mean()), 1)}
                        for k, v in best.parts.items()]},
        "considered": [_option_row(inp, i, primary[i], i == best.chain[0]) for i in range(len(inp.options))],
        "bingo_line": ride_bingo_line(ctx, inp, rng),
        "levers": [], "earning_rs_per_hour": inp.earning_rs_per_hour,
    }
    if compute_levers:
        out["levers"] = _levers(inp, ctx, best, decision, seed, cfg)
    out["compute_ms"] = int(round(1000 * (time.perf_counter() - t0)))
    return out


def _levers(inp: RideInput, ctx: Ctx, best: ChainEval, decision: str, seed: int, cfg: ForecastConfig) -> list[dict]:
    levers = []
    base = best.confidence
    if inp.vehicle.ac_on:
        alt = ride_check(replace(inp, vehicle=replace(inp.vehicle, ac_on=False)), seed=seed, cfg=cfg, compute_levers=False)
        flips = (alt["decision"] == "GO") != (decision == "GO")
        if alt["confidence"] >= round_prob(base) + LEVER_MIN_GAIN or flips:
            levers.append({"lever_id": "ac_off", "label": "Ride with the AC off", "new_confidence": alt["confidence"],
                           "time_cost_min": 0, "rupee_cost": 0})
    for i in best.chain:
        node = inp.options[i].node
        if not _stale(node):
            continue
        e = evaluate_chain(ctx, inp, best.chain, np.random.default_rng(seed + 3), {node.id: node.p_hi})
        if e.confidence > base + LEVER_MIN_GAIN or (decision == "INSUFFICIENT_EVIDENCE" and
                                                     round_prob(e.confidence) >= inp.target - 1e-9):
            levers.append({"lever_id": f"verify_{node.id}", "label": f"Ask for a recent report on {_name(node)} "
                           "(confidence if it is confirmed working)", "new_confidence": round_prob(e.confidence),
                           "time_cost_min": 0, "rupee_cost": 0})
    return levers


def _no_charger(inp: RideInput, ride: dict, t0: float) -> dict:
    """No charger is known near the drop: the after-drop plan cannot be checked, so the engine refuses a GO."""
    return {"decision": "NO_GO", "confidence": 0.0, "reason": "No known charger near the drop, so the after-drop "
            "plan cannot be checked. Decline, or charge before taking the ride.", "ride": ride,
            "after_drop": None, "considered": [], "bingo_line": [], "levers": [],
            "earning_rs_per_hour": inp.earning_rs_per_hour, "compute_ms": int(round(1000 * (time.perf_counter() - t0)))}
