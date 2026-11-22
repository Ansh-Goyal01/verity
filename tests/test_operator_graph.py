from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import jwt
from fastapi.testclient import TestClient

from api.main import app
from api.operator_graph import OperatorGraph, account_report
from verity.coverage import critical
from verity.schemas import ChargerRecord
from verity.trust.graph import InMemoryGraph
from verity.trust.graph_build import GraphHandle


def test_only_browser_safe_auth_keys_are_published(monkeypatch):
    client = TestClient(app)
    monkeypatch.setenv("SUPABASE_URL", "https://accounts.example")
    monkeypatch.delenv("SUPABASE_PUBLISHABLE_KEY", raising=False)
    for key in ("sb_secret_server_only", jwt.encode({"role": "service_role"}, "test-secret" * 4)):
        monkeypatch.setenv("SUPABASE_ANON_KEY", key)
        assert client.get("/api/auth/config").json() == {"available": False}
    key = jwt.encode({"role": "anon"}, "test-secret" * 4)
    monkeypatch.setenv("SUPABASE_ANON_KEY", key)
    assert client.get("/api/auth/config").json()["publishable_key"] == key
    monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", "sb_publishable_browser_key")
    assert client.get("/api/auth/config").json()["publishable_key"] == "sb_publishable_browser_key"


def test_coverage_checks_the_whole_route_before_removal():
    near = [SimpleNamespace(km_along=km, charger=SimpleNamespace(id=str(km), name=str(km))) for km in (70, 35)]
    full, rows = critical(near, 105, 40)
    assert full == 35 and {r["charger_id"] for r in rows} == {"35", "70"}
    assert critical(near, 105, 30) == (35, [])
    assert critical(near, 105, 70) == (35, [])


def test_open_reports_and_grid_risk_stay_with_operator(monkeypatch):
    graph = InMemoryGraph()
    cars = [ChargerRecord(id=c, name=c, lat=28, lon=77, operator=op)
            for c, op in (("a", "mine"), ("b", "mine"), ("c", "other"))]
    graph.add_chargers(cars)
    handle = GraphHandle(graph, "memory", graph, "test")
    ops = OperatorGraph(SimpleNamespace(graph=handle, chargers=cars))
    monkeypatch.setattr("api.operator_graph.restore_reports", lambda svc: None)
    now = datetime.now(timezone.utc)
    issue = account_report("owner", "a", "not_working", now)
    graph.add_reports([issue, account_report("operator", "a", "working", now + timedelta(minutes=1), repair=True),
                       account_report("owner", "b", "not_working", now), account_report("owner", "c", "not_working", now)])
    assert issue.source_id != "owner" and issue.status == "broken"
    assert [r["charger_id"] for r in ops.cpo_open_reports("mine")] == ["b"]
    monkeypatch.setattr(graph, "substation_links", lambda: {"a": ("s", 1.0), "b": ("s", 1.5), "c": ("s", 2.0)})
    assert [c["charger_id"] for c in ops.shared_grid_risk("mine")[0]["chargers"]] == ["a","b"]


def test_suspected_rings_count_only_sources_that_reported_this_operators_chargers(monkeypatch):
    graph = InMemoryGraph()
    cars = [ChargerRecord(id=c, name=c, lat=28, lon=77, operator=op)
            for c, op in (("a", "mine"), ("b", "mine"), ("c", "other"))]
    graph.add_chargers(cars)
    handle = GraphHandle(graph, "memory", graph, "test")
    ops = OperatorGraph(SimpleNamespace(graph=handle, chargers=cars))
    monkeypatch.setattr("api.operator_graph.restore_reports", lambda svc: None)
    now = datetime.now(timezone.utc)
    reports = [account_report("u1", "a", "not_working", now), account_report("u2", "b", "not_working", now),
               account_report("u3", "c", "not_working", now)]
    graph.add_reports(reports)
    ring = SimpleNamespace(sources=tuple(r.source_id for r in reports))  # u3 only reported another operator's charger
    monkeypatch.setattr(graph, "agreement_edges", lambda: [])
    monkeypatch.setattr("api.operator_graph.communities", lambda edges: [ring])
    rows = ops.suspected_rings(["a", "b"])
    assert rows == [{"sources_in_ring": 2, "charger_ids": ["a", "b"], "includes_simulated_reports": False}]
    assert ops.suspected_rings(["zzz"]) == []
