"""The two stages of a spoken charger report (W3.3), kept out of ``main.py`` so the routes stay thin.

Stage 1, ``hear_report_stage``: audio (one Sarvam call) or an already-made transcript -> status by rule + charger through the
name index (``names.decide``: one match, or a short list, or nothing) -> a read-back to confirm, or a question. It writes nothing.
Stage 2, ``record_report_stage``: the driver's yes + consent -> the report goes into the graph through the same path as
``POST /api/reports`` -> the cached plans that use this charger are re-planned -> one sentence: the charger's trust before and
after, and whether the plan moved. A "no" records and speaks nothing but that.

The audio is never stored; the transcript is, as the report's note, with the driver's consent. A scripted demo note
(``from_real_person=false``) is stored as simulated under its own source.
"""

from __future__ import annotations

import logging
import time

from verity.audio_cache import AudioCache
from verity.report_voice import NOT_RECORDED, interpret_report, name_query, prompt_for, recorded_sentence
from verity.ride_voice import reply_language
from verity.voice import SarvamClient

from .models import VoiceReportConfirm
from .service import EngineService
from .speech import try_synthesise


log = logging.getLogger(__name__)


def _ms(since: float) -> int:
    return int(round(1000 * (time.perf_counter() - since)))


def _candidate(hit) -> dict:
    return {"charger_id": hit.charger_id, "name": hit.name, "score": round(hit.score, 2)}


def hear_report_stage(svc: EngineService, voice: SarvamClient | None, cache: AudioCache, *, transcript: str | None,
                      audio: bytes | None = None, filename: str = "", content_type: str = "", language: str = "unknown",
                      speak: bool = True) -> dict:
    """Blocking (possibly one STT call, one name search, one TTS call): run it in a worker thread.
    Raises ``VoiceUnavailable`` if hearing fails. Exactly one of ``transcript`` and ``audio`` is given."""
    started = time.perf_counter()
    timings: dict[str, int | None] = {"hear": None, "parse": None, "speak": None}
    language_code = None
    if audio is not None:
        t = time.perf_counter()
        heard = voice.stt_full(audio, filename, content_type, language)
        timings["hear"] = _ms(t)
        transcript, language_code = heard["transcript"], heard.get("language_code")
    transcript = transcript or ""

    t = time.perf_counter()
    hits = svc.graph.call(lambda g: g.search_names(name_query(transcript)))
    reading = interpret_report(transcript, hits)
    timings["parse"] = _ms(t)

    reply = reply_language(language_code or (None if language == "unknown" else language), transcript)
    prompt, confirming = prompt_for(reading, reply)
    clip = None
    if speak:
        t = time.perf_counter()
        clip = try_synthesise(prompt, reply, voice, cache)
        timings["speak"] = _ms(t)
    one = reading.charger.kind == "match"
    return {"stage": "confirm" if confirming else "clarify", "transcript": transcript, "language_code": language_code,
            "reply_language": reply, "status": reading.status,
            "charger": _candidate(reading.charger.hits[0]) if one else None,
            "candidates": [_candidate(h) for h in reading.charger.hits], "needs_confirmation": confirming,
            "prompt": prompt, "prompt_audio": clip, "timings_ms": {**timings, "total": _ms(started)},
            "is_simulated": False}


def record_report_stage(svc: EngineService, voice: SarvamClient | None, cache: AudioCache,
                        confirm: VoiceReportConfirm) -> dict:
    """Blocking (the graph write, up to two re-plans, maybe one TTS call): run it in a worker thread.
    Raises ``ApiError`` 422 without consent and 404 for an unknown charger (before anything is written)."""
    started = time.perf_counter()
    timings: dict[str, int | None] = {"write": None, "replan": None, "speak": None}
    scripted = not confirm.from_real_person
    if not confirm.confirmed:
        speech = NOT_RECORDED[confirm.language]
        return {"stage": "not_recorded", "reply_language": confirm.language, "report": None, "plans_checked": 0,
                "plans_pending": 0, "replan_failed": False, "plans": [], "speech": speech, "audio": _speak(confirm, speech, voice, cache, timings),
                "timings_ms": {**timings, "total": _ms(started)}, "is_simulated": scripted}

    note = f"voice: {confirm.transcript}" if confirm.transcript else "voice report"
    t = time.perf_counter()
    report = svc.add_report({"charger_id": confirm.charger_id, "status": confirm.status, "consent": confirm.consent,
                             "channel": "driver_voice", "note": note}, is_simulated=scripted)
    timings["write"] = _ms(t)
    t = time.perf_counter()
    plans, pending, failed = [], 0, False
    changed = report["trust_before"]["p_works"] != report["trust_after"]["p_works"]  # shown (5 %) trust, the same the driver hears
    try:  # the report is already written: a failed re-plan must not turn it into an error
        plans, pending = svc.replan_after_report(confirm.charger_id, trust_changed=changed)
    except Exception:  # noqa: BLE001 - logged with its traceback, reported to the driver as "could not re-check"
        log.exception("re-plan after a report on %s failed", confirm.charger_id)
        failed = True
    timings["replan"] = _ms(t)

    name = next((c.name for c in svc.chargers if c.id == confirm.charger_id), confirm.charger_id)
    speech = recorded_sentence(name, report["trust_before"], report["trust_after"], plans, confirm.language,
                               pending=pending, failed=failed)
    return {"stage": "recorded", "reply_language": confirm.language, "report": report, "plans_checked": len(plans),
            "plans_pending": pending, "replan_failed": failed, "plans": plans, "speech": speech, "audio": _speak(confirm, speech, voice, cache, timings),
            "timings_ms": {**timings, "total": _ms(started)}, "is_simulated": scripted}


def _speak(confirm: VoiceReportConfirm, text: str, voice: SarvamClient | None, cache: AudioCache,
           timings: dict[str, int | None]) -> dict | None:
    if not confirm.speak:
        return None
    t = time.perf_counter()
    clip = try_synthesise(text, confirm.language, voice, cache)
    timings["speak"] = _ms(t)
    return clip
