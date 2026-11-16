"""GET /api/graph/why/{plan_id}, GET /api/graph/charger/{id} and POST /api/reports (W2.6, contract v4).

Runs on its own EngineService so a live report written here cannot change the trust other test modules read."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.limits import RateLimiter
from api.main import app, get_report_cooldown, get_report_limiter, get_service
from api.service import EngineService

BODY = {"origin": {"lat": 28.5355, "lon": 77.3910}, "destination": {"lat": 27.1767, "lon": 78.0081},
        "departure_time": "2026-10-28T14:00:00+05:30", "passengers": 4, "ac_on": True, "target_confidence": 0.9,
        "vehicle_id": "V12", "start_soc_pct": 62}


@pytest.fixture(scope="module")
def svc():
    service = EngineService(n_runs=200, graph_backend="memory")
    yield service
    service.close()


@pytest.fixture()
def client(svc: EngineService):
    app.dependency_overrides[get_service] = lambda: svc
    app.dependency_overrides[get_report_limiter] = lambda: RateLimiter(1000, 60.0)
    app.dependency_overrides[get_report_cooldown] = lambda: RateLimiter(1000, 60.0)
    yield TestClient(app)  # no lifespan: the module's own service is the only one built
    app.dependency_overrides.clear()


@pytest.fixture(scope="module")
def plan(svc: EngineService) -> dict:
    return svc.dispatch(BODY)


def test_why_returns_the_plan_as_a_path_with_its_query(client: TestClient, plan: dict) -> None:
    r = client.get(f"/api/graph/why/{plan['plan_id']}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["plan_id"] == plan["plan_id"] and body["backend"] == "memory" and body["executed"] is False
    assert "MATCH (p:Plan" in body["cypher"] and body["query_ms"] >= 0 and body["is_simulated"] is True
    assert {"STOPS_AT", "BACKUP_FOR", "AT", "REPORTED", "VIA"} <= {e["type"] for e in body["edges"]}
    stops = [e["to"] for e in body["edges"] if e["type"] == "STOPS_AT"]
    assert stops == [f"Charger:{s['charger_id']}" for s in plan["stops"]]
    backups = {e["to"] for e in body["edges"] if e["type"] == "BACKUP_FOR"}
    assert backups == {f"Charger:{a['charger_id']}" for a in plan["alternates"]}
    assert all({"id", "label", "props"} == set(n) for n in body["nodes"])
    assert all({"from", "to", "type", "props"} == set(e) for e in body["edges"])


def test_why_for_an_unknown_plan_is_a_404_in_the_error_shape(client: TestClient) -> None:
    r = client.get("/api/graph/why/PL-nope")
    assert r.status_code == 404 and r.json()["error"] == "unknown_plan"


def test_the_charger_view_carries_trust_rounded_to_five_percent(client: TestClient, plan: dict) -> None:
    cid = plan["stops"][0]["charger_id"]
    r = client.get(f"/api/graph/charger/{cid}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["charger_id"] == cid and f"Charger:{cid}" in {n["id"] for n in body["nodes"]}
    assert any(e["type"] == "STOPS_AT" and e["from"] == f"Plan:{plan['plan_id']}" for e in body["edges"])
    p = body["trust"]["p_works"]
    assert p is None or abs(p * 20 - round(p * 20)) < 1e-9
    assert client.get("/api/graph/charger/NOPE").json()["error"] == "unknown_charger"


def test_a_report_needs_consent_a_known_charger_and_a_valid_status(client: TestClient, plan: dict) -> None:
    cid = plan["stops"][0]["charger_id"]
    no_consent = client.post("/api/reports", json={"charger_id": cid, "status": "working", "consent": False})
    assert no_consent.status_code == 422 and no_consent.json()["error"] == "consent_required"
    unknown = client.post("/api/reports", json={"charger_id": "NOPE", "status": "working", "consent": True})
    assert unknown.status_code == 404 and unknown.json()["error"] == "unknown_charger"
    bad = client.post("/api/reports", json={"charger_id": cid, "status": "on fire", "consent": True})
    assert bad.status_code == 422 and bad.json()["error"] == "invalid_request"
    edges = client.get(f"/api/graph/charger/{cid}").json()["edges"]
    assert not [e for e in edges if e["type"] == "REPORTED" and e["from"] == "Source:driver:driver_voice"]  # refusals wrote nothing


def test_a_report_is_a_real_non_simulated_edge_that_shows_up_in_the_next_view(client: TestClient, plan: dict) -> None:
    cid = plan["stops"][0]["charger_id"]
    r = client.post("/api/reports", json={"charger_id": cid, "status": "broken", "consent": True, "note": "no power"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["is_simulated"] is False and body["source_id"] == "driver:driver_voice" and body["backend"] == "memory"
    before, after = body["trust_before"], body["trust_after"]
    assert after["n_reports"] == before["n_reports"] + 1
    assert after["days_since_last"] is not None and after["days_since_last"] >= 0  # never "in the future"
    view = client.get(f"/api/graph/charger/{cid}").json()
    reported = [e for e in view["edges"] if e["type"] == "REPORTED" and e["from"] == "Source:driver:driver_voice"]
    assert len(reported) == 1 and reported[0]["props"]["status"] == "broken" and reported[0]["props"]["is_simulated"] is False
    assert "Channel:driver_voice" in {n["id"] for n in view["nodes"]}


def test_reports_are_rate_limited_per_address_with_retry_after(svc: EngineService, plan: dict) -> None:
    limiter = RateLimiter(2, 60.0)
    app.dependency_overrides[get_service] = lambda: svc
    app.dependency_overrides[get_report_limiter] = lambda: limiter
    app.dependency_overrides[get_report_cooldown] = lambda: RateLimiter(1000, 60.0)
    try:
        c = TestClient(app)
        payload = {"charger_id": plan["stops"][0]["charger_id"], "status": "working", "consent": True}
        assert [c.post("/api/reports", json=payload).status_code for _ in range(2)] == [200, 200]
        third = c.post("/api/reports", json=payload)
        assert third.status_code == 429 and third.json()["error"] == "rate_limited" and int(third.headers["Retry-After"]) >= 1
        assert "report" in third.json()["message"]
    finally:
        app.dependency_overrides.clear()


def test_one_address_cannot_stack_reports_on_one_charger(svc: EngineService, plan: dict) -> None:
    cooldown = RateLimiter(1, 600.0)
    app.dependency_overrides[get_service] = lambda: svc
    app.dependency_overrides[get_report_limiter] = lambda: RateLimiter(1000, 60.0)
    app.dependency_overrides[get_report_cooldown] = lambda: cooldown
    try:
        c = TestClient(app)
        a, b = plan["stops"][0]["charger_id"], plan["alternates"][0]["charger_id"]
        post = lambda cid, consent=True: c.post("/api/reports", json={"charger_id": cid, "status": "broken", "consent": consent})  # noqa: E731
        assert post(a, consent=False).status_code == 422  # a refusal does not use up the cooldown
        assert post(a).status_code == 200
        again = post(a)
        assert again.status_code == 429 and again.json()["error"] == "already_reported" and int(again.headers["Retry-After"]) >= 1
        assert post(b).status_code == 200  # another charger is a different witness statement
    finally:
        app.dependency_overrides.clear()
