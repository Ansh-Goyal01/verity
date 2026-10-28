"""Dispatch: candidate charging plans, backup evaluation, Bingo Line, point of no return, levers.

CLAUDE.md rule: the graph never decides alone. A leg graph (LEG edges stored per battery band, in
``InMemoryGraph`` or Neo4j) feeds Yen's k-shortest-paths to PROPOSE candidate charging chains; every
candidate is then SCORED by the vectorised Monte Carlo with backups: when the planned charger fails (or
cannot be reached) the run diverts to a reachable, working alternate ahead; otherwise it is stranded.

* Bingo SoC at km x = lowest SoC from which P(reach the next planned charger OR a reachable working
  alternate OR the destination, keeping the reserve) >= the driver's target confidence.
* Point of no return = first km from which returning to the last charger (or the origin) behind is no
  longer possible with probability >= target.
* Decision: GO / NO_GO from the success probability; INSUFFICIENT_EVIDENCE (refuse, golden rule 6) when
  the vehicle has no calibrated trips or when the decision would flip under pessimistic values for chargers
  whose evidence is stale or missing.

All numbers marked "approximate - verify" are planning assumptions, not measurements.
"""

from __future__ import annotations

import heapq
import logging
import time
import uuid
from dataclasses import dataclass, field, replace
from typing import Sequence

import numpy as np

from .calibrate import Posterior
from .chargers import RouteCharger
from .forecast import (
    FREE_SPEED_MPS,
    RESERVE_PCT,
    ForecastConfig,
    ForecastRuns,
    PlannedRoute,
    round_prob,
    simulate_runs,
)
from .physics import total_mass_kg
from .schemas import ChargerRecord
from .trust.engine import ChargerTrust
from .trust.graph import ChargerGraph, InMemoryGraph, Leg
from .vehicles import get_prior

Array = np.ndarray
log = logging.getLogger(__name__)

RES_STOP = 5.0  # SoC that must remain on arrival at a charger (approximate - verify)
CHARGE_CAP = 80.0  # fast charging is planned only up to this SoC (approximate - verify)
SAFETY_PP = 3.0  # margin on top of the 90th-percentile energy of the next leg
ARRIVE_GUESS_PCT = 20.0  # typical arrival SoC, only used to weight edges when proposing candidates
STOP_OVERHEAD_MIN = 6.0  # parking, plugging in, paying (approximate - verify)
FAILURE_PENALTY_MIN = 45.0  # minutes lost, on average, when a planned charger turns out broken (edge weights)
FAILED_STOP_MIN = 20.0  # minutes spent reaching, trying and giving up on a broken charger (approximate - verify)
ALT_AHEAD_KM = 60.0  # an alternate must lie within this many km ahead (sparse real network; approximate - verify)
MAX_ALTS = 3
ASSUMED_CHARGER_KW = 25.0  # when a charger's power is not listed (approximate - verify)
VEHICLE_MAX_KW = {"small_ecar": 30.0, "compact_esuv": 50.0, "e_scooter": 3.0}  # approximate - verify
TAPER = 0.85  # average-power factor for charging up to 80 %
K_CANDIDATES = 6
MAX_BAND = 100
LEVER_MIN_GAIN = 0.04
CRUISE_LEVER_MPS = 80.0 / 3.6
EXTRA_CHARGE_MIN = 10.0
BINGO_SUBSAMPLE = 1000
MIN_CALIBRATED_TRIPS = 1
MIN_STOP_MINUTES = 4.0  # a stop that adds less charging than this is not worth its 6-minute overhead

BREAKDOWN_LABELS = {"rolling": "Rolling resistance", "aero": "Air drag", "grade": "Climbs", "inertia": "Stop-and-go",
                    "regen": "Regenerative braking", "aux": "AC and electronics", "charging": "Charging stops"}


# --- data ---------------------------------------------------------------------------------------------------


@dataclass(frozen=True, eq=False)
class Node:
    id: str
    km: float
    p_works: float
    p_lo: float
    p_hi: float
    kw: float | None
    charger: ChargerRecord | None = None
    trust: ChargerTrust | None = None


@dataclass(frozen=True, eq=False)
class Stop:
    node: Node
    charge_to: float
    alts: tuple[Node, ...]


@dataclass(frozen=True, eq=False)
class VehicleContext:
    vehicle_id: str
    class_key: str
    post: Posterior
    passengers: int
    ac_on: bool
    trips_calibrated: int
    cargo_kg: float = 0.0


@dataclass(frozen=True, eq=False)
class Ctx:
    """Everything a plan evaluation needs about one vehicle on one route."""

    runs: ForecastRuns
    route: PlannedRoute
    vehicle: VehicleContext
    start_soc: float
    target: float
    usable_kwh: float
    drive_cum_min: Array  # cumulative drive minutes at the end of each segment

    @property
    def total_km(self) -> float:
        return float(self.runs.km_end[-1])

    def minutes_at(self, km: float) -> float:
        return float(np.interp(km, np.concatenate([[0.0], self.runs.km_end]), np.concatenate([[0.0], self.drive_cum_min])))

    def min_per_pp(self, node: Node) -> float:
        """Minutes to add one SoC point at this charger (power-, vehicle- and heat-limited)."""
        temp = float(self.route.temp_c.mean())
        heat = 1.0 - 0.2 * float(np.clip((temp - 30.0) / 10.0, 0.0, 1.0))  # heat slows charging 10-30 % (PLAN s3)
        kw = min(VEHICLE_MAX_KW[self.vehicle.class_key], node.kw or ASSUMED_CHARGER_KW) * TAPER * heat
        return self.usable_kwh / 100.0 / kw * 60.0


