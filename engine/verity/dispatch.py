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
from typing import Callable, Sequence

import numpy as np

from .calibrate import Posterior
from .chargers import RouteCharger
from .forecast import (
    FREE_SPEED_MPS,
    RESERVE_PCT,
    ForecastConfig,
    ForecastRuns,
    PlannedRoute,
    confidence_display,
    round_prob,
    simulate_runs,
)
from .physics import total_mass_kg
from .schemas import ChargerRecord
from .sites import WorksSampler, assign_sites, operators_from_pairs, p_all_fail
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
PRIOR_ONLY_MARGIN = 0.05  # WP2 option B, relaxed in WP3b: the class prior's spread is already in the Monte Carlo
MAX_CHARGE_PCT = 100.0  # a stop may charge to full when the next leg needs it; above CHARGE_CAP it is slower
ABOVE_CAP_SLOWDOWN = 2.5  # SoC points above CHARGE_CAP take this many times longer (DC taper; approximate - verify)
PROPOSE_QUANTILE = 0.5  # a candidate leg must fit at its MEDIAN energy; the Monte Carlo then scores the real risk
DETOUR_ROAD_FACTOR = 1.3  # road km per straight-line km between the route and an off-route charger (approximate - verify)
CREDIBLE_DRAWS = 24  # Beta draws of the stale chargers' reliability for the evidence check (WP3b)
CREDIBLE_Q = 0.10  # the check uses this quantile of plan confidence over those draws (a 90 % credible lower bound)
IF_NEEDED_PP = 1.0  # a stop whose median top-up is below this is an "only if needed" insurance stop (WP3b)
THIN_ABOVE = 60  # with more candidate chargers than this, proposals use the best few per stretch of road (WP3b)
THIN_BIN_KM = 5.0
THIN_PER_BIN = 3
MIN_STOP_MINUTES = 4.0  # a stop that adds less charging than this is not worth its 6-minute overhead
DETOUR_SPEED_KMH = 25.0  # speed on the access road between the route and an off-route charger (approximate - verify)
# Minutes lost when stranded: waiting for a tow or mobile charger plus the rest of the shift. Assumption
# (approximate - verify), used only to rank plans that already meet the confidence target.
STRANDING_PENALTY_MIN = 240.0
K_RELIABLE = 3  # extra candidates ranked by reliability (F8d)
RELIABILITY_WEIGHT_MIN = 600.0  # edge weight per unit of failure probability for the reliability-ranked chains
K_SPATIAL = 2  # extra candidates avoiding the Sites and the stretch of the cheapest chain (F8d)
SPATIAL_SEP_KM = 15.0  # a spatially distinct chain stops at least this far (km along the route) from it

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
    site: str = ""  # Site id (chargers within ~150 m); "" = a Site of its own
    detour_min: float = 0.0  # there and back between the route and the charger
    wait_min: float = 0.0  # expected queue wait from queue evidence
    operator: str = ""  # operator shared with another charger of this route (W2.2); "" = no shared operator
    detour_km: float = 0.0  # road km there and back between the route and the charger (energy, WP3b)
    substation: str = ""  # nearest substation shared with another charger of this route (WP8c); "" = none shared

    @property
    def site_id(self) -> str:
        return self.site or self.id


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
    curb_mass_kg: float | None = None  # the car's own kerb mass (WP2); None = the class prior's
    prior_only: bool = False  # a real car with no calibrated trips, planned from its class prior (WP2 option B)


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
    reserve_pct: float = RESERVE_PCT  # battery that must remain at the destination (WP4: the driver's arrival buffer)

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

    def charge_minutes(self, node: Node, soc_from: Array | float, gain: Array | float) -> Array:
        """Minutes to add ``gain`` points from ``soc_from``: points above CHARGE_CAP are ABOVE_CAP_SLOWDOWN times
        slower (DC charging tapers near full), so charging to 100 % is allowed but costs what it costs."""
        start = np.asarray(soc_from, dtype=float)
        g = np.clip(np.asarray(gain, dtype=float), 0.0, None)
        below = np.clip(np.minimum(start + g, CHARGE_CAP) - start, 0.0, None)
        return self.min_per_pp(node) * (below + ABOVE_CAP_SLOWDOWN * (g - below))

    @property
    def pp_per_km(self) -> Array:
        """Each run's average battery use per km (SoC points): prices a detour's energy."""
        return self.runs.total_used / max(self.total_km, 1e-6)


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
    lost_minutes: Array | None = None  # minutes + stranding penalty: the objective plans minimise (F8a)
    charged_at: list[Array] | None = None  # per stop, per run: PLANNED, the backup's index, or NOT_CHARGED (W0.6)
    charge_min: list[Array] | None = None  # per stop, per run: charging minutes (taper above CHARGE_CAP, WP3b)

    @property
    def confidence(self) -> float:
        return float(self.success.mean())

    @property
    def mean_minutes(self) -> float:
        return float(self.minutes.mean())

    @property
    def expected_lost_minutes(self) -> float:
        return float((self.minutes if self.lost_minutes is None else self.lost_minutes).mean())


# --- nodes, graph, candidates -----------------------------------------------------------------------------


def graph_links(graph: ChargerGraph, route_chargers: Sequence[RouteCharger]) -> tuple[dict[str, str], dict[str, str]]:
    """(charger -> shared operator, charger -> shared nearest substation) for this route, from ONE shared-cause query
    (W2.2, WP8c). A charger appears only when it shares that cause with another charger of the route."""
    pairs = graph.shared_causes([rc.charger.id for rc in route_chargers])
    ops = operators_from_pairs([(cid, sc.shared_operator or "") for sc in pairs for cid in (sc.a, sc.b)])
    subs = operators_from_pairs([(cid, getattr(sc, "shared_substation", None) or "") for sc in pairs
                                 for cid in (sc.a, sc.b)])
    return ops, subs


def graph_operators(graph: ChargerGraph, route_chargers: Sequence[RouteCharger]) -> dict[str, str]:
    """W2.2: which chargers of this route share an operator, asked of the graph (one query for every pair)."""
    return graph_links(graph, route_chargers)[0]


