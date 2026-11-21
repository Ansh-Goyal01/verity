"""Charger graph behind one interface: an in-memory implementation and a Neo4j implementation.

Schema (CLAUDE.md): nodes Charger, Operator, Site, HardwareModel, VehicleModel, Source, RoadSegment;
relationships (Source)-[:REPORTED {status, t}]->(Charger), (Charger)-[:RUN_BY]->(Operator),
(Charger)-[:AT]->(Site), (Charger)-[:IS]->(HardwareModel),
(VehicleModel)-[:WORKS_WITH {success_rate, n}]->(HardwareModel),
(Charger)-[:LEG {soc_band, p_reach, minutes}]->(Charger), (RoadSegment)-[:ALTERNATE {p_works}]->(Charger).
A LEG is stored per battery band, never as one fixed probability.

The planner only talks to ``ChargerGraph``; ``InMemoryGraph`` is the fallback (offline, deterministic) and
``Neo4jGraph`` stores the same data in AuraDB when credentials exist and the connection works. Both return
chargers ordered by id, reports ordered by (time, source, charger) and legs ordered by (from, to, band), so a
plan computed from either is identical. Site nodes are keyed by site type for now because there is no site
directory yet. Credentials come from ``.env`` and are never printed or logged.

Extensions over CLAUDE.md, all recorded in docs/DECISIONS.md: LEGs carry a ``scope`` (one per dispatch, dropped
afterwards) and a trip's origin/destination are per-scope ``RoadSegment`` nodes ``<scope>:START`` / ``<scope>:END``;
ALTERNATE edges also carry ``km_ahead``; REPORTED edges carry ``key``, ``ts`` and ``is_simulated``.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import defaultdict
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NamedTuple, Protocol

from ..paths import REPO_ROOT
from ..schemas import ChargerRecord, Report
from .channels import channel_of
from .graph_structure import MemoryStructure, Neo4jStructure
from .graph_types import AliasRec, AuditView, NameHit, PlanRec, SharedCause, SiteRec
from .ring_edges import AgreementEdge, agreement_edges, verdicts_of

log = logging.getLogger(__name__)

NODE_LABELS = ("Charger", "Operator", "Site", "SiteType", "HardwareModel", "VehicleModel", "Source", "Channel",
               "RoadSegment", "Plan", "Alias", "Substation")
TEST_PREFIX = "TEST-"
RING_WINDOW_HOURS = 72.0  # reports this close in time can belong to one burst (approximate - verify)
STALE_LEG_SECONDS = 3600.0  # scoped LEGs older than this were left behind by a crashed request
QUERY_TIMEOUT_S = 30.0  # server-side limit per query (the driver has no socket read timeout; see DECISIONS A12)
CLIENT_DEADLINE_S = QUERY_TIMEOUT_S + 5.0  # client-side wall clock per query: a stalled socket must not hang (F8e)
MAX_CHAIN_LEGS = 6  # START -> up to 5 stops -> END; the chains the planner scores are far shorter (W2.3)
CHAINS_BAND = "0-10"  # every band of a leg carries the same ``minutes``, so ONE band proposes each chain once


def chains_cypher(max_legs: int) -> str:
    """W2.3: the k cheapest loopless chains START -> chargers -> END over this scope's LEGs. Legs only point forward
    along the route, so a path cannot revisit a node. ``$fail`` (charger id -> failure probability) with ``$w`` adds
    the reliability-weighted cost of the planner's second candidate family; ``$banned`` removes chargers for the
    third (spatially distinct) one. The length bound cannot be a parameter in Cypher, hence the checked int."""
    n = int(max_legs)
    if not 1 <= n <= 12:
        raise ValueError("max_legs must be between 1 and 12")
    return (f"MATCH p = (:RoadSegment {{id: $start}})-[legs:LEG*1..{n} {{scope: $scope, soc_band: $band}}]->"
            "(:RoadSegment {id: $end}) "
            "WHERE none(n IN nodes(p)[1..-1] WHERE n.id IN $banned) "
            "WITH [n IN nodes(p) | n.id] AS chain, "
            "reduce(c = 0.0, l IN legs | c + l.minutes + $w * coalesce($fail[startNode(l).id], 0.0)) AS cost "
            "RETURN chain, cost ORDER BY cost, chain LIMIT $k")


class Neo4jStalled(TimeoutError):
    """A query got no answer within the client deadline. A TimeoutError is an OSError, which ``GraphHandle``
    treats as a backend failure, so the request falls back to the in-memory graph instead of hanging."""


def run_with_deadline(fn: Any, deadline_s: float | None) -> Any:
    """Run ``fn()`` on a daemon thread and wait at most ``deadline_s``. A stalled call is abandoned (its daemon
    thread cannot block interpreter exit) and ``Neo4jStalled`` is raised; errors inside ``fn`` propagate."""
    if deadline_s is None:
        return fn()
    box: dict[str, Any] = {}

    def target() -> None:
        try:
            box["value"] = fn()
        except BaseException as exc:  # noqa: BLE001 - re-raised on the caller's thread below
            box["error"] = exc

    worker = threading.Thread(target=target, name="neo4j-query", daemon=True)
    worker.start()
    worker.join(deadline_s)
    if worker.is_alive():
        raise Neo4jStalled(f"no answer from Neo4j within {deadline_s:.0f} s")
    if "error" in box:
        raise box["error"]
    return box.get("value")


@dataclass(frozen=True)
class Leg:
    """Travel between two chargers for one battery band (SoC percent at departure)."""

    from_id: str
    to_id: str
    soc_lo: int
    soc_hi: int
    p_reach: float
    minutes: float

    @property
    def soc_band(self) -> str:
        return f"{self.soc_lo}-{self.soc_hi}"


class RoadSegmentRec(NamedTuple):
    segment_id: str
    km_from: float
    km_to: float


class Alternate(NamedTuple):
    """Charger working probability; energy reach is evaluated per vehicle elsewhere."""

    segment_id: str
    charger_id: str
    p_works: float
    km_ahead: float | None = None


class Ring(NamedTuple):
    """Sources that file the same verdicts on the same chargers; ``shared`` = most chargers any two have in common."""

    sources: tuple[str, ...]
    shared: int


class QueryStats(NamedTuple):
    queries: int
    seconds: float


class GraphCounts(NamedTuple):
    """Everything the graph in use holds, for /api/health. Each backend counts its own store: Neo4j the whole
    database (including anything else in it), memory the nodes and relationships it would have written."""

    nodes: int
    relationships: int


def report_key(rep: Report) -> str:
    """Identity of a report: one source's verdict on one charger at one instant."""
    return f"{rep.source_id}|{rep.charger_id}|{rep.t.isoformat()}"


