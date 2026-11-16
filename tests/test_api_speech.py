"""W0.5: the engine hands the frontend the EXACT sentences to speak, and text-to-speech falls back to the cached
clip of that sentence, flagged ``cached: true``, only when live text-to-speech fails.

Every voice call here goes to a stub: a test must never become a paid Sarvam call.
"""

from __future__ import annotations

import base64
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.main import app, get_audio_cache, get_voice_client
from verity.audio_cache import AudioCache
from verity.voice import VoiceUnavailable
from verity.voice_loop import DECISION_TEMPLATES, spoken_answers

WAV = b"RIFF\x24\x00\x00\x00WAVEfmt cached-clip-bytes"
LIVE_WAV = b"RIFF\x24\x00\x00\x00WAVEfmt live-clip-bytes"
BASE = {"origin": {"lat": 28.5355, "lon": 77.3910}, "destination": {"lat": 27.1767, "lon": 78.0081},
        "departure_time": "2026-10-28T14:00:00+05:30", "passengers": 4, "ac_on": True, "target_confidence": 0.9}
DEMO = {"V07": ("NO_GO", 70), "V12": ("GO", 62), "V19": ("INSUFFICIENT_EVIDENCE", 40)}
DEVANAGARI = re.compile(r"[ऀ-ॿ]")


# --- the sentences themselves (pure) -----------------------------------------------------------------------------------


def test_a_go_is_spoken_in_both_languages_with_the_confidence_in_words_and_the_bingo_line() -> None:
    speech = spoken_answers("GO", destination="Agra", confidence=0.95, bingo=46.0, reason_facts=None)
    assert set(speech) == {"en-IN", "hi-IN"}
    assert speech["en-IN"] == "Go. The chance of reaching Agra is 95 percent or more. Divert if the battery falls below 46 percent."
    assert "95 प्रतिशत या उससे अधिक" in speech["hi-IN"] and DEVANAGARI.search(speech["hi-IN"])


@pytest.mark.parametrize("confidence", [0.0, 0.05, 0.5, 0.8, 0.95, 1.0])
def test_no_sentence_ever_says_certainty(confidence: float) -> None:
    for decision in ("GO", "NO_GO"):
        for text in spoken_answers(decision, destination="Agra", confidence=confidence, bingo=40.0, reason_facts=None).values():
            assert "%" not in text and "100" not in text
            assert not re.search(r"\b0 (percent|प्रतिशत)", text)  # "40 percent" is fine; a bare "0 percent" is not


def test_a_refusal_is_the_fixed_template_whatever_the_numbers() -> None:
    speech = spoken_answers("INSUFFICIENT_EVIDENCE", destination="Agra", confidence=0.9, bingo=None, reason_facts=None)
    assert speech == {lang: DECISION_TEMPLATES[lang]["INSUFFICIENT_EVIDENCE"] for lang in ("en-IN", "hi-IN")}


def test_the_spoken_reason_says_the_target_in_words_in_both_languages() -> None:
    # The written reason keeps "(target 90%)"; read aloud it must not carry a symbol a voice may mangle.
    facts = {"kind": "NO_GO", "role": "stop", "name": "Tata", "p_works": 0.85, "n_reports": 1, "p_reach": 0.9,
             "confidence": 0.8, "target": 0.9}
    speech = spoken_answers("NO_GO", destination="Agra", confidence=0.8, bingo=None, reason_facts=facts)
    assert "target 90 percent" in speech["en-IN"] and "लक्ष्य 90 प्रतिशत" in speech["hi-IN"]
    assert all("%" not in text for text in speech.values())


def test_a_go_without_a_bingo_line_is_not_spoken_at_all() -> None:
    # "Divert below N percent" needs N from the engine; refuse rather than guess (golden rule 6).
    assert spoken_answers("GO", destination="Agra", confidence=0.95, bingo=None, reason_facts=None) == {}


