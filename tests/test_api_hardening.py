"""W0.7 at the HTTP level: /api/health reports the graph and whether voice has a key, the two voice endpoints are
rate limited per address, the plan and ride caches are bounded, and CORS origins and limits come from settings.

Every voice call here goes to a stub: a test must never become a paid Sarvam call, whatever is in the developer's .env.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api import main as api_main
from api import service as api_service
from api.limits import LruCache, RateLimiter
from api.main import app, get_service, get_voice_client, get_voice_limiter, voice_key_present

TTS_BODY = {"text": "Divert now", "language": "hi-IN"}
STT_FILES = {"audio": ("a.wav", b"x", "audio/wav")}


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def no_paid_voice():
    app.dependency_overrides[get_voice_client] = lambda: None
    yield
    app.dependency_overrides.pop(get_voice_client, None)


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def small_limiter(no_paid_voice):
    clock = Clock()
    limiter = RateLimiter(limit=2, window_s=60.0, clock=clock)
    limiter.clock = clock  # lets a test move time
    app.dependency_overrides[get_voice_limiter] = lambda: limiter
    yield limiter
    app.dependency_overrides.pop(get_voice_limiter, None)


# --- /api/health -------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("has_key", [False, True])
def test_health_reports_the_graph_in_use_and_whether_voice_has_a_key(client: TestClient, has_key: bool) -> None:
    app.dependency_overrides[voice_key_present] = lambda: has_key
    try:
        body = client.get("/api/health").json()
    finally:
        app.dependency_overrides.pop(voice_key_present, None)
    assert set(body) == {"status", "version", "neo4j", "is_simulated", "graph", "sarvam_key_present"}
    assert body["neo4j"] == "in_memory" and body["graph"]["backend"] == "memory"
    assert body["graph"]["nodes"] > 0 and body["graph"]["relationships"] > 0
    assert body["sarvam_key_present"] is has_key


SENTINEL_KEY = "sk-SENTINEL-must-never-be-echoed-0123456789"


def test_health_shows_that_a_key_exists_but_never_the_key(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    # The REAL presence check runs here (no dependency override), reading a key that exists only in this test.
    monkeypatch.setattr("verity.voice.load_env", lambda path=None: {"SARVAM_API_KEY": SENTINEL_KEY})
    r = client.get("/api/health")
    assert r.json()["sarvam_key_present"] is True
    assert SENTINEL_KEY not in r.text and SENTINEL_KEY not in str(dict(r.headers))


@pytest.mark.parametrize("env", [{}, {"SARVAM_API_KEY": ""}, {"NEO4J_URI": "x"}])
def test_health_says_voice_is_off_when_no_key_is_configured(client: TestClient, monkeypatch: pytest.MonkeyPatch,
                                                            env: dict) -> None:
    monkeypatch.setattr("verity.voice.load_env", lambda path=None: env)
    assert client.get("/api/health").json()["sarvam_key_present"] is False


def test_health_counts_are_cached_so_polling_does_not_hit_the_graph(monkeypatch: pytest.MonkeyPatch) -> None:
    svc = get_service()
    calls: list[int] = []
    real = svc.graph.mirror.counts
    monkeypatch.setattr(svc.graph.mirror, "counts", lambda: calls.append(1) or real())
    monkeypatch.setattr(svc, "_counts_cache", None)
    for _ in range(3):
        svc.health()
    assert len(calls) == 1
    monkeypatch.setattr(api_service, "HEALTH_COUNTS_TTL_S", 0.0)
    svc.health()
    assert len(calls) == 2


# --- rate limit on the voice endpoints ---------------------------------------------------------------------------------


def test_the_third_voice_call_in_a_window_is_refused_with_a_retry_after(client: TestClient, small_limiter) -> None:
    assert client.post("/api/voice/tts", json=TTS_BODY).status_code == 503  # no key: allowed through, not paid
    assert client.post("/api/voice/stt", files=STT_FILES, data={"language": "hi-IN"}).status_code == 503
    small_limiter.clock.now += 20
    r = client.post("/api/voice/tts", json=TTS_BODY)  # tts and stt share one budget: it is the paid calls that count
    assert r.status_code == 429
    assert r.json()["error"] == "rate_limited" and r.json()["message"]
    assert r.headers["retry-after"] == "40"


class RecordingVoice:
    """Stands in for SarvamClient and records every call that would have been paid for."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def tts(self, text: str, language: str) -> dict:
        self.calls.append("tts")
        return {"audio_base64": "QUJD", "format": "wav"}

    def stt(self, data: bytes, filename: str, content_type: str, language: str) -> str:
        self.calls.append("stt")
        return "battery forty two percent"


def test_a_refused_call_never_reaches_sarvam(client: TestClient, small_limiter) -> None:
    voice = RecordingVoice()
    app.dependency_overrides[get_voice_client] = lambda: voice  # replaced again by the fixture's teardown
    assert client.post("/api/voice/tts", json=TTS_BODY).status_code == 200
    assert client.post("/api/voice/stt", files=STT_FILES, data={"language": "en-IN"}).status_code == 200
    assert voice.calls == ["tts", "stt"]
    refused = [client.post("/api/voice/tts", json=TTS_BODY),
               client.post("/api/voice/stt", files=STT_FILES, data={"language": "en-IN"})]
    assert [r.status_code for r in refused] == [429, 429]
    assert voice.calls == ["tts", "stt"]  # no third and fourth paid call


def test_retry_after_can_be_read_by_a_page_on_another_origin(client: TestClient, small_limiter) -> None:
    origin = api_main.ALLOWED_ORIGINS[0]
    for _ in range(2):
        client.post("/api/voice/tts", json=TTS_BODY, headers={"Origin": origin})
    r = client.post("/api/voice/tts", json=TTS_BODY, headers={"Origin": origin})
    assert r.status_code == 429 and r.headers["access-control-allow-origin"] == origin
    exposed = [h.strip().lower() for h in r.headers["access-control-expose-headers"].split(",")]
    assert "retry-after" in exposed  # otherwise fetch() in the browser reads null and cannot wait the right time