@dataclass
class PlanEval:
    success: Array
    arrival: Array
    minutes: Array
    gains: list[Array]
    stop_arrival: list[Array]  # SoC on arrival at each planned stop (NaN where the run never got there)
    stop_alive_in: list[Array]
    soc_in: list[Array]
    used_in: list[Array]
    trace: Array
    ctx: Ctx

    @property
    def confidence(self) -> float:
        return float(self.success.mean())

    @property
    def mean_minutes(self) -> float:
        return float(self.minutes.mean())


# --- nodes, graph, candidates -----------------------------------------------------------------------------


def make_nodes(route_chargers: Sequence[RouteCharger], trust: dict[str, ChargerTrust], *, trust_aware: bool = True,
               prior_p: float = 0.82) -> list[Node]:
    """Route chargers as planning nodes. A reliability-blind planner (E3) assumes every charger works."""
    nodes = []
    for rc in route_chargers:
        t = trust.get(rc.charger.id)
        if not trust_aware:
            p, lo, hi = 1.0, 1.0, 1.0
        elif t is not None:
            p, lo, hi = t.p_works, t.p_lo, t.p_hi
        else:
            p, lo, hi = prior_p, max(prior_p - 0.3, 0.05), min(prior_p + 0.1, 1.0)
        kw = max((c.kw for c in rc.charger.connectors), default=None)
        nodes.append(Node(rc.charger.id, rc.km_along, float(p), float(lo), float(hi), kw, rc.charger, t))
    return sorted(nodes, key=lambda n: n.km)


def drive_cum_minutes(route: PlannedRoute, highway_speed_mps: float | None = None) -> Array:
    speeds = np.asarray(FREE_SPEED_MPS[route.vtype])[route.road_class]
    if highway_speed_mps is not None:
        speeds = np.where(route.road_class == 0, min(highway_speed_mps, speeds.max()), speeds)
    return np.cumsum(route.length_m / speeds) / 60.0


def _dijkstra(adj: dict[str, dict[str, float]], src: str, dst: str, banned_nodes: set[str],
              banned_edges: set[tuple[str, str]]) -> tuple[float, list[str]] | None:
    heap: list[tuple[float, str, list[str]]] = [(0.0, src, [src])]
    seen: set[str] = set()
    while heap:
        cost, node, path = heapq.heappop(heap)
        if node == dst:
            return cost, path
        if node in seen:
            continue
        seen.add(node)
        for nxt, w in adj.get(node, {}).items():
            if nxt in banned_nodes or (node, nxt) in banned_edges or nxt in seen:
                continue
            heapq.heappush(heap, (cost + w, nxt, path + [nxt]))
    return None


def k_shortest_paths(adj: dict[str, dict[str, float]], src: str, dst: str, k: int) -> list[tuple[float, list[str]]]:
    """Yen's algorithm for the k lowest-cost loopless paths."""
    first = _dijkstra(adj, src, dst, set(), set())
    if first is None:
        return []
    found = [first]
    candidates: list[tuple[float, list[str]]] = []

    def path_cost(p: list[str]) -> float:
        return sum(adj[a][b] for a, b in zip(p[:-1], p[1:]))

    for _ in range(1, k):
        prev = found[-1][1]
        for i in range(len(prev) - 1):
            root = prev[: i + 1]
            banned_edges = {(p[i], p[i + 1]) for _, p in found if p[: i + 1] == root and len(p) > i + 1}
            spur = _dijkstra(adj, prev[i], dst, set(root[:-1]), banned_edges)
            if spur is not None:
                cand = (path_cost(root) + spur[0], root[:-1] + spur[1])
                if cand not in candidates and all(cand[1] != p for _, p in found):
                    candidates.append(cand)
        if not candidates:
            break
        candidates.sort(key=lambda c: (c[0], c[1]))
        found.append(candidates.pop(0))
    return found


