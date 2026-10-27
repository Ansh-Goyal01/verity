"""Voice: Sarvam speech-to-text / text-to-speech client, battery-% parsing, and a multipart body parser.

STATUS: built and tested against a MOCK transport only. Nothing here has been run against the real
Sarvam API (no key tonight, and the plan forbids calling it). The endpoint paths, header name, model ids
and payload fields below follow CLAUDE.md (Saaras v3 STT, Bulbul v3 TTS) plus my recollection of Sarvam's
REST API and are UNVERIFIED: check them against the current Sarvam documentation before the first real call.
The API key is read from SARVAM_API_KEY (.env), sent only in a request header, and never logged.
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from email import policy
from email.parser import BytesParser
from pathlib import Path

import httpx

from .trust.graph import load_env

SARVAM_BASE = "https://api.sarvam.ai"
STT_PATH = "/speech-to-text"  # UNVERIFIED
TTS_PATH = "/text-to-speech"  # UNVERIFIED
KEY_HEADER = "api-subscription-key"  # UNVERIFIED
STT_MODEL = "saaras:v3"  # per CLAUDE.md; UNVERIFIED id
TTS_MODEL = "bulbul:v3"  # per CLAUDE.md; UNVERIFIED id
TIMEOUT_S = 30.0


class VoiceUnavailable(RuntimeError):
    """No Sarvam key configured, or the service could not be reached."""


@dataclass
class SarvamClient:
    api_key: str
    client: httpx.Client

    @classmethod
    def from_env(cls, env_path: Path | None = None, client: httpx.Client | None = None) -> SarvamClient | None:
        key = load_env(env_path).get("SARVAM_API_KEY", "")
        if not key:
            return None
        return cls(key, client or httpx.Client(base_url=SARVAM_BASE, timeout=TIMEOUT_S))

    def _post(self, path: str, **kwargs) -> dict:
        try:
            resp = self.client.post(path, headers={KEY_HEADER: self.api_key}, **kwargs)
            resp.raise_for_status()
            return resp.json()
        except (httpx.HTTPError, ValueError) as exc:  # the message never contains the key (it is only in a header)
            raise VoiceUnavailable(f"voice service error: {type(exc).__name__}") from exc

    def tts(self, text: str, language: str) -> dict:
        """Text -> ``{"audio_base64": ..., "format": "wav"}``."""
        data = self._post(TTS_PATH, json={"text": text, "target_language_code": language, "model": TTS_MODEL})
        audio = (data.get("audios") or [None])[0]
        if not audio:
            raise VoiceUnavailable("voice service returned no audio")
        return {"audio_base64": audio, "format": "wav"}

    def stt(self, audio: bytes, filename: str, content_type: str, language: str) -> str:
        data = self._post(STT_PATH, files={"file": (filename, audio, content_type)},
                          data={"language_code": language, "model": STT_MODEL})
        return str(data.get("transcript", ""))


# --- battery percentage from a transcript -------------------------------------------------------------------

_DEVANAGARI = str.maketrans("०१२३४५६७८९", "0123456789")
_ONES = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
    "seventeen eighteen nineteen".split())}
_TENS = {w: 10 * i for i, w in enumerate("_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()) if w != "_"}
_PERCENT = r"(?:%|percent|per cent|प्रतिशत|फीसदी|फ़ीसदी)"


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