def make_nodes(route_chargers: Sequence[RouteCharger], trust: dict[str, ChargerTrust], *, trust_aware: bool = True,
               prior_p: float = 0.82, queue_wait_min: dict[str, float] | None = None,
               operators: dict[str, str] | None = None, link_operators: bool = True,
               substations: dict[str, str] | None = None) -> list[Node]:
    """Route chargers as planning nodes. A reliability-blind planner (E3) assumes every charger works.
    Chargers within ~150 m share a Site (correlated failures); the detour is there and back at DETOUR_SPEED_KMH.
    Chargers of one operator share a weaker operator-wide cause (W2.2). ``operators`` (charger id -> operator, from
    the graph's shared-cause query) decides which; without it the directory's own operator field does (the Python
    fallback, same answer). ``link_operators=False`` is the Site-only model of before W2.2 (E3/E4, E6's baseline).
    ``substations`` (WP8c): charger id -> nearest substation shared with another charger here; none = no such tier."""
    sites = assign_sites([(rc.charger.id, rc.charger.lat, rc.charger.lon) for rc in route_chargers])
    if not link_operators:
        ops: dict[str, str] = {}
    elif operators is not None:
        ops = dict(operators)
    else:
        ops = {rc.charger.id: rc.charger.operator for rc in route_chargers if rc.charger.operator}
    waits = queue_wait_min or {}
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
        detour_km = 2.0 * rc.offset_km * DETOUR_ROAD_FACTOR
        nodes.append(Node(rc.charger.id, rc.km_along, float(p), float(lo), float(hi), kw, rc.charger, t,
                          sites[rc.charger.id], detour_km / DETOUR_SPEED_KMH * 60.0,
                          float(waits.get(rc.charger.id, 0.0)), ops.get(rc.charger.id, ""), detour_km,
                          (substations or {}).get(rc.charger.id, "") if link_operators else ""))
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


LegRow = tuple[str, str, bool, bool, float, tuple[float, ...]]  # from, to, strict ok, lenient ok, minutes, p_reach/band
BAND_MIDS = np.arange(5.0, MAX_BAND, 10.0)  # one LEG per 10-point battery band, never one fixed probability


def leg_table(ctx: Ctx, nodes: Sequence[Node], *, trust_aware: bool) -> tuple[dict[str, Array], list[LegRow]]:
    """Every START/charger/END pair once, vectorised over the pairs leaving each point (WP3b): its energy quantiles,
    whether the STRICT filter (90th-percentile energy, charging up to CHARGE_CAP: the original rule) and the LENIENT
    one (median energy, charging up to 100 %) keep it, its edge minutes and its reach probability per battery band."""
    ids = ["START"] + [n.id for n in nodes] + ["END"]
    kms = np.array([0.0] + [n.km for n in nodes] + [ctx.total_km])
    u = np.vstack([np.zeros(ctx.runs.n_runs)] + [ctx.runs.used_at_km(n.km) for n in nodes] + [ctx.runs.total_used])
    used = dict(zip(ids, u))
    ppk = float(np.median(ctx.pp_per_km))
    half = np.array([0.0] + [n.detour_km / 2.0 * ppk for n in nodes] + [0.0])
    mins = np.array([ctx.minutes_at(float(k)) for k in kms])
    need = np.array([RES_STOP] * (len(ids) - 1) + [ctx.reserve_pct])  # what must remain on arriving at each point
    rows: list[LegRow] = []
    for a in range(len(ids) - 1):
        # leaving charger a's access road and driving down the next charger's access road cost battery too (WP3b)
        energy = u[a + 1:] - u[a] + half[a] + half[a + 1:, None]
        q_mid, q90 = np.quantile(energy, [PROPOSE_QUANTILE, 0.9], axis=1)
        nd = need[a + 1:]
        if a == 0:
            # Nothing can be added at the start: a first leg reached less often than the target can never meet it,
            # so both filters ask for the target's quantile (the strict 90th percentile at a 90 % target).
            q_target = np.quantile(energy, min(max(ctx.target, PROPOSE_QUANTILE), 0.99), axis=1)
            ok_lenient = q_target + nd <= ctx.start_soc
            ok_strict = q90 + nd <= ctx.start_soc
        else:
            ok_lenient = q_mid + nd <= MAX_CHARGE_PCT
            ok_strict = q90 + nd + SAFETY_PP <= CHARGE_CAP
        keep = np.flatnonzero(ok_lenient | ok_strict)
        if not len(keep):
            continue
        minutes = mins[a + 1:][keep] - mins[a]
        if a > 0:
            node = nodes[a - 1]
            charge_to = np.minimum(q90[keep] + nd[keep] + SAFETY_PP, MAX_CHARGE_PCT)
            minutes = minutes + STOP_OVERHEAD_MIN + node.detour_min + ctx.charge_minutes(
                node, ARRIVE_GUESS_PCT, np.maximum(charge_to - ARRIVE_GUESS_PCT, 0.0))
            if trust_aware:
                minutes = minutes + FAILURE_PENALTY_MIN * (1.0 - node.p_works)
        p_reach = ((BAND_MIDS[None, :, None] - energy[keep][:, None, :]) >= nd[keep][:, None, None]).mean(axis=2)
        for j, b in enumerate(keep):
            rows.append((ids[a], ids[a + 1 + b], bool(ok_strict[b]), bool(ok_lenient[b]), float(minutes[j]),
                         tuple(float(x) for x in p_reach[j])))
    return used, rows


def build_leg_graph(ctx: Ctx, nodes: Sequence[Node], *, trust_aware: bool, graph: ChargerGraph | None = None,
                    scope: str = "", lenient: bool = True,
                    table: tuple[dict[str, Array], list[LegRow]] | None = None) -> tuple[ChargerGraph, dict[str, Array]]:
    """LEG edges per battery band between START, chargers and END (only legs the chosen filter keeps), written to
    ``graph`` (a fresh in-memory one by default) in ONE batch under ``scope``. ``lenient`` (WP3b) drops only clearly
    impossible legs and leaves the risk to the Monte Carlo; the strict filter is the original rule, kept so that no
    chain the old planner found is ever lost. ``table`` reuses a ``leg_table`` already computed for this plan."""
    graph = InMemoryGraph() if graph is None else graph
    used, rows = table if table is not None else leg_table(ctx, nodes, trust_aware=trust_aware)
    legs = [Leg(a, b, int(lo), int(lo) + 10, pr, m) for a, b, ok_s, ok_l, m, reach in rows if (ok_l if lenient else ok_s)
            for lo, pr in zip(BAND_MIDS - 5.0, reach)]
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