def build_leg_graph(ctx: Ctx, nodes: Sequence[Node], *, trust_aware: bool, graph: ChargerGraph | None = None,
                    scope: str = "") -> tuple[ChargerGraph, dict[str, Array]]:
    """LEG edges per battery band between START, chargers and END (only legs that are feasible). They are written
    to ``graph`` (a fresh in-memory one by default) in ONE batch under ``scope``."""
    graph = InMemoryGraph() if graph is None else graph
    legs: list[Leg] = []
    kms = [0.0] + [n.km for n in nodes] + [ctx.total_km]
    ids = ["START"] + [n.id for n in nodes] + ["END"]
    used = {i: u for i, u in zip(ids, [np.zeros(ctx.runs.n_runs)] + [ctx.runs.used_at_km(n.km) for n in nodes]
                                 + [ctx.runs.total_used])}
    by_id = {n.id: n for n in nodes}
    for a in range(len(ids) - 1):
        for b in range(a + 1, len(ids)):
            energy = used[ids[b]] - used[ids[a]]
            end = ids[b] == "END"
            need_after = (RESERVE_PCT if end else RES_STOP)
            q90 = float(np.quantile(energy, 0.9))
            limit = ctx.start_soc if ids[a] == "START" else CHARGE_CAP
            if q90 + need_after + (0.0 if ids[a] == "START" else SAFETY_PP) > limit:
                continue
            drive = ctx.minutes_at(kms[b]) - ctx.minutes_at(kms[a])
            minutes = drive
            if ids[a] != "START":
                node = by_id[ids[a]]
                charge_pp = max(q90 + need_after + SAFETY_PP - ARRIVE_GUESS_PCT, 0.0)
                minutes += STOP_OVERHEAD_MIN + charge_pp * ctx.min_per_pp(node)
                if trust_aware:
                    minutes += FAILURE_PENALTY_MIN * (1.0 - node.p_works)
            for lo in range(0, MAX_BAND, 10):  # one LEG per battery band, never one fixed probability
                mid = lo + 5.0
                p_reach = float(np.mean(mid - energy >= need_after))
                legs.append(Leg(ids[a], ids[b], lo, lo + 10, p_reach, float(minutes)))
    graph.add_legs(legs, scope)
    return graph, used


def _adjacency(graph: ChargerGraph, ids: Sequence[str], scope: str = "") -> dict[str, dict[str, float]]:
    adj: dict[str, dict[str, float]] = {i: {} for i in ids}
    for leg in graph.legs(scope):  # one read for the whole plan: a Neo4j round trip per node would dominate
        if leg.from_id in adj:
            adj[leg.from_id][leg.to_id] = leg.minutes
    return adj


def alts_for(node: Node, nodes: Sequence[Node], exclude: set[str]) -> tuple[Node, ...]:
    ahead = [n for n in nodes if node.km < n.km <= node.km + ALT_AHEAD_KM and n.id not in exclude]
    return tuple(sorted(ahead, key=lambda n: n.km)[:MAX_ALTS])


def make_stops(ctx: Ctx, path: Sequence[Node], all_nodes: Sequence[Node], extra_pp: float = 0.0) -> list[Stop]:
    on_path = {n.id for n in path}
    stops = []
    for i, node in enumerate(path):
        nxt_km = path[i + 1].km if i + 1 < len(path) else ctx.total_km
        res = RES_STOP if i + 1 < len(path) else RESERVE_PCT
        energy = ctx.runs.used_at_km(nxt_km) - ctx.runs.used_at_km(node.km)
        target = float(np.clip(res + SAFETY_PP + np.quantile(energy, 0.9) + extra_pp, 0.0, 100.0))
        stops.append(Stop(node, min(target, 100.0), alts_for(node, all_nodes, on_path)))
    return stops


def _drop_best_effort(graph: ChargerGraph, scope: str) -> None:
    """Leftover LEGs must never fail a plan or hide its real error: the service's periodic sweep removes them."""
    try:
        graph.drop_legs(scope)
    except Exception as exc:  # noqa: BLE001 - logged by class name only
        log.warning("could not drop the legs of scope %s (%s); the periodic sweep will", scope, type(exc).__name__)


def propose_paths(ctx: Ctx, nodes: Sequence[Node], *, trust_aware: bool, k: int = K_CANDIDATES,
                  graph: ChargerGraph | None = None) -> tuple[list[list[Node]], ChargerGraph]:
    """Candidate charging chains. With an external ``graph`` (Neo4j) the LEGs live there under a fresh scope for
    the duration of this call only and are dropped afterwards, so concurrent plans never see each other's legs."""
    scope = uuid.uuid4().hex if graph is not None else ""
    try:
        leg_graph, _ = build_leg_graph(ctx, nodes, trust_aware=trust_aware, graph=graph, scope=scope)
        ids = ["START"] + [n.id for n in nodes] + ["END"]
        by_id = {n.id: n for n in nodes}
        paths = [[by_id[i] for i in p[1:-1]]
                 for _, p in k_shortest_paths(_adjacency(leg_graph, ids, scope), "START", "END", k)]
    finally:
        if graph is not None:
            _drop_best_effort(graph, scope)
    return paths, leg_graph


# --- Monte Carlo with backups -----------------------------------------------------------------------------


