"""W3.2: POST /api/voice/ride-check - hear, parse, confirm, decide, speak.

Stage 1 (multipart audio) hears and parses and answers with a read-back; it never runs the ride-check. Stage 2 (JSON,
``confirmed: true``) runs the existing ride-check and speaks the fixed-template answer. Every Sarvam call goes to a fake:
no test here is a paid call.
"""

from __future__ import annotations

import base64
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.main import app, get_audio_cache, get_service, get_voice_client
from api.service import DRIVER_DEMO
from verity.audio_cache import AudioCache
from verity.ride_voice import LLM_TIMEOUT_S
from verity.voice import VoiceUnavailable

WAV = b"RIFF\x24\x00\x00\x00WAVEfmt fake-voice-clip"
HINGLISH = "Battery 45 percent hai, Sector 18 se airport ka ride mila hai"
HINDI = "बैटरी 45 प्रतिशत है, सेक्टर 18 से एयरपोर्ट का राइड मिला है"
CONFIRM = {"confirmed": True, "soc_pct": 45, "pickup_id": "noida-sector-18", "drop_id": "igi-airport-t3", "language": "en-IN"}
AUDIO = {"audio": ("ride.wav", b"fake-audio", "audio/wav")}


class FakeSarvam:
    """Stands in for SarvamClient: hears what it is told to, and records every call that would have been paid for."""

    def __init__(self, transcript: str = HINGLISH, language_code: str | None = "en-IN", chat: dict | Exception | None = None,
                 tts_fails: bool = False) -> None:
        self.transcript, self.language_code, self.chat, self.tts_fails = transcript, language_code, chat, tts_fails
        self.stt_calls, self.chat_calls, self.tts_calls = [], [], []

    def stt_full(self, audio: bytes, filename: str, content_type: str, language: str = "unknown", mode: str | None = None) -> dict:
        self.stt_calls.append((filename, language, mode))
        return {"transcript": self.transcript, "language_code": self.language_code}

    def chat_json(self, system: str, user: str, schema: dict, name: str = "labels", timeout: float | None = None) -> dict:
        self.chat_calls.append({"user": user, "timeout": timeout})
        if isinstance(self.chat, Exception) or self.chat is None:
            raise self.chat or VoiceUnavailable("voice service error: ReadTimeout")
        return self.chat

    def tts(self, text: str, language: str) -> dict:
        self.tts_calls.append((language, text))
        if self.tts_fails:
            raise VoiceUnavailable("voice service error: ConnectError")
        return {"audio_base64": base64.b64encode(WAV).decode(), "format": "wav"}


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def cache(tmp_path: Path):
    cache = AudioCache(tmp_path / "audio_cache")
    app.dependency_overrides[get_audio_cache] = lambda: cache
    yield cache
    app.dependency_overrides.pop(get_audio_cache, None)
    app.dependency_overrides.pop(get_voice_client, None)


def with_voice(fake: FakeSarvam | None) -> None:
    app.dependency_overrides[get_voice_client] = lambda: fake


def hear(client: TestClient, language: str = "unknown", extra: dict | None = None):
    return client.post("/api/voice/ride-check", files=AUDIO, data={"language": language, **(extra or {})})


# --- stage 1: hear and read back -----------------------------------------------------------------------------------------------


