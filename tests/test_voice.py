"""Voice client (against a mock transport only), battery-% parsing and the multipart parser."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from verity.voice import SarvamClient, VoiceUnavailable, parse_multipart, parse_soc_percent

KEY = "TEST-KEY-DO-NOT-LEAK"


def client_for(handler) -> SarvamClient:
    return SarvamClient(KEY, httpx.Client(base_url="https://api.sarvam.ai", transport=httpx.MockTransport(handler)))


@pytest.mark.parametrize("text, expected", [
    ("battery is 42 percent", 42), ("it says 7%", 7), ("४२ प्रतिशत", 42), ("forty two percent", 42),
    ("fifty-five", 55), ("one hundred", 100), ("seventy", 70), ("eleven percent", 11), ("100", 100), ("0 percent", 0),
    ("I do not know", None), ("250", None), ("", None),
])
def test_battery_percentage_is_read_from_digits_devanagari_and_words(text: str, expected: int | None) -> None:
    assert parse_soc_percent(text) == expected


def test_tts_sends_text_language_and_key_header_and_returns_base64_wav() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["key"] = request.headers.get("api-subscription-key")
        seen["body"] = request.read().decode("utf-8")
        return httpx.Response(200, json={"audios": ["QUJD"]})

    out = client_for(handler).tts("Divert now", "hi-IN")
    assert out == {"audio_base64": "QUJD", "format": "wav"}
    assert seen["key"] == KEY and "Divert now" in seen["body"] and "hi-IN" in seen["body"]


def test_stt_returns_the_transcript() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert b"audio-bytes" in request.read()
        return httpx.Response(200, json={"transcript": "battery forty two percent"})

    text = client_for(handler).stt(b"audio-bytes", "clip.wav", "audio/wav", "en-IN")
    assert parse_soc_percent(text) == 42


def test_service_errors_become_voice_unavailable_without_leaking_the_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": f"bad key {KEY}"})

    with pytest.raises(VoiceUnavailable) as err:
        client_for(handler).tts("x", "hi-IN")
    assert KEY not in str(err.value)

    def empty(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"audios": []})

    with pytest.raises(VoiceUnavailable):
        client_for(empty).tts("x", "hi-IN")


def test_from_env_is_none_without_a_key(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("SARVAM_API_KEY=\n", encoding="utf-8")
    assert SarvamClient.from_env(env) is None and SarvamClient.from_env(tmp_path / "missing") is None
    env.write_text(f"SARVAM_API_KEY={KEY}\n", encoding="utf-8")
    assert SarvamClient.from_env(env).api_key == KEY


def test_multipart_parser_returns_fields_and_files_with_the_standard_library_only() -> None:
    boundary = "XBOUNDARYX"
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"language\"\r\n\r\nhi-IN\r\n"
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"a.wav\"\r\n"
            f"Content-Type: audio/wav\r\n\r\n").encode() + b"\x00\x01RIFF\xff" + f"\r\n--{boundary}--\r\n".encode()
    fields, files = parse_multipart(f"multipart/form-data; boundary={boundary}", body)
    assert fields == {"language": "hi-IN"}
    assert files["file"] == ("a.wav", "audio/wav", b"\x00\x01RIFF\xff")
    with pytest.raises(ValueError):
        parse_multipart("application/json", b"{}")