def evaluate_plan(ctx: Ctx, stops: Sequence[Stop], rng: np.random.Generator, *, use_alts: bool = True,
                  p_override: dict[str, float] | None = None) -> PlanEval:
    """Run every Monte Carlo run through the plan: planned charger, else a reachable working alternate ahead,
    else carry on if the next stop is still reachable, else stranded."""
    runs, n = ctx.runs, ctx.runs.n_runs
    p_of = lambda node: (p_override or {}).get(node.id, node.p_works)  # noqa: E731
    soc = np.full(n, ctx.start_soc)
    pos_km, pos_used = np.zeros(n), np.zeros(n)
    alive = np.ones(n, dtype=bool)
    minutes = np.full(n, ctx.minutes_at(ctx.total_km))
    gains, stop_arrival, alive_in, soc_in, used_in = [], [], [], [], []
    for st in stops:
        node = st.node
        soc_in.append(soc.copy()); used_in.append(pos_used.copy()); alive_in.append(alive.copy())
        behind = alive & (pos_km >= node.km - 1e-9)
        active = alive & ~behind
        used_st = runs.used_at_km(node.km)
        arr = soc - (used_st - pos_used)
        stop_arrival.append(np.where(active, arr, np.nan))
        works = rng.random(n) < p_of(node)
        ok = active & (arr >= RES_STOP) & works
        minutes = minutes + np.where(active & (arr >= RES_STOP) & ~works, FAILED_STOP_MIN, 0.0)
        cur_km, cur_used, cur_soc = (np.where(ok, v, w) for v, w in ((node.km, pos_km), (used_st, pos_used), (arr, soc)))
        need = active & ~ok
        if use_alts:
            for alt in st.alts:
                used_alt = runs.used_at_km(alt.km)
                arr_alt = soc - (used_alt - pos_used)
                can = need & (arr_alt >= RES_STOP) & (rng.random(n) < p_of(alt))
                cur_km, cur_used, cur_soc = (np.where(can, v, w) for v, w in
                                             ((alt.km, cur_km), (used_alt, cur_used), (arr_alt, cur_soc)))
                ok |= can
                need &= ~can
        alive = alive & ~(need & (arr < RES_STOP))  # could not even reach the planned charger: stranded
        gain = np.where(ok, np.clip(st.charge_to - cur_soc, 0.0, 100.0 - cur_soc), 0.0)
        soc = np.where(ok, cur_soc + gain, soc)
        pos_km, pos_used = np.where(ok, cur_km, pos_km), np.where(ok, cur_used, pos_used)
        minutes = minutes + np.where(ok, STOP_OVERHEAD_MIN + gain * ctx.min_per_pp(node), 0.0)
        gains.append(gain)
    arrival = soc - (runs.total_used - pos_used)
    success = alive & (arrival >= RESERVE_PCT)
    trace = ctx.start_soc - runs.soc_used_cum
    for st, gain in zip(stops, gains):
        trace = trace + np.where(runs.km_end >= st.node.km, 1.0, 0.0)[None, :] * gain[:, None]
    return PlanEval(success, arrival, minutes, gains, stop_arrival, alive_in, soc_in, used_in, trace, ctx)


# --- Bingo Line, point of no return -----------------------------------------------------------------------


def bingo_line(ctx: Ctx, stops: Sequence[Stop], nodes: Sequence[Node], rng: np.random.Generator) -> list[dict]:
    """Per segment end: the lowest SoC from which a planned charger, a working alternate or the destination
    is still reachable with probability >= the target."""
    runs = ctx.runs
    sub = rng.choice(runs.n_runs, size=min(BINGO_SUBSAMPLE, runs.n_runs), replace=False)
    grid = np.arange(0.0, 100.5, 1.0)
    node_used = {n.id: runs.used_at_km(n.km)[sub] for n in nodes}
    dest_used = runs.total_used[sub]
    out = []
    for si, km in enumerate(runs.km_end):
        used_x = runs.soc_used_cum[sub, si]
        planned = next((s.node for s in stops if s.node.km > km), None)
        cands: list[tuple[Array, float, float]] = [(dest_used - used_x, 1.0, RESERVE_PCT)]
        for n in nodes:
            if km < n.km <= km + ALT_AHEAD_KM or (planned is not None and n.id == planned.id):
                cands.append((node_used[n.id] - used_x, n.p_works, RES_STOP))
        energy = np.stack([c[0] for c in cands])  # (C, n)
        p = np.array([c[1] for c in cands])[:, None, None]
        res = np.array([c[2] for c in cands])[:, None, None]
        reach = (grid[None, None, :] - energy[:, :, None]) >= res  # (C, n, G)
        fail = np.prod(1.0 - p * reach, axis=0)  # (n, G): probability that every candidate fails
        prob = 1.0 - fail.mean(axis=0)  # (G,)
        ok = np.nonzero(prob >= ctx.target)[0]
        bingo = float(grid[ok[0]]) if len(ok) else 100.0
        out.append({"km": round(float(km), 1), "bingo_soc_pct": int(round(bingo))})
    return out


def point_of_no_return_km(ev: PlanEval, stops: Sequence[Stop]) -> float:
    """First km from which getting back to the last charger (or the origin) behind is no longer >= target."""
    ctx, runs = ev.ctx, ev.ctx.runs
    last_used = np.zeros(runs.n_runs)
    stop_ptr = 0
    for si, km in enumerate(runs.km_end):
        while stop_ptr < len(stops) and stops[stop_ptr].node.km <= km:
            last_used = runs.used_at_km(stops[stop_ptr].node.km)
            stop_ptr += 1
        back = runs.soc_used_cum[:, si] - last_used
        p_return = float(np.mean(ev.trace[:, si] - np.maximum(back, 0.0) >= RES_STOP))
        if p_return < ctx.target:
            return float(km)
    return float(runs.km_end[-1])


# --- result ---------------------------------------------------------------------------------------------------


@dataclass
class DispatchResult:
    vehicle_id: str
    decision: str  # GO | NO_GO | INSUFFICIENT_EVIDENCE
    confidence: float  # rounded to 5 %
    confidence_raw: float
    arrival_soc: dict
    distance_km: float
    duration_min: int
    stops: list[dict]
    alternates: list[dict]
    bingo_line: list[dict]
    point_of_no_return_km: float
    breakdown: list[dict]
    levers: list[dict]
    risk_sources: list[dict]
    refusal_reason: str | None
    compute_ms: int
    state: "PlanState | None" = field(default=None, repr=False)