def _report_order(rep: Report) -> tuple[float, str, str]:
    return rep.t.timestamp(), rep.source_id, rep.charger_id


def _leg_order(leg: Leg) -> tuple[str, str, int]:
    return leg.from_id, leg.to_id, leg.soc_lo


def _check_scope(scope: str) -> None:
    """Waypoint ids are ``<scope>:START``, so a scope containing ':' could reach into another scope's waypoints."""
    if ":" in scope:
        raise ValueError("a leg scope must not contain ':'")


def group_rings(pairs: Sequence[tuple[str, str, int]]) -> list[Ring]:
    """Connected components of suspicious source pairs, largest ring first."""
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b, _ in pairs:
        parent[find(a)] = find(b)
    members: dict[str, set[str]] = defaultdict(set)
    shared: dict[str, int] = defaultdict(int)
    for a, b, n in pairs:
        root = find(a)
        members[root] |= {a, b}
        shared[root] = max(shared[root], n)
    rings = [Ring(tuple(sorted(m)), shared[r]) for r, m in members.items()]
    return sorted(rings, key=lambda ring: (-len(ring.sources), ring.sources))


def communities(edges: Sequence[AgreementEdge], min_size: int = 3) -> list[Ring]:
    """W2.4: connected components of the agreement edges with at least ``min_size`` sources (the rings)."""
    return [r for r in group_rings([(e.a, e.b, e.shared) for e in edges]) if len(r.sources) >= min_size]


def ring_id_of(ring: Ring) -> str:
    """A ring's id: its smallest member, so the same ring keeps its id from one batch to the next."""
    return f"ring:{ring.sources[0]}"


# W2.4: Jaccard agreement edges between sources, computed inside Neo4j. One verdict per (source, charger) (the majority
# of that source's reports there), every pair of sources per charger, then the shared / agreeing counts and Jaccard.
AGREEMENT_CYPHER = """\
CALL {
  MATCH (s:Source)-[r:REPORTED]->(c:Charger)
  RETURN s.id AS sid, c.id AS cid, avg(CASE WHEN r.status IN ['working', 'occupied'] THEN 1.0 ELSE 0.0 END) >= 0.5 AS v
}
WITH cid, collect({s: sid, v: v}) AS cl
UNWIND range(0, size(cl) - 2) AS i
UNWIND range(i + 1, size(cl) - 1) AS j
WITH cl[i] AS x, cl[j] AS y
WITH CASE WHEN x.s < y.s THEN x.s ELSE y.s END AS a, CASE WHEN x.s < y.s THEN y.s ELSE x.s END AS b,
     count(*) AS shared, sum(CASE WHEN x.v = y.v THEN 1 ELSE 0 END) AS same
WHERE shared >= $min_shared AND same >= $min_agree * shared
CALL { WITH a MATCH (:Source {id: a})-[:REPORTED]->(c:Charger) RETURN count(DISTINCT c) AS na }
CALL { WITH b MATCH (:Source {id: b})-[:REPORTED]->(c:Charger) RETURN count(DISTINCT c) AS nb }
WITH a, b, shared, toFloat(shared) / (na + nb - shared) AS jaccard WHERE jaccard >= $min_jaccard
RETURN a, b, shared, jaccard ORDER BY shared DESC, a, b"""


