"""The venue voice kit's one endpoint (guide W3.5): validate a consented utterance, hear it, score it, store the sample.

Kept out of ``main.py`` so the route stays thin. Off unless the operator sets ``VERITY_LAB_MODE=1`` (anyone could otherwise add
rows to the dataset the results are computed from). Stores a transcript and scores, never the audio, a name or an address.
"""

from __future__ import annotations

import hmac
import os
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from verity.names import decide
from verity.paths import DATA_DIR, writable_dir
from verity.report_voice import name_query
from verity.voice import SarvamClient, parse_soc_percent
from verity.voice_lab import SAMPLE_SCHEMA, SPOKEN_LANGUAGES, SampleStore, compare_configs

from .service import ApiError, EngineService

SPEAKER_ID = re.compile(r"[A-Za-z0-9_-]{1,16}")  # an anonymous label such as "s07"; a space or a full name does not fit
DEVICE = re.compile(r"[A-Za-z0-9 ._-]{1,40}")
LANGUAGE = re.compile(r"unknown|[a-z]{2,3}-[A-Z]{2}")  # fullmatch: only a code-shaped value reaches a paid call
TRUE = {"1", "true", "yes", "on"}
_stores: dict[Path, SampleStore] = {}


def lab_enabled() -> bool:
    """On only with ``VERITY_LAB_MODE=1`` AND a ``VERITY_LAB_TOKEN``: the token is what stops anyone who can reach the server from
    posting 30 samples as ten invented speakers (or from running up the paid calls)."""
    return os.environ.get("VERITY_LAB_MODE", "").strip().lower() in TRUE and bool(os.environ.get("VERITY_LAB_TOKEN", "").strip())


def token_ok(supplied: str | None) -> bool:
    expected = os.environ.get("VERITY_LAB_TOKEN", "").strip()
    return bool(expected) and hmac.compare_digest((supplied or "").encode("utf-8"), expected.encode("utf-8"))


def lab_store() -> SampleStore:
    """One store (one write lock) per file; ``VERITY_LAB_DIR`` moves it (default ``data/lab``, which is gitignored)."""
    path = writable_dir(Path(os.environ.get("VERITY_LAB_DIR") or DATA_DIR / "lab")) / "voice_samples.jsonl"
    return _stores.setdefault(path, SampleStore(path))


def _invalid(message: str) -> ApiError:
    return ApiError(422, "invalid_request", message)


def _expected_soc(raw: str | None) -> int | None:
    if raw is None or raw == "":
        return None
    if not (raw.isascii() and raw.isdigit()) or int(raw) > 100:  # isascii: "²".isdigit() is true and int("²") raises
        raise _invalid("expected_soc_pct must be a whole number from 0 to 100")
    return int(raw)


def validate(fields: dict[str, str], known_chargers: set[str]) -> dict:
    """The form's fields, checked. Raises ``ApiError`` before any paid call is made."""
    if fields.get("consent", "").strip().lower() not in TRUE:
        raise ApiError(422, "consent_required", "The speaker must agree to the recording being transcribed and scored "
                                                "(consent=true). Audio is not kept; the transcript and scores are.")
    speaker_id = fields.get("speaker_id", "")
    if not SPEAKER_ID.fullmatch(speaker_id):
        raise _invalid("speaker_id must be an anonymous label of 1-16 letters, digits, - or _ (for example s07), not a name")
    spoken = fields.get("spoken_language", "")
    if spoken not in SPOKEN_LANGUAGES:
        raise _invalid(f"spoken_language must be one of {', '.join(SPOKEN_LANGUAGES)}")
    device = fields.get("device", "")
    if not DEVICE.fullmatch(device):
        raise _invalid("device must be 1-40 letters, digits, spaces, . _ or -")
    language = fields.get("language", "unknown")
    if not LANGUAGE.fullmatch(language):
        raise _invalid("language must be 'unknown' or a code like hi-IN")
    expected_soc = _expected_soc(fields.get("expected_soc_pct"))
    expected_charger = fields.get("expected_charger_id") or None
    if expected_soc is None and expected_charger is None:
        raise ApiError(422, "nothing_to_score", "Give expected_soc_pct and/or expected_charger_id: the utterance is scored against it.")
    if expected_charger is not None and expected_charger not in known_chargers:
        raise ApiError(404, "unknown_charger", f"No charger {expected_charger!r}")
    return {"speaker_id": speaker_id, "spoken_language": spoken, "device": device, "language": language,
            "expected_soc_pct": expected_soc, "expected_charger_id": expected_charger,
            "compare": fields.get("compare", "").strip().lower() in TRUE}


def _match(svc: EngineService, transcript: str):
    return decide(svc.graph.call(lambda g: g.search_names(name_query(transcript))))


def _scorers(svc: EngineService, expected_soc: int | None, expected_charger: str | None):
    def battery(transcript: str) -> bool | None:
        return None if expected_soc is None else parse_soc_percent(transcript) == expected_soc

    def charger(transcript: str) -> bool | None:
        if expected_charger is None:
            return None
        found = _match(svc, transcript)
        return found.kind == "match" and found.hits[0].charger_id == expected_charger

    def combined(transcript: str) -> bool | None:  # both asked for: the replay must get both right
        scored = [r for r in (battery(transcript), charger(transcript)) if r is not None]
        return all(scored) if scored else None

    return battery, charger, combined


def capture_stage(svc: EngineService, voice: SarvamClient, form: dict, audio: bytes, filename: str, content_type: str) -> dict:
    """Blocking (one to five Sarvam calls): run it in a worker thread. Raises ``VoiceUnavailable`` if the first hearing fails."""
    battery, charger, combined = _scorers(svc, form["expected_soc_pct"], form["expected_charger_id"])
    started = time.perf_counter()
    heard = voice.stt_full(audio, filename, content_type, form["language"])
    stt_ms = int(round(1000 * (time.perf_counter() - started)))
    transcript = heard["transcript"]
    found = _match(svc, transcript) if form["expected_charger_id"] else None
    sample = {
        "schema": SAMPLE_SCHEMA,
        "sample_id": uuid.uuid4().hex,
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "speaker_id": form["speaker_id"],
        "spoken_language": form["spoken_language"],
        "device": form["device"],
        "language_detected": heard.get("language_code"),
        "stt_model": voice.models.stt_model,
        "stt_mode": voice.models.stt_mode,
        "stt_ms": stt_ms,
        "transcript": transcript,
        "expected_soc_pct": form["expected_soc_pct"],
        "parsed_soc_pct": parse_soc_percent(transcript),
        "battery_correct": battery(transcript),
        "expected_charger_id": form["expected_charger_id"],
        "matched_charger_id": found.hits[0].charger_id if found is not None and found.kind == "match" else None,
        "charger_kind": found.kind if found is not None else None,
        "charger_correct": charger(transcript),
        "comparisons": (compare_configs(voice, audio, filename, content_type, form["language"], combined)
                        if form["compare"] else []),
        "consent": True,
        "is_simulated": False,
    }
    lab_store().append(sample)
    return sample
