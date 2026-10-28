"""Build the demo graph and choose its backend.

``open_graph`` loads chargers and reports into Neo4j when credentials exist and the connection works, and into an
in-memory mirror always. Any failure (no credentials, unreachable, auth, a query erroring mid-sync) leaves the
mirror in use and records why; the reason holds exception CLASS names only, never messages or credentials.
``GraphHandle.call`` is how the API reads: on Neo4j, and if the link drops mid-request, once more on the mirror.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TypeVar

from neo4j.exceptions import DriverError, Neo4jError

from ..chargers import RouteCharger
from ..schemas import ChargerRecord, Report
from .engine import ChargerTrust
from .graph import Alternate, ChargerGraph, InMemoryGraph, Neo4jGraph, RoadSegmentRec, report_key

log = logging.getLogger(__name__)
T = TypeVar("T")

SEGMENT_KM = 10.0  # the route is cut into road segments of this length for the coverage-gap query
MIN_ALT_P_WORKS = 0.5  # an alternate below this does not count as coverage (approximate - verify)
# Only these mean "the graph is unavailable". A bug in the engine code run on the graph must surface as that bug,
# not switch Neo4j off for everyone and fail again on the mirror. (ConnectionError and timeouts are OSErrors.)
BACKEND_FAILURES = (Neo4jError, DriverError, OSError)


class GraphUnavailable(RuntimeError):
    """``prefer="neo4j"`` was requested but Neo4j cannot be used."""


def usable_alternate(p_works: float, trust_level: str) -> bool:
    """Does a charger count as a backup for coverage? Golden rule 6 (refuse rather than guess): a charger nobody
    has reported on ("unknown") is not coverage, however good its prior looks; otherwise it must be more likely
    than not to work."""
    return trust_level != "unknown" and p_works >= MIN_ALT_P_WORKS


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
        if old is not None:
            try:
                old.close()
            except Exception:  # noqa: BLE001 - closing a dead link may fail; we are already falling back
                pass

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
    graph.add_reports(reports)
    graph.prune_simulated_reports({report_key(r) for r in reports if r.is_simulated})
    graph.sweep_stale_legs()


def open_graph(chargers: Sequence[ChargerRecord], reports: Sequence[Report], *, prefer: str = "auto") -> GraphHandle:
    """``prefer``: auto (Neo4j if it works), memory (never connect), neo4j (raise ``GraphUnavailable`` if not)."""
    if prefer not in ("auto", "memory", "neo4j"):
        raise ValueError(f"VERITY_GRAPH must be auto, memory or neo4j, not {prefer!r}")
    mirror = InMemoryGraph()
    mirror.add_chargers(chargers)
    mirror.add_reports(reports)
    if prefer == "memory":
        return GraphHandle(mirror, "memory", mirror, "VERITY_GRAPH=memory")
    reason, graph = "no NEO4J_URI / NEO4J_USERNAME / NEO4J_PASSWORD", None
    try:
        graph = Neo4jGraph.from_env()  # inside the try: a malformed URI raises here and its message can hold the URI
        if graph is not None:
            _sync_neo4j(graph, chargers, reports)
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
    (the planner's own backup window). ALTERNATE ``p_reach`` is the alternate's P(works) from the trust model:
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