class ChargerGraph(Protocol):
    def add_charger(self, rec: ChargerRecord) -> None: ...
    def add_chargers(self, recs: Sequence[ChargerRecord]) -> None: ...
    def add_report(self, rep: Report) -> None: ...
    def add_reports(self, reps: Sequence[Report]) -> None: ...
    def chargers(self) -> list[ChargerRecord]: ...
    def reports(self, charger_id: str | None = None) -> list[Report]: ...
    def prune_simulated_reports(self, keep: Collection[str]) -> None: ...
    def add_leg(self, leg: Leg) -> None: ...
    def add_legs(self, legs: Sequence[Leg], scope: str = "") -> None: ...
    def legs(self, scope: str = "") -> list[Leg]: ...
    def legs_from(self, charger_id: str, scope: str = "") -> list[Leg]: ...
    def drop_legs(self, scope: str) -> None: ...
    def sweep_stale_legs(self, max_age_s: float = STALE_LEG_SECONDS) -> None: ...
    def chains(self, scope: str, k: int, *, fail: Mapping[str, float] | None = None, weight: float = 0.0,
               banned: Collection[str] = (), max_legs: int = MAX_CHAIN_LEGS) -> list[tuple[float, list[str]]] | None: ...
    def shared_causes(self, ids: Sequence[str]) -> list[SharedCause]: ...
    def add_substation_links(self, substations: Sequence[dict], links: dict[str, tuple[str, float]]) -> None: ...
    def substation_links(self) -> dict[str, tuple[str, float]]: ...
    def add_alternate(self, segment_id: str, charger_id: str, p_works: float) -> None: ...
    def add_alternates(self, edges: Sequence[Alternate]) -> None: ...
    def alternates(self, segment_id: str) -> list[tuple[str, float]]: ...
    def add_road_segments(self, segments: Sequence[RoadSegmentRec]) -> None: ...
    def reset_road_network(self) -> None: ...
    def coverage_gaps(self, min_p: float = 0.0) -> list[RoadSegmentRec]: ...
    def add_works_with(self, vehicle_model: str, hardware_model: str, success_rate: float, n: int) -> None: ...
    def works_with(self, vehicle_model: str, hardware_model: str) -> tuple[float, int] | None: ...
    def co_reporting_pairs(self, min_shared: int = 4) -> list[tuple[str, str, int]]: ...
    def report_rings(self, min_shared: int = 4, window_hours: float | None = RING_WINDOW_HOURS) -> list[Ring]: ...
    def agreement_edges(self, min_shared: int = 4, min_jaccard: float = 0.5,
                        min_agree: float = 0.9) -> list[AgreementEdge]: ...
    def write_ring_ids(self, rings: Sequence[Ring]) -> None: ...
    def ring_ids(self) -> dict[str, str]: ...
    def counts(self) -> GraphCounts: ...
    # W2.1 / W2.5 / W2.6: physical sites, plans, aliases, name search, audit views
    def sync_sites(self) -> None: ...
    def sites(self) -> list[SiteRec]: ...
    def add_plan(self, plan: PlanRec) -> None: ...
    def plan(self, plan_id: str) -> PlanRec | None: ...
    def plan_ids(self) -> list[str]: ...
    def sweep_plans(self, keep: int, max_age_s: float, now: float | None = None) -> None: ...
    def add_aliases(self, aliases: Sequence[AliasRec]) -> None: ...
    def aliases(self) -> list[AliasRec]: ...
    def search_names(self, spoken: str, limit: int = 3) -> list[NameHit]: ...
    def why(self, plan_id: str) -> AuditView | None: ...
    def charger_neighbourhood(self, charger_id: str) -> AuditView | None: ...


