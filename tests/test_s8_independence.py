"""S8 (W2.2, W2.3): the graph decides which backups are independent, and proposes the charging chains.

Offline tests use the in-memory graph (the fallback); the live tests compare it with Neo4j and skip cleanly when
Neo4j is unreachable (same rule as tests/test_graph_parity.py).
"""

from __future__ import annotations

import numpy as np
import pytest

from verity.chargers import RouteCharger
from verity.dispatch import Node, graph_chains, graph_operators, make_nodes
from verity.schemas import ChargerRecord
from verity.sites import BETA_OPERATOR, BETA_SITE, WorksSampler, operators_from_pairs, p_all_fail
from verity.trust.graph import InMemoryGraph, Neo4jGraph, chains_cypher
from verity.trust.graph_types import SharedCause

N = 400_000
P1, P2 = 0.8, 0.7


def rec(cid: str, lat: float, operator: str = "") -> ChargerRecord:
    return ChargerRecord(id=cid, name=f"Charger {cid}", lat=lat, lon=77.5, operator=operator, site_type="highway")


# -- the sampler ---------------------------------------------------------------------------------------------------
def test_a_charger_without_an_operator_draws_exactly_what_it_drew_before() -> None:
    a, b = WorksSampler(np.random.default_rng(7), 1000), WorksSampler(np.random.default_rng(7), 1000)
    for site, p in (("S1", 0.8), ("S1", 0.6), ("S2", 0.9)):
        assert np.array_equal(a.works(site, p), b.works(site, p, ""))


def test_operator_sharing_keeps_every_marginal_exact() -> None:
    s = WorksSampler(np.random.default_rng(1), N)
    w1, w2 = s.works("S1", P1, "OpA"), s.works("S2", P2, "OpA")
    assert w1.mean() == pytest.approx(P1, abs=0.004) and w2.mean() == pytest.approx(P2, abs=0.004)


def test_same_operator_fails_together_more_than_independent_but_less_than_one_site() -> None:
    def both_fail(site2: str, op2: str) -> float:
        s = WorksSampler(np.random.default_rng(2), N)
        return float((~s.works("S1", P1, "OpA") & ~s.works(site2, P2, op2)).mean())

    independent = (1 - P1) * (1 - P2)
    different_operator = both_fail("S2", "OpB")
    same_operator = both_fail("S2", "OpA")
    same_site_same_operator = both_fail("S1", "OpA")
    assert different_operator == pytest.approx(independent, abs=0.004)
    assert same_operator > independent + 0.005
    assert same_site_same_operator > same_operator


def test_the_two_shares_may_not_exceed_one() -> None:
    with pytest.raises(ValueError):
        WorksSampler(np.random.default_rng(0), 10, beta=0.7, beta_operator=0.4)


# -- the analytic probability --------------------------------------------------------------------------------------
REACH2 = np.ones((2, 1), dtype=bool)


def test_p_all_fail_without_operators_is_the_site_only_number() -> None:
    plain = p_all_fail([0.8, 0.8], ["A", "B"], REACH2)
    assert np.array_equal(plain, p_all_fail([0.8, 0.8], ["A", "B"], REACH2, operators=["", ""]))
    assert np.array_equal(plain, p_all_fail([0.8, 0.8], ["A", "B"], REACH2, operators=None))


def test_p_all_fail_same_operator_other_site_uses_the_operator_share() -> None:
    f = 0.2
    got = p_all_fail([0.8, 0.8], ["A", "B"], REACH2, operators=["X", "X"])[0]
    assert got == pytest.approx(f * (BETA_OPERATOR + (1 - BETA_OPERATOR) * f))


def test_p_all_fail_the_site_link_wins_over_the_operator_link() -> None:
    f = 0.2
    got = p_all_fail([0.8, 0.8], ["A", "A"], REACH2, operators=["X", "X"])[0]
    assert got == pytest.approx(f * (BETA_SITE + (1 - BETA_SITE) * f))


def test_p_all_fail_an_unreachable_candidate_adds_no_correlation() -> None:
    reach = np.array([[True], [False]])
    assert p_all_fail([0.8, 0.8], ["A", "B"], reach, operators=["X", "X"])[0] == pytest.approx(0.2)


# -- the graph's shared-cause query (memory twin) -----------------------------------------------------------------
def memory_graph() -> InMemoryGraph:
    g = InMemoryGraph()
    # A, B: one Site (111 m), operator X.  C: 5.5 km away, operator X.  D, E: no operator.  F: operator Y alone.
    g.add_chargers([rec("A", 28.000, "X"), rec("B", 28.001, "X"), rec("C", 28.050, "X"),
                    rec("D", 28.100), rec("E", 28.150), rec("F", 28.200, "Y")])
    return g


def test_shared_causes_name_the_site_and_the_operator_and_never_an_unknown_operator() -> None:
    got = memory_graph().shared_causes(["F", "E", "D", "C", "B", "A"])
    assert got == [SharedCause("A", "B", "A", "X", None), SharedCause("A", "C", None, "X", None),
                   SharedCause("B", "C", None, "X", None)]