@dataclass(frozen=True, eq=False)
class PlanState:
    """What the live forecast update needs later."""

    ctx: Ctx
    stops: tuple[Stop, ...]
    ev: PlanEval
    bingo: tuple[dict, ...]
    nodes: tuple[Node, ...]


def _evidence(node: Node) -> dict:
    t = node.trust
    return {"n_reports": t.n_reports if t else 0, "days_since_last": None if not t or t.days_since_last is None
            else int(round(t.days_since_last)), "trust_level": t.trust_level if t else "unknown"}


def _stale(node: Node) -> bool:
    return node.trust is None or node.trust.trust_level in ("unknown", "low")


def _build_ctx(vehicle: VehicleContext, route: PlannedRoute, start_soc: float, target: float, rng: np.random.Generator,
               cfg: ForecastConfig, highway_speed_mps: float | None = None) -> Ctx:
    prior = get_prior(vehicle.class_key)
    mass = total_mass_kg(prior.base.curb_mass_kg, vehicle.passengers, vehicle.cargo_kg)
    runs = simulate_runs(vehicle.post, route, mass_kg=mass, ac_on=vehicle.ac_on, rng=rng, cfg=cfg,
                         highway_speed_mps=highway_speed_mps)
    usable = float(vehicle.post.nominal_kwh * vehicle.post.summary(n=1000)["usable_frac"][0])
    return Ctx(runs, route, vehicle, float(start_soc), float(target), usable, drive_cum_minutes(route, highway_speed_mps))


def _choose(ctx: Ctx, nodes: Sequence[Node], rng: np.random.Generator, *, trust_aware: bool,
            use_alts: bool = True, graph: ChargerGraph | None = None
            ) -> tuple[list[Stop], PlanEval, list[tuple[list[Stop], PlanEval]]]:
    paths, _ = propose_paths(ctx, nodes, trust_aware=trust_aware, graph=graph)
    if [] not in paths:
        paths.append([])  # the no-stop plan is always scored, even when infeasible (then it simply fails)
    scored = []
    for path in paths:
        stops = make_stops(ctx, path, nodes)
        ev = evaluate_plan(ctx, stops, rng, use_alts=use_alts)
        scored.append((stops, ev))
        trivial = {s.node.id for s, g in zip(stops, ev.gains) if float(g.mean()) * ctx.min_per_pp(s.node) < MIN_STOP_MINUTES}
        if trivial:  # also score the plan without the pointless top-ups
            kept = [s.node for s in stops if s.node.id not in trivial]
            pruned = make_stops(ctx, kept, nodes)
            scored.append((pruned, evaluate_plan(ctx, pruned, rng, use_alts=use_alts)))
    meeting = [s for s in scored if s[1].confidence >= ctx.target]
    # With nothing meeting the target, prefer the most reliable plan, but compare confidence at the Monte
    # Carlo's own resolution (2 points) so a 3-stop plan does not beat a 1-stop plan on noise alone.
    best = (min(meeting, key=lambda s: (s[1].mean_minutes, len(s[0]))) if meeting
            else max(scored, key=lambda s: (round(s[1].confidence / 0.02), -s[1].mean_minutes)))
    return best[0], best[1], scored


def plan_only(vehicle: VehicleContext, route: PlannedRoute, route_chargers: Sequence[RouteCharger],
              trust: dict[str, ChargerTrust], *, start_soc: float, target_confidence: float = 0.9, seed: int = 0,
              cfg: ForecastConfig = ForecastConfig(), trust_aware: bool = True,
              graph: ChargerGraph | None = None) -> tuple[list[Stop], PlanEval, Ctx]:
    """Propose and score candidate chains and return the chosen plan (no Bingo Line, levers or texts):
    the light path used by the simulation experiments E3 and E4."""
    rng = np.random.default_rng(seed)
    ctx = _build_ctx(vehicle, route, start_soc, target_confidence, rng, cfg)
    stops, ev, _ = _choose(ctx, make_nodes(route_chargers, trust, trust_aware=trust_aware), rng, trust_aware=trust_aware,
                           graph=graph)
    return stops, ev, ctx


