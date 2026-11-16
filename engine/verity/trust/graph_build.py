"""Build the demo graph and choose its backend.

``open_graph`` loads chargers and reports into Neo4j when credentials exist and the connection works, and into an
in-memory mirror always. Any failure (no credentials, unreachable, auth, a query erroring mid-sync) leaves the
mirror in use and records why; the reason holds exception CLASS names only, never messages or credentials.
``GraphHandle.call`` is how the API reads: on Neo4j, and if the link drops mid-request, once more on the mirror.
"""

from __future__ import annotations

import json
import logging
import math
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from neo4j.exceptions import DriverError, Neo4jError

from ..chargers import RouteCharger
from ..paths import DATA_DIR
from ..places import normalise
from ..schemas import ChargerRecord, Report
from .engine import ChargerTrust
from .graph import TEST_PREFIX, Alternate, ChargerGraph, InMemoryGraph, Neo4jGraph, RoadSegmentRec, report_key
from .graph_types import (
    AVOIDED_LOW_P_WORKS,
    AVOIDED_NO_REPORTS,
    AliasRec,
    PlanAvoidedRec,
    PlanBackupRec,
    PlanRec,
    PlanStopRec,
)

log = logging.getLogger(__name__)
T = TypeVar("T")

ALIAS_FILE = DATA_DIR / "gazetteer" / "charger_aliases.json"
MAX_AVOIDED_STORED = 30  # AVOIDED edges kept per plan (the rest are only counted in Plan.avoided_total)
PLAN_KEEP = 300  # newest plans kept in the graph: the same bound as the service's plan cache
PLAN_MAX_AGE_S = 24 * 3600.0  # and none older than a day (swept like the plan legs are)
SEGMENT_KM = 10.0  # the route is cut into road segments of this length for the coverage-gap query
MIN_ALT_P_WORKS = 0.5  # an alternate below this does not count as coverage (approximate - verify)
# Only these mean "the graph is unavailable". A bug in the engine code run on the graph must surface as that bug,
# not switch Neo4j off for everyone and fail again on the mirror. (ConnectionError and timeouts are OSErrors.)
BACKEND_FAILURES = (Neo4jError, DriverError, OSError)


class GraphUnavailable(RuntimeError):
    """``prefer="neo4j"`` was requested but Neo4j cannot be used."""


def _close_quietly(graph: Neo4jGraph) -> None:
    try:
        graph.close()
    except Exception:  # noqa: BLE001 - closing a dead link may fail; we are already falling back
        pass


def avoided_reason(p_works: float, trust_level: str) -> str | None:
    """Why a charger does not count as a backup, or None when it does. Golden rule 6 (refuse rather than guess): a
    charger nobody has reported on ("unknown") is not coverage, however good its prior looks; otherwise it must be
    more likely than not to work. The one rule behind both the coverage-gap query and the plan's AVOIDED edges."""
    if trust_level == "unknown":
        return AVOIDED_NO_REPORTS
    return AVOIDED_LOW_P_WORKS if p_works < MIN_ALT_P_WORKS else None


def usable_alternate(p_works: float, trust_level: str) -> bool:
    """Does a charger count as a backup for coverage? See ``avoided_reason``."""
    return avoided_reason(p_works, trust_level) is None


def plan_record(plan_id: str, response: dict, route_chargers: Sequence[RouteCharger], trust: dict[str, ChargerTrust],
                alt_ahead_km: float, created_ts: float) -> PlanRec:
    """A released plan as graph edges. AVOIDED = chargers inside a stop's backup window that the coverage rule refuses
    (``avoided_reason``); stored nearest-first up to MAX_AVOIDED_STORED, with the full count kept in ``avoided_total``."""
    stops = tuple(PlanStopRec(i, s["charger_id"]) for i, s in enumerate(response["stops"]))
    backups = tuple(PlanBackupRec(a["for_stop_index"], a["charger_id"], a["p_reach"], a["p_works"])
                    for a in response["alternates"])
    by_id = {rc.charger.id: rc for rc in route_chargers}
    used = {s.charger_id for s in stops} | {b.charger_id for b in backups}
    seen: dict[str, PlanAvoidedRec] = {}
    for stop in stops:
        origin = by_id.get(stop.charger_id)
        if origin is None:
            continue
        for rc in sorted(route_chargers, key=lambda r: (r.km_along, r.charger.id)):
            t = trust.get(rc.charger.id)
            if rc.charger.id in used or rc.charger.id in seen or t is None:
                continue
            if 0.0 < rc.km_along - origin.km_along <= alt_ahead_km:
                reason = avoided_reason(t.p_works, t.trust_level)
                if reason is not None:
                    seen[rc.charger.id] = PlanAvoidedRec(stop.order, rc.charger.id, reason)
    avoided = tuple(list(seen.values())[:MAX_AVOIDED_STORED])
    return PlanRec(plan_id, response["vehicle_id"], response["decision"], response["confidence"], response["n_runs"],
                   created_ts, stops, backups, avoided, len(seen), bool(response.get("is_simulated", True)))


def derive_aliases(chargers: Sequence[ChargerRecord], alias_file: Path | None = None) -> list[AliasRec]:
    """Names a driver might say: each charger's operator (when it is not already its name) and the entries of the
    committed alias file. Nothing is invented: unknown ids in the file are skipped, and a charger with neither stays
    findable only by its own directory name."""
    out = [AliasRec(c.operator, c.id, "operator") for c in chargers
           if c.operator and normalise(c.operator) != normalise(c.name) and not c.id.startswith(TEST_PREFIX)]
    path = alias_file or ALIAS_FILE
    if path.is_file():
        known = {c.id for c in chargers}
        for a in json.loads(path.read_text(encoding="utf-8")).get("aliases", []):
            if a["charger_id"] in known and normalise(a["text"]):
                out.append(AliasRec(a["text"], a["charger_id"], "alias_file"))
    return out


