"""Live parity: the same dispatch through the in-memory graph and through Neo4j must give the same answer.

Needs NEO4J_* in .env (or the environment) and a reachable database; otherwise every test here skips, with a
reason that names only the error class. Nothing here prints or logs credentials. The fake-data analytics test
uses ids starting with ``TEST-`` and deletes them afterwards.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from api.service import EngineService
from verity.chargers import load_chargers
from verity.schemas import ChargerRecord, Report
from verity.trust.graph import Alternate, InMemoryGraph, Leg, Neo4jGraph, Ring, RoadSegmentRec
from verity.trust.graph_build import GraphUnavailable, open_graph

N_RUNS = 500  # parity does not depend on the run count; fewer runs keep the live tests quick
DEMO_SOC = {"V07": 82, "V12": 62, "V19": 40}
MAX_DISPATCH_S = 15.0  # the budget from the brief: a dispatch through Neo4j must stay under this
T0 = datetime(2026, 10, 27, 10, 0, tzinfo=timezone(timedelta(hours=5, minutes=30)))
BASE = {"origin": {"lat": 28.5355, "lon": 77.3910}, "destination": {"lat": 27.1767, "lon": 78.0081},
        "departure_time": "2026-10-28T14:00:00+05:30", "passengers": 4, "ac_on": True, "target_confidence": 0.9}


def request(vid: str) -> dict:
    return {**BASE, "vehicle_id": vid, "start_soc_pct": DEMO_SOC[vid]}


def live_graph() -> Neo4jGraph:
    graph = Neo4jGraph.from_env()
    if graph is None:
        pytest.skip("no NEO4J_URI / NEO4J_USERNAME / NEO4J_PASSWORD")
    try:
        graph.verify_connectivity()
    except Exception as exc:  # noqa: BLE001 - class name only: the message may echo the connection string
        graph.close()
        pytest.skip(f"Neo4j unreachable ({type(exc).__name__})")
    return graph


@pytest.fixture(scope="module")
def services():
    memory = EngineService(n_runs=N_RUNS, graph_backend="memory")
    try:
        neo4j = EngineService(n_runs=N_RUNS, graph_backend="neo4j")
    except GraphUnavailable as exc:
        memory.close()
        pytest.skip(str(exc))
    yield memory, neo4j
    neo4j.close()
    memory.close()


def test_neo4j_is_really_the_graph_in_use(services) -> None:
    memory, neo4j = services
    assert (memory.health()["neo4j"], neo4j.health()["neo4j"]) == ("in_memory", "connected")


def test_every_charger_has_the_same_trust_in_both_graphs(services) -> None:
    memory, neo4j = services
    assert set(memory.trust) == set(neo4j.trust) and len(memory.trust) > 100
    for cid, m in memory.trust.items():
        n = neo4j.trust[cid]
        assert (n.n_reports, n.trust_level) == (m.n_reports, m.trust_level), cid
        assert n.p_works == pytest.approx(m.p_works, rel=1e-9, abs=1e-12), cid
        assert (n.p_lo, n.p_hi) == pytest.approx((m.p_lo, m.p_hi), rel=1e-9, abs=1e-12), cid


@pytest.mark.parametrize("vid", sorted(DEMO_SOC))
def test_dispatch_is_identical_through_either_graph(services, vid: str) -> None:
    memory, neo4j = services
    a, b = memory.dispatch(request(vid)), neo4j.dispatch(request(vid))
    assert neo4j.graph.backend == "neo4j", "fell back to memory: the comparison would be meaningless"
    for key in ("decision", "confidence", "stops", "alternates", "arrival_soc", "bingo_line", "levers", "refusal_reason"):
        assert a[key] == b[key], key
    assert {k: v for k, v in a.items() if k != "compute_ms"} == {k: v for k, v in b.items() if k != "compute_ms"}
    timing = neo4j.last_graph_timing
    assert timing["queries"] > 0 and timing["total_s"] < MAX_DISPATCH_S, timing
    print(f"\n{vid}: {timing['queries']} Neo4j queries, {timing['graph_s']:.2f} s of {timing['total_s']:.2f} s")


def test_a_dispatch_leaves_no_legs_behind(services) -> None:
    _, neo4j = services
    neo4j.dispatch(request("V12"))
    left = neo4j.graph.neo4j._run("MATCH ()-[l:LEG]->() RETURN count(l) AS n")[0]["n"]
    waypoints = neo4j.graph.neo4j._run("MATCH (n:RoadSegment) WHERE n.km_from IS NULL RETURN count(n) AS n")[0]["n"]
    assert (left, waypoints) == (0, 0)


def test_simulated_reports_and_sources_are_tagged_and_syncing_again_changes_nothing(services) -> None:
    _, neo4j = services
    run = neo4j.graph.neo4j._run
    counts = "MATCH ()-[r:REPORTED]->() WITH count(r) AS r MATCH (c:Charger) WITH r, count(c) AS c MATCH (s:Source) RETURN r, c, count(s) AS s"
    before = run(counts)[0]
    assert before["r"] >= len(neo4j.demo_reports) > 0
    untagged = run("MATCH ()-[r:REPORTED]->() WHERE r.evidence = 'SIMULATED demo report' AND coalesce(r.is_simulated, false) = false "
                   "RETURN count(r) AS n")[0]["n"]
    sources = run("MATCH (s:Source) WHERE s.id STARTS WITH 'demo-user-' AND coalesce(s.is_simulated, false) = false "
                  "RETURN count(s) AS n")[0]["n"]
    assert (untagged, sources) == (0, 0)
    directory, _ = load_chargers()
    again = open_graph(directory, neo4j.demo_reports, prefer="neo4j")
    try:
        assert run(counts)[0] == before
    finally:
        again.close()


def test_a_dropped_link_mid_request_falls_back_and_health_follows(monkeypatch: pytest.MonkeyPatch, services) -> None:
    memory, _ = services
    live_graph().close()  # skip here, not later, when the database is unreachable
    broken = EngineService(n_runs=N_RUNS, graph_backend="neo4j")
    try:
        assert broken.health()["neo4j"] == "connected"
        def drop(self, *a, **k):
            raise ConnectionError("link dropped")
        monkeypatch.setattr(Neo4jGraph, "_run", drop)
        got = broken.dispatch(request("V12"))
        want = memory.dispatch(request("V12"))
        assert broken.health()["neo4j"] == "in_memory"
        assert {k: v for k, v in got.items() if k != "compute_ms"} == {k: v for k, v in want.items() if k != "compute_ms"}
    finally:
        monkeypatch.undo()
        broken.close()


def test_start_and_end_legs_round_trip_through_neo4j_and_drop_without_a_trace() -> None:
    graph = live_graph()
    chargers = [ChargerRecord(id=f"TEST-L{i}", name=f"TEST-L{i}", lat=0.0, lon=0.0) for i in range(2)]
    legs = [Leg("START", "TEST-L0", 0, 10, 0.5, 30.0), Leg("TEST-L0", "TEST-L1", 10, 20, 0.6, 20.0),
            Leg("TEST-L1", "END", 90, 100, 0.99, 10.0)]
    try:
        graph.delete_test_data()
        graph.add_chargers(chargers)
        graph.add_legs(legs, scope="TEST-scope")
        assert graph.legs("TEST-scope") == legs  # START and END survive: the case the first Neo4j version lost silently
        assert graph.legs("TEST-other") == []  # another scope sees nothing
        graph.drop_legs("TEST-scope")
        assert graph.legs("TEST-scope") == []
        waypoints = graph._run("MATCH (n:RoadSegment) WHERE n.id STARTS WITH 'TEST-scope:' RETURN count(n) AS n")
        assert waypoints[0]["n"] == 0
    finally:
        graph.delete_test_data()
        graph.close()


def test_queries_find_a_fake_ring_and_a_coverage_gap_and_match_the_in_memory_answers() -> None:
    graph = live_graph()
    # Far from any route: even if a running server ever read these, they could not be planned with.
    chargers = [ChargerRecord(id=f"TEST-C{i}", name=f"TEST-C{i}", lat=0.0, lon=0.0) for i in range(5)]
    reports = [Report(charger_id=f"TEST-C{i}", source_id=f"TEST-S{k}", status="broken", is_simulated=True,
                      t=T0 + timedelta(minutes=i * 1000 + k)) for i in range(5) for k in range(3)]
    memory = InMemoryGraph()
    memory.add_chargers(chargers)
    memory.add_reports(reports)
    try:
        graph.delete_test_data()
        graph.add_chargers(chargers)
        graph.add_reports(reports)
        graph.add_road_segments([RoadSegmentRec("TEST-seg-0", 0.0, 10.0), RoadSegmentRec("TEST-seg-1", 10.0, 20.0)])
        graph.add_alternates([Alternate("TEST-seg-0", "TEST-C0", 0.9, 5.0)])
        ours = lambda rings: [r for r in rings if all(s.startswith("TEST-") for s in r.sources)]  # noqa: E731
        assert ours(graph.report_rings(4, 72.0)) == ours(memory.report_rings(4, 72.0)) == [Ring(("TEST-S0", "TEST-S1", "TEST-S2"), 5)]
        assert [s.segment_id for s in graph.coverage_gaps() if s.segment_id.startswith("TEST-")] == ["TEST-seg-1"]
    finally:
        graph.delete_test_data()
        graph.close()
