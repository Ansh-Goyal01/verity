"""The API serves docs/API_CONTRACT.md: shapes (via the strict models), the rules the UI relies on, CORS,
errors, voice 503, and idempotent plans. The demo engine runs for real (real route, simulated fleet)."""

from __future__ import annotations

import re

import httpx
import pytest
from fastapi.testclient import TestClient

from api.main import ALLOWED_ORIGINS, app, get_voice_client
from verity.voice import SarvamClient

DISPATCH_BODY = {"origin": {"lat": 28.5355, "lon": 77.3910}, "destination": {"lat": 27.1767, "lon": 78.0081},
                 "departure_time": "2026-10-28T14:00:00+05:30", "passengers": 4, "ac_on": True, "target_confidence": 0.9}
DEMO_SOC = {"V07": 70, "V12": 62, "V19": 40}  # the demo scenario's starting charge (api/service.py FLEET_META)


def body(vid: str, soc: float | None = None) -> dict:
    return {**DISPATCH_BODY, "vehicle_id": vid, "start_soc_pct": DEMO_SOC[vid] if soc is None else soc}


def is_five_percent(p: float) -> bool:
    return abs(p * 20 - round(p * 20)) < 1e-9


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def plans(client: TestClient) -> dict[str, dict]:
    out = {}
    for vid in DEMO_SOC:
        r = client.post("/api/dispatch", json=body(vid))
        assert r.status_code == 200, r.text
        out[vid] = r.json()
    return out


# --- health, scenario, CORS ------------------------------------------------------------------------------------------


def test_health_matches_the_contract(client: TestClient) -> None:
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json() == {"status": "ok", "version": "0.1.0", "neo4j": "in_memory", "is_simulated": True}


@pytest.mark.parametrize("origin", ALLOWED_ORIGINS)
def test_cors_allows_exactly_the_origins_her_pages_use(client: TestClient, origin: str) -> None:
    r = client.options("/api/health", headers={"Origin": origin, "Access-Control-Request-Method": "GET"})
    assert r.status_code == 200 and r.headers["access-control-allow-origin"] == origin
    assert client.get("/api/health", headers={"Origin": origin}).headers["access-control-allow-origin"] == origin


