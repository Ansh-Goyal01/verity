"""Voice: Sarvam speech-to-text / text-to-speech / translate / chat client, battery-% parsing, multipart parser.

STATUS (F7, 2026-11-12): endpoint paths, the auth header, request fields and model ids below were checked against
the current docs at docs.sarvam.ai and exercised with a handful of logged live calls (docs/ENGINE_NOTES.md, Sarvam API notes).
STT: POST /speech-to-text, multipart (file, model, mode, language_code; 'unknown' = auto-detect), response
``transcript`` + ``language_code``; the synchronous endpoint is for clips under 30 s. TTS: POST /text-to-speech,
JSON (text, language_code, model, speaker), response ``audios`` (base64 WAV), max 2500 characters on bulbul:v3.
The API key is read from SARVAM_API_KEY (.env), sent only in request headers, and never logged.
"""

from __future__ import annotations

import base64
import json
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from email import policy
from email.parser import BytesParser
from pathlib import Path

import httpx

from .trust.graph import load_env

SARVAM_BASE = "https://api.sarvam.ai"
STT_PATH = "/speech-to-text"
TTS_PATH = "/text-to-speech"
KEY_HEADER = "api-subscription-key"
STT_MODEL = "saaras:v4"  # chosen by measurement (W3.1, results/voice_ride_check_latency.json): v4 writes digits where v3 writes number words
STT_MODES = ("transcribe", "translate", "verbatim", "translit", "codemix")
TTS_MODEL = "bulbul:v3"  # also bulbul:v2, bulbul:v4-flash; v3 default speaker "shubh"
TTS_LANGUAGES = ("bn-IN", "en-IN", "gu-IN", "hi-IN", "kn-IN", "ml-IN", "mr-IN", "od-IN", "pa-IN", "ta-IN", "te-IN")
# Chat completions and translation (verified 2026-11-12, F6).
CHAT_PATH = "/v1/chat/completions"  # Authorization: Bearer <key> (+ api-subscription-key)
CHAT_MODEL = "sarvam-105b"  # also listed: sarvam-105b-conversations; "10 Indic languages along with English"
TRANSLATE_PATH = "/translate"  # fields input, source_language_code ('auto' with mayura:v1), target_language_code
TRANSLATE_MODEL = "mayura:v1"  # max 1000 characters of input
TIMEOUT_S = 30.0
CHAT_TIMEOUT_S = 90.0  # a 30 s read timed out on the first real chat call (2026-11-12)
# reasoning_effort: the docs list none|low|medium|high|max, but the live API (2026-11-12) accepts only low|medium|high.


class VoiceUnavailable(RuntimeError):
    """No Sarvam key configured, or the service could not be reached."""


CHAT_REASONING = ("low", "medium", "high", "none", "omit")  # "none" sends null (thinking off); "omit" sends nothing


@dataclass(frozen=True)
class VoiceModels:
    """Which Sarvam models a client uses (W3.1). The defaults are the ids verified live here (F7, 2026-11-12); the vendor
    docs read on 2026-11-16 (docs/ENGINE_NOTES.md, Sarvam API notes) recommend newer ones (saaras:v4, bulbul:v4-flash), so each id is a
    setting and the paid-call log names the id every call actually used."""

    stt_model: str = STT_MODEL
    stt_mode: str = "transcribe"
    tts_model: str = TTS_MODEL
    tts_speaker: str | None = None  # None: the model's own default speaker (bulbul:v4-flash REJECTS v3's "shubh")
    chat_model: str = CHAT_MODEL
    chat_reasoning: str = "low"
    translate_model: str = TRANSLATE_MODEL


def models_from_settings(get: Callable[[str], str | None]) -> VoiceModels:
    """Models from SARVAM_STT_MODEL, SARVAM_STT_MODE, SARVAM_TTS_MODEL, SARVAM_TTS_SPEAKER, SARVAM_CHAT_MODEL,
    SARVAM_CHAT_REASONING. Blank means the default; a value the client cannot use fails here, naming the setting."""
    def value(name: str, default: str) -> str:
        return (get(name) or "").strip() or default

    mode, reasoning = value("SARVAM_STT_MODE", "transcribe"), value("SARVAM_CHAT_REASONING", "low")
    if mode not in STT_MODES:
        raise ValueError(f"SARVAM_STT_MODE must be one of {STT_MODES}, not {mode!r}")
    if reasoning not in CHAT_REASONING:
        raise ValueError(f"SARVAM_CHAT_REASONING must be one of {CHAT_REASONING}, not {reasoning!r}")
    return VoiceModels(stt_model=value("SARVAM_STT_MODEL", STT_MODEL), stt_mode=mode,
                       tts_model=value("SARVAM_TTS_MODEL", TTS_MODEL), tts_speaker=value("SARVAM_TTS_SPEAKER", "") or None,
                       chat_model=value("SARVAM_CHAT_MODEL", CHAT_MODEL), chat_reasoning=reasoning)


