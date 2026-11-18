"""W3.1: Sarvam model ids and the key are settings, not constants.

Read from the vendor docs on 2026-11-16 (docs/ENGINE_NOTES.md, Sarvam API notes): saaras:v4 and bulbul:v4-flash exist and the
vendor recommends them; the models verified live here are saaras:v3 and bulbul:v3. So every id is configurable, the
default is whatever was last MEASURED to work, and the paid-call log names the id each call actually used.
Mock transport only: no test here is a paid call.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from verity.paths import REPO_ROOT
from verity.trust.graph import load_env
from verity.voice import CHAT_MODEL, STT_MODEL, TTS_MODEL, SarvamClient, VoiceModels, models_from_settings

KEY = "TEST-KEY-DO-NOT-LEAK"


def recording_client(reply: dict, models: VoiceModels | None = None) -> tuple[SarvamClient, dict]:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"], seen["path"], seen["timeout"] = request.read(), request.url.path, request.extensions.get("timeout")
        return httpx.Response(200, json=reply)

    http = httpx.Client(base_url="https://api.sarvam.ai", transport=httpx.MockTransport(handler))
    return (SarvamClient(KEY, http) if models is None else SarvamClient(KEY, http, models=models)), seen


# --- the settings ---------------------------------------------------------------------------------------------------------


def test_the_defaults_are_the_models_verified_live() -> None:
    models = VoiceModels()
    assert (models.stt_model, models.stt_mode, models.tts_model, models.chat_model) == (
        STT_MODEL, "transcribe", TTS_MODEL, CHAT_MODEL)
    assert models.tts_speaker is None and models.chat_reasoning == "low"


def test_every_id_can_be_set_from_settings() -> None:
    values = {"SARVAM_STT_MODEL": "saaras:v4", "SARVAM_STT_MODE": "codemix", "SARVAM_TTS_MODEL": "bulbul:v4-flash",
              "SARVAM_TTS_SPEAKER": "simran_en_customer", "SARVAM_CHAT_MODEL": "sarvam-105b-conversations",
              "SARVAM_CHAT_REASONING": "omit"}
    models = models_from_settings(values.get)
    assert (models.stt_model, models.stt_mode, models.tts_model, models.tts_speaker) == (
        "saaras:v4", "codemix", "bulbul:v4-flash", "simran_en_customer")
    assert (models.chat_model, models.chat_reasoning) == ("sarvam-105b-conversations", "omit")


@pytest.mark.parametrize("blank", [None, "", "   "])
def test_a_blank_setting_keeps_the_default(blank: str | None) -> None:
    assert models_from_settings(lambda name: blank) == VoiceModels()


@pytest.mark.parametrize(("name", "value"), [("SARVAM_STT_MODE", "shout"), ("SARVAM_CHAT_REASONING", "extreme")])
def test_a_bad_value_fails_at_startup_naming_the_setting(name: str, value: str) -> None:
    with pytest.raises(ValueError, match=name):
        models_from_settings({name: value}.get)


# --- the models are what the requests carry ---------------------------------------------------------------------------------


def test_text_to_speech_sends_the_configured_model_and_speaker() -> None:
    models = VoiceModels(tts_model="bulbul:v4-flash", tts_speaker="simran_en_customer")
    client, seen = recording_client({"audios": ["QUJD"]}, models)
    client.tts("Divert now", "hi-IN")
    body = json.loads(seen["body"])
    assert body["model"] == "bulbul:v4-flash" and body["speaker"] == "simran_en_customer" and body["language_code"] == "hi-IN"
    assert client.calls[-1]["model"] == "bulbul:v4-flash"  # the paid-call log names the id actually used


def test_the_default_text_to_speech_sends_no_speaker() -> None:
    client, seen = recording_client({"audios": ["QUJD"]})
    client.tts("Divert now", "hi-IN")
    assert "speaker" not in json.loads(seen["body"]) and json.loads(seen["body"])["model"] == TTS_MODEL


def test_speech_to_text_sends_the_configured_model_and_mode() -> None:
    client, seen = recording_client({"transcript": "ok", "language_code": "hi-IN"},
                                    VoiceModels(stt_model="saaras:v4", stt_mode="codemix"))
    client.stt_full(b"audio", "a.wav", "audio/wav", "unknown")
    assert b"saaras:v4" in seen["body"] and b"codemix" in seen["body"] and seen["path"] == "/speech-to-text"
    assert client.calls[-1]["model"] == "saaras:v4"


def test_a_mode_can_still_be_chosen_per_call() -> None:
    client, seen = recording_client({"transcript": "ok", "language_code": "en-IN"})
    client.stt_full(b"audio", "a.wav", "audio/wav", "unknown", "translate")
    assert b"translate" in seen["body"]


@pytest.mark.parametrize(("reasoning", "present", "value"), [("low", True, "low"), ("high", True, "high"),
                                                              ("none", True, None), ("omit", False, None)])
def test_chat_reasoning_effort_is_low_high_off_or_left_out(reasoning: str, present: bool, value: str | None) -> None:
    client, seen = recording_client({"choices": [{"message": {"content": "{\"a\": 1}"}}]},
                                    VoiceModels(chat_model="sarvam-105b", chat_reasoning=reasoning))
    assert client.chat_json("s", "u", {"type": "object"}) == {"a": 1}
    body = json.loads(seen["body"])
    assert body["model"] == "sarvam-105b" and ("reasoning_effort" in body) is present
    if present:
        assert body["reasoning_effort"] == value  # "none" is sent as null: the docs' way to switch thinking off


def test_a_chat_call_can_carry_its_own_short_timeout() -> None:
    client, seen = recording_client({"choices": [{"message": {"content": "{}"}}]})
    client.chat_json("s", "u", {"type": "object"}, timeout=4.0)
    assert seen["timeout"]["read"] == 4.0  # the fallback must never sit in the main path for 22 s again


# --- the key ----------------------------------------------------------------------------------------------------------------


def test_the_key_comes_from_the_environment_first_then_dotenv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("verity.voice.load_env", lambda path=None: {"SARVAM_API_KEY": "from-dotenv"})
    monkeypatch.delenv("SARVAM_API_KEY", raising=False)
    assert SarvamClient.from_env().api_key == "from-dotenv"
    monkeypatch.setenv("SARVAM_API_KEY", "from-environment")
    assert SarvamClient.from_env().api_key == "from-environment"  # a hosted deploy sets real environment variables


def test_an_explicit_env_file_is_read_alone_so_tests_are_hermetic(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SARVAM_API_KEY", "from-environment")
    env = tmp_path / ".env"
    env.write_text("SARVAM_API_KEY=from-file\nSARVAM_STT_MODEL=saaras:v4\n", encoding="utf-8")
    client = SarvamClient.from_env(env)
    assert client.api_key == "from-file" and client.models.stt_model == "saaras:v4"
    env.write_text("SARVAM_API_KEY=\n", encoding="utf-8")
    assert SarvamClient.from_env(env) is None  # the environment variable does not leak into an explicit file's answer


def test_copying_env_example_to_env_changes_nothing() -> None:
    # The .env reader takes everything after "=" as the value, so a comment after a value would become a model id.
    values = load_env(REPO_ROOT / ".env.example")
    assert {"SARVAM_API_KEY", "SARVAM_STT_MODEL", "SARVAM_TTS_MODEL", "VERITY_CORS_ORIGINS"} <= set(values)
    assert all(value == "" for value in values.values()), {k: v for k, v in values.items() if v}


def test_models_in_the_environment_reach_the_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("verity.voice.load_env", lambda path=None: {})
    monkeypatch.setenv("SARVAM_API_KEY", KEY)
    monkeypatch.setenv("SARVAM_TTS_MODEL", "bulbul:v4-flash")
    assert SarvamClient.from_env().models.tts_model == "bulbul:v4-flash"