class InMemoryGraph(MemoryStructure):
    """Dictionary-backed graph with the same behaviour as the Neo4j one (fallback for the API, default for tests)."""

    def __init__(self) -> None:
        self._init_structure()
        self._chargers: dict[str, ChargerRecord] = {}
        self._reports: dict[str, Report] = {}
        self._legs: dict[str, dict[tuple[str, str, str], Leg]] = defaultdict(dict)
        self._segments: dict[str, RoadSegmentRec] = {}
        self._alternates: dict[str, dict[str, tuple[float, float | None]]] = defaultdict(dict)
        self._works_with: dict[tuple[str, str], tuple[float, int]] = {}
        self._ring_ids: dict[str, str] = {}

    def add_charger(self, rec: ChargerRecord) -> None:
        self.add_chargers([rec])

    def add_chargers(self, recs: Sequence[ChargerRecord]) -> None:
        for rec in recs:
            self._chargers[rec.id] = rec

    def add_report(self, rep: Report) -> None:
        self.add_reports([rep])

    def add_reports(self, reps: Sequence[Report]) -> None:
        for rep in reps:
            if rep.charger_id not in self._chargers:
                raise KeyError(f"unknown charger {rep.charger_id!r}")
        for rep in reps:
            self._reports[report_key(rep)] = rep  # same source, charger and instant: replaced, not duplicated

    def chargers(self) -> list[ChargerRecord]:
        return sorted(self._chargers.values(), key=lambda c: c.id)

    def reports(self, charger_id: str | None = None) -> list[Report]:
        # A snapshot first: live reports (POST /api/reports) are added while other requests read.
        keep = (r for r in list(self._reports.values()) if charger_id is None or r.charger_id == charger_id)
        return sorted(keep, key=_report_order)

    def prune_simulated_reports(self, keep: Collection[str]) -> None:
        # Only this scenario's own simulated reports go: real reports and other runs' TEST- data are left alone.
        self._reports = {k: r for k, r in self._reports.items()
                         if not r.is_simulated or k in keep or r.source_id.startswith(TEST_PREFIX)}

    def add_leg(self, leg: Leg) -> None:
        self.add_legs([leg])

    def add_legs(self, legs: Sequence[Leg], scope: str = "") -> None:
        _check_scope(scope)
        for leg in legs:
            self._legs[scope][(leg.from_id, leg.to_id, leg.soc_band)] = leg  # one LEG per (pair, band): upsert

    def legs(self, scope: str = "") -> list[Leg]:
        return sorted(self._legs.get(scope, {}).values(), key=_leg_order)  # .get: reading must not create a scope

    def legs_from(self, charger_id: str, scope: str = "") -> list[Leg]:
        return [leg for leg in self.legs(scope) if leg.from_id == charger_id]

    def drop_legs(self, scope: str) -> None:
        if not scope:
            raise ValueError("refusing to drop the unscoped legs")
        _check_scope(scope)
        self._legs.pop(scope, None)

    def sweep_stale_legs(self, max_age_s: float = STALE_LEG_SECONDS) -> None:
        return None  # nothing outlives the process in memory

    def add_alternate(self, segment_id: str, charger_id: str, p_works: float) -> None:
        self.add_alternates([Alternate(segment_id, charger_id, p_works)])

    def add_alternates(self, edges: Sequence[Alternate]) -> None:
        for e in edges:
            self._alternates[e.segment_id][e.charger_id] = (e.p_works, e.km_ahead)

    def alternates(self, segment_id: str) -> list[tuple[str, float]]:
        return sorted(((cid, p) for cid, (p, _) in self._alternates[segment_id].items()), key=lambda kv: (-kv[1], kv[0]))

    def add_road_segments(self, segments: Sequence[RoadSegmentRec]) -> None:
        for seg in segments:
            self._segments[seg.segment_id] = seg

    def reset_road_network(self) -> None:
        self._segments.clear()
        self._alternates.clear()

    def coverage_gaps(self, min_p: float = 0.0) -> list[RoadSegmentRec]:
        gaps = (s for s in self._segments.values()
                if not any(p >= min_p for p, _ in self._alternates[s.segment_id].values()))
        return sorted(gaps, key=lambda s: (s.km_from, s.segment_id))

    def add_works_with(self, vehicle_model: str, hardware_model: str, success_rate: float, n: int) -> None:
        self._works_with[(vehicle_model, hardware_model)] = (success_rate, n)

    def works_with(self, vehicle_model: str, hardware_model: str) -> tuple[float, int] | None:
        return self._works_with.get((vehicle_model, hardware_model))

    def counts(self) -> GraphCounts:
        """Nodes and relationships by the schema's kinds, the way the Neo4j writes create them: Operator and Site
        nodes are shared by value, a scoped request adds START and END waypoints (RoadSegment), and a road segment
        exists once it is stored or is the end of an ALTERNATE edge."""
        # Snapshots first: request threads add legs and alternates (even a READ of ``alternates`` inserts into its
        # defaultdict), and iterating a dict while another thread grows it raises. ``list(...)`` of a built-in dict
        # view runs in C under the GIL, so each snapshot is consistent.
        chargers = list(self._chargers.values())
        reports = list(self._reports.values())
        legs = list(self._legs.items())
        alternates = list(self._alternates.items())
        works_with = list(self._works_with)
        segments = set(list(self._segments)) | {seg for seg, edges in alternates if edges}
        waypoints = 2 * sum(1 for scope, scope_legs in legs if scope and scope_legs)
        extra_nodes, extra_rels = self.structure_counts()  # Site, SiteType, Channel, Plan, Alias and their edges
        nodes = (len(chargers) + len({c.operator for c in chargers if c.operator})
                 + len({r.source_id for r in reports})
                 + len({v for v, _ in works_with}) + len({h for _, h in works_with})
                 + len(segments) + waypoints + extra_nodes)
        relationships = (len(reports) + sum(1 for c in chargers if c.operator)
                         + sum(len(scope_legs) for _, scope_legs in legs)
                         + sum(len(edges) for _, edges in alternates) + len(works_with) + extra_rels)
        return GraphCounts(nodes, relationships)

    def chains(self, scope: str, k: int, *, fail: Mapping[str, float] | None = None, weight: float = 0.0,
               banned: Collection[str] = (), max_legs: int = MAX_CHAIN_LEGS) -> list[tuple[float, list[str]]] | None:
        """``None`` = "ask the planner's own Python path": the in-memory graph is the fallback for the Cypher query."""
        return None

    def co_reporting_pairs(self, min_shared: int = 4) -> list[tuple[str, str, int]]:
        by_source: dict[str, set[str]] = defaultdict(set)
        for r in list(self._reports.values()):
            by_source[r.source_id].add(r.charger_id)
        ids = sorted(by_source)
        pairs = [(a, b, len(by_source[a] & by_source[b])) for i, a in enumerate(ids) for b in ids[i + 1:]]
        return sorted((p for p in pairs if p[2] >= min_shared), key=lambda p: (-p[2], p[0], p[1]))

    def report_rings(self, min_shared: int = 4, window_hours: float | None = RING_WINDOW_HOURS) -> list[Ring]:
        window_s = None if window_hours is None else window_hours * 3600.0
        by_charger: dict[str, list[Report]] = defaultdict(list)
        for r in list(self._reports.values()):
            by_charger[r.charger_id].append(r)
        shared: dict[tuple[str, str], set[str]] = defaultdict(set)
        for cid, reps in by_charger.items():
            for i, a in enumerate(reps):
                for b in reps[i + 1:]:
                    if a.source_id == b.source_id or a.status != b.status:
                        continue
                    if window_s is not None and abs(a.t.timestamp() - b.t.timestamp()) > window_s:
                        continue
                    shared[tuple(sorted((a.source_id, b.source_id)))].add(cid)  # type: ignore[arg-type]
        return group_rings([(a, b, len(c)) for (a, b), c in shared.items() if len(c) >= min_shared])

    def agreement_edges(self, min_shared: int = 4, min_jaccard: float = 0.5,
                        min_agree: float = 0.9) -> list[AgreementEdge]:
        """The Python twin of ``AGREEMENT_CYPHER`` (same verdicts, same thresholds, same order)."""
        verdicts = verdicts_of((r.source_id, r.charger_id, r.status) for r in list(self._reports.values()))
        return agreement_edges(verdicts, min_shared=min_shared, min_jaccard=min_jaccard, min_agree=min_agree)

    def write_ring_ids(self, rings: Sequence[Ring]) -> None:
        """Replace every Source's ``ring_id`` with this batch's (derived data: the last batch is the truth)."""
        self._ring_ids = {s: ring_id_of(r) for r in rings for s in r.sources}

    def ring_ids(self) -> dict[str, str]:
        return dict(sorted(self._ring_ids.items()))