def _settings(env_path: Path | None = None) -> Callable[[str], str | None]:
    """Setting lookup. An explicit ``env_path`` is read ALONE (hermetic); otherwise a real environment variable wins
    over ``.env`` (as for the Neo4j settings), so a hosted deploy that only sets environment variables works."""
    file_values = load_env(env_path)
    if env_path is not None:
        return lambda name: file_values.get(name) or None
    return lambda name: os.environ.get(name) or file_values.get(name) or None


def _read_key(env_path: Path | None = None) -> str:
    return _settings(env_path)("SARVAM_API_KEY") or ""


def sarvam_key_present(env_path: Path | None = None) -> bool:
    """Is a key configured? The rule ``SarvamClient.from_env`` uses, without building an HTTP client or exposing the key."""
    return bool(_read_key(env_path))


@dataclass
class SarvamClient:
    api_key: str
    client: httpx.Client
    calls: list[dict] = field(default_factory=list)  # paid-call log: path, model, ok, ms (never text or key)
    models: VoiceModels = field(default_factory=VoiceModels)

    @classmethod
    def from_env(cls, env_path: Path | None = None, client: httpx.Client | None = None) -> SarvamClient | None:
        get = _settings(env_path)
        key = get("SARVAM_API_KEY")
        if not key:
            return None
        return cls(key, client or httpx.Client(base_url=SARVAM_BASE, timeout=TIMEOUT_S), models=models_from_settings(get))

    def _post(self, path: str, model: str = "", bearer: bool = False, **kwargs) -> dict:
        headers = {KEY_HEADER: self.api_key, **({"Authorization": f"Bearer {self.api_key}"} if bearer else {})}
        t0, status = time.perf_counter(), None
        try:
            resp = self.client.post(path, headers=headers, **kwargs)
            status = resp.status_code
            if 400 <= status < 500:  # the server's own message says which field is wrong; never echo the key
                self._log(path, model, False, status, t0)
                detail = resp.text[:300].replace(self.api_key, "***")
                raise VoiceUnavailable(f"voice service error: HTTP {status}: {detail}")
            resp.raise_for_status()
            data = resp.json()
            self._log(path, model, True, status, t0)
            return data
        except (httpx.HTTPError, ValueError) as exc:  # the message never contains the key (it is only in a header)
            self._log(path, model, False, status, t0)
            raise VoiceUnavailable(f"voice service error: {type(exc).__name__}"
                                   + (f" (HTTP {status})" if status else "")) from exc

    def _log(self, path: str, model: str, ok: bool, status: int | None, t0: float) -> None:
        self.calls.append({"path": path, "model": model, "ok": ok, "http_status": status,
                           "ms": int(round(1000 * (time.perf_counter() - t0)))})

    def chat_json(self, system: str, user: str, schema: dict, name: str = "labels", timeout: float | None = None) -> dict:
        """One chat completion with Structured Outputs (json_schema); returns the parsed JSON object. ``timeout`` is
        this call's read limit in seconds (default CHAT_TIMEOUT_S): the spoken ride-check allows its fallback 4 s."""
        body = {"model": self.models.chat_model, "temperature": 0.0, "max_tokens": 4096,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "response_format": {"type": "json_schema", "json_schema": {"name": name, "schema": schema}}}
        if self.models.chat_reasoning != "omit":  # "none" is JSON null: the docs' way to switch thinking off
            body["reasoning_effort"] = None if self.models.chat_reasoning == "none" else self.models.chat_reasoning
        data = self._post(CHAT_PATH, self.models.chat_model, bearer=True, json=body, timeout=timeout or CHAT_TIMEOUT_S)
        try:
            choice = data["choices"][0]
            content = choice["message"].get("content") or ""
        except (KeyError, IndexError, TypeError, AttributeError) as exc:
            raise VoiceUnavailable(f"chat response has no choices ({type(exc).__name__})") from exc
        try:
            return json.loads(content[content.find("{"): content.rfind("}") + 1])
        except ValueError as exc:  # sizes only: the content may echo review text
            reasoning = choice["message"].get("reasoning_content") or ""
            raise VoiceUnavailable(f"chat returned no JSON object (finish_reason={choice.get('finish_reason')}, "
                                   f"content_chars={len(content)}, reasoning_chars={len(reasoning)}, "
                                   f"usage={data.get('usage')})") from exc

    def translate(self, text: str, target: str = "en-IN", source: str = "auto") -> str:
        data = self._post(TRANSLATE_PATH, self.models.translate_model, json={
            "input": text[:1000], "source_language_code": source, "target_language_code": target,
            "model": self.models.translate_model})
        return str(data.get("translated_text", ""))

    def tts(self, text: str, language: str, speaker: str | None = None) -> dict:
        """Text -> ``{"audio_base64": ..., "format": "wav"}`` (the configured model, bulbul:v3 by default;
        ``language`` e.g. hi-IN / en-IN; ``speaker`` overrides the configured one)."""
        speaker = speaker or self.models.tts_speaker
        body = {"text": text, "language_code": language, "model": self.models.tts_model,
                **({"speaker": speaker} if speaker else {})}
        data = self._post(TTS_PATH, self.models.tts_model, json=body)
        audio = (data.get("audios") or [None])[0]
        if not audio:
            raise VoiceUnavailable("voice service returned no audio")
        return {"audio_base64": audio, "format": "wav"}

    def stt_full(self, audio: bytes, filename: str, content_type: str, language: str = "unknown",
                 mode: str | None = None) -> dict:
        """Audio -> ``{"transcript", "language_code"}`` with the configured model and mode (``mode`` overrides the
        mode for this call). ``language`` 'unknown' lets the model detect it. Clips must be under 30 s."""
        mode = mode or self.models.stt_mode
        if mode not in STT_MODES:
            raise ValueError(f"mode must be one of {STT_MODES}")
        data = self._post(STT_PATH, self.models.stt_model, files={"file": (filename, audio, content_type)},
                          data={"language_code": language, "model": self.models.stt_model, "mode": mode})
        return {"transcript": str(data.get("transcript", "")), "language_code": data.get("language_code")}

    def stt(self, audio: bytes, filename: str, content_type: str, language: str, mode: str | None = None) -> str:
        return self.stt_full(audio, filename, content_type, language, mode)["transcript"]