def test_shared_causes_only_look_at_the_chargers_asked_about() -> None:
    assert memory_graph().shared_causes(["A", "C"]) == [SharedCause("A", "C", None, "X", None)]
    assert memory_graph().shared_causes(["A", "nope"]) == []


def test_operators_from_pairs_keeps_only_real_operators() -> None:
    assert operators_from_pairs([("A", "X"), ("B", "X"), ("C", "")]) == {"A": "X", "B": "X"}


def route(*specs: tuple[str, float, str]) -> list[RouteCharger]:
    return [RouteCharger(rec(cid, 28.0 + km / 111.0, op), km, 0.1) for cid, km, op in specs]


def test_nodes_get_their_operator_from_the_graph_and_the_python_fallback_agrees() -> None:
    chargers = route(("A", 20.0, "X"), ("B", 80.0, "X"), ("C", 120.0, "Y"), ("D", 150.0, ""))
    g = InMemoryGraph()
    g.add_chargers([rc.charger for rc in chargers])
    from_graph = make_nodes(chargers, {}, operators=graph_operators(g, chargers))
    from_field = make_nodes(chargers, {})
    assert [(n.id, n.operator) for n in from_graph] == [("A", "X"), ("B", "X"), ("C", ""), ("D", "")]
    assert [n.operator for n in from_field] == ["X", "X", "Y", ""]  # a lone operator is harmless: it shares with nobody
    assert [n.operator for n in make_nodes(chargers, {}, link_operators=False)] == ["", "", "", ""]


# -- chains proposed by the graph (W2.3) ---------------------------------------------------------------------------
def test_the_in_memory_graph_leaves_chain_proposal_to_the_python_path() -> None:
    g = memory_graph()
    assert g.chains("scope1", 6) is None
    assert graph_chains(g, "scope1", [Node("A", 10.0, 0.9, 0.8, 0.95, 50.0)]) is None


def test_the_chain_query_is_bounded_and_checked() -> None:
    q = chains_cypher(6)
    assert "LEG*1..6" in q and "ORDER BY cost, chain LIMIT $k" in q and "$banned" in q and "$fail" in q
    for bad in (0, 13, -1):
        with pytest.raises(ValueError):
            chains_cypher(bad)


# -- live: Neo4j against the in-memory twin -----------------------------------------------------------------------
@pytest.fixture(scope="module")
def live_services():
    from api.service import EngineService
    from verity.trust.graph_build import GraphUnavailable

    graph = Neo4jGraph.from_env()
    if graph is None:
        pytest.skip("no NEO4J_URI / NEO4J_USERNAME / NEO4J_PASSWORD")
    try:
        graph.verify_connectivity()
    except Exception as exc:  # noqa: BLE001 - class name only: the message may echo the connection string
        graph.close()
        pytest.skip(f"Neo4j unreachable ({type(exc).__name__})")
    graph.close()
    memory = EngineService(n_runs=500, graph_backend="memory")
    try:
        neo4j = EngineService(n_runs=500, graph_backend="neo4j")
    except GraphUnavailable as exc:
        memory.close()
        pytest.skip(str(exc))
    yield memory, neo4j
    neo4j.close()
    memory.close()


DEMO_SOC = {"V07": 70, "V12": 62, "V19": 40}
BASE = {"origin": {"lat": 28.5355, "lon": 77.3910}, "destination": {"lat": 27.1767, "lon": 78.0081},
        "departure_time": "2026-10-28T14:00:00+05:30", "passengers": 4, "ac_on": True, "target_confidence": 0.9}


def test_live_shared_causes_are_the_same_in_neo4j_and_in_memory(live_services) -> None:
    memory, neo4j = live_services
    ids = sorted(c.id for c in memory.graph.mirror.chargers())[:60]
    assert neo4j.graph.neo4j is not None
    assert neo4j.graph.neo4j.shared_causes(ids) == memory.graph.mirror.shared_causes(ids)


@pytest.mark.parametrize("vehicle", sorted(DEMO_SOC))
def test_live_neo4j_proposes_the_same_chains_as_yens_algorithm(live_services, monkeypatch, vehicle: str) -> None:
    import verity.dispatch as dispatch_module

    memory, neo4j = live_services
    seen: dict[str, list[list[list[str]]]] = {"memory": [], "neo4j": []}
    real = dispatch_module.propose_paths

    def spy(ctx, nodes, **kw):
        paths, leg_graph = real(ctx, nodes, **kw)
        seen[spy.label].append([[n.id for n in p] for p in paths])
        return paths, leg_graph

    monkeypatch.setattr(dispatch_module, "propose_paths", spy)
    request = {**BASE, "vehicle_id": vehicle, "start_soc_pct": DEMO_SOC[vehicle]}
    for label, svc in (("memory", memory), ("neo4j", neo4j)):
        spy.label = label
        svc.dispatch(request)
    assert neo4j.graph.backend == "neo4j", "fell back to memory: the comparison would be meaningless"
    assert seen["memory"] and seen["memory"] == seen["neo4j"]