def test_the_demo_sentence_is_heard_parsed_by_rules_and_read_back_without_running_the_ride_check(
        client: TestClient, cache: AudioCache, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeSarvam()
    with_voice(fake)
    monkeypatch.setattr(get_service(), "ride_check", lambda req: pytest.fail("nothing runs on an unconfirmed parse"))
    r = hear(client)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["stage"] == "confirm" and body["needs_confirmation"] is True
    assert body["transcript"] == HINGLISH and body["language_code"] == "en-IN" and body["reply_language"] == "en-IN"
    assert body["slots"]["soc_pct"] == 45 and body["slots"]["parsed_by"] == "rules" and body["slots"]["missing"] == []
    assert (body["slots"]["pickup"]["id"], body["slots"]["drop"]["id"]) == ("noida-sector-18", "igi-airport-t3")
    assert body["prompt"] == "45 percent battery, Sector 18 to IGI Airport Terminal 3. Is that right?"
    assert body["prompt_audio"]["cached"] is False and base64.b64decode(body["prompt_audio"]["audio_base64"]) == WAV
    assert fake.chat_calls == [] and len(fake.stt_calls) == 1 and fake.stt_calls[0][1] == "unknown"  # the rule path is ONE call


def test_every_stage_reports_its_time(client: TestClient, cache: AudioCache) -> None:
    with_voice(FakeSarvam())
    timings = hear(client).json()["timings_ms"]
    assert set(timings) == {"hear", "parse", "llm", "decide", "speak", "total"}
    assert all(isinstance(timings[k], int) and timings[k] >= 0 for k in ("hear", "parse", "speak", "total"))
    assert timings["llm"] is None and timings["decide"] is None  # not used on this stage / on the rule path
    assert timings["total"] >= timings["hear"] + timings["parse"]


def test_the_driver_is_answered_in_the_language_that_was_detected(client: TestClient, cache: AudioCache) -> None:
    with_voice(FakeSarvam(transcript=HINDI, language_code="hi-IN"))
    body = hear(client).json()
    assert body["reply_language"] == "hi-IN" and body["transcript"] == HINDI  # the transcript stays in the spoken script
    assert body["prompt"] == "बैटरी 45 प्रतिशत, सेक्टर 18 से आईजीआई एयरपोर्ट टर्मिनल 3। सही है?"


def test_a_missing_slot_goes_to_the_model_with_the_four_second_limit_and_is_confirmed_like_any_other(
        client: TestClient, cache: AudioCache) -> None:
    fake = FakeSarvam(transcript="बैटरी पैंतालीस प्रतिशत है, सेक्टर 18 से एयरपोर्ट का राइड", language_code="hi-IN",
                      chat={"soc_pct": 45, "pickup": "sector 18", "drop": "airport"})
    with_voice(fake)
    body = hear(client).json()
    assert body["stage"] == "confirm" and body["slots"]["parsed_by"] == "rules+llm" and body["slots"]["soc_pct"] == 45
    assert len(fake.chat_calls) == 1 and fake.chat_calls[0]["timeout"] == LLM_TIMEOUT_S
    assert isinstance(body["timings_ms"]["llm"], int)


def test_a_slow_model_means_asking_again_never_an_error_and_never_a_decision(
        client: TestClient, cache: AudioCache, monkeypatch: pytest.MonkeyPatch) -> None:
    with_voice(FakeSarvam(transcript="बैटरी पैंतालीस प्रतिशत है, सेक्टर 18 से एयरपोर्ट", language_code="hi-IN"))  # chat times out
    monkeypatch.setattr(get_service(), "ride_check", lambda req: pytest.fail("nothing runs on an unconfirmed parse"))
    r = hear(client)
    assert r.status_code == 200
    body = r.json()
    assert body["stage"] == "clarify" and body["needs_confirmation"] is False
    assert body["slots"]["missing"] == ["soc_pct"] and "बैटरी प्रतिशत" in body["prompt"]


def test_a_request_with_no_place_at_all_is_asked_about_every_missing_thing(client: TestClient, cache: AudioCache) -> None:
    with_voice(FakeSarvam(transcript="hello", chat={"soc_pct": None, "pickup": None, "drop": None}))
    body = hear(client).json()
    assert body["stage"] == "clarify" and body["slots"]["missing"] == ["soc_pct", "pickup", "drop"]
    assert body["prompt"] == "I did not catch the battery percentage, the pickup place and the drop place. Please say it again."


def test_a_place_the_model_hears_but_we_do_not_know_is_said_out_loud(client: TestClient, cache: AudioCache) -> None:
    with_voice(FakeSarvam(transcript="battery 45 percent, radisson se airport",
                          chat={"soc_pct": 45, "pickup": "Radisson", "drop": "airport"}))
    body = hear(client).json()
    assert body["stage"] == "clarify" and body["slots"]["unresolved"] == ["Radisson"]
    assert "I do not know the place Radisson yet" in body["prompt"]


def test_a_read_back_that_cannot_be_synthesised_is_still_returned_as_text(client: TestClient, cache: AudioCache) -> None:
    with_voice(FakeSarvam(tts_fails=True))
    body = hear(client).json()
    assert body["stage"] == "confirm" and body["prompt"] and body["prompt_audio"] is None  # the app uses browser speech


def test_the_read_back_comes_from_the_cache_when_sarvam_cannot_speak_it(client: TestClient, cache: AudioCache) -> None:
    cache.add("45 percent battery, Sector 18 to IGI Airport Terminal 3. Is that right?", "en-IN", WAV,
              tts_model="bulbul:v3", source="test")
    with_voice(FakeSarvam(tts_fails=True))
    clip = hear(client).json()["prompt_audio"]
    assert clip["cached"] is True and base64.b64decode(clip["audio_base64"]) == WAV


def test_speaking_can_be_switched_off_for_a_text_only_client(client: TestClient, cache: AudioCache) -> None:
    fake = FakeSarvam()
    with_voice(fake)
    body = hear(client, extra={"speak": "0"}).json()
    assert body["prompt_audio"] is None and fake.tts_calls == [] and body["timings_ms"]["speak"] is None


def test_without_a_key_hearing_is_an_honest_503(client: TestClient, cache: AudioCache) -> None:
    with_voice(None)
    r = hear(client)
    assert r.status_code == 503 and r.json()["error"] == "voice_unavailable"


def test_a_request_without_audio_is_a_422(client: TestClient, cache: AudioCache) -> None:
    with_voice(FakeSarvam())
    r = client.post("/api/voice/ride-check", files={"other": ("x", b"y", "audio/wav")}, data={"language": "unknown"})
    assert r.status_code == 422 and r.json()["error"] == "invalid_request"


# --- stage 2: confirmed -> decide -> speak ------------------------------------------------------------------------------------


def test_a_confirmed_ride_runs_the_existing_ride_check_and_speaks_the_fixed_sentence(client: TestClient, cache: AudioCache) -> None:
    fake = FakeSarvam()
    with_voice(fake)
    r = client.post("/api/voice/ride-check", json=CONFIRM)
    assert r.status_code == 200, r.text
    body = r.json()
    direct = client.post("/api/ride-check", json=DRIVER_DEMO).json()  # the same request, made the ordinary way
    assert body["stage"] == "decided" and body["reply_language"] == "en-IN"
    assert (body["ride_check"]["decision"], body["ride_check"]["confidence"]) == (direct["decision"], direct["confidence"])
    assert body["speech"] == direct["speech"]["en-IN"] and body["speech"].startswith("Take the ride.")
    assert fake.tts_calls == [("en-IN", body["speech"])] and body["audio"]["cached"] is False
    assert isinstance(body["timings_ms"]["decide"], int) and isinstance(body["timings_ms"]["speak"], int)
    assert body["timings_ms"]["hear"] is None and body["timings_ms"]["total"] >= body["timings_ms"]["decide"]


def test_an_unconfirmed_ride_runs_nothing_and_speaks_nothing(client: TestClient, cache: AudioCache,
                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeSarvam()
    with_voice(fake)
    monkeypatch.setattr(get_service(), "ride_check", lambda req: pytest.fail("the driver said no"))
    body = client.post("/api/voice/ride-check", json={**CONFIRM, "confirmed": False}).json()
    assert body["stage"] == "not_confirmed" and body["ride_check"] is None and body["speech"] is None
    assert body["audio"] is None and fake.tts_calls == []


def test_a_confirmed_ride_can_be_answered_in_hindi(client: TestClient, cache: AudioCache) -> None:
    with_voice(FakeSarvam())
    body = client.post("/api/voice/ride-check", json={**CONFIRM, "language": "hi-IN"}).json()
    assert body["reply_language"] == "hi-IN" and body["speech"].startswith("राइड ले लीजिए।")


def test_the_decision_works_with_no_key_at_all_and_uses_the_cached_clip_when_there_is_one(client: TestClient, cache: AudioCache) -> None:
    direct = client.post("/api/ride-check", json=DRIVER_DEMO).json()
    with_voice(None)
    text_only = client.post("/api/voice/ride-check", json=CONFIRM).json()
    assert text_only["stage"] == "decided" and text_only["speech"] and text_only["audio"] is None  # offline, nothing cached
    cache.add(direct["speech"]["en-IN"], "en-IN", WAV, tts_model="bulbul:v3", source="test")
    cached = client.post("/api/voice/ride-check", json=CONFIRM).json()
    assert cached["audio"]["cached"] is True and base64.b64decode(cached["audio"]["audio_base64"]) == WAV


@pytest.mark.parametrize("patch", [{"pickup_id": "nowhere"}, {"drop_id": "noida-sector-18"}, {"soc_pct": 140},
                                   {"language": "ta-IN"}, {"soc_pct": "high"}])
def test_a_confirmation_that_does_not_make_sense_is_a_422_not_a_guess(client: TestClient, cache: AudioCache, patch: dict) -> None:
    with_voice(FakeSarvam())
    r = client.post("/api/voice/ride-check", json={**CONFIRM, **patch})
    assert r.status_code == 422 and r.json()["error"] == "invalid_request"


def test_a_ride_from_a_place_to_itself_is_refused(client: TestClient, cache: AudioCache) -> None:
    with_voice(FakeSarvam())
    r = client.post("/api/voice/ride-check", json={**CONFIRM, "drop_id": CONFIRM["pickup_id"]})
    assert r.status_code == 422 and "same place" in r.json()["message"]


# --- the ordinary ride-check carries the sentence too ------------------------------------------------------------------------


def test_the_typed_ride_check_response_carries_the_sentences_to_speak(client: TestClient) -> None:
    body = client.post("/api/ride-check", json=DRIVER_DEMO).json()
    assert set(body["speech"]) == {"en-IN", "hi-IN"} and "%" not in body["speech"]["en-IN"] and "100" not in body["speech"]["en-IN"]
