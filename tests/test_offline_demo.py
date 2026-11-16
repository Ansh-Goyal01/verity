"""W0.5 acceptance (F9): every API call the demo makes succeeds with the network cut.

A venue's wifi is the one thing the demo cannot control. Here every connection to anything but this machine, and every
DNS lookup, is refused and recorded; the service starts with Neo4j credentials that cannot be reached (so it must fall
back to the in-memory graph), the demo's calls run, and the spoken answers come from the committed audio cache while
Sarvam is unreachable. The decisive assertion: the demo's own calls never even TRIED to reach the network.
"""

from __future__ import annotations

import base64
import socket

import httpx
import pytest
from fastapi.testclient import TestClient

import api.main as api_main
from api.main import app, get_voice_client
from api.service import DRIVER_DEMO, FLEET_META, EngineService, demo_dispatch_request
from verity.audio_cache import AudioCache
from verity.voice import SARVAM_BASE, SarvamClient

LOOPBACK = {"127.0.0.1", "::1", "localhost", "0.0.0.0", "", None}  # asyncio's own wake-up sockets use loopback
PINNED = {"V07": "NO_GO", "V12": "GO", "V19": "INSUFFICIENT_EVIDENCE"}
FAKE_NEO4J = {"NEO4J_URI": "neo4j+s://offline.invalid", "NEO4J_USERNAME": "neo4j", "NEO4J_PASSWORD": "not-a-real-password"}


def dead_sarvam() -> SarvamClient:
    """A real client configured with a key whose network is gone: the unreachable case, not the no-key case."""
    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("network is unreachable", request=request)

    return SarvamClient("test-key-not-real", httpx.Client(base_url=SARVAM_BASE, transport=httpx.MockTransport(unreachable)))


@pytest.fixture(scope="module")
def offline():
    """Network cut for the whole module; yields (client, attempts) with ``attempts`` = refused connections so far."""
    attempts: list[str] = []
    real_connect, real_connect_ex, real_lookup = socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo

    def target(address) -> object:
        return address[0] if isinstance(address, tuple) else address

    def guarded_connect(self, address):
        if target(address) not in LOOPBACK:
            attempts.append(f"connect {target(address)}")
            raise OSError(f"network disabled by the test: {target(address)}")
        return real_connect(self, address)

    def guarded_connect_ex(self, address):
        if target(address) not in LOOPBACK:
            attempts.append(f"connect_ex {target(address)}")
            return 101  # ENETUNREACH
        return real_connect_ex(self, address)

    def guarded_lookup(host, *args, **kwargs):
        if host not in LOOPBACK:
            attempts.append(f"dns {host}")
            raise socket.gaierror(f"network disabled by the test: {host}")
        return real_lookup(host, *args, **kwargs)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(socket.socket, "connect", guarded_connect)
        mp.setattr(socket.socket, "connect_ex", guarded_connect_ex)
        mp.setattr(socket, "getaddrinfo", guarded_lookup)
        for name, value in FAKE_NEO4J.items():  # an unreachable Neo4j is "configured"; real env vars beat .env, so this
            mp.setenv(name, value)  # holds whatever the shell (e.g. NEO4J_URI= to skip live tests) or .env says
        service = EngineService(graph_backend="auto")  # must fall back to memory, not hang or crash
        previous, api_main._service = api_main._service, service
        app.dependency_overrides[get_voice_client] = dead_sarvam
        try:
            with TestClient(app) as client:
                yield client, attempts
        finally:
            app.dependency_overrides.pop(get_voice_client, None)
            api_main._service = previous


@pytest.fixture(scope="module")
def started(offline) -> int:
    """How many connections had already been refused once the service was up (the Neo4j attempt at startup)."""
    return len(offline[1])


@pytest.fixture(scope="module")
def plans(offline) -> dict[str, dict]:
    client, _ = offline
    out = {}
    for vehicle in FLEET_META:
        r = client.post("/api/dispatch", json=demo_dispatch_request(vehicle))
        assert r.status_code == 200, r.text
        out[vehicle] = r.json()
    return out


def test_the_service_started_with_neo4j_unreachable_and_fell_back_to_memory(offline) -> None:
    client, attempts = offline
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["neo4j"] == "in_memory" and health.json()["graph"]["backend"] == "memory"
    assert any("offline.invalid" in a for a in attempts), "the test did not actually cut off a Neo4j attempt"


def test_the_demo_scenario_and_every_decision_work_offline_and_are_the_pinned_ones(offline, plans, started) -> None:
    client, _ = offline
    assert client.get("/api/demo/scenario").status_code == 200
    assert {v: p["decision"] for v, p in plans.items()} == PINNED
    assert all(p["is_simulated"] is True and p["n_runs"] >= 1 for p in plans.values())


def test_replay_live_update_ride_check_driver_demo_and_chargers_work_offline(offline, plans, started) -> None:
    client, _ = offline
    pid = plans["V12"]["plan_id"]
    assert client.get(f"/api/demo/replay/{pid}").status_code == 200
    update = {"plan_id": pid, "t": "2026-10-28T14:55:00+05:30", "lat": 28.10, "lon": 77.60, "soc_pct": 45}
    assert client.post("/api/forecast/update", json=update).status_code == 200
    ride = client.post("/api/ride-check", json=DRIVER_DEMO)
    assert ride.status_code == 200 and ride.json()["decision"] in ("GO", "NO_GO", "INSUFFICIENT_EVIDENCE")
    assert client.get("/api/demo/driver").status_code == 200
    box = {"min_lat": 27.0, "min_lon": 77.0, "max_lat": 29.5, "max_lon": 78.5}
    assert client.get("/api/chargers", params=box).status_code == 200


def test_every_spoken_answer_of_the_demo_comes_from_the_cache_when_sarvam_is_unreachable(offline, plans, started) -> None:
    client, _ = offline
    cache, spoken = AudioCache(), 0
    for vehicle, plan in plans.items():
        assert set(plan["speech"]) == {"en-IN", "hi-IN"}, f"{vehicle} has nothing to say"
        for language, text in plan["speech"].items():
            r = client.post("/api/voice/tts", json={"text": text, "language": language})
            assert r.status_code == 200, (
                f"{vehicle} [{language}] is not cached: run  uv run python scripts/build_audio_cache.py  (paid TTS calls)")
            body = r.json()
            audio = base64.b64decode(body["audio_base64"])
            assert body["cached"] is True and body["format"] == "wav" and audio.startswith(b"RIFF")
            assert audio == cache.lookup(text, language)
            spoken += 1
    assert spoken == 2 * len(FLEET_META)


def test_speech_to_text_has_no_offline_clip_and_says_so_honestly(offline, started) -> None:
    client, _ = offline
    r = client.post("/api/voice/stt", files={"audio": ("a.wav", b"x", "audio/wav")}, data={"language": "hi-IN"})
    assert r.status_code == 503 and r.json()["error"] == "voice_unavailable"  # the UI falls back to the keypad


def test_the_demos_own_calls_never_tried_to_reach_the_network(offline, plans, started) -> None:
    _, attempts = offline
    assert attempts[started:] == [], f"the demo reached for the network: {attempts[started:]}"