# --- battery percentage from a transcript -------------------------------------------------------------------

_DEVANAGARI = str.maketrans("०१२३४५६७८९", "0123456789")
_ONES = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
    "seventeen eighteen nineteen".split())}
_TENS = {w: 10 * i for i, w in enumerate("_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()) if w != "_"}
_PERCENT = r"(?:%|percent|per cent|pratishat|prateeshat|fisadi|feesadi|प्रतिशत|फीसदी|फ़ीसदी)"


def parse_soc_percent(text: str) -> int | None:
    """The battery percentage in a transcript (digits, Devanagari digits or English number words), else None."""
    t = text.translate(_DEVANAGARI).lower()
    m = re.search(rf"(\d{{1,3}})\s*{_PERCENT}", t) or re.search(r"\b(\d{1,3})\b", t)
    if m and 0 <= int(m.group(1)) <= 100:
        return int(m.group(1))
    words = re.findall(r"[a-z]+", t.replace("-", " "))
    if "hundred" in words:  # the only valid battery reading containing "hundred" is 100 (checked before "one")
        return 100
    for i, w in enumerate(words):
        if w in _TENS:
            ones = _ONES.get(words[i + 1], 0) if i + 1 < len(words) and 0 < _ONES.get(words[i + 1], 0) < 10 else 0
            return _TENS[w] + ones
        if w in _ONES:
            return _ONES[w]
    return None


# --- multipart/form-data without python-multipart --------------------------------------------------------------


def parse_multipart(content_type: str, body: bytes) -> tuple[dict[str, str], dict[str, tuple[str, str, bytes]]]:
    """(text fields, files as name -> (filename, content type, bytes)) using only the standard library."""
    msg = BytesParser(policy=policy.HTTP).parsebytes(b"Content-Type: " + content_type.encode("latin-1") + b"\r\n\r\n" + body)
    if not msg.is_multipart():
        raise ValueError("body is not multipart/form-data")
    fields: dict[str, str] = {}
    files: dict[str, tuple[str, str, bytes]] = {}
    for part in msg.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if not name:
            continue
        filename = part.get_filename()
        payload = part.get_payload(decode=True) or b""
        if filename is not None:
            files[name] = (filename, part.get_content_type(), payload)
        else:
            fields[name] = payload.decode("utf-8", errors="replace")
    return fields, files


__all__ = ["SarvamClient", "VoiceUnavailable", "parse_soc_percent", "parse_multipart", "base64"]