def _leg_need(ctx: Ctx, path: Sequence[Node], i: int, j: int) -> float:
    """Charge needed at path[i] to reach path[j] (or END when j == len(path)) at the 90th-percentile energy."""
    node = path[i]
    to_km = path[j].km if j < len(path) else ctx.total_km
    res = RES_STOP if j < len(path) else ctx.reserve_pct
    to_detour = path[j].detour_km / 2.0 if j < len(path) else 0.0
    energy = (ctx.runs.used_at_km(to_km) - ctx.runs.used_at_km(node.km)
              + (node.detour_km / 2.0 + to_detour) * ctx.pp_per_km)
    return float(res + SAFETY_PP + np.quantile(energy, 0.9))


def make_stops(ctx: Ctx, path: Sequence[Node], all_nodes: Sequence[Node], extra_pp: float = 0.0,
               robust: bool = False) -> list[Stop]:
    """Stops with their charge targets: enough for the next leg. ``robust`` (WP3b) charges each stop, when the battery
    allows, enough to SKIP the next stop too, the way a driver treats an unreported charger ahead as a bonus top-up
    rather than a must-work stop."""
    on_path = {n.id for n in path}
    stops = []
    for i, node in enumerate(path):
        if robust and i + 1 < len(path):  # as close to skipping the next stop as a full battery allows
            skip = max(_leg_need(ctx, path, i, i + 2), _leg_need(ctx, path, i, i + 1))
            stops.append(Stop(node, float(np.clip(skip + extra_pp, 0.0, MAX_CHARGE_PCT)),
                              alts_for(node, all_nodes, on_path)))
            continue
        nxt_km = path[i + 1].km if i + 1 < len(path) else ctx.total_km
        res = RES_STOP if i + 1 < len(path) else ctx.reserve_pct
        nxt_detour = path[i + 1].detour_km / 2.0 if i + 1 < len(path) else 0.0
        energy = (ctx.runs.used_at_km(nxt_km) - ctx.runs.used_at_km(node.km)
                  + (node.detour_km / 2.0 + nxt_detour) * ctx.pp_per_km)
        target = float(np.clip(res + SAFETY_PP + np.quantile(energy, 0.9) + extra_pp, 0.0, MAX_CHARGE_PCT))
        stops.append(Stop(node, min(target, 100.0), alts_for(node, all_nodes, on_path)))
    return stops


def _drop_best_effort(graph: ChargerGraph, scope: str) -> None:
    """Leftover LEGs must never fail a plan or hide its real error: the service's periodic sweep removes them."""
    try:
        graph.drop_legs(scope)
    except Exception as exc:  # noqa: BLE001 - logged by class name only
        log.warning("could not drop the legs of scope %s (%s); the periodic sweep will", scope, type(exc).__name__)


def diverse_paths(adj: dict[str, dict[str, float]], nodes: Sequence[Node], k: int = K_CANDIDATES, *,
                  diversity: bool = True) -> list[list[str]]:
    """Candidate chains as charger-id lists: the ``k`` cheapest by expected minutes, then (F8d) reliability-ranked
    chains (edges out of a charger cost RELIABILITY_WEIGHT_MIN more per unit of failure probability) and spatially
    distinct chains (no stop at a Site of the cheapest chain or within SPATIAL_SEP_KM of its stops)."""
    found = [p[1:-1] for _, p in k_shortest_paths(adj, "START", "END", k)]
    if not diversity:
        return found
    by_id = {n.id: n for n in nodes}
    risky = {a: {b: w + (RELIABILITY_WEIGHT_MIN * (1.0 - by_id[a].p_works) if a in by_id else 0.0)
                 for b, w in out.items()} for a, out in adj.items()}
    extra = [p[1:-1] for _, p in k_shortest_paths(risky, "START", "END", K_RELIABLE)]
    if found and found[0]:
        banned = _spatially_banned(nodes, found[0])
        apart = {a: {b: w for b, w in out.items() if b not in banned} for a, out in adj.items() if a not in banned}
        extra += [p[1:-1] for _, p in k_shortest_paths(apart, "START", "END", K_SPATIAL)]
    for p in extra:
        if p not in found:
            found.append(p)
    return found


def _spatially_banned(nodes: Sequence[Node], base_chain: Sequence[str]) -> set[str]:
    """Chargers a spatially distinct chain must avoid: the cheapest chain's Sites and everything near its stops."""
    by_id = {n.id: n for n in nodes}
    base = [by_id[i] for i in base_chain]
    return {n.id for n in nodes if any(n.site_id == b.site_id or abs(n.km - b.km) < SPATIAL_SEP_KM for b in base)}


def graph_chains(graph: ChargerGraph, scope: str, nodes: Sequence[Node], k: int = K_CANDIDATES, *,
                 diversity: bool = True) -> list[list[str]] | None:
    """``diverse_paths`` with the chains proposed by the graph itself (W2.3, Cypher in ``chains_cypher``): the same three
    candidate families, each one query. ``None`` when the graph has no such query (the in-memory fallback)."""
    first = graph.chains(scope, k)
    if first is None:
        return None
    found = [c for _, c in first]
    if not diversity:
        return found
    fail = {n.id: 1.0 - n.p_works for n in nodes}
    extra = [c for _, c in graph.chains(scope, K_RELIABLE, fail=fail, weight=RELIABILITY_WEIGHT_MIN) or []]
    if found and found[0]:
        extra += [c for _, c in graph.chains(scope, K_SPATIAL, banned=_spatially_banned(nodes, found[0])) or []]
    for c in extra:
        if c not in found:
            found.append(c)
    return found