def test_cors_does_not_allow_other_origins(client: TestClient) -> None:
    r = client.get("/api/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in r.headers


def test_demo_scenario_is_the_noida_agra_booking_with_three_calibrated_vehicles(client: TestClient) -> None:
    d = client.get("/api/demo/scenario").json()
    assert d["is_simulated"] is True and d["booking"]["booking_id"] == "BK-001"
    assert d["booking"]["origin"]["name"] == "Noida" and d["booking"]["destination"]["name"] == "Agra"
    assert [v["vehicle_id"] for v in d["vehicles"]] == ["V07", "V12", "V19"]
    for v in d["vehicles"]:
        u = v["usable_kwh_estimate"]
        assert u["p10"] <= u["p50"] <= u["p90"] and v["trips_calibrated"] > 0 and v["health_note"]


# --- dispatch --------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("vid", list(DEMO_SOC))
def test_dispatch_obeys_the_rules_the_ui_relies_on(plans: dict, vid: str) -> None:
    d = plans[vid]
    assert d["is_simulated"] is True and re.fullmatch(r"PL-[0-9a-f]{4,}", d["plan_id"]) and d["vehicle_id"] == vid
    assert d["decision"] in ("GO", "NO_GO", "INSUFFICIENT_EVIDENCE")
    # probabilities are rounded to the nearest 0.05 by the engine
    probs = [d["confidence"], *(s["p_works"] for s in d["stops"]), *(a["p_reach"] for a in d["alternates"]),
             *(a["p_works"] for a in d["alternates"]), *(lv["new_confidence"] for lv in d["levers"])]
    assert all(0.0 <= p <= 1.0 and is_five_percent(p) for p in probs)
    # starting SoC plus the breakdown (including charging) equals the arrival median
    assert DEMO_SOC[vid] + sum(row["soc_pct"] for row in d["breakdown"]) == d["arrival_soc"]["p50"]
    assert d["arrival_soc"]["p10"] <= d["arrival_soc"]["p50"] <= d["arrival_soc"]["p90"]
    kms = [p["km"] for p in d["bingo_line"]]
    assert kms == sorted(kms) and len(kms) > 100 and all(0 <= p["bingo_soc_pct"] <= 100 for p in d["bingo_line"])
    # refusal is explained, and only then
    assert (d["decision"] == "INSUFFICIENT_EVIDENCE") == bool(d["refusal_reason"])
    assert [s["km_along_route"] for s in d["stops"]] == sorted(s["km_along_route"] for s in d["stops"])
    assert all(0 <= a["for_stop_index"] < len(d["stops"]) for a in d["alternates"])
    assert 0 < d["point_of_no_return_km"] <= d["route"]["distance_km"] and d["compute_ms"] > 0
    assert all(rs["kind"] in ("unknown", "unpredictable") and rs["label"] for rs in d["risk_sources"])
    assert d["route"]["distance_km"] == pytest.approx(190.8, abs=1.0) and len(d["route"]["polyline"]) > 100


def test_the_demo_shows_more_than_one_kind_of_decision(plans: dict) -> None:
    assert len({d["decision"] for d in plans.values()}) >= 2


def test_dispatch_is_idempotent_and_changes_with_the_inputs(client: TestClient, plans: dict) -> None:
    again = client.post("/api/dispatch", json=body("V12")).json()
    assert again == plans["V12"]
    other = client.post("/api/dispatch", json=body("V12", 90)).json()
    assert other["plan_id"] != plans["V12"]["plan_id"] and other["confidence"] >= plans["V12"]["confidence"]


# --- errors ------------------------------------------------------------------------------------------------------------------


def test_errors_use_the_contract_shape(client: TestClient) -> None:
    for resp, status, code in [
        (client.post("/api/dispatch", json={"vehicle_id": "V07"}), 422, "invalid_request"),
        (client.post("/api/dispatch", json=body("V07", 140)), 422, "invalid_request"),
        (client.post("/api/dispatch", json=body("V99", 50)), 404, "unknown_vehicle"),
        (client.post("/api/dispatch", json={**body("V07"), "destination": {"lat": 12.97, "lon": 77.59}}), 422, "unsupported_route"),
        (client.get("/api/demo/replay/PL-nope"), 404, "unknown_plan"),
        (client.post("/api/forecast/update", json={"plan_id": "PL-nope", "t": "x", "lat": 28.0, "lon": 77.5, "soc_pct": 50}), 404, "unknown_plan"),
        (client.get("/api/no/such/path"), 404, "not_found"),
        (client.get("/api/chargers"), 422, "invalid_request"),
    ]:
        assert resp.status_code == status and set(resp.json()) == {"error", "message"} and resp.json()["error"] == code, resp.text


# --- live update, chargers, replay ----------------------------------------------------------------------------------


def test_forecast_update_follows_the_contract(client: TestClient, plans: dict) -> None:
    pid = plans["V12"]["plan_id"]
    ok = client.post("/api/forecast/update", json={"plan_id": pid, "t": "2026-10-28T14:55:00+05:30", "lat": 28.10, "lon": 77.60, "soc_pct": 45})
    assert ok.status_code == 200
    d = ok.json()
    assert d["plan_id"] == pid and d["is_simulated"] is True and d["action"] in ("CONTINUE", "DIVERT", "SLOW_DOWN")
    assert is_five_percent(d["confidence"]) and 0 < d["km_along_route"] < 190 and d["message"]
    assert (d["action"] == "DIVERT") == (d["divert_to"] is not None)
    assert d["arrival_soc"]["p10"] <= d["arrival_soc"]["p50"] <= d["arrival_soc"]["p90"]
    far = client.post("/api/forecast/update", json={"plan_id": pid, "t": "x", "lat": 12.97, "lon": 77.59, "soc_pct": 45})
    assert far.status_code == 422 and far.json()["error"] == "off_route"


def test_a_lower_battery_reading_lowers_confidence(client: TestClient, plans: dict) -> None:
    pid = plans["V12"]["plan_id"]
    send = lambda soc: client.post("/api/forecast/update", json={"plan_id": pid, "t": "x", "lat": 28.10, "lon": 77.60, "soc_pct": soc}).json()  # noqa: E731
    high, low = send(55), send(25)
    assert low["confidence"] <= high["confidence"] and low["arrival_soc"]["p50"] <= high["arrival_soc"]["p50"]


def test_chargers_in_a_bounding_box(client: TestClient) -> None:
    box = {"min_lat": 27.0, "min_lon": 77.0, "max_lat": 29.5, "max_lon": 78.5}
    d = client.get("/api/chargers", params=box).json()
    assert d["is_simulated"] is True and len(d["chargers"]) > 20
    for c in d["chargers"]:
        assert 27.0 <= c["lat"] <= 29.5 and 77.0 <= c["lon"] <= 78.5 and is_five_percent(c["p_works"])
        assert c["trust_level"] in ("high", "medium", "low", "unknown") and c["source"] in ("osm", "ocm", "audit", "simulated")
        assert (c["n_reports"] == 0) == (c["days_since_last"] is None)
    tiny = client.get("/api/chargers", params={"min_lat": 0, "min_lon": 0, "max_lat": 1, "max_lon": 1}).json()
    assert tiny["chargers"] == []
    bad = client.get("/api/chargers", params={**box, "min_lat": 40})
    assert bad.status_code == 422 and bad.json()["error"] == "invalid_bbox"


def test_the_demo_directory_contains_chargers_at_every_trust_level(client: TestClient) -> None:
    levels = {c["trust_level"] for c in client.get("/api/chargers", params={"min_lat": 27, "min_lon": 77, "max_lat": 29.5, "max_lon": 78.5}).json()["chargers"]}
    assert {"high", "unknown"} <= levels


def test_replay_is_an_ordered_script_ending_in_a_forecast(client: TestClient, plans: dict) -> None:
    d = client.get(f"/api/demo/replay/{plans['V12']['plan_id']}").json()
    ev = d["events"]
    assert d["is_simulated"] is True and [e["t_offset_s"] for e in ev] == sorted(e["t_offset_s"] for e in ev)
    assert ev[0]["type"] == "soc_reading" and ev[0]["km"] == 0 and ev[-1]["type"] == "forecast"
    assert {e["type"] for e in ev} == {"soc_reading", "charger_report", "forecast"}
    report = next(e for e in ev if e["type"] == "charger_report")
    assert report["status"] == "broken" and report["charger_id"] == plans["V12"]["stops"][0]["charger_id"]
    assert ev[-1]["action"] in ("DIVERT", "SLOW_DOWN") and ev[-1]["message"]


# --- voice ------------------------------------------------------------------------------------------------------------------


def test_voice_endpoints_return_503_without_a_key(client: TestClient) -> None:
    app.dependency_overrides[get_voice_client] = lambda: None
    try:
        tts = client.post("/api/voice/tts", json={"text": "Divert now", "language": "hi-IN"})
        stt = client.post("/api/voice/stt", files={"audio": ("a.wav", b"x", "audio/wav")}, data={"language": "hi-IN"})
    finally:
        app.dependency_overrides.pop(get_voice_client, None)
    for r in (tts, stt):
        assert r.status_code == 503 and r.json()["error"] == "voice_unavailable" and r.json()["message"]


def test_voice_proxies_to_sarvam_through_a_mock_when_a_key_exists(client: TestClient) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("text-to-speech"):
            return httpx.Response(200, json={"audios": ["QUJD"]})
        return httpx.Response(200, json={"transcript": "battery forty two percent"})

    mock = SarvamClient("TEST-KEY", httpx.Client(base_url="https://api.sarvam.ai", transport=httpx.MockTransport(handler)))
    app.dependency_overrides[get_voice_client] = lambda: mock
    try:
        tts = client.post("/api/voice/tts", json={"text": "Divert now", "language": "hi-IN"})
        stt = client.post("/api/voice/stt", files={"audio": ("a.wav", b"abc", "audio/wav")}, data={"language": "en-IN"})
        missing = client.post("/api/voice/stt", files={"other": ("a.wav", b"abc", "audio/wav")})
    finally:
        app.dependency_overrides.pop(get_voice_client, None)
    assert tts.status_code == 200 and tts.json() == {"audio_base64": "QUJD", "format": "wav"}
    assert stt.status_code == 200 and stt.json() == {"text": "battery forty two percent", "soc_pct": 42}
    assert missing.status_code == 422 and missing.json()["error"] == "invalid_request"
