"""F7 voice loop: battery %, spoken charger reports, decision templates, and the verified request fields (mock)."""

from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest

from verity.schemas import ChargerRecord
from verity.voice import SarvamClient
from verity.voice_loop import match_spoken_charger, parse_status, render_decision, soc_from_transcript, voice_report

T = datetime(2026, 11, 12, 18, 0, tzinfo=timezone.utc)
CHARGERS = [
    ChargerRecord(id="CH-0001", name="Tata Power Sector 62 Noida", lat=28.62, lon=77.36, operator="Tata Power"),
    ChargerRecord(id="CH-0002", name="Statiq Sector 18", lat=28.57, lon=77.32, operator="Statiq"),
    ChargerRecord(id="CH-0003", name="Statiq Sector 18", lat=28.58, lon=77.33, operator="Statiq"),
]


@pytest.mark.parametrize("text, expected", [
    ("Sector 62 wala charger band hai", "broken"), ("charger kharab hai", "broken"), ("चार्जर बंद है", "broken"),
    ("bijli nahi hai yahan", "no_power"), ("lambi line hai", "occupied"), ("charger chal raha hai", "working"),
    ("petrol car parked hai", "blocked"), ("hello", None),
])
def test_status_words_in_hinglish_hindi_and_english(text: str, expected: str | None) -> None:
    assert parse_status(text) == expected


def test_battery_from_hinglish_and_translated_hindi_words() -> None:
    assert soc_from_transcript("battery 42 pratishat hai") == (42, "transcript")
    assert soc_from_transcript("बैटरी बयालीस प्रतिशत") == (None, "not found")
    assert soc_from_transcript("बैटरी बयालीस प्रतिशत", lambda t: "battery forty two percent") == (42, "translated")
    # STT translit mode writes "Battery bayalis pratishat hai": romanized Hindi, read through translation
    assert soc_from_transcript("Battery bayalis pratishat hai", lambda t: "Battery is 42 percent", "hi-IN") == (
        42, "translated")
    assert soc_from_transcript("Battery bayalis pratishat hai", lambda t: "x", "en-IN") == (None, "not found")


def test_spoken_charger_match_is_unique_or_refused() -> None:
    hit = match_spoken_charger("Sector 62 wala Tata charger band hai", CHARGERS)
    assert hit["charger_id"] == "CH-0001" and hit["coverage"] >= 0.5
    tie = match_spoken_charger("Statiq sector 18 band hai", CHARGERS)
    assert tie["charger_id"] is None and "several" in tie["reason"]
    near = match_spoken_charger("Statiq sector 18 band hai", CHARGERS, near=(28.57, 77.32), radius_km=0.5)
    assert near["charger_id"] == "CH-0002"
    assert match_spoken_charger("Sector 62 wala charger band hai", CHARGERS[1:])["charger_id"] is None
    # the live check's false match: only the generic word "sector" in common must never match
    assert match_spoken_charger("Sector basath wala charger band hai", CHARGERS)["charger_id"] is None
    record, _ = voice_report("Sector basath wala Tata charger band hai", CHARGERS, T, "d", from_real_person=False,
                             translation="The Sector 62 Tata charger is closed")
    assert record["charger_id"] == "CH-0001"
    # second live false match: "Battery Smart" (a swap operator) must not match the spoken word "battery"
    swap = [ChargerRecord(id="CH-0195", name="Battery Smart", lat=28.69, lon=77.21, operator="Battery Smart")]
    assert match_spoken_charger("Battery bayaalis pratishat hai Sector baasath wala charger band hai The battery is "
                                "forty-two percent, the sixty-two sector charger is off.", swap)["charger_id"] is None


def test_voice_report_is_simulated_unless_from_a_real_person() -> None:
    record, report = voice_report("Sector 62 wala Tata charger band hai", CHARGERS, T, "d-01", from_real_person=False)
    assert record["source_kind"] == "driver_voice" and record["is_simulated"] is True
    assert report is not None and report.is_simulated and report.status == "broken"
    assert report.source_id == "driver_voice:d-01" and report.charger_id == "CH-0001"
    real, real_report = voice_report("Sector 62 wala Tata charger band hai", CHARGERS, T, "d-01", from_real_person=True)
    assert real["is_simulated"] is False and real_report is not None and real_report.is_simulated is False
    _, none = voice_report("charger band hai", CHARGERS, T, "d-01", from_real_person=True)
    assert none is None


def test_decision_templates_take_numbers_only_from_the_engine() -> None:
    hi = render_decision("GO", "hi-IN", destination="Agra", confidence=0.95, bingo=11)
    assert "95 प्रतिशत" in hi and "11 प्रतिशत" in hi and "Agra" in hi
    assert "85 percent" in render_decision("NO_GO", "en-IN", destination="Agra", confidence=0.85)
    assert "evidence" in render_decision("INSUFFICIENT_EVIDENCE", "en-IN")
    with pytest.raises(ValueError):
        render_decision("GO", "en-IN", destination="Agra")


def test_tts_and_stt_send_the_documented_fields() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen[request.url.path] = request.read()
        if request.url.path == "/text-to-speech":
            return httpx.Response(200, json={"audios": ["QUJD"]})
        return httpx.Response(200, json={"transcript": "battery 42 pratishat", "language_code": "hi-IN"})

    client = SarvamClient("K", httpx.Client(base_url="https://api.sarvam.ai", transport=httpx.MockTransport(handler)))
    client.tts("नमस्ते", "hi-IN")
    tts = seen["/text-to-speech"].decode("utf-8")
    assert '"language_code"' in tts and "target_language_code" not in tts and "bulbul:v3" in tts
    out = client.stt_full(b"RIFF", "a.wav", "audio/wav", "unknown", "translit")
    stt = seen["/speech-to-text"]
    assert out == {"transcript": "battery 42 pratishat", "language_code": "hi-IN"}
    assert b"translit" in stt and b"saaras:v3" in stt and b"unknown" in stt
    assert [c["path"] for c in client.calls] == ["/text-to-speech", "/speech-to-text"]
    with pytest.raises(ValueError):
        client.stt_full(b"", "a.wav", "audio/wav", "unknown", "shout")
