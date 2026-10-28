"""Backend selection and fallback: Neo4j only when it is configured AND works, the mirror otherwise, and the reason
never carries credentials. The Neo4j side is a fake driver here; the live side is tests/test_graph_parity.py."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tests.graph_fakes import FakeDriver
from verity.chargers import RouteCharger
from verity.schemas import ChargerRecord, Report
from verity.trust.engine import ChargerTrust
from verity.trust.graph import InMemoryGraph, Neo4jGraph
from verity.trust.graph_build import (
    GraphUnavailable,
    open_graph,
    populate_road_network,
    usable_alternate,
)

IST = timezone(timedelta(hours=5, minutes=30))
T0 = datetime(2026, 10, 27, 10, 0, tzinfo=IST)
N_SYNC_QUERIES = 7 + 1 + 1 + 1 + 1  # constraints, chargers, reports, prune, sweep


def charger(i: int) -> ChargerRecord:
    return ChargerRecord(id=f"C{i}", name=f"c{i}", lat=28.5, lon=77.4, operator="OpA", site_type="mall")


CHARGERS = [charger(1), charger(2)]
REPORTS = [Report(charger_id="C1", source_id="s1", status="working", t=T0, is_simulated=True),
           Report(charger_id="C2", source_id="s1", status="broken", t=T0 + timedelta(minutes=1), is_simulated=True)]


def fake_neo4j(monkeypatch: pytest.MonkeyPatch, **driver_kwargs) -> FakeDriver:
    canned = [[]] * 8 + [[{"n": len(REPORTS)}]] + [[], []]  # 7 constraints + chargers, then the report count
    drv = FakeDriver(canned, **driver_kwargs)
    monkeypatch.setattr(Neo4jGraph, "from_env", classmethod(lambda cls, env_path=None, connection_timeout=10.0:
                                                            Neo4jGraph(driver=drv)))
    return drv


def no_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Neo4jGraph, "from_env", classmethod(lambda cls, env_path=None, connection_timeout=10.0: None))


# --- choosing the backend ---------------------------------------------------------------------------------------


def test_no_credentials_means_memory_with_the_data_loaded(monkeypatch: pytest.MonkeyPatch) -> None:
    no_credentials(monkeypatch)
    h = open_graph(CHARGERS, REPORTS)
    assert h.backend == "memory" and h.graph is h.mirror and "NEO4J" in h.reason
    assert [c.id for c in h.graph.chargers()] == ["C1", "C2"] and len(h.graph.reports()) == 2


def test_memory_preference_never_looks_for_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(cls, *a, **k):
        raise AssertionError("must not read credentials")
    monkeypatch.setattr(Neo4jGraph, "from_env", classmethod(boom))
    assert open_graph(CHARGERS, REPORTS, prefer="memory").backend == "memory"


def test_an_unknown_preference_is_rejected() -> None:
    with pytest.raises(ValueError):
        open_graph(CHARGERS, REPORTS, prefer="postgres")


def test_a_working_neo4j_is_used_and_synced_with_batches(monkeypatch: pytest.MonkeyPatch) -> None:
    drv = fake_neo4j(monkeypatch)
    h = open_graph(CHARGERS, REPORTS)
    assert h.backend == "neo4j" and isinstance(h.graph, Neo4jGraph) and h.graph is not h.mirror
    queries = [q for q, _ in drv.log]
    assert len(queries) == N_SYNC_QUERIES and sum("UNWIND $rows" in q for q in queries) == 2
    assert [c.id for c in h.mirror.chargers()] == ["C1", "C2"]  # the mirror is always populated too


def test_connection_failure_falls_back_and_never_echoes_the_error_text(monkeypatch: pytest.MonkeyPatch) -> None:
    drv = fake_neo4j(monkeypatch, connectivity_error=RuntimeError("bolt://user:hunter2@host"))
    h = open_graph(CHARGERS, REPORTS)
    assert h.backend == "memory" and h.graph is h.mirror and drv.closed
    assert "RuntimeError" in h.reason and "hunter2" not in h.reason and "bolt" not in h.reason


def test_a_failure_halfway_through_the_sync_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    drv = fake_neo4j(monkeypatch, fail_after=3)
    h = open_graph(CHARGERS, REPORTS)
    assert h.backend == "memory" and drv.closed and "ConnectionError" in h.reason


def test_a_malformed_uri_that_raises_while_building_the_driver_falls_back_without_echoing_it(
        monkeypatch: pytest.MonkeyPatch) -> None:
    def malformed(cls, *args, **kwargs):
        raise ValueError("Unable to parse bolt://user:hunter2@host")
    monkeypatch.setattr(Neo4jGraph, "from_env", classmethod(malformed))
    h = open_graph(CHARGERS, REPORTS)
    assert h.backend == "memory" and "ValueError" in h.reason and "hunter2" not in h.reason


def test_a_bug_in_the_code_run_on_the_graph_surfaces_and_does_not_switch_neo4j_off(
        monkeypatch: pytest.MonkeyPatch) -> None:
    drv = fake_neo4j(monkeypatch)
    h = open_graph(CHARGERS, REPORTS)
    with pytest.raises(ZeroDivisionError):
        h.call(lambda g: 1 / 0)
    assert h.backend == "neo4j" and not drv.closed


def test_requiring_neo4j_raises_instead_of_falling_back(monkeypatch: pytest.MonkeyPatch) -> None:
    no_credentials(monkeypatch)
    with pytest.raises(GraphUnavailable):
        open_graph(CHARGERS, REPORTS, prefer="neo4j")


# --- using the handle -------------------------------------------------------------------------------------------


def test_call_switches_to_the_mirror_when_the_link_drops_mid_request(monkeypatch: pytest.MonkeyPatch) -> None:
    drv = fake_neo4j(monkeypatch, fail_after=N_SYNC_QUERIES)
    h = open_graph(CHARGERS, REPORTS)
    assert h.backend == "neo4j"
    assert [c.id for c in h.call(lambda g: g.chargers())] == ["C1", "C2"]  # served by the mirror after the failure
    assert h.backend == "memory" and drv.closed and "ConnectionError" in h.reason
    assert [c.id for c in h.call(lambda g: g.chargers())] == ["C1", "C2"]


def test_populate_writes_the_mirror_and_neo4j_and_falls_back_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    drv = fake_neo4j(monkeypatch, fail_after=N_SYNC_QUERIES)
    h = open_graph(CHARGERS, REPORTS)
    h.populate(lambda g: g.reset_road_network())
    assert h.backend == "memory" and drv.closed  # the Neo4j write failed; the mirror write had already happened


# --- the road network -------------------------------------------------------------------------------------------


def trust(cid: str, p: float, level: str) -> ChargerTrust:
    return ChargerTrust(cid, p, p - 0.1, p + 0.1, 3, 2.0, level, 0.8)


def test_usable_alternate_needs_evidence_and_better_than_even_odds() -> None:
    assert usable_alternate(0.9, "high") and usable_alternate(0.5, "low")
    assert not usable_alternate(0.9, "unknown") and not usable_alternate(0.49, "high")


def test_road_network_links_segments_to_usable_chargers_ahead_and_exposes_the_gaps() -> None:
    g = InMemoryGraph()
    g.add_chargers([charger(1), charger(2), charger(3)])
    near = [RouteCharger(charger(1), 25.0, 0.5), RouteCharger(charger(2), 95.0, 0.5), RouteCharger(charger(3), 96.0, 0.5)]
    t = {"C1": trust("C1", 0.9, "high"), "C2": trust("C2", 0.9, "unknown"), "C3": trust("C3", 0.3, "medium")}
    populate_road_network(g, near, t, total_km=100.0, alt_ahead_km=60.0)
    assert g.alternates("seg-0000") == [("C1", 0.9)] and g.alternates("seg-0002") == [("C1", 0.9)]
    assert g.alternates("seg-0003") == []  # C1 is behind from km 30 on; C2/C3 are not usable alternates
    assert [s.segment_id for s in g.coverage_gaps()] == [f"seg-{i:04d}" for i in range(3, 10)]
    populate_road_network(g, near, t, total_km=100.0, alt_ahead_km=60.0)  # rebuilding is idempotent
    assert len(g.coverage_gaps()) == 7