def test_waiting_out_the_window_restores_the_budget(client: TestClient, small_limiter) -> None:
    for _ in range(2):
        client.post("/api/voice/tts", json=TTS_BODY)
    assert client.post("/api/voice/tts", json=TTS_BODY).status_code == 429
    small_limiter.clock.now += 61
    assert client.post("/api/voice/tts", json=TTS_BODY).status_code == 503


def test_only_the_voice_endpoints_are_limited(client: TestClient, small_limiter) -> None:
    for _ in range(5):
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/demo/scenario").status_code == 200


def test_the_limit_is_per_address_behind_a_trusted_proxy(client: TestClient, small_limiter, monkeypatch) -> None:
    monkeypatch.setattr(api_main, "TRUST_PROXY", True)
    one, other = {"X-Forwarded-For": "203.0.113.7"}, {"X-Forwarded-For": "198.51.100.9"}
    forged = {"X-Forwarded-For": "6.6.6.6, 203.0.113.7"}  # the caller wrote the first entry; the proxy wrote the last
    assert [client.post("/api/voice/tts", json=TTS_BODY, headers=h).status_code for h in (one, forged)] == [503, 503]
    assert client.post("/api/voice/tts", json=TTS_BODY, headers=one).status_code == 429
    assert client.post("/api/voice/tts", json=TTS_BODY, headers=other).status_code == 503


def test_a_forwarded_header_is_ignored_unless_the_proxy_is_trusted(client: TestClient, small_limiter,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api_main, "TRUST_PROXY", False)  # whatever the developer's .env says
    statuses = [client.post("/api/voice/tts", json=TTS_BODY, headers={"X-Forwarded-For": f"10.0.0.{i}"}).status_code
                for i in range(3)]
    assert statuses == [503, 503, 429]  # one TCP peer, however many addresses it claims


# --- bounded caches ----------------------------------------------------------------------------------------------------


def test_the_plan_and_ride_caches_are_lru_bounded_to_a_few_hundred() -> None:
    svc = get_service()
    assert isinstance(svc.plans, LruCache) and svc.plans.maxsize == api_service.PLAN_CACHE_SIZE
    assert isinstance(svc._ride_cache, LruCache) and svc._ride_cache.maxsize == api_service.RIDE_CACHE_SIZE
    assert 100 <= api_service.PLAN_CACHE_SIZE <= 1000 and 100 <= api_service.RIDE_CACHE_SIZE <= 1000


def test_an_evicted_plan_is_a_clean_404_not_a_crash(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    svc = get_service()
    monkeypatch.setattr(svc, "plans", LruCache(1))
    svc.plans["PL-old"] = "stand-in"  # type: ignore[assignment]
    svc.plans["PL-new"] = "stand-in"  # type: ignore[assignment]  # evicts PL-old
    r = client.get("/api/demo/replay/PL-old")
    assert r.status_code == 404 and r.json()["error"] == "unknown_plan"


# --- settings ---------------------------------------------------------------------------------------------------------


def test_cors_origins_come_from_the_environment_and_replace_the_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api_main, "load_env", lambda: {})
    monkeypatch.setenv("VERITY_CORS_ORIGINS", "https://verity.example, https://b.example/")
    assert api_main.cors_origins() == ["https://verity.example", "https://b.example"]
    monkeypatch.delenv("VERITY_CORS_ORIGINS")
    assert api_main.cors_origins() == api_main.DEFAULT_ORIGINS


def test_cors_origins_can_be_set_in_dotenv_and_the_environment_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api_main, "load_env", lambda: {"VERITY_CORS_ORIGINS": "https://dotenv.example"})
    monkeypatch.delenv("VERITY_CORS_ORIGINS", raising=False)
    assert api_main.cors_origins() == ["https://dotenv.example"]
    monkeypatch.setenv("VERITY_CORS_ORIGINS", "https://env.example")
    assert api_main.cors_origins() == ["https://env.example"]


def test_the_default_cors_origins_are_unchanged() -> None:
    assert api_main.DEFAULT_ORIGINS == ["null", "http://localhost:5500", "http://127.0.0.1:5500",
                                        "https://ansh-goyal01.github.io"]


def test_the_voice_budget_is_a_setting_with_a_default_of_twenty_a_minute(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api_main, "load_env", lambda: {})
    monkeypatch.delenv("VERITY_VOICE_RATE_PER_MIN", raising=False)
    assert api_main.voice_rate_per_min() == 20
    monkeypatch.setenv("VERITY_VOICE_RATE_PER_MIN", "5")
    assert api_main.voice_rate_per_min() == 5
    monkeypatch.setenv("VERITY_VOICE_RATE_PER_MIN", "0")
    with pytest.raises(ValueError, match="VERITY_VOICE_RATE_PER_MIN"):
        api_main.voice_rate_per_min()


@pytest.mark.parametrize(("raw", "expected"), [(None, False), ("", False), ("0", False), ("no", False),
                                                ("1", True), ("true", True), ("YES", True)])
def test_trusting_a_proxy_is_opt_in(monkeypatch: pytest.MonkeyPatch, raw: str | None, expected: bool) -> None:
    monkeypatch.setattr(api_main, "load_env", lambda: {})
    if raw is None:
        monkeypatch.delenv("VERITY_TRUST_PROXY", raising=False)
    else:
        monkeypatch.setenv("VERITY_TRUST_PROXY", raw)
    assert api_main.trust_proxy_enabled() is expected