# --- what the dispatch endpoint hands over ----------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.mark.parametrize("vehicle", sorted(DEMO))
def test_every_demo_dispatch_carries_the_sentences_to_speak(client: TestClient, vehicle: str) -> None:
    decision, soc = DEMO[vehicle]
    body = client.post("/api/dispatch", json={**BASE, "vehicle_id": vehicle, "start_soc_pct": soc}).json()
    assert body["decision"] == decision and set(body["speech"]) == {"en-IN", "hi-IN"}
    assert body["speech"]["en-IN"].startswith(DECISION_TEMPLATES["en-IN"][decision].split("{")[0])
    assert DEVANAGARI.search(body["speech"]["hi-IN"])
    assert all("%" not in t and "100" not in t for t in body["speech"].values())


def test_the_go_sentence_speaks_the_planned_bingo_line_and_the_charger_that_carries_it(client: TestClient) -> None:
    body = client.post("/api/dispatch", json={**BASE, "vehicle_id": "V12", "start_soc_pct": 62}).json()
    bingo = int(round(body["bingo_line"][0]["bingo_soc_pct"]))
    english = body["speech"]["en-IN"]
    assert f"below {bingo} percent" in english and "95 percent or more" in english
    assert "reachable" in english  # the W0.6 reason sentence follows the decision


# --- text-to-speech with the cache behind it --------------------------------------------------------------------------------


class LiveVoice:
    def tts(self, text: str, language: str) -> dict:
        return {"audio_base64": base64.b64encode(LIVE_WAV).decode(), "format": "wav"}


class UnreachableVoice:
    def tts(self, text: str, language: str) -> dict:
        raise VoiceUnavailable("voice service error: ConnectError")


@pytest.fixture
def cache(tmp_path: Path) -> AudioCache:
    cache = AudioCache(tmp_path / "audio_cache")
    cache.add("Go. Cached sentence.", "en-IN", WAV, tts_model="bulbul:v3", source="test")
    app.dependency_overrides[get_audio_cache] = lambda: cache
    yield cache
    app.dependency_overrides.pop(get_audio_cache, None)
    app.dependency_overrides.pop(get_voice_client, None)


def speak(client: TestClient, text: str = "Go. Cached sentence.", language: str = "en-IN"):
    return client.post("/api/voice/tts", json={"text": text, "language": language})


def test_live_voice_wins_when_it_works_and_is_not_flagged_cached(client: TestClient, cache: AudioCache) -> None:
    app.dependency_overrides[get_voice_client] = lambda: LiveVoice()
    r = speak(client)
    assert r.status_code == 200 and r.json()["cached"] is False
    assert base64.b64decode(r.json()["audio_base64"]) == LIVE_WAV  # not the cached clip


def test_with_no_key_a_cached_sentence_is_served_and_flagged(client: TestClient, cache: AudioCache) -> None:
    app.dependency_overrides[get_voice_client] = lambda: None
    r = speak(client)
    assert r.status_code == 200 and r.json()["cached"] is True and r.json()["format"] == "wav"
    assert base64.b64decode(r.json()["audio_base64"]) == WAV


def test_when_sarvam_is_unreachable_the_cached_sentence_is_served_and_flagged(client: TestClient, cache: AudioCache) -> None:
    app.dependency_overrides[get_voice_client] = lambda: UnreachableVoice()
    r = speak(client)
    assert r.status_code == 200 and r.json()["cached"] is True
    assert base64.b64decode(r.json()["audio_base64"]) == WAV


@pytest.mark.parametrize("voice", [None, UnreachableVoice()])
def test_a_sentence_that_was_never_cached_still_gets_the_honest_503(client: TestClient, cache: AudioCache, voice) -> None:
    app.dependency_overrides[get_voice_client] = lambda: voice
    r = speak(client, text="A sentence nobody generated.")
    assert r.status_code == 503 and r.json()["error"] == "voice_unavailable" and r.json()["message"]


def test_the_cached_clip_is_for_that_language_only(client: TestClient, cache: AudioCache) -> None:
    app.dependency_overrides[get_voice_client] = lambda: None
    assert speak(client, language="hi-IN").status_code == 503  # same text, other language: never cached