def thin_nodes(nodes: Sequence[Node], vehicle_max_kw: float) -> list[Node]:
    """Candidate STOPS for chain proposals on a dense route (WP3b): per THIN_BIN_KM of road, the THIN_PER_BIN
    chargers a planner would pick first (most likely to work, then fastest for this car, then the shortest detour).
    Backups are still chosen from every charger; with THIN_ABOVE or fewer candidates nothing is dropped."""
    if len(nodes) <= THIN_ABOVE:
        return list(nodes)
    bins: dict[int, list[Node]] = {}
    for n in nodes:
        bins.setdefault(int(n.km // THIN_BIN_KM), []).append(n)
    keep = [n for group in bins.values() for n in sorted(
        group, key=lambda n: (-round(n.p_works, 2), -min(n.kw or ASSUMED_CHARGER_KW, vehicle_max_kw), n.detour_min)
    )[:THIN_PER_BIN]]
    return sorted(keep, key=lambda n: n.km)


def propose_paths(ctx: Ctx, nodes: Sequence[Node], *, trust_aware: bool, k: int = K_CANDIDATES,
                  graph: ChargerGraph | None = None, diversity: bool = True) -> tuple[list[list[Node]], ChargerGraph]:
    """Candidate charging chains. With an external ``graph`` (Neo4j) the LEGs live there under a fresh scope for
    the duration of this call only and are dropped afterwards, so concurrent plans never see each other's legs."""
    nodes = thin_nodes(nodes, VEHICLE_MAX_KW.get(ctx.vehicle.class_key, max(VEHICLE_MAX_KW.values())))
    ids = ["START"] + [n.id for n in nodes] + ["END"]
    by_id = {n.id: n for n in nodes}
    found: list[list[str]] = []
    leg_graph: ChargerGraph | None = None
    table = leg_table(ctx, nodes, trust_aware=trust_aware)  # computed once, filtered twice
    for lenient in (False, True):  # the strict chains first, then the ones only the lenient filter allows (WP3b)
        scope = uuid.uuid4().hex if graph is not None else ""
        try:
            leg_graph, _ = build_leg_graph(ctx, nodes, trust_aware=trust_aware, graph=graph, scope=scope,
                                           lenient=lenient, table=table)
            chains = graph_chains(leg_graph, scope, nodes, k, diversity=diversity) if graph is not None else None
            if chains is None:  # no graph, or the in-memory fallback: the Python path (Yen) proposes the same chains
                chains = diverse_paths(_adjacency(leg_graph, ids, scope), nodes, k, diversity=diversity)
        finally:
            if graph is not None:
                _drop_best_effort(graph, scope)
        found += [c for c in chains if c not in found]
    return [[by_id[i] for i in p] for p in found], leg_graph


# --- Monte Carlo with backups -----------------------------------------------------------------------------


PLANNED, NOT_CHARGED = -1, -2  # PlanEval.charged_at codes; 0, 1, ... = that stop's backup index


def evaluate_plan(ctx: Ctx, stops: Sequence[Stop], rng: np.random.Generator, *, use_alts: bool = True,
                  p_override: dict[str, float] | None = None) -> PlanEval:
    """Run every Monte Carlo run through the plan: planned charger, else a reachable working alternate ahead,
    else carry on if the next stop is still reachable, else stranded."""
    runs, n = ctx.runs, ctx.runs.n_runs
    p_of = lambda node: (p_override or {}).get(node.id, node.p_works)  # noqa: E731
    sampler = WorksSampler(rng, n)  # chargers of one Site (and, weaker, one operator) share common-cause failures
    soc = np.full(n, ctx.start_soc)
    pos_km, pos_used = np.zeros(n), np.zeros(n)
    alive = np.ones(n, dtype=bool)
    minutes = np.full(n, ctx.minutes_at(ctx.total_km))
    gains, stop_arrival, alive_in, soc_in, used_in, charged_at, charge_min = [], [], [], [], [], [], []
    ppk = ctx.pp_per_km
    for st in stops:
        node = st.node
        soc_in.append(soc.copy()); used_in.append(pos_used.copy()); alive_in.append(alive.copy())
        behind = alive & (pos_km >= node.km - 1e-9)
        active = alive & ~behind
        used_st = runs.used_at_km(node.km)
        det = node.detour_km * ppk  # there and back off the route, per run (WP3b)
        arr = soc - (used_st - pos_used) - det / 2.0
        stop_arrival.append(np.where(active, arr, np.nan))
        works = sampler.works(node.site_id, p_of(node), node.operator, node.substation)
        tried = active & (arr >= RES_STOP)
        ok = tried & works
        waste = np.where(tried & ~works, det, 0.0)  # drove to a broken charger and back
        minutes = minutes + np.where(tried, node.detour_min, 0.0) + np.where(tried & ~works, FAILED_STOP_MIN, 0.0)
        cur_km, cur_used, cur_soc = (np.where(ok, v, w) for v, w in ((node.km, pos_km), (used_st, pos_used), (arr, soc)))
        cur_det = np.where(ok, det, 0.0)
        cur_wait = np.where(ok, node.wait_min, 0.0)
        where = np.where(ok, PLANNED, NOT_CHARGED)
        need = active & ~ok
        if use_alts:
            for j, alt in enumerate(st.alts):
                used_alt = runs.used_at_km(alt.km)
                det_alt = alt.detour_km * ppk
                arr_alt = soc - waste - (used_alt - pos_used) - det_alt / 2.0
                can = need & (arr_alt >= RES_STOP) & sampler.works(alt.site_id, p_of(alt), alt.operator, alt.substation)
                cur_km, cur_used, cur_soc = (np.where(can, v, w) for v, w in
                                             ((alt.km, cur_km), (used_alt, cur_used), (arr_alt, cur_soc)))
                cur_det = np.where(can, det_alt, cur_det)
                cur_wait = np.where(can, alt.wait_min, cur_wait)
                where = np.where(can, j, where)
                minutes = minutes + np.where(can, alt.detour_min, 0.0)
                ok |= can
                need &= ~can
        alive = alive & ~(need & (arr < RES_STOP))  # could not even reach the planned charger: stranded
        gain = np.where(ok, np.clip(st.charge_to - cur_soc, 0.0, 100.0 - cur_soc), 0.0)
        c_min = np.where(ok, ctx.charge_minutes(node, np.where(ok, cur_soc, 0.0), gain), 0.0)
        soc = np.where(ok, cur_soc + gain - cur_det / 2.0, soc - waste)  # back to the route after charging
        pos_km, pos_used = np.where(ok, cur_km, pos_km), np.where(ok, cur_used, pos_used)
        minutes = minutes + np.where(ok, STOP_OVERHEAD_MIN, 0.0) + c_min + cur_wait
        gains.append(gain)
        charge_min.append(c_min)
        charged_at.append(where)
    arrival = soc - (runs.total_used - pos_used)
    success = alive & (arrival >= ctx.reserve_pct)
    lost = minutes + np.where(success, 0.0, STRANDING_PENALTY_MIN)
    trace = ctx.start_soc - runs.soc_used_cum
    for st, gain in zip(stops, gains):
        trace = trace + np.where(runs.km_end >= st.node.km, 1.0, 0.0)[None, :] * gain[:, None]
    return PlanEval(success, arrival, minutes, gains, stop_arrival, alive_in, soc_in, used_in, trace, ctx, lost,
                    charged_at, charge_min)


# --- Bingo Line, point of no return -----------------------------------------------------------------------


def vehicle_mass_kg(vehicle: VehicleContext) -> float:
    """Total mass for one vehicle: its own kerb mass when known (WP2), else its class prior's, plus people and cargo.
    The one place this is computed, so dispatch and the ride check can never disagree."""
    curb = vehicle.curb_mass_kg if vehicle.curb_mass_kg is not None else get_prior(vehicle.class_key).base.curb_mass_kg
    return total_mass_kg(curb, vehicle.passengers, vehicle.cargo_kg)


def prior_only_too_close(shown: float, target: float, margin: float = PRIOR_ONLY_MARGIN) -> bool:
    """WP2 option B (Ansh, 2026-11-20): a car planned from its class prior alone answers GO or NO_GO only when the
    shown confidence is at least ``margin`` away from the target; closer than that is too close to call."""
    return abs(shown - target) < margin - 1e-9


def meets_target(p: float | Array, target: float) -> bool | Array:
    """The one "meets the target" test (W0.1), shared by the GO decision, the Bingo Line and the live update:
    the DISPLAYED confidence (nearest 5 %, golden rule 5) against the target, so the three never disagree."""
    return np.round(np.asarray(p, dtype=float) * 20.0) / 20.0 >= target - 1e-9


def policy_ahead(stops: Sequence[Stop], km: float) -> tuple[Node, ...]:
    """The plan's own policy from ``km`` on (W0.1), as ``evaluate_plan`` runs it: the next planned stop, then its
    backups wherever they are, then (a run whose stop failed carries on) the later planned stops and their
    backups. The destination always follows. Past the last stop only the destination is left. No duplicates."""
    seen: dict[str, Node] = {}
    for st in (s for s in stops if s.node.km > km):
        for n in (st.node, *st.alts):
            seen.setdefault(n.id, n)
    return tuple(seen.values())


def bingo_line(ctx: Ctx, stops: Sequence[Stop], rng: np.random.Generator) -> list[dict]:
    """Per segment end: the lowest SoC from which the plan's own policy (next planned stop, its backups, the
    destination; the failure model of ``evaluate_plan``) still meets the target (W0.1, F1/F2)."""
    runs = ctx.runs
    sub = rng.choice(runs.n_runs, size=min(BINGO_SUBSAMPLE, runs.n_runs), replace=False)
    grid = np.arange(0.0, 100.5, 1.0)
    dest_used = runs.total_used[sub]
    out = []
    for si, km in enumerate(runs.km_end):
        used_x = runs.soc_used_cum[sub, si]
        cands: list[tuple[Array, float, float, str, str, str]] = [
            (dest_used - used_x, 1.0, ctx.reserve_pct, "__destination__", "", "")]
        for n in policy_ahead(stops, float(km)):
            cands.append((runs.used_at_km(n.km)[sub] - used_x, n.p_works, RES_STOP, n.site_id, n.operator, n.substation))
        energy = np.stack([c[0] for c in cands])  # (C, n)
        res = np.array([c[2] for c in cands])[:, None, None]
        reach = (grid[None, None, :] - energy[:, :, None]) >= res  # (C, n, G)
        fail = p_all_fail([c[1] for c in cands], [c[3] for c in cands], reach,  # (n, G), Site- and operator-correlated
                          operators=[c[4] for c in cands], substations=[c[5] for c in cands])
        prob = 1.0 - fail.mean(axis=0)  # (G,)
        ok = np.nonzero(meets_target(prob, ctx.target))[0]
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
    reason: str = ""  # one sentence naming the charger that carries the decision (W0.6)
    reason_facts: dict | None = None  # the numbers behind it, for speech in other languages
    verify_chargers: list[dict] = field(default_factory=list)  # WP3: verifying these could clear a refusal


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


def trust_block(node: Node) -> dict:
    """WP3 ``trust`` on every stop and backup: level, recent reports, age and where the estimate comes from."""
    e, t = _evidence(node), node.trust
    return {"level": e["trust_level"], "n_reports": e["n_reports"], "days_since_last": e["days_since_last"],
            "basis": t.basis if t is not None else "prior only"}


def credible_confidence(ctx: Ctx, stops: Sequence[Stop], stale: Sequence[Node], seed: int,
                        draws: int = CREDIBLE_DRAWS, q: float = CREDIBLE_Q) -> float:
    """A credible LOWER bound on the plan's confidence over what the stale chargers' missing reports could show
    (WP3b): draw each stale charger's P(works) from its Beta posterior, score the plan for each draw on the same
    Monte Carlo runs, and take the ``q`` quantile. It replaces "every stale charger at its own 10th percentile at
    once", a joint event far rarer than 10 % that made almost every plan on a new corridor refuse."""
    rng = np.random.default_rng(seed + 7)
    unique = list({n.id: n for n in stale}.values())
    confs = []
    for _ in range(draws):
        over = {n.id: (float(rng.beta(n.trust.a_post, n.trust.b_post))
                       if n.trust is not None and n.trust.a_post > 0 and n.trust.b_post > 0 else n.p_lo)
                for n in unique}
        confs.append(evaluate_plan(ctx, stops, np.random.default_rng(seed + 1), p_override=over).confidence)
    return float(np.quantile(confs, q))


def _stale(node: Node) -> bool:
    return node.trust is None or node.trust.trust_level in ("unknown", "low")


def _build_ctx(vehicle: VehicleContext, route: PlannedRoute, start_soc: float, target: float, rng: np.random.Generator,
               cfg: ForecastConfig, highway_speed_mps: float | None = None, reserve_pct: float = RESERVE_PCT) -> Ctx:
    mass = vehicle_mass_kg(vehicle)
    runs = simulate_runs(vehicle.post, route, mass_kg=mass, ac_on=vehicle.ac_on, rng=rng, cfg=cfg,
                         highway_speed_mps=highway_speed_mps)
    usable = float(vehicle.post.nominal_kwh * vehicle.post.summary(n=1000)["usable_frac"][0])
    return Ctx(runs, route, vehicle, float(start_soc), float(target), usable, drive_cum_minutes(route, highway_speed_mps),
               float(reserve_pct))


def _choose(ctx: Ctx, nodes: Sequence[Node], rng: np.random.Generator, *, trust_aware: bool,
            use_alts: bool = True, graph: ChargerGraph | None = None, diversity: bool = True,
            stop_nodes: Sequence[Node] | None = None
            ) -> tuple[list[Stop], PlanEval, list[tuple[list[Stop], PlanEval]]]:
    """Among plans meeting the confidence target, the one with the fewest expected lost minutes (driving, detours,
    charging, queueing, failed stops and the stranding penalty, F8a); otherwise the most reliable one.
    ``stop_nodes`` (WP4 preferred operators) limits the PLANNED stops; backups still come from every node."""
    paths, _ = propose_paths(ctx, nodes if stop_nodes is None else stop_nodes, trust_aware=trust_aware, graph=graph,
                             diversity=diversity)
    if [] not in paths:
        paths.append([])  # the no-stop plan is always scored, even when infeasible (then it simply fails)
    scored = []
    for path in paths:
        stops = make_stops(ctx, path, nodes)
        ev = evaluate_plan(ctx, stops, rng, use_alts=use_alts)
        scored.append((stops, ev))
        if len(path) >= 2:  # WP3b: the same chain with each stop charging enough to skip the next one
            robust = make_stops(ctx, path, nodes, robust=True)
            if [s.charge_to for s in robust] != [s.charge_to for s in stops]:
                scored.append((robust, evaluate_plan(ctx, robust, rng, use_alts=use_alts)))
        trivial = {s.node.id for s, g in zip(stops, ev.gains) if float(g.mean()) * ctx.min_per_pp(s.node) < MIN_STOP_MINUTES}
        if trivial:  # also score the plan without the pointless top-ups
            kept = [s.node for s in stops if s.node.id not in trivial]
            pruned = make_stops(ctx, kept, nodes)
            scored.append((pruned, evaluate_plan(ctx, pruned, rng, use_alts=use_alts)))
    meeting = [s for s in scored if s[1].confidence >= ctx.target]
    # With nothing meeting the target, prefer the most reliable plan, but compare confidence at the Monte
    # Carlo's own resolution (2 points) so a 3-stop plan does not beat a 1-stop plan on noise alone.
    best = (min(meeting, key=lambda s: (s[1].expected_lost_minutes, len(s[0]))) if meeting
            else max(scored, key=lambda s: (round(s[1].confidence / 0.02), -s[1].expected_lost_minutes)))
    return best[0], best[1], scored


def plan_only(vehicle: VehicleContext, route: PlannedRoute, route_chargers: Sequence[RouteCharger],
              trust: dict[str, ChargerTrust], *, start_soc: float, target_confidence: float = 0.9, seed: int = 0,
              cfg: ForecastConfig = ForecastConfig(), trust_aware: bool = True,
              graph: ChargerGraph | None = None, diversity: bool = True, link_operators: bool = True
              ) -> tuple[list[Stop], PlanEval, Ctx]:
    """Propose and score candidate chains and return the chosen plan (no Bingo Line, levers or texts):
    the light path used by the simulation experiments E3, E4 and E6."""
    rng = np.random.default_rng(seed)
    ctx = _build_ctx(vehicle, route, start_soc, target_confidence, rng, cfg)
    ops, subs = graph_links(graph, route_chargers) if graph is not None and link_operators else (None, None)
    stops, ev, _ = _choose(ctx, make_nodes(route_chargers, trust, trust_aware=trust_aware, operators=ops,
                                           link_operators=link_operators, substations=subs), rng, trust_aware=trust_aware,
                           graph=graph, diversity=diversity)
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
    queue_wait_min: dict[str, float] | None = None,
) -> DispatchResult:
    t0 = time.perf_counter()
    rng = np.random.default_rng(seed)
    ctx = _build_ctx(vehicle, route, start_soc, target_confidence, rng, cfg, highway_speed_mps)
    ops, subs = graph_links(graph, route_chargers) if graph is not None else (None, None)
    nodes = make_nodes(route_chargers, trust, trust_aware=trust_aware, queue_wait_min=queue_wait_min, operators=ops,
                       substations=subs)
    stops, ev, _ = _choose(ctx, nodes, rng, trust_aware=trust_aware, graph=graph)
    result = _release(vehicle, route, route_chargers, trust, ctx, nodes, stops, ev, rng, start_soc=start_soc,
                      target_confidence=target_confidence, polyline=polyline, seed=seed, cfg=cfg,
                      trust_aware=trust_aware, other_vehicles=other_vehicles, compute_levers=compute_levers, graph=graph)
    result.compute_ms = int(round(1000 * (time.perf_counter() - t0)))
    return result


def _release(vehicle: VehicleContext, route: PlannedRoute, route_chargers: Sequence[RouteCharger],
             trust: dict[str, ChargerTrust], ctx: Ctx, nodes: Sequence[Node], stops: list[Stop], ev: PlanEval,
             rng: np.random.Generator, *, start_soc: float, target_confidence: float, polyline: list | None, seed: int,
             cfg: ForecastConfig, trust_aware: bool, other_vehicles: Sequence[tuple[VehicleContext, float]],
             compute_levers: bool, graph: ChargerGraph | None, replan_levers: bool = True) -> DispatchResult:
    """One chosen plan -> a full release: evidence check, decision, Bingo Line, reason and levers. Shared by
    ``dispatch`` and every WP4 trip option, so an option is held to exactly the same rules as a dispatch.
    ``replan_levers`` = also try the levers that re-plan from scratch (cruise at 80, another vehicle)."""
    conf = ev.confidence

    # evidence check: would the decision flip if stale/unknown chargers were at their pessimistic value?
    stale = [s.node for s in stops if _stale(s.node)] + [a for s in stops for a in s.alts if _stale(a)]
    # Only a would-be GO can be withheld for thin evidence, so the bound is computed only then (it costs 24 re-scores).
    could_withhold = bool(stale) and trust_aware and meets_target(conf, target_confidence)
    conf_pess = credible_confidence(ctx, stops, stale, seed) if could_withhold else conf
    thin_vehicle = vehicle.trips_calibrated < MIN_CALIBRATED_TRIPS and not vehicle.prior_only
    # The decision follows the DISPLAYED confidence (nearest 5 %, golden rule 5), so the UI never shows
    # "90 % / NO_GO" for a 90 % target; 2.5 points is below the Monte Carlo's own resolution anyway.
    shown, shown_pess = round_prob(conf), round_prob(conf_pess)
    decision, refusal = "GO" if meets_target(conf, target_confidence) else "NO_GO", None
    charger_refusal = False  # the refusal is about charger evidence (WP3: say which chargers to verify)
    if thin_vehicle:
        decision = "INSUFFICIENT_EVIDENCE"
        refusal = ("This vehicle has no calibrated trips yet, so its real range is unknown. Log one or two short "
                   "trips with the logger before planning an outstation booking.")
    elif vehicle.prior_only and prior_only_too_close(shown, target_confidence):
        decision = "INSUFFICIENT_EVIDENCE"
        unreachable = target_confidence + PRIOR_ONLY_MARGIN > 1.0 + 1e-9  # no shown value can clear it (DECISIONS WP2)
        refusal = (f"Too close to call without this car's trip history: planned from its class only, confidence is "
                   f"{confidence_display(conf)} against a {target_confidence:.0%} target. "
                   + ("At a target above 90 % a car with no trip history cannot be cleared: log a trip in this car "
                      "or lower the target." if unreachable else
                      "Log a trip in this car, or change the plan (charge longer, drive slower) until the answer is "
                      "clear."))
    elif decision == "GO" and shown_pess < target_confidence - 1e-9 and trust_aware:
        weak = ", ".join(sorted({n.charger.name if n.charger else n.id for n in stale}))
        decision, charger_refusal = "INSUFFICIENT_EVIDENCE", True
        n_weak = len({n.id for n in stale})
        refusal = (f"The plan only meets the {target_confidence:.0%} target if chargers with stale or missing reports "
                   f"({weak}) work; with a pessimistic reading of them (the worst 10 % of what their missing reports could "
                   f"show) confidence falls to about {shown_pess:.0%}. "
                   f"Verify {'this charger' if n_weak == 1 else f'these {n_weak} chargers'} and the plan stands at "
                   f"today's estimate of {shown:.0%} (target {target_confidence:.0%}).")

    result = _assemble(vehicle, ctx, stops, ev, nodes, decision, refusal, polyline or [], rng)
    if decision == "GO" and any(p["expected_soc_pct"] < p["bingo_soc_pct"] for p in result.bingo_line):
        # A release must never contradict its own Bingo Line (W0.1). This happens only at the rounding edge (e.g.
        # 88.75 % shown as 90 %), where the plan's score and the line's own estimate disagree by Monte Carlo noise:
        # the borderline plan is then not released as a GO (WP3b).
        decision = result.decision = "NO_GO"
    if charger_refusal:
        seen: dict[str, dict] = {}
        for n in stale:
            seen.setdefault(n.id, {"charger_id": n.id, "name": n.charger.name if n.charger else n.id,
                                   "trust": trust_block(n)})
        result.verify_chargers = list(seen.values())
    if compute_levers:  # lever re-plans read only the confidence; skip their reason
        without = lambda node: evaluate_plan(ctx, stops, np.random.default_rng(seed + 4),  # noqa: E731
                                             p_override=None if node is None else {node.id: 0.0}).confidence
        facts = reason_facts(ctx, stops, ev, decision, carrying_charger(stops, ev, without))
        result.reason_facts = facts
        result.reason = (refusal or "") if facts is None else render_reason(facts, REASON_TEMPLATES_EN, confidence_display,
                                                                     reports_en, ROLE_EN)
    if compute_levers:
        result.levers = _levers(vehicle, route, route_chargers, trust, ctx, stops, ev, nodes, start_soc, target_confidence,
                                seed, cfg, trust_aware, other_vehicles, stale, conf, decision, graph,
                                replan=replan_levers)
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
            "charge_minutes": int(round(float(ev.charge_min[i].mean()) if ev.charge_min else
                                        float(ev.gains[i].mean()) * ctx.min_per_pp(n))),
            "p_works": round_prob(n.p_works), "evidence": _evidence(n), "trust": trust_block(n),
            # WP3b: an insurance stop: most runs arrive with enough and drive past; it charges only when the battery
            # came in lower than expected, which is what it adds to the plan's confidence
            "if_needed": bool(float(np.median(ev.gains[i])) < IF_NEEDED_PP),
            "power_assumed": n.kw is None,
        })
        for alt in st.alts:
            reach = float(np.mean(ev.soc_in[i] - (runs.used_at_km(alt.km) - ev.used_in[i]) >= RES_STOP))
            alt_dicts.append({"for_stop_index": i, "charger_id": alt.id,
                              "name": alt.charger.name if alt.charger else alt.id,
                              "lat": alt.charger.lat if alt.charger else None,
                              "lon": alt.charger.lon if alt.charger else None,
                              "p_reach": round_prob(reach), "p_works": round_prob(alt.p_works),
                              "trust": trust_block(alt)})
    bingo = bingo_line(ctx, stops, rng)
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


