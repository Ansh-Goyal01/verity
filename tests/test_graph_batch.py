"""Batch, scoped, ordered and analytic behaviour of the charger graph: in memory, and as Cypher shapes through a
fake driver (the live equivalence of the two backends is tests/test_graph_parity.py)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tests.graph_fakes import FakeDriver
from verity.schemas import ChargerRecord, Report
from verity.trust.graph import Alternate, InMemoryGraph, Leg, Neo4jGraph, RoadSegmentRec, Ring, report_key

IST = timezone(timedelta(hours=5, minutes=30))
T0 = datetime(2026, 10, 27, 10, 0, tzinfo=IST)


def charger(i: int) -> ChargerRecord:
    return ChargerRecord(id=f"C{i}", name=f"c{i}", lat=28.5, lon=77.4, operator="OpA", site_type="mall")


def report(cid: str, sid: str, status: str = "working", minutes: int = 0, sim: bool = False) -> Report:
    return Report(charger_id=cid, source_id=sid, status=status, t=T0 + timedelta(minutes=minutes), is_simulated=sim)


# --- in memory ---------------------------------------------------------------------------------------------


def test_reports_are_idempotent_and_come_back_in_canonical_order() -> None:
    g = InMemoryGraph()
    g.add_chargers([charger(2), charger(1)])
    batch = [report("C1", "b", minutes=5), report("C2", "a", minutes=5), report("C1", "a", minutes=0)]
    g.add_reports(batch)
    g.add_reports(batch)  # loading the same reports again adds nothing
    assert [c.id for c in g.chargers()] == ["C1", "C2"]
    assert [(r.t, r.source_id, r.charger_id) for r in g.reports()] == [
        (T0, "a", "C1"), (T0 + timedelta(minutes=5), "a", "C2"), (T0 + timedelta(minutes=5), "b", "C1")]


def test_add_reports_rejects_an_unknown_charger() -> None:
    g = InMemoryGraph()
    g.add_chargers([charger(1)])
    with pytest.raises(KeyError):
        g.add_reports([report("C1", "s"), report("C9", "s")])


def test_prune_removes_only_simulated_reports_that_are_no_longer_in_the_scenario() -> None:
    g = InMemoryGraph()
    g.add_chargers([charger(1)])
    keep, stale, real = report("C1", "keep", sim=True), report("C1", "stale", sim=True), report("C1", "real")
    g.add_reports([keep, stale, real])
    g.prune_simulated_reports({report_key(keep)})
    assert sorted(r.source_id for r in g.reports()) == ["keep", "real"]
    assert {r.source_id: r.is_simulated for r in g.reports()} == {"keep": True, "real": False}


def test_legs_are_scoped_so_two_plans_never_overwrite_each_other() -> None:
    g = InMemoryGraph()
    g.add_legs([Leg("START", "C1", 0, 10, 0.5, 30.0)], scope="plan-a")
    g.add_legs([Leg("START", "C1", 0, 10, 0.9, 30.0)], scope="plan-b")
    assert g.legs("plan-a")[0].p_reach == 0.5 and g.legs("plan-b")[0].p_reach == 0.9 and g.legs() == []
    g.drop_legs("plan-a")
    assert g.legs("plan-a") == [] and len(g.legs("plan-b")) == 1
    with pytest.raises(ValueError):
        g.drop_legs("")  # the unscoped graph is never dropped wholesale


def test_legs_come_back_in_a_canonical_order() -> None:
    g = InMemoryGraph()
    g.add_legs([Leg("B", "END", 10, 20, 0.5, 1.0), Leg("A", "B", 20, 30, 0.5, 1.0), Leg("A", "B", 0, 10, 0.5, 1.0)], "s")
    assert [(leg.from_id, leg.to_id, leg.soc_lo) for leg in g.legs("s")] == [("A", "B", 0), ("A", "B", 20), ("B", "END", 10)]


def test_a_scope_cannot_contain_a_colon_so_one_scope_never_reaches_into_another() -> None:
    leg = Leg("A", "B", 0, 10, 0.5, 1.0)
    with pytest.raises(ValueError):
        InMemoryGraph().add_legs([leg], scope="x:y")
    with pytest.raises(ValueError):
        InMemoryGraph().drop_legs("x:y")
    with pytest.raises(ValueError):
        Neo4jGraph(driver=FakeDriver()).add_legs([leg], scope="x:y")
    with pytest.raises(ValueError):
        Neo4jGraph(driver=FakeDriver()).drop_legs("x:y")


def test_prune_leaves_test_data_and_other_runs_alone_in_both_backends() -> None:
    mine, other = report("C1", "demo-user-0", sim=True), report("C1", "TEST-S0", sim=True)
    g = InMemoryGraph()
    g.add_chargers([charger(1)])
    g.add_reports([mine, other])
    g.prune_simulated_reports(set())
    assert [r.source_id for r in g.reports()] == ["TEST-S0"]
    drv = FakeDriver()
    Neo4jGraph(driver=drv).prune_simulated_reports(set())
    ((query, params),) = drv.log
    assert params["test_prefix"] == "TEST-" and "NOT s.id STARTS WITH $test_prefix" in query
    assert "o.is_simulated = true" in query  # only orphaned SIMULATED sources are cleaned up


def test_coverage_gaps_are_road_segments_without_a_qualifying_alternate() -> None:
    g = InMemoryGraph()
    g.add_road_segments([RoadSegmentRec("seg-0", 0, 10), RoadSegmentRec("seg-1", 10, 20), RoadSegmentRec("seg-2", 20, 30)])
    g.add_alternates([Alternate("seg-0", "C1", 0.9, 5.0), Alternate("seg-1", "C2", 0.3, 8.0)])
    assert [s.segment_id for s in g.coverage_gaps()] == ["seg-2"]
    assert [s.segment_id for s in g.coverage_gaps(min_p=0.5)] == ["seg-1", "seg-2"]
    g.reset_road_network()
    assert g.coverage_gaps() == []


def _ring_world(g: InMemoryGraph) -> None:
    g.add_chargers([charger(i) for i in range(5)])
    for i in range(5):
        for k, source in enumerate(("ring-a", "ring-b", "ring-c")):  # same verdict, same chargers, within minutes
            g.add_reports([report(f"C{i}", source, "broken", minutes=i * 1000 + k)])
        g.add_reports([report(f"C{i}", "honest-1", "working", minutes=i * 1000)])
        g.add_reports([report(f"C{i}", "honest-2", "working", minutes=i * 1000 + 60 * 24 * 40)])  # 40 days later


def test_report_rings_need_agreeing_reports_close_in_time() -> None:
    g = InMemoryGraph()
    _ring_world(g)
    assert g.report_rings(min_shared=4, window_hours=72.0) == [Ring(("ring-a", "ring-b", "ring-c"), 5)]
    assert g.report_rings(min_shared=6, window_hours=72.0) == []


def test_without_a_time_window_honest_repeat_reporters_look_like_a_ring_too() -> None:
    g = InMemoryGraph()
    _ring_world(g)
    assert g.report_rings(min_shared=4, window_hours=None) == [Ring(("ring-a", "ring-b", "ring-c"), 5),
                                                               Ring(("honest-1", "honest-2"), 5)]


# --- Neo4j through a fake driver -----------------------------------------------------------------------------


def test_add_reports_is_one_idempotent_merge_batch() -> None:
    drv = FakeDriver([[{"n": 2}]])
    Neo4jGraph(driver=drv).add_reports([report("C1", "s1", sim=True), report("C2", "s1", "broken", minutes=3)])
    ((query, params),) = drv.log
    assert "UNWIND $rows" in query and "MERGE (s)-[r:REPORTED" in query and "CREATE" not in query
    first, second = params["rows"]
    assert first["key"] == f"s1|C1|{T0.isoformat()}" and first["is_simulated"] is True
    assert second["status"] == "broken" and second["is_simulated"] is False and second["ts"] > first["ts"]


def test_add_reports_raises_when_a_charger_is_missing_from_the_graph() -> None:
    drv = FakeDriver([[{"n": 1}]])  # two rows sent, only one matched a Charger node
    with pytest.raises(KeyError):
        Neo4jGraph(driver=drv).add_reports([report("C1", "s"), report("C2", "s")])


def test_chargers_and_reports_are_read_in_canonical_order() -> None:
    drv = FakeDriver()
    g = Neo4jGraph(driver=drv)
    g.chargers()
    g.reports()
    assert "ORDER BY c.id" in drv.log[0][0] and "ORDER BY r.ts, s.id, c.id" in drv.log[1][0]


def test_scoped_legs_are_one_batch_one_read_and_waypoints_are_road_segments() -> None:
    drv = FakeDriver([[{"n": 1}], [{"a": "s1:START", "b": "C1", "band": "0-10", "p": 0.5, "m": 30.0}]])
    g = Neo4jGraph(driver=drv)
    g.add_legs([Leg("START", "C1", 0, 10, 0.5, 30.0)], scope="s1")
    legs = g.legs("s1")
    (q_write, p_write), (q_read, p_read) = drv.log
    assert "UNWIND $rows" in q_write and "RoadSegment" in q_write and "MERGE (a)-[l:LEG" in q_write
    assert p_write["scope"] == "s1" and p_write["rows"][0]["a_key"] == "s1:START" and p_write["rows"][0]["b_key"] == "s1:C1"
    assert p_read["scope"] == "s1" and "ORDER BY" not in q_read  # ordering is done once, in Python, for both backends
    assert legs == [Leg("START", "C1", 0, 10, 0.5, 30.0)]  # the scope prefix is stripped on the way back


def test_drop_legs_is_one_scoped_statement_and_refuses_an_empty_scope() -> None:
    drv = FakeDriver()
    g = Neo4jGraph(driver=drv)
    g.drop_legs("s1")
    ((query, params),) = drv.log
    assert "DELETE" in query and params["scope"] == "s1" and params["prefix"] == "s1:"
    with pytest.raises(ValueError):
        g.drop_legs("")


def test_coverage_gaps_and_report_rings_are_parameterised_and_parsed() -> None:
    drv = FakeDriver([[{"id": "seg-2", "km_from": 20.0, "km_to": 30.0}],
                      [{"a": "x", "b": "y", "shared": 5}, {"a": "y", "b": "z", "shared": 4}]])
    g = Neo4jGraph(driver=drv)
    assert g.coverage_gaps(min_p=0.5) == [RoadSegmentRec("seg-2", 20.0, 30.0)]
    assert g.report_rings(min_shared=4, window_hours=72.0) == [Ring(("x", "y", "z"), 5)]
    assert "$min_p" in drv.log[0][0] and drv.log[0][1]["min_p"] == 0.5
    assert drv.log[1][1]["k"] == 4 and drv.log[1][1]["window_s"] == 72 * 3600 and "$window_s" in drv.log[1][0]


def test_no_window_means_no_time_filter_in_the_ring_query() -> None:
    drv = FakeDriver()
    Neo4jGraph(driver=drv).report_rings(min_shared=4, window_hours=None)
    assert "$window_s" not in drv.log[0][0]


def test_query_stats_count_every_round_trip() -> None:
    g = Neo4jGraph(driver=FakeDriver())
    g.chargers()
    g.reports()
    stats = g.query_stats()
    assert stats.queries == 2 and stats.seconds >= 0.0
    g.reset_query_stats()
    assert g.query_stats().queries == 0


def test_the_driver_is_created_with_a_connection_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict = {}
    monkeypatch.setattr("neo4j.GraphDatabase.driver", lambda uri, **kw: seen.update(kw) or FakeDriver())
    Neo4jGraph("neo4j+s://example.invalid", "u", "p", connection_timeout=7)
    assert seen["connection_timeout"] == 7 and seen["connection_acquisition_timeout"] == 7


def test_queries_to_the_real_driver_carry_a_server_side_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list = []

    class Recording(FakeDriver):
        def run(self, query, **params):
            sent.append(query)
            return super().run(getattr(query, "text", query), **params)

    monkeypatch.setattr("neo4j.GraphDatabase.driver", lambda uri, **kw: Recording())
    Neo4jGraph("neo4j+s://example.invalid", "u", "p").chargers()
    assert getattr(sent[0], "timeout", None) == 30.0


def test_a_leg_with_an_unresolvable_endpoint_is_an_error_not_a_silent_drop() -> None:
    drv = FakeDriver([[{"n": 0}]])  # one leg sent, none matched both of its endpoints
    with pytest.raises(KeyError):
        Neo4jGraph(driver=drv).add_legs([Leg("START", "C1", 0, 10, 0.5, 1.0)], scope="")


def test_a_dropped_pooled_connection_is_retried_once_but_other_errors_are_not(monkeypatch: pytest.MonkeyPatch) -> None:
    from neo4j.exceptions import ServiceUnavailable

    calls: list[int] = []

    class StaleThenFine(FakeDriver):
        def run(self, query, **params):
            calls.append(1)
            if len(calls) == 1:
                raise ServiceUnavailable("connection the server already closed")
            return super().run(getattr(query, "text", query), **params)

    monkeypatch.setattr("neo4j.GraphDatabase.driver", lambda uri, **kw: StaleThenFine())
    assert Neo4jGraph("neo4j+s://example.invalid", "u", "p").chargers() == [] and len(calls) == 2
    with pytest.raises(ConnectionError):  # not a stale-connection error: raised straight away, once
        Neo4jGraph(driver=FakeDriver(fail_after=0)).chargers()
