"""POST /api/voice/report and /api/voice/report/confirm (W3.3) plus the engine clock for real reports.

Runs on its own EngineService (memory graph, no Sarvam) so reports written here cannot change other modules' trust."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from api.limits import RateLimiter
from api.main import app, get_report_cooldown, get_report_limiter, get_service, get_voice_client
from api.service import EngineService

BODY = {"origin": {"lat": 28.5355, "lon": 77.3910}, "destination": {"lat": 27.1767, "lon": 78.0081},
        "departure_time": "2026-10-28T14:00:00+05:30", "passengers": 4, "ac_on": True, "target_confidence": 0.9,
        "vehicle_id": "V12", "start_soc_pct": 62}
START = datetime(2026, 11, 17, 15, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture(scope="module")
def clock() -> Clock:
    return Clock()


@pytest.fixture(scope="module")
def svc(clock: Clock):
    service = EngineService(n_runs=200, graph_backend="memory", clock=clock)
    yield service
    service.close()


@pytest.fixture(scope="module")
def plan(svc: EngineService) -> dict:
    return svc.dispatch(BODY)


@pytest.fixture()
def client(svc: EngineService):
    app.dependency_overrides[get_service] = lambda: svc
    app.dependency_overrides[get_voice_client] = lambda: None  # no Sarvam: the transcript path and no speech
    app.dependency_overrides[get_report_limiter] = lambda: RateLimiter(1000, 60.0)
    app.dependency_overrides[get_report_cooldown] = lambda: RateLimiter(1000, 60.0)
    yield TestClient(app)
    app.dependency_overrides.clear()


def _stop(plan: dict) -> dict:
    return plan["stops"][0]


def _heard(client: TestClient, transcript: str) -> dict:
    r = client.post("/api/voice/report", json={"transcript": transcript, "language": "en-IN", "speak": False})
    assert r.status_code == 200, r.text
    return r.json()


def _confirm(client: TestClient, charger_id: str, **over):
    body = {"confirmed": True, "charger_id": charger_id, "status": "broken", "consent": True, "language": "en-IN",
            "transcript": "x band hai", "speak": False, **over}
    return client.post("/api/voice/report/confirm", json=body)


def _n_reports(svc: EngineService) -> int:
    return len(svc.graph.call(lambda g: g.reports()))


def test_hearing_a_named_charger_reads_it_back_and_writes_nothing(client, svc, plan) -> None:
    stop, before = _stop(plan), _n_reports(svc)
    body = _heard(client, f"{stop['name']} band hai")
    assert body["stage"] == "confirm" and body["needs_confirmation"] is True
    assert body["charger"]["charger_id"] == stop["charger_id"] and body["status"] == "broken"
    assert "not working" in body["prompt"] and body["is_simulated"] is False
    assert _n_reports(svc) == before


def test_no_charger_name_asks_for_one_instead_of_guessing(client, svc) -> None:
    before = _n_reports(svc)
    body = _heard(client, "charger band hai")
    assert body["stage"] == "clarify" and body["needs_confirmation"] is False and body["charger"] is None
    assert _n_reports(svc) == before


def test_a_name_without_a_status_asks_how_the_charger_is(client, plan) -> None:
    body = _heard(client, _stop(plan)["name"])
    assert body["stage"] == "clarify" and body["status"] is None and "Say:" in body["prompt"]


def test_confirm_records_a_real_report_and_says_what_changed(client, svc, plan) -> None:
    stop = _stop(plan)
    r = _confirm(client, stop["charger_id"])
    assert r.status_code == 200, r.text
    body = r.json()
    report = body["report"]
    assert body["stage"] == "recorded" and report["is_simulated"] is False and report["source_id"] == "driver:driver_voice"
    assert report["trust_after"]["n_reports"] == report["trust_before"]["n_reports"] + 1
    assert body["plans_checked"] >= 1 and all(p["moved"] is (p["changed"] != []) for p in body["plans"])
    assert body["speech"].startswith("Recorded.") and "percent" in body["speech"]
    stored = [x for x in svc.graph.call(lambda g: g.reports()) if x.source_id == "driver:driver_voice"]
    assert stored and all(not x.is_simulated and x.evidence.startswith("voice: ") for x in stored)


def test_without_consent_nothing_is_written(client, svc, plan) -> None:
    before = _n_reports(svc)
    r = _confirm(client, _stop(plan)["charger_id"], consent=False)
    assert r.status_code == 422 and r.json()["error"] == "consent_required"
    assert _n_reports(svc) == before


def test_a_no_records_nothing_and_says_so(client, svc, plan) -> None:
    before = _n_reports(svc)
    body = _confirm(client, _stop(plan)["charger_id"], confirmed=False).json()
    assert body["stage"] == "not_recorded" and body["report"] is None and body["plans_checked"] == 0
    assert _n_reports(svc) == before


def test_an_unknown_charger_is_a_404(client) -> None:
    r = _confirm(client, "NO-SUCH-CHARGER")
    assert r.status_code == 404 and r.json()["error"] == "unknown_charger"


def test_a_scripted_note_is_simulated_under_its_own_source(client, svc, plan) -> None:
    body = _confirm(client, _stop(plan)["charger_id"], from_real_person=False, status="working").json()
    assert body["is_simulated"] is True and body["report"]["is_simulated"] is True
    assert body["report"]["source_id"] == "driver:driver_voice_scripted"


def test_a_real_report_ages_by_real_time_not_by_the_frozen_simulation_clock(svc, clock, plan) -> None:
    cid = plan["alternates"][0]["charger_id"]  # a charger no other test here reports on (a simulated note would sit at age 0)
    clock.now = START + timedelta(days=100)
    svc.add_report({"charger_id": cid, "status": "broken", "consent": True, "channel": "driver_voice"})
    svc._adopt(svc.graph.call(svc._read_view))
    assert svc.trust[cid].days_since_last == pytest.approx(0, abs=0.01)  # not negative, not stuck at "fresh" for ever
    clock.now += timedelta(days=10)
    svc._adopt(svc.graph.call(svc._read_view))
    assert svc.trust[cid].days_since_last == pytest.approx(10, abs=0.01)
    clock.now = START