# --- the reason sentence (W0.6, F11) ---------------------------------------------------------------------------

# One plain sentence per decision naming the charger that carries it; every number comes from the plan's own
# Monte Carlo. voice_loop speaks the same templates (en-IN) or its Hindi ones. INSUFFICIENT_EVIDENCE uses refusal.
REASON_TEMPLATES_EN: dict[str, str] = {
    "GO": "GO because {role} {name} ({p_works} likely to work, {reports}) is reachable {p_reach} of the time.",
    "GO_direct": "GO because the destination is reached with reserve, without charging, in {confidence} of runs.",
    "NO_GO": "NO_GO because the best plan relies on {role} {name} ({p_works} likely to work, {reports}) and reaches "
             "only {confidence} confidence (target {target}).",
    "NO_GO_direct": "NO_GO because no charging plan reaches the {target} target; the best reaches {confidence}.",
}
ROLE_EN = {"stop": "planned stop", "backup": "backup"}


def reports_en(n: int) -> str:
    return "no reports" if n == 0 else f"{n} report{'' if n == 1 else 's'}"


def carrying_charger(stops: Sequence[Stop], ev: PlanEval,
                     confidence_without: Callable[[Node | None], float]) -> tuple[int, Node] | None:
    """The charger the decision rests on, as (stop index, node); node is ``stops[i].node`` or one of
    ``stops[i].alts``. None when the plan has no charging stop.

    Available evidence:
    * ``ev.charged_at[i]``: per run, where it charged at stop i (PLANNED, a backup index j, or NOT_CHARGED);
      ``ev.success`` marks the runs that arrived with reserve.
    * ``confidence_without(node)``: the plan's confidence if that charger never works (one more Monte Carlo
      pass over the same runs, ~10-30 ms each); ``confidence_without(None)`` is the same-seed baseline. Every
      call shares one seed (common random numbers), so compare the calls with EACH OTHER; against
      ``ev.confidence`` they also differ by sampling noise.

    Rule (Ansh, W0.6): the most depended-on charger, i.e. the one whose failure costs the most confidence. When
    no single failure costs more than LEVER_MIN_GAIN (redundant backups: the drops are within Monte Carlo noise),
    fall back to the charger that charged the most successful runs.
    """
    if not stops:
        return None
    cands = [(i, n, code) for i, st in enumerate(stops) for code, n in ((PLANNED, st.node), *enumerate(st.alts))]
    base = confidence_without(None)
    drops = [base - confidence_without(n) for _, n, _ in cands]
    k = int(np.argmax(drops))
    if drops[k] <= LEVER_MIN_GAIN and ev.charged_at is not None:
        k = int(np.argmax([np.sum(ev.success & (ev.charged_at[i] == code)) for i, _, code in cands]))
    return cands[k][0], cands[k][1]


