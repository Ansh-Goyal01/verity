"""The service reads trust and dispatch inputs through the graph, per request, and survives the graph failing.

Hermetic: the in-memory graph stands in for Neo4j (a stand-in that can be told to fail). The live equivalence of
the two real backends is tests/test_graph_parity.py.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from api.service import EngineService
from verity.schemas import Report
from verity.trust.graph import InMemoryGraph
from verity.trust.graph_build import GraphHandle
from verity.trust.reports_sim import NOW

BASE = {"origin": {"lat": 28.5355, "lon": 77.3910}, "destination": {"lat": 27.1767, "lon": 78.0081},
        "departure_time": "2026-10-28T14:00:00+05:30", "passengers": 4, "ac_on": True, "target_confidence": 0.9}


def request(vid: str, soc: int) -> dict:
    return {**BASE, "vehicle_id": vid, "start_soc_pct": soc}


class FlakyGraph(InMemoryGraph):
    """Stands in for a Neo4j that can drop the link."""

    broken = False

    def chargers(self):
        if self.broken:
            raise ConnectionError("link dropped")
        return super().chargers()


@pytest.fixture(scope="module")
def svc():
    service = EngineService(n_runs=300, graph_backend="memory")
    yield service
    service.close()


def test_memory_backend_reports_in_memory(svc: EngineService) -> None:
    assert svc.health()["neo4j"] == "in_memory" and svc.graph.backend == "memory"


def test_trust_for_a_dispatch_is_read_from_the_graph_not_from_a_side_copy(svc: EngineService) -> None:
    cid = svc.demo_reports[0].charger_id
    before = svc.trust[cid].p_works
    svc.graph.populate(lambda g: g.add_reports([
        Report(charger_id=cid, source_id=f"late-{i}", status="broken", t=NOW - timedelta(days=1, minutes=i))
        for i in range(8)]))
    svc.dispatch(request("V07", 81))
    assert svc.trust[cid].p_works < before - 0.05


def test_health_says_connected_only_while_neo4j_is_in_use_and_a_failure_falls_back(svc: EngineService) -> None:
    original = svc.graph
    flaky = FlakyGraph()
    flaky.add_chargers(svc.graph.graph.chargers())
    flaky.add_reports(svc.graph.graph.reports())
    svc.graph = GraphHandle(flaky, "neo4j", original.mirror, "connected")
    try:
        assert svc.health()["neo4j"] == "connected"
        flaky.broken = True
        response = svc.dispatch(request("V12", 61))  # the read fails; the request still completes on the mirror
        assert response["decision"] in ("GO", "NO_GO", "INSUFFICIENT_EVIDENCE")
        assert svc.health()["neo4j"] == "in_memory" and svc.graph.backend == "memory"
    finally:
        svc.graph = original


def test_fake_test_chargers_in_a_shared_graph_never_reach_planning(svc: EngineService) -> None:
    from verity.schemas import ChargerRecord

    fake = ChargerRecord(id="TEST-C9", name="fake charger", lat=28.5355, lon=77.3910)  # right at the route start
    svc.graph.populate(lambda g: g.add_chargers([fake]))
    svc.dispatch(request("V19", 39))
    assert "TEST-C9" not in svc.trust and all(c.id != "TEST-C9" for c in svc.chargers)
    assert all(rc.charger.id != "TEST-C9" for rc in svc.near)