@dataclass
class GraphHandle:
    """The graph in use plus the always-present in-memory mirror (fallback and parity reference)."""

    graph: ChargerGraph
    backend: str  # "neo4j" | "memory"
    mirror: InMemoryGraph
    reason: str

    @property
    def neo4j(self) -> Neo4jGraph | None:
        return self.graph if isinstance(self.graph, Neo4jGraph) else None

    def fall_back(self, reason: str) -> None:
        old = self.neo4j
        self.graph, self.backend, self.reason = self.mirror, "memory", reason
        log.warning("graph backend: in-memory (%s)", reason)
        if old is not None:  # closing a stalled link can itself hang: do it off the request thread (F8e)
            threading.Thread(target=_close_quietly, args=(old,), name="neo4j-close", daemon=True).start()

    def call(self, fn: Callable[[ChargerGraph], T]) -> T:
        """Run ``fn`` on the graph in use; if Neo4j fails, switch to the mirror and run it again from scratch."""
        if self.backend == "neo4j":
            try:
                return fn(self.graph)
            except BACKEND_FAILURES as exc:
                self.fall_back(f"{type(exc).__name__} during a request")
        return fn(self.mirror)

    def populate(self, fn: Callable[[ChargerGraph], None]) -> None:
        """Apply a write to the mirror, and to Neo4j while it is in use (a Neo4j failure falls back)."""
        fn(self.mirror)
        if self.backend == "neo4j":
            try:
                fn(self.graph)
            except BACKEND_FAILURES as exc:
                self.fall_back(f"{type(exc).__name__} while syncing")

    def close(self) -> None:
        if self.neo4j is not None:
            self.neo4j.close()


def _sync_neo4j(graph: Neo4jGraph, chargers: Sequence[ChargerRecord], reports: Sequence[Report]) -> None:
    graph.verify_connectivity()
    graph.ensure_constraints()
    graph.add_chargers(chargers)
    graph.sync_sites()
    graph.add_reports(reports)
    graph.prune_simulated_reports({report_key(r) for r in reports if r.is_simulated})
    graph.add_aliases(derive_aliases(chargers))
    graph.sweep_stale_legs()
    graph.sweep_plans(PLAN_KEEP, PLAN_MAX_AGE_S)


def open_graph(chargers: Sequence[ChargerRecord], reports: Sequence[Report], *, prefer: str = "auto") -> GraphHandle:
    """``prefer``: auto (Neo4j if it works), memory (never connect), neo4j (raise ``GraphUnavailable`` if not)."""
    if prefer not in ("auto", "memory", "neo4j"):
        raise ValueError(f"VERITY_GRAPH must be auto, memory or neo4j, not {prefer!r}")
    mirror = InMemoryGraph()
    mirror.add_chargers(chargers)
    mirror.add_reports(reports)
    mirror.add_aliases(derive_aliases(chargers))
    if prefer == "memory":
        return GraphHandle(mirror, "memory", mirror, "VERITY_GRAPH=memory")
    reason, graph = "no NEO4J_URI / NEO4J_USERNAME / NEO4J_PASSWORD", None
    try:
        graph = Neo4jGraph.from_env()  # inside the try: a malformed URI raises here and its message can hold the URI
        if graph is not None:
            _sync_neo4j(graph, chargers, reports)
            known = {c.id for c in chargers}  # real reports written in an earlier run: the mirror must hold them too,
            mirror.add_reports([r for r in graph.reports() if not r.is_simulated and r.charger_id in known])  # or a fallback loses them
            log.info("graph backend: neo4j (%d chargers, %d reports synced)", len(chargers), len(reports))
            return GraphHandle(graph, "neo4j", mirror, "connected")
    except Exception as exc:  # noqa: BLE001 - class name only: messages may echo the connection string
        reason = f"Neo4j unavailable ({type(exc).__name__})"
        if graph is not None:
            try:
                graph.close()
            except Exception:  # noqa: BLE001
                pass
    if prefer == "neo4j":
        raise GraphUnavailable(reason)
    log.warning("graph backend: in-memory (%s)", reason)
    return GraphHandle(mirror, "memory", mirror, reason)


def populate_road_network(graph: ChargerGraph, route_chargers: Sequence[RouteCharger], trust: dict[str, ChargerTrust],
                          total_km: float, alt_ahead_km: float, segment_km: float = SEGMENT_KM) -> None:
    """Cut the route into road segments and link each to the usable chargers lying ahead within ``alt_ahead_km``
    (the planner's own backup window). ALTERNATE ``p_works`` is the alternate's P(works) from the trust model:
    whether a vehicle has the energy to get there is settled per vehicle by the Monte Carlo, not stored here."""
    n = max(math.ceil(total_km / segment_km), 1)
    segments = [RoadSegmentRec(f"seg-{i:04d}", i * segment_km, min((i + 1) * segment_km, total_km)) for i in range(n)]
    edges = []
    for seg in segments:
        for rc in route_chargers:
            ahead, t = rc.km_along - seg.km_from, trust.get(rc.charger.id)
            if 0.0 < ahead <= alt_ahead_km and t is not None and usable_alternate(t.p_works, t.trust_level):
                edges.append(Alternate(seg.segment_id, rc.charger.id, t.p_works, ahead))
    graph.reset_road_network()
    graph.add_road_segments(segments)
    graph.add_alternates(edges)