def _p_reach(ctx: Ctx, stops: Sequence[Stop], ev: PlanEval, i: int, node: Node) -> float:
    """P(the car gets to ``node`` with reserve) as the response's stops/alternates measure it."""
    if node is stops[i].node:
        arr = ev.stop_arrival[i]
        return float(np.mean(np.where(np.isfinite(arr), arr, -np.inf) >= RES_STOP))
    return float(np.mean(ev.soc_in[i] - (ctx.runs.used_at_km(node.km) - ev.used_in[i]) >= RES_STOP))


def reason_facts(ctx: Ctx, stops: Sequence[Stop], ev: PlanEval, decision: str,
                 carrier: tuple[int, Node] | None) -> dict | None:
    """The numbers behind the reason sentence (raw probabilities; each language formats them)."""
    if decision not in ("GO", "NO_GO"):
        return None
    facts = {"kind": decision if carrier else f"{decision}_direct", "confidence": ev.confidence,
             "target": ctx.target}
    if carrier is not None:
        i, node = carrier
        facts.update({"role": "stop" if node is stops[i].node else "backup", "charger_id": node.id,
                      "name": node.charger.name if node.charger else node.id, "p_works": node.p_works,
                      "n_reports": _evidence(node)["n_reports"], "p_reach": _p_reach(ctx, stops, ev, i, node)})
    return facts