def load_env(path: Path | None = None) -> dict[str, str]:
    """Minimal ``.env`` parser (KEY=VALUE lines). Values are returned, never printed or logged."""
    path = path or REPO_ROOT / ".env"
    out: dict[str, str] = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                out[key.strip()] = value.strip().strip('"').strip("'")
    return out


_CHARGER_UPSERT = (
    "UNWIND $rows AS row MERGE (c:Charger {id:row.id}) "
    "SET c.name=row.name, c.lat=row.lat, c.lon=row.lon, c.source_ids=row.source_ids, "
    "c.connectors=row.connectors, c.operator=row.operator, c.site_type=row.site_type "
    "FOREACH (_ IN CASE WHEN row.operator <> '' THEN [1] ELSE [] END | "
    "MERGE (o:Operator {id:row.operator}) MERGE (c)-[:RUN_BY]->(o))"
)  # Site nodes are physical places now: ``sync_sites`` writes them from all chargers at once (W2.1)
# One round trip for a whole batch: the AuraDB round trip (~100 ms) is what makes per-edge writes unusable.
_REPORT_UPSERT = (
    "UNWIND $rows AS row MATCH (c:Charger {id:row.charger_id}) MERGE (s:Source {id:row.source_id}) "
    "SET s.is_simulated = CASE WHEN s.is_simulated IS NULL THEN row.is_simulated "
    "ELSE s.is_simulated AND row.is_simulated END "
    "MERGE (s)-[r:REPORTED {key:row.key}]->(c) "
    "SET r.status=row.status, r.t=row.t, r.ts=row.ts, r.evidence=row.evidence, r.is_simulated=row.is_simulated "
    "MERGE (ch:Channel {id:row.channel}) MERGE (s)-[:VIA]->(ch) "
    "RETURN count(r) AS n"
)
# Waypoints (START/END) are RoadSegment nodes of this scope; chargers are matched by id. Both ends are resolved
# with OPTIONAL MATCH + coalesce so one statement serves charger-charger, START-charger and charger-END legs.
_LEG_UPSERT = (
    "UNWIND $waypoints AS w MERGE (:RoadSegment {id:w}) WITH count(*) AS _ UNWIND $rows AS row "
    "OPTIONAL MATCH (ac:Charger {id:row.a}) OPTIONAL MATCH (ar:RoadSegment {id:row.a_key}) "
    "OPTIONAL MATCH (bc:Charger {id:row.b}) OPTIONAL MATCH (br:RoadSegment {id:row.b_key}) "
    "WITH row, coalesce(ac, ar) AS a, coalesce(bc, br) AS b WHERE a IS NOT NULL AND b IS NOT NULL "
    "MERGE (a)-[l:LEG {soc_band: row.band, scope: $scope}]->(b) "
    "SET l.p_reach=row.p, l.minutes=row.m, l.t=timestamp() RETURN count(l) AS n"
)