def dispatch(
    vehicle: VehicleContext,
    route: PlannedRoute,
    route_chargers: Sequence[RouteCharger],
    trust: dict[str, ChargerTrust],
    *,
    start_soc: float,
    target_confidence: float = 0.9,
    polyline: list | None = None,
    seed: int = 0,
    cfg: ForecastConfig = ForecastConfig(),
    trust_aware: bool = True,
    other_vehicles: Sequence[tuple[VehicleContext, float]] = (),
    compute_levers: bool = True,
    highway_speed_mps: float | None = None,
    graph: ChargerGraph | None = None,
) -> DispatchResult:
    t0 = time.perf_counter()
    rng = np.random.default_rng(seed)
    ctx = _build_ctx(vehicle, route, start_soc, target_confidence, rng, cfg, highway_speed_mps)
    nodes = make_nodes(route_chargers, trust, trust_aware=trust_aware)
    stops, ev, _ = _choose(ctx, nodes, rng, trust_aware=trust_aware, graph=graph)
    conf = ev.confidence

    # evidence check: would the decision flip if stale/unknown chargers were at their pessimistic value?
    stale = [s.node for s in stops if _stale(s.node)] + [a for s in stops for a in s.alts if _stale(a)]
    pess = {n.id: n.p_lo for n in stale}
    conf_pess = evaluate_plan(ctx, stops, np.random.default_rng(seed + 1), p_override=pess).confidence if pess else conf
    thin_vehicle = vehicle.trips_calibrated < MIN_CALIBRATED_TRIPS
    # The decision follows the DISPLAYED confidence (nearest 5 %, golden rule 5), so the UI never shows
    # "90 % / NO_GO" for a 90 % target; 2.5 points is below the Monte Carlo's own resolution anyway.
    shown, shown_pess = round_prob(conf), round_prob(conf_pess)
    decision, refusal = "GO" if shown >= target_confidence - 1e-9 else "NO_GO", None
    if thin_vehicle:
        decision = "INSUFFICIENT_EVIDENCE"
        refusal = ("This vehicle has no calibrated trips yet, so its real range is unknown. Log one or two short "
                   "trips with the logger before planning an outstation booking.")
    elif decision == "GO" and shown_pess < target_confidence - 1e-9 and trust_aware:
        weak = ", ".join(sorted({n.charger.name if n.charger else n.id for n in stale}))
        decision = "INSUFFICIENT_EVIDENCE"
        refusal = (f"The plan only meets the {target_confidence:.0%} target if chargers with stale or missing reports "
                   f"({weak}) work; with a pessimistic reading of them confidence falls to about {shown_pess:.0%}. "
                   "Ask the community to verify them before departure.")

    result = _assemble(vehicle, ctx, stops, ev, nodes, decision, refusal, polyline or [], rng)
    if compute_levers:
        result.levers = _levers(vehicle, route, route_chargers, trust, ctx, stops, ev, nodes, start_soc, target_confidence,
                                seed, cfg, trust_aware, other_vehicles, stale, conf, decision, graph)
    result.compute_ms = int(round(1000 * (time.perf_counter() - t0)))
    return result


def _assemble(vehicle: VehicleContext, ctx: Ctx, stops: Sequence[Stop], ev: PlanEval, nodes: Sequence[Node],
              decision: str, refusal: str | None, polyline: list, rng: np.random.Generator) -> DispatchResult:
    runs = ctx.runs
    arrival = np.clip(ev.arrival, 0.0, 100.0)
    # breakdown from the central runs, so that start + sum(terms) == arrival p50 exactly (after rounding)
    lo, hi = np.quantile(ev.arrival, [0.4, 0.6])
    central = (ev.arrival >= lo) & (ev.arrival <= hi)
    central = central if central.any() else np.ones_like(central)
    t = {k: float(v[central].mean()) for k, v in runs.terms_soc.items()}
    terms = {"rolling": -t["rolling"], "aero": -t["aero"], "grade": -t["grade"], "inertia": -t["inertia"],
             "regen": -t["regen"], "aux": -(t["aux"] + t["base"]),
             "charging": float(sum(g[central].mean() for g in ev.gains)) if ev.gains else 0.0}
    p50 = int(round(ctx.start_soc + sum(terms.values())))
    ints = {k: int(round(v)) for k, v in terms.items()}
    residual = (p50 - int(round(ctx.start_soc))) - sum(ints.values())
    if residual:
        big = max((k for k in ints if k != "charging"), key=lambda k: abs(terms[k]))
        ints[big] += residual
    breakdown = [{"term": k, "label": BREAKDOWN_LABELS[k], "soc_pct": ints[k]} for k in BREAKDOWN_LABELS]
    p10, p90 = (int(round(float(q))) for q in np.quantile(arrival, [0.1, 0.9]))
    arrival_soc = {"p10": min(p10, p50), "p50": p50, "p90": max(p90, p50)}

    stop_dicts, alt_dicts = [], []
    for i, st in enumerate(stops):
        arr = ev.stop_arrival[i]
        n = st.node
        stop_dicts.append({
            "charger_id": n.id, "name": n.charger.name if n.charger else n.id,
            "lat": n.charger.lat if n.charger else None, "lon": n.charger.lon if n.charger else None,
            "km_along_route": round(n.km, 1),
            "arrive_soc_p50": int(round(float(np.nanmedian(arr)))) if np.isfinite(arr).any() else 0,
            "charge_minutes": int(round(float(ev.gains[i].mean()) * ctx.min_per_pp(n) + 0.0)),
            "p_works": round_prob(n.p_works), "evidence": _evidence(n),
            "power_assumed": n.kw is None,
        })
        for alt in st.alts:
            reach = float(np.mean(ev.soc_in[i] - (runs.used_at_km(alt.km) - ev.used_in[i]) >= RES_STOP))
            alt_dicts.append({"for_stop_index": i, "charger_id": alt.id,
                              "name": alt.charger.name if alt.charger else alt.id,
                              "lat": alt.charger.lat if alt.charger else None,
                              "lon": alt.charger.lon if alt.charger else None,
                              "p_reach": round_prob(reach), "p_works": round_prob(alt.p_works)})
    bingo = bingo_line(ctx, stops, nodes, rng)
    exp = np.quantile(ev.trace, [0.1, 0.5, 0.9], axis=0)
    for i, row in enumerate(bingo):
        row.update({"expected_soc_pct": int(round(float(exp[1, i]))), "expected_soc_p10": int(round(float(exp[0, i]))),
                    "expected_soc_p90": int(round(float(exp[2, i])))})
    bingo.insert(0, {"km": 0.0, "bingo_soc_pct": bingo[0]["bingo_soc_pct"], "expected_soc_pct": int(round(ctx.start_soc)),
                     "expected_soc_p10": int(round(ctx.start_soc)), "expected_soc_p90": int(round(ctx.start_soc))})
    risks = _risk_sources(vehicle, ctx, stops)
    state = PlanState(ctx, tuple(stops), ev, tuple(bingo), tuple(nodes))
    return DispatchResult(
        vehicle.vehicle_id, decision, round_prob(ev.confidence), ev.confidence, arrival_soc, round(ctx.total_km, 1),
        int(round(ev.mean_minutes)), stop_dicts, alt_dicts, bingo, point_of_no_return_km(ev, stops), breakdown, [],
        risks, refusal, 0, state)


