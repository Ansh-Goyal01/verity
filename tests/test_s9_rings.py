"""S9 (W2.4): report rings as graph analytics - Jaccard agreement edges, communities, ``ring_id`` on Source.

Offline: the edge definition, and the in-memory graph against the numpy detector E5b uses. Live (skips cleanly when
Neo4j is unreachable): the Cypher edges against the in-memory twin, on clearly fake ``TEST-`` data that is deleted.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest

from verity.schemas import ChargerRecord, Report
from verity.trust.graph import InMemoryGraph, Neo4jGraph, Ring, communities, ring_id_of
from verity.trust.reports_sim import NOW, WorldConfig, simulate_world
from verity.trust.ring_edges import AgreementEdge, agreement_edges, verdicts_of
from verity.trust.rings import detect_rings
from verity.trust.source_trust import ReportSet

T0 = datetime(2026, 10, 27, 10, 0, tzinfo=timezone(timedelta(hours=5, minutes=30)))


# -- the edge definition --------------------------------------------------------------------------------------------
def test_a_verdict_is_the_sources_majority_and_occupied_counts_as_working() -> None:
    got = verdicts_of([("a", "c1", "working"), ("a", "c1", "broken"), ("a", "c1", "broken"),  # 1 of 3: broken
                       ("a", "c2", "working"), ("a", "c2", "broken"),                         # a tie: working
                       ("a", "c3", "occupied"), ("b", "c1", "no_power")])
    assert got == {("a", "c1"): False, ("a", "c2"): True, ("a", "c3"): True, ("b", "c1"): False}


def ring_verdicts(n_sources: int = 3, n_chargers: int = 5) -> dict[tuple[str, str], bool]:
    return {(f"s{k}", f"c{i}"): False for k in range(n_sources) for i in range(n_chargers)}


def test_sources_that_share_enough_chargers_and_agree_are_joined() -> None:
    edges = agreement_edges(ring_verdicts(), min_shared=4)
    assert [(e.a, e.b, e.shared, e.jaccard) for e in edges] == [("s0", "s1", 5, 1.0), ("s0", "s2", 5, 1.0), ("s1", "s2", 5, 1.0)]


def test_too_few_shared_chargers_disagreement_or_low_overlap_break_an_edge() -> None:
    assert agreement_edges(ring_verdicts(n_chargers=3), min_shared=4) == []  # share 3 < 4
    split = {**ring_verdicts(2), **{("s1", f"c{i}"): True for i in range(5)}}
    assert agreement_edges(split, min_shared=4) == []  # opposite verdicts on every shared charger
    wide = {**ring_verdicts(2), **{("s1", f"x{i}"): False for i in range(20)}}  # s1 also reports on 20 other chargers
    assert agreement_edges(wide, min_shared=4, min_jaccard=0.5) == []  # Jaccard 5 / 25 = 0.2
    assert len(agreement_edges(wide, min_shared=4, min_jaccard=0.1)) == 1


def test_communities_are_the_components_of_at_least_min_size() -> None:
    edges = [AgreementEdge("a", "b", 5, 1.0), AgreementEdge("b", "c", 5, 1.0), AgreementEdge("x", "y", 5, 1.0)]
    assert communities(edges, min_size=3) == [Ring(("a", "b", "c"), 5)]
    assert communities(edges, min_size=2) == [Ring(("a", "b", "c"), 5), Ring(("x", "y"), 5)]
    assert ring_id_of(Ring(("a", "b", "c"), 5)) == "ring:a"


# -- the in-memory graph agrees with the numpy detector the experiment uses --------------------------------------
def graph_from(world) -> InMemoryGraph:
    g = InMemoryGraph()
    g.add_chargers(world.chargers)
    g.add_reports(world.reports)
    return g


@pytest.mark.parametrize("extra", [dict(ring_size=12, ring_window_h=120.0, n_repeat=8, repeat_targets=15),
                                   dict(n_repeat=8, repeat_targets=6)])
def test_the_graphs_communities_are_the_numpy_detectors_rings(extra: dict) -> None:
    world = simulate_world(WorldConfig(seed=100, n_honest=100, reports_per_honest=(6, 24), **extra))
    rs = ReportSet.from_reports(world.reports, [c.id for c in world.chargers], NOW)
    numpy_rings = {frozenset(rs.source_ids[i] for i in g) for g in detect_rings(rs, max_hours=math.inf).groups}
    graph_rings = {frozenset(r.sources) for r in communities(graph_from(world).agreement_edges())}
    assert graph_rings == numpy_rings


def test_the_evasive_ring_is_found_without_a_time_window_and_the_burst_detector_misses_it() -> None:
    world = simulate_world(WorldConfig(seed=100, n_honest=100, reports_per_honest=(6, 24), ring_size=12, ring_window_h=120.0))
    rs = ReportSet.from_reports(world.reports, [c.id for c in world.chargers], NOW)
    assert detect_rings(rs).flagged == ()  # E5's detector (12 h window): nothing
    rings = communities(graph_from(world).agreement_edges())
    assert [set(r.sources) for r in rings] == [world.ring_sources]


def test_ring_ids_are_written_back_replaced_and_cleared() -> None:
    g = InMemoryGraph()
    g.write_ring_ids([Ring(("s0", "s1", "s2"), 5), Ring(("t0", "t1", "t2"), 4)])
    assert g.ring_ids() == {"s0": "ring:s0", "s1": "ring:s0", "s2": "ring:s0", "t0": "ring:t0", "t1": "ring:t0", "t2": "ring:t0"}
    g.write_ring_ids([Ring(("t0", "t1", "t2"), 4)])  # the last batch is the truth: s0..s2 are no longer flagged
    assert set(g.ring_ids()) == {"t0", "t1", "t2"}
    g.write_ring_ids([])
    assert g.ring_ids() == {}


# -- live: Cypher against the in-memory twin ---------------------------------------------------------------------
def fake_world() -> tuple[list[ChargerRecord], list[Report]]:
    chargers = [ChargerRecord(id=f"TEST-R{i}", name=f"TEST-R{i}", lat=0.0, lon=float(i)) for i in range(8)]
    reports = [Report(charger_id=f"TEST-R{i}", source_id=f"TEST-S{k}", status="broken", t=T0, is_simulated=True)
               for k in range(3) for i in range(6)]  # a ring of three: the same six chargers, all "broken"
    reports += [Report(charger_id=f"TEST-R{i}", source_id="TEST-S3", status="working", t=T0 + timedelta(days=i), is_simulated=True)
                for i in range(6)]  # the same chargers with the opposite verdict: no edge to the ring
    reports += [Report(charger_id=f"TEST-R{i}", source_id="TEST-S4", status="broken", t=T0, is_simulated=True) for i in (0, 1)]
    return chargers, reports  # S4 shares only two chargers: below min_shared 4


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


def test_live_cypher_edges_and_ring_ids_match_the_in_memory_twin() -> None:
    graph = live_graph()
    chargers, reports = fake_world()
    memory = InMemoryGraph()
    memory.add_chargers(chargers)
    memory.add_reports(reports)
    ours = lambda edges: [e for e in edges if e.a.startswith("TEST-") and e.b.startswith("TEST-")]  # noqa: E731
    try:
        graph.ensure_constraints()
        graph.add_chargers(chargers)
        graph.add_reports(reports)
        for kwargs in (dict(min_shared=4), dict(min_shared=2, min_jaccard=0.3), dict(min_shared=4, min_agree=0.5)):
            assert ours(graph.agreement_edges(**kwargs)) == ours(memory.agreement_edges(**kwargs)), kwargs
        assert ours(graph.agreement_edges(min_shared=4))  # a non-empty comparison: the ring really is found
        rings = [r for r in communities(graph.agreement_edges(min_shared=4)) if r.sources[0].startswith("TEST-")]
        assert [r.sources for r in rings] == [("TEST-S0", "TEST-S1", "TEST-S2")]
        graph.write_ring_ids(rings)
        assert {s: r for s, r in graph.ring_ids().items() if s.startswith("TEST-")} == {
            "TEST-S0": "ring:TEST-S0", "TEST-S1": "ring:TEST-S0", "TEST-S2": "ring:TEST-S0"}
    finally:
        graph.write_ring_ids([])
        graph.delete_test_data()
        graph.close()