def render_reason(facts: dict, templates: dict[str, str], pct: Callable[[float], str],
                  reports: Callable[[int], str], roles: dict[str, str],
                  target_text: Callable[[float], str] = lambda t: f"{t:.0%}") -> str:
    """Fill one reason template; ``pct`` formats a probability (confidence_display, or spoken words). ``target_text``
    formats the driver's target confidence: written "90%" by default, spoken words when the reason is read aloud."""
    return templates[facts["kind"]].format(
        role=roles.get(facts.get("role", ""), ""), name=facts.get("name", ""),
        p_works=pct(facts.get("p_works", 0.0)), reports=reports(facts.get("n_reports", 0)),
        p_reach=pct(facts.get("p_reach", 0.0)), confidence=pct(facts["confidence"]), target=target_text(facts["target"]))


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
            others, stale, base_conf, decision, graph=None, *, replan: bool = True) -> list[dict]:
    levers = []
    base_min = ev.mean_minutes
    slow = dispatch(vehicle, route, route_chargers, trust, start_soc=start_soc, target_confidence=target, seed=seed,
                    cfg=cfg, trust_aware=trust_aware, compute_levers=False, highway_speed_mps=CRUISE_LEVER_MPS,
                    graph=graph) if replan else None
    if slow is not None and slow.confidence_raw > base_conf + LEVER_MIN_GAIN:
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
    for other, other_soc in (others if replan else ()):
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
    """Condition the stored plan evaluation on a new reading and decide CONTINUE / DIVERT / SLOW_DOWN.

    Precedence (W0.1, DECISIONS.md): (1) the next planned charger reported broken -> DIVERT to its best reachable
    backup, else SLOW_DOWN; (2) the reweighted confidence meets the target -> CONTINUE, whatever the Bingo Line
    says (the full plan model outranks its one-number summary); (3) below the Bingo Line -> DIVERT to a reachable
    backup of the next stop, else SLOW_DOWN; (4) otherwise (confidence below target) -> SLOW_DOWN."""
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
    policy = policy_ahead(state.stops, km_now)  # the same set the Bingo Line was built from
    next_stop = policy[0] if policy else None
    # a divert goes to a backup or a later planned stop, never to the next planned stop itself
    ahead = [n for n in policy[1:] if n.id not in broken]

    def reach(n: Node) -> float:
        return float(np.sum(w * (soc_now - (runs.used_at_km(n.km) - used_now) >= RES_STOP)))

    action, divert_to, message = "CONTINUE", None, "On plan: still above the Bingo Line."
    next_broken = next_stop is not None and next_stop.id in broken
    on_target = bool(meets_target(conf, ctx.target))
    if on_target and not next_broken:
        if soc_now < bingo_here:
            message = "On plan: confidence still meets the target."
        elif soc_now < float(np.median(expected)) - 3.0:
            message = "Battery is draining faster than planned; still above the Bingo Line."
    elif next_broken or soc_now < bingo_here:
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
    else:
        action = "SLOW_DOWN"
        message = "Battery is draining faster than planned; confidence is below target. Slow down to save energy."
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