def _risk_sources(vehicle: VehicleContext, ctx: Ctx, stops: Sequence[Stop]) -> list[dict]:
    risks = []
    for st in stops:
        n = st.node
        name = n.charger.name if n.charger else n.id
        ev_ = _evidence(n)
        if ev_["trust_level"] in ("unknown", "low"):
            what = "has no recent reports" if ev_["n_reports"] == 0 else "has few recent reports"
            risks.append({"kind": "unknown", "label": f"Charger {name} {what}"})
        if n.kw is None:
            risks.append({"kind": "unknown", "label": f"Charging power at {name} is not listed; assumed {ASSUMED_CHARGER_KW:.0f} kW"})
    if vehicle.trips_calibrated < 3:
        risks.append({"kind": "unknown", "label": f"Vehicle has only {vehicle.trips_calibrated} calibrated trips"})
    if float(np.mean(ctx.route.road_class >= 1)) > 0.05:
        risks.append({"kind": "unpredictable", "label": "Traffic on arterial and urban stretches is highly variable"})
    if float(ctx.route.temp_c.mean()) > 35.0:
        risks.append({"kind": "unpredictable", "label": "Heat raises AC load and slows charging"})
    return risks


def _levers(vehicle, route, route_chargers, trust, ctx, stops, ev, nodes, start_soc, target, seed, cfg, trust_aware,
            others, stale, base_conf, decision, graph=None) -> list[dict]:
    levers = []
    base_min = ev.mean_minutes
    slow = dispatch(vehicle, route, route_chargers, trust, start_soc=start_soc, target_confidence=target, seed=seed,
                    cfg=cfg, trust_aware=trust_aware, compute_levers=False, highway_speed_mps=CRUISE_LEVER_MPS,
                    graph=graph)
    if slow.confidence_raw > base_conf + LEVER_MIN_GAIN:
        levers.append({"lever_id": "cruise_80", "label": "Cruise at 80 km/h on the highway",
                       "new_confidence": slow.confidence, "time_cost_min": int(round(slow.duration_min - base_min))})
    if stops:
        node = stops[0].node
        extra_pp = EXTRA_CHARGE_MIN / ctx.min_per_pp(node)
        boosted = [replace(stops[0], charge_to=min(stops[0].charge_to + extra_pp, 100.0)), *stops[1:]]
        e2 = evaluate_plan(ctx, boosted, np.random.default_rng(seed + 2))
        if e2.confidence > base_conf + LEVER_MIN_GAIN:
            levers.append({"lever_id": "charge_more_1", "label": f"Charge {EXTRA_CHARGE_MIN:.0f} more minutes at stop 1",
                           "new_confidence": round_prob(e2.confidence), "time_cost_min": int(round(EXTRA_CHARGE_MIN))})
    for other, other_soc in others:
        alt = dispatch(other, route, route_chargers, trust, start_soc=other_soc, target_confidence=target, seed=seed,
                       cfg=cfg, trust_aware=trust_aware, compute_levers=False, graph=graph)
        if alt.confidence_raw > base_conf + LEVER_MIN_GAIN and alt.decision != "INSUFFICIENT_EVIDENCE":
            levers.append({"lever_id": f"vehicle_{other.vehicle_id}", "label": f"Use {other.vehicle_id} instead",
                           "new_confidence": alt.confidence, "time_cost_min": int(round(alt.duration_min - base_min))})
    for n in stale:  # what-if: confidence if the stale charger were confirmed working (optimistic 90th percentile)
        e3 = evaluate_plan(ctx, stops, np.random.default_rng(seed + 3), p_override={n.id: n.p_hi})
        restores_target = decision == "INSUFFICIENT_EVIDENCE" and round_prob(e3.confidence) >= target - 1e-9
        if restores_target or e3.confidence > base_conf + LEVER_MIN_GAIN:
            name = n.charger.name if n.charger else n.id
            levers.append({"lever_id": f"verify_{n.id}", "label": f"Ask the community to verify {name} before departure "
                           "(confidence if it is confirmed working)", "new_confidence": round_prob(e3.confidence),
                           "time_cost_min": 0})
    return levers


# --- live update ---------------------------------------------------------------------------------------------