class Neo4jGraph(Neo4jStructure):
    """Neo4j implementation of ``ChargerGraph``. Pass ``driver`` to inject a fake in tests."""

    def __init__(self, uri: str | None = None, user: str | None = None, password: str | None = None, *,
                 driver: Any = None, database: str | None = None, connection_timeout: float = 10.0,
                 query_timeout: float = QUERY_TIMEOUT_S, deadline_s: float | None = CLIENT_DEADLINE_S) -> None:
        self._query_timeout: float | None = None  # only the real driver gets a server-side limit
        self._deadline_s = deadline_s  # client-side, for real and fake drivers alike (F8e)
        if driver is None:
            from neo4j import GraphDatabase  # imported lazily: only needed when credentials exist

            driver = GraphDatabase.driver(uri, auth=(user, password), connection_timeout=connection_timeout,
                                          connection_acquisition_timeout=connection_timeout)
            self._query_timeout = query_timeout
        self._driver = driver
        self._database = database
        self._lock = threading.Lock()
        self._queries, self._seconds = 0, 0.0

    @classmethod
    def from_env(cls, env_path: Path | None = None, connection_timeout: float = 10.0) -> Neo4jGraph | None:
        """A graph handle, or ``None`` when NEO4J_URI / NEO4J_USERNAME / NEO4J_PASSWORD are not all set.
        Environment variables win over ``.env``. The handle may still be unreachable: call ``verify_connectivity``."""
        env = {**load_env(env_path), **{k: v for k, v in os.environ.items() if k.startswith("NEO4J_")}}
        uri, user, password = env.get("NEO4J_URI"), env.get("NEO4J_USERNAME"), env.get("NEO4J_PASSWORD")
        if not (uri and user and password):
            return None
        return cls(uri, user, password, connection_timeout=connection_timeout)

    def verify_connectivity(self) -> None:
        self._driver.verify_connectivity()

    def close(self) -> None:
        self._driver.close()

    def query_stats(self) -> QueryStats:
        with self._lock:
            return QueryStats(self._queries, self._seconds)

    def reset_query_stats(self) -> None:
        with self._lock:
            self._queries, self._seconds = 0, 0.0

    def counts(self) -> GraphCounts:
        """Every node and relationship in the database: one read-only round trip."""
        rows = self._run("MATCH (n) WITH count(n) AS nodes OPTIONAL MATCH ()-[r]->() "
                         "RETURN nodes, count(r) AS relationships")
        return GraphCounts(int(rows[0]["nodes"]), int(rows[0]["relationships"]))

    def _run(self, query: str, **params: Any) -> list[dict]:
        start = time.perf_counter()
        if self._query_timeout is not None:
            from neo4j import Query

            query = Query(query, timeout=self._query_timeout)  # type: ignore[assignment]
        def once() -> list[dict]:
            with self._driver.session(database=self._database) as session:
                return [record.data() for record in session.run(query, **params)]

        try:
            for attempt in (1, 2):
                try:
                    return run_with_deadline(once, self._deadline_s)
                except Exception as exc:
                    from neo4j.exceptions import ServiceUnavailable, SessionExpired

                    # A pooled connection the server already dropped: retry once. Every statement here is idempotent.
                    if attempt == 2 or not isinstance(exc, (ServiceUnavailable, SessionExpired)):
                        raise
            raise AssertionError("unreachable")
        finally:
            with self._lock:
                self._queries += 1
                self._seconds += time.perf_counter() - start

    def ensure_constraints(self) -> None:
        for label in NODE_LABELS:
            self._run(f"CREATE CONSTRAINT {label.lower()}_id IF NOT EXISTS FOR (n:{label}) REQUIRE n.id IS UNIQUE")
        self.ensure_name_index()

    # -- chargers and reports -------------------------------------------------------------------------------
    def add_charger(self, rec: ChargerRecord) -> None:
        self.add_chargers([rec])

    def add_chargers(self, recs: Sequence[ChargerRecord]) -> None:
        rows = [{"id": r.id, "name": r.name, "lat": r.lat, "lon": r.lon, "source_ids": r.source_ids,
                 "connectors": json.dumps([c.model_dump() for c in r.connectors]), "operator": r.operator,
                 "site_type": r.site_type} for r in recs]
        if rows:
            self._run(_CHARGER_UPSERT, rows=rows)

    def add_report(self, rep: Report) -> None:
        self.add_reports([rep])

    def add_reports(self, reps: Sequence[Report]) -> None:
        rows = [{"key": report_key(r), "charger_id": r.charger_id, "source_id": r.source_id, "status": r.status,
                 "t": r.t.isoformat(), "ts": r.t.timestamp(), "evidence": r.evidence, "is_simulated": r.is_simulated,
                 "channel": channel_of(r.source_id)} for r in reps]
        if not rows:
            return
        out = self._run(_REPORT_UPSERT, rows=rows)
        if (out[0]["n"] if out else 0) != len(rows):  # a row whose Charger node is missing is silently skipped by MATCH
            raise KeyError("a report refers to a charger that is not in the graph")

    def chargers(self) -> list[ChargerRecord]:
        rows = self._run("MATCH (c:Charger) RETURN c.id AS id, c.name AS name, c.lat AS lat, c.lon AS lon, "
                         "c.operator AS operator, c.site_type AS site_type, c.connectors AS connectors, "
                         "c.source_ids AS source_ids ORDER BY c.id")
        return [ChargerRecord(id=r["id"], name=r["name"], lat=r["lat"], lon=r["lon"], operator=r["operator"] or "",
                              site_type=r["site_type"] if r["site_type"] is not None else "unknown",
                              connectors=json.loads(r["connectors"] or "[]"),
                              source_ids=r["source_ids"] or []) for r in rows]

    def reports(self, charger_id: str | None = None) -> list[Report]:
        rows = self._run("MATCH (s:Source)-[r:REPORTED]->(c:Charger) WHERE $cid IS NULL OR c.id = $cid "
                         "RETURN c.id AS charger_id, s.id AS source_id, r.status AS status, r.t AS t, "
                         "r.evidence AS evidence, coalesce(r.is_simulated, false) AS is_simulated "
                         "ORDER BY r.ts, s.id, c.id", cid=charger_id)
        return [Report(**row) for row in rows]

    def prune_simulated_reports(self, keep: Collection[str]) -> None:
        # Only this scenario's own simulated reports: real reports and other runs' TEST- data are left alone, and
        # only simulated sources left without any report are removed.
        self._run("MATCH (s:Source)-[r:REPORTED]->() WHERE r.is_simulated = true AND NOT r.key IN $keep "
                  "AND NOT s.id STARTS WITH $test_prefix DELETE r WITH count(*) AS _ "
                  # a source with no report left goes, with its VIA edge to the Channel (WP8a: a reset leaves no node)
                  "MATCH (o:Source) WHERE o.is_simulated = true AND NOT (o)-[:REPORTED]->() DETACH DELETE o",
                  keep=sorted(keep), test_prefix=TEST_PREFIX)

    # -- legs (one scope per dispatch) ---------------------------------------------------------------------
    def add_leg(self, leg: Leg) -> None:
        self.add_legs([leg])

    def add_legs(self, legs: Sequence[Leg], scope: str = "") -> None:
        _check_scope(scope)
        if not legs:
            return
        rows = [{"a": leg.from_id, "b": leg.to_id, "a_key": f"{scope}:{leg.from_id}", "b_key": f"{scope}:{leg.to_id}",
                 "band": leg.soc_band, "p": leg.p_reach, "m": leg.minutes} for leg in legs]
        waypoints = [f"{scope}:START", f"{scope}:END"] if scope else []
        out = self._run(_LEG_UPSERT, rows=rows, waypoints=waypoints, scope=scope)
        if (out[0]["n"] if out else 0) != len(rows):  # a leg whose endpoint resolves to nothing is dropped by the WHERE
            raise KeyError("a leg refers to a charger or waypoint that is not in the graph")

    def legs(self, scope: str = "") -> list[Leg]:
        rows = self._run("MATCH (a)-[l:LEG {scope:$scope}]->(b) RETURN a.id AS a, b.id AS b, l.soc_band AS band, "
                         "l.p_reach AS p, l.minutes AS m", scope=scope)
        prefix, legs = f"{scope}:", []
        for r in rows:
            lo, hi = (int(x) for x in r["band"].split("-"))
            legs.append(Leg(r["a"].removeprefix(prefix), r["b"].removeprefix(prefix), lo, hi, r["p"], r["m"]))
        return sorted(legs, key=_leg_order)

    def legs_from(self, charger_id: str, scope: str = "") -> list[Leg]:
        return [leg for leg in self.legs(scope) if leg.from_id == charger_id]

    def chains(self, scope: str, k: int, *, fail: Mapping[str, float] | None = None, weight: float = 0.0,
               banned: Collection[str] = (), max_legs: int = MAX_CHAIN_LEGS) -> list[tuple[float, list[str]]] | None:
        """Candidate chains (cost, charger ids without START/END) computed INSIDE Neo4j (W2.3, ``chains_cypher``)."""
        if not scope:
            raise ValueError("chains need a scope")
        _check_scope(scope)
        rows = self._run(chains_cypher(max_legs), start=f"{scope}:START", end=f"{scope}:END", scope=scope,
                         band=CHAINS_BAND, fail=dict(fail or {}), w=float(weight), banned=sorted(banned), k=int(k))
        prefix = f"{scope}:"
        return [(float(r["cost"]), [i.removeprefix(prefix) for i in r["chain"]][1:-1]) for r in rows]

    def drop_legs(self, scope: str) -> None:
        if not scope:
            raise ValueError("refusing to drop the unscoped legs")
        _check_scope(scope)
        self._run("MATCH ()-[l:LEG {scope:$scope}]->() DELETE l WITH count(*) AS _ "
                  "MATCH (n:RoadSegment) WHERE n.id STARTS WITH $prefix AND n.km_from IS NULL DETACH DELETE n",
                  scope=scope, prefix=f"{scope}:")

    def sweep_stale_legs(self, max_age_s: float = STALE_LEG_SECONDS) -> None:
        self._run("MATCH ()-[l:LEG]->() WHERE l.scope <> '' AND l.t < timestamp() - $max_age_ms DELETE l "
                  "WITH count(*) AS _ MATCH (n:RoadSegment) WHERE n.km_from IS NULL AND NOT (n)--() DELETE n",
                  max_age_ms=int(max_age_s * 1000))

    # -- road network, alternates, coverage ---------------------------------------------------------------
    def add_alternate(self, segment_id: str, charger_id: str, p_works: float) -> None:
        self.add_alternates([Alternate(segment_id, charger_id, p_works)])

    def add_alternates(self, edges: Sequence[Alternate]) -> None:
        rows = [{"seg": e.segment_id, "cid": e.charger_id, "p": e.p_works, "km": e.km_ahead} for e in edges]
        if rows:
            self._run("UNWIND $rows AS row MATCH (c:Charger {id:row.cid}) MERGE (rs:RoadSegment {id:row.seg}) "
                      "MERGE (rs)-[a:ALTERNATE]->(c) SET a.p_works=row.p, a.km_ahead=row.km REMOVE a.p_reach", rows=rows)

    def alternates(self, segment_id: str) -> list[tuple[str, float]]:
        rows = self._run("MATCH (rs:RoadSegment {id:$seg})-[a:ALTERNATE]->(c:Charger) "
                         "RETURN c.id AS id, a.p_works AS p ORDER BY p DESC, id", seg=segment_id)
        return [(r["id"], r["p"]) for r in rows]

    def add_road_segments(self, segments: Sequence[RoadSegmentRec]) -> None:
        rows = [{"id": s.segment_id, "km_from": s.km_from, "km_to": s.km_to} for s in segments]
        if rows:
            self._run("UNWIND $rows AS row MERGE (rs:RoadSegment {id:row.id}) "
                      "SET rs.km_from=row.km_from, rs.km_to=row.km_to", rows=rows)

    def reset_road_network(self) -> None:
        """Delete the route's road segments (and their ALTERNATE edges): derived data, rebuilt on every sync."""
        self._run("MATCH (rs:RoadSegment) WHERE rs.km_from IS NOT NULL DETACH DELETE rs")

    def coverage_gaps(self, min_p: float = 0.0) -> list[RoadSegmentRec]:
        rows = self._run("MATCH (rs:RoadSegment) WHERE rs.km_from IS NOT NULL "
                         "OPTIONAL MATCH (rs)-[a:ALTERNATE]->(:Charger) WHERE a.p_works >= $min_p "
                         "WITH rs, count(a) AS n WHERE n = 0 "
                         "RETURN rs.id AS id, rs.km_from AS km_from, rs.km_to AS km_to ORDER BY rs.km_from, rs.id",
                         min_p=min_p)
        return [RoadSegmentRec(r["id"], r["km_from"], r["km_to"]) for r in rows]

    # -- vehicle/hardware compatibility, report analytics --------------------------------------------------
    def add_works_with(self, vehicle_model: str, hardware_model: str, success_rate: float, n: int) -> None:
        self._run("MERGE (v:VehicleModel {id:$v}) MERGE (h:HardwareModel {id:$h}) "
                  "MERGE (v)-[w:WORKS_WITH]->(h) SET w.success_rate=$rate, w.n=$n",
                  v=vehicle_model, h=hardware_model, rate=success_rate, n=n)

    def works_with(self, vehicle_model: str, hardware_model: str) -> tuple[float, int] | None:
        rows = self._run("MATCH (:VehicleModel {id:$v})-[w:WORKS_WITH]->(:HardwareModel {id:$h}) "
                         "RETURN w.success_rate AS rate, w.n AS n", v=vehicle_model, h=hardware_model)
        return (rows[0]["rate"], rows[0]["n"]) if rows else None

    def co_reporting_pairs(self, min_shared: int = 4) -> list[tuple[str, str, int]]:
        rows = self._run("MATCH (a:Source)-[:REPORTED]->(c:Charger)<-[:REPORTED]-(b:Source) WHERE a.id < b.id "
                         "WITH a.id AS a, b.id AS b, count(DISTINCT c) AS shared WHERE shared >= $k "
                         "RETURN a, b, shared ORDER BY shared DESC, a, b", k=min_shared)
        return [(r["a"], r["b"], r["shared"]) for r in rows]

    def report_rings(self, min_shared: int = 4, window_hours: float | None = RING_WINDOW_HOURS) -> list[Ring]:
        """Source pairs that file the same verdict on >= ``min_shared`` chargers (within the time window), grouped."""
        window = "" if window_hours is None else "AND abs(ra.ts - rb.ts) <= $window_s "
        rows = self._run("MATCH (a:Source)-[ra:REPORTED]->(c:Charger)<-[rb:REPORTED]-(b:Source) "
                         "WHERE a.id < b.id AND ra.status = rb.status " + window +
                         "WITH a.id AS a, b.id AS b, count(DISTINCT c) AS shared WHERE shared >= $k "
                         "RETURN a, b, shared", k=min_shared,
                         **({} if window_hours is None else {"window_s": window_hours * 3600.0}))
        return group_rings([(r["a"], r["b"], r["shared"]) for r in rows])

    def agreement_edges(self, min_shared: int = 4, min_jaccard: float = 0.5,
                        min_agree: float = 0.9) -> list[AgreementEdge]:
        rows = self._run(AGREEMENT_CYPHER, min_shared=int(min_shared), min_jaccard=float(min_jaccard),
                         min_agree=float(min_agree))
        return [AgreementEdge(r["a"], r["b"], int(r["shared"]), float(r["jaccard"])) for r in rows]

    def write_ring_ids(self, rings: Sequence[Ring]) -> None:
        rows = [{"sid": s, "rid": ring_id_of(r)} for r in rings for s in r.sources]
        self._run("MATCH (s:Source) WHERE s.ring_id IS NOT NULL REMOVE s.ring_id")
        if rows:
            out = self._run("UNWIND $rows AS row MATCH (s:Source {id: row.sid}) SET s.ring_id = row.rid "
                            "RETURN count(s) AS n", rows=rows)
            if (out[0]["n"] if out else 0) != len(rows):
                raise KeyError("a ring member is not a Source in the graph")

    def ring_ids(self) -> dict[str, str]:
        rows = self._run("MATCH (s:Source) WHERE s.ring_id IS NOT NULL RETURN s.id AS sid, s.ring_id AS rid ORDER BY sid")
        return {r["sid"]: r["rid"] for r in rows}

    def delete_test_data(self, prefix: str = TEST_PREFIX) -> None:
        """Delete only nodes whose id starts with ``prefix`` (the smoke test's clearly fake data)."""
        if not prefix:
            raise ValueError("refusing to delete without a prefix")
        self._run("MATCH (n) WHERE n.id STARTS WITH $prefix DETACH DELETE n", prefix=prefix)