def live_update_plan(state: PlanState, km_now: float, soc_now: float, *, broken: set[str] = frozenset(),
                     sigma_obs: float = 0.8, min_ess: float = 40.0) -> dict:
    """Condition the stored plan evaluation on a new reading and decide CONTINUE / DIVERT / SLOW_DOWN."""
    ctx, ev, runs = state.ctx, state.ev, state.ctx.runs
    idx = int(min(np.searchsorted(runs.km_end, km_now), len(runs.km_end) - 1))
    expected = ev.trace[:, idx]
    resid = soc_now - expected
    sigma = sigma_obs
    for _ in range(8):
        w = np.exp(-0.5 * (resid / sigma) ** 2)
        if w.sum() > 0 and w.sum() ** 2 / (w**2).sum() >= min_ess:
            break
        sigma *= 1.6
    w = w / w.sum()
    conf = float(np.sum(w * ev.success))
    arrival = np.clip(ev.arrival, 0.0, 100.0)
    order = np.argsort(arrival)
    cdf = np.cumsum(w[order])
    q = [float(arrival[order][min(np.searchsorted(cdf, p), len(cdf) - 1)]) for p in (0.1, 0.5, 0.9)]
    bingo_here = state.bingo[min(idx + 1, len(state.bingo) - 1)]["bingo_soc_pct"]
    used_now = runs.used_at_km(km_now)
    next_stop = next((s.node for s in state.stops if s.node.km > km_now), None)
    ahead = [n for n in state.nodes if km_now < n.km <= km_now + ALT_AHEAD_KM and n.id not in broken]

    def reach(n: Node) -> float:
        return float(np.sum(w * (soc_now - (runs.used_at_km(n.km) - used_now) >= RES_STOP)))

    action, divert_to, message = "CONTINUE", None, "On plan: still above the Bingo Line."
    next_broken = next_stop is not None and next_stop.id in broken
    if next_broken or soc_now < bingo_here:
        options = sorted(ahead, key=lambda n: -(reach(n) * n.p_works))
        if options and reach(options[0]) > 0.5:
            n = options[0]
            action = "DIVERT"
            divert_to = {"charger_id": n.id, "name": n.charger.name if n.charger else n.id,
                         "lat": n.charger.lat if n.charger else None, "lon": n.charger.lon if n.charger else None,
                         "p_works": round_prob(n.p_works)}
            message = ("Planned charger reported broken. Divert to the alternate ahead." if next_broken
                       else "Battery is below the Bingo Line. Divert to the nearest working charger now.")
        else:
            action = "SLOW_DOWN"
            message = ("Planned charger reported broken and no other charger is reachable with confidence: slow down "
                       "and save energy." if next_broken else
                       "Battery is below the Bingo Line and no alternate is reachable with confidence: slow down "
                       "and save energy.")
    elif conf < ctx.target:
        action = "SLOW_DOWN"
        message = "Battery is draining faster than planned; confidence is below target. Slow down to save energy."
    elif soc_now < float(np.median(expected)) - 3.0:
        message = "Battery is draining faster than planned; still above the Bingo Line."
    return {"km_along_route": round(float(km_now), 1), "confidence": round_prob(conf),
            "arrival_soc": {"p10": int(round(q[0])), "p50": int(round(q[1])), "p90": int(round(q[2]))},
            "bingo_soc_pct_here": int(bingo_here), "action": action, "divert_to": divert_to, "message": message}


# --- executing a plan in a simulated world (E3 / E4) ------------------------------------------------------------


@dataclass(frozen=True)
class Outcome:
    stranded: bool
    minutes: float
    final_soc: float
    stops_used: int


def execute_trip(stops: Sequence[Stop], used_at: "callable", start_soc: float, works: dict[str, bool], base_minutes: float,
                 min_per_pp: "callable", total_km: float, *, use_alts: bool) -> Outcome:
    """Drive one trip in a world where each charger either works or not. ``used_at(km)`` is the REAL SoC
    use up to km. Policy: planned charger if it works and is reachable, else (dispatch) a working alternate
    ahead, else carry on to the next stop; stranded when the battery cannot reach the next charger."""
    soc, pos, used_pos, minutes, n_used = start_soc, 0.0, 0.0, base_minutes, 0
    for st in stops:
        if st.node.km <= pos + 1e-9:
            continue
        arr = soc - (used_at(st.node.km) - used_pos)
        target = None
        if arr >= RES_STOP and works.get(st.node.id, False):
            target = st.node
        elif use_alts:
            for alt in st.alts:
                a = soc - (used_at(alt.km) - used_pos)
                if a >= RES_STOP and works.get(alt.id, False):
                    target, arr = alt, a
                    break
        if target is None:
            if arr < RES_STOP:
                return Outcome(True, minutes, soc - (used_at(st.node.km) - used_pos), n_used)
            continue  # carry on past a broken charger while the battery allows
        gain = float(np.clip(st.charge_to - arr, 0.0, 100.0 - arr))
        soc, pos, used_pos = arr + gain, target.km, used_at(target.km)
        minutes += STOP_OVERHEAD_MIN + gain * min_per_pp(target)
        n_used += 1
    final = soc - (used_at(total_km) - used_pos)
    return Outcome(final < RESERVE_PCT, minutes, final, n_used)
