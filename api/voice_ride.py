"""The two stages of the spoken ride-check (W3.2), kept out of ``main.py`` so the route stays thin.

Stage 1, ``hear_stage``: audio -> transcript (one Sarvam call) -> slots by rules -> the model ONLY if something is missing
(4 s limit) -> a read-back to confirm, or a question about what was not understood. It never runs the ride-check.
Stage 2, ``decide_stage``: the driver's yes -> the existing ``EngineService.ride_check`` on the confirmed slots -> the fixed
template sentence the engine already wrote, spoken. A "no" runs and speaks nothing.

Every stage reports its own milliseconds so the "3 seconds" goal is measured, never assumed (a stage a call did not run is
``null``). ``is_simulated`` is true on both: the vehicle, chargers and reports behind the decision are the simulated demo fleet.
"""

from __future__ import annotations

import time
from functools import lru_cache

from verity.audio_cache import AudioCache
from verity.places import Gazetteer, Place
from verity.ride_voice import RideSlots, clarify, complete_with_llm, parse_ride_request, read_back, reply_language
from verity.voice import SarvamClient

from .models import VoiceRideConfirm
from .service import DRIVER_DEMO, ApiError, EngineService
from .speech import try_synthesise


@lru_cache(maxsize=1)
def gazetteer() -> Gazetteer:
    return Gazetteer.from_file()


def _ms(since: float) -> int:
    return int(round(1000 * (time.perf_counter() - since)))


def _place_ref(place: Place | None) -> dict | None:
    return None if place is None else {"id": place.id, "name": place.name, "lat": place.lat, "lon": place.lon}


def _slots_out(slots: RideSlots) -> dict:
    return {"soc_pct": slots.soc_pct, "pickup": _place_ref(slots.pickup), "drop": _place_ref(slots.drop),
            "missing": slots.missing, "unresolved": list(slots.unresolved), "parsed_by": slots.parsed_by}


def hear_stage(voice: SarvamClient, cache: AudioCache, audio: bytes, filename: str, content_type: str, language: str,
               speak: bool) -> dict:
    """Blocking (three possible network calls): run it in a worker thread. Raises ``VoiceUnavailable`` if hearing fails."""
    started = time.perf_counter()
    t = time.perf_counter()
    heard = voice.stt_full(audio, filename, content_type, language)
    hear_ms = _ms(t)

    transcript, language_code = heard["transcript"], heard.get("language_code")
    gaz = gazetteer()
    t = time.perf_counter()
    slots = parse_ride_request(transcript, gaz, language_code)
    parse_ms = _ms(t)

    llm_ms = None
    if slots.missing:  # the model only fills gaps; the rule result stands if it is slow or fails
        t = time.perf_counter()
        slots = complete_with_llm(slots, transcript, voice, gaz)
        llm_ms = _ms(t)

    reply = reply_language(language_code, transcript)
    confirming = not slots.missing
    prompt = read_back(slots, reply) if confirming else clarify(slots, reply)

    speak_ms, clip = None, None
    if speak:
        t = time.perf_counter()
        clip = try_synthesise(prompt, reply, voice, cache)
        speak_ms = _ms(t)

    return {"stage": "confirm" if confirming else "clarify", "transcript": transcript, "language_code": language_code,
            "reply_language": reply, "slots": _slots_out(slots), "needs_confirmation": confirming, "prompt": prompt,
            "prompt_audio": clip,
            "timings_ms": {"hear": hear_ms, "parse": parse_ms, "llm": llm_ms, "decide": None, "speak": speak_ms,
                           "total": _ms(started)},
            "is_simulated": True}


def _confirmed_places(confirm: VoiceRideConfirm) -> tuple[Place, Place]:
    gaz = gazetteer()
    pickup, drop = gaz.by_id(confirm.pickup_id), gaz.by_id(confirm.drop_id)
    for role, place, place_id in (("pickup", pickup, confirm.pickup_id), ("drop", drop, confirm.drop_id)):
        if place is None:
            raise ApiError(422, "invalid_request", f"{role}_id: unknown place {place_id!r}")
    if pickup.id == drop.id:
        raise ApiError(422, "invalid_request", "pickup_id and drop_id are the same place; a ride needs two")
    return pickup, drop


def decide_stage(svc: EngineService, voice: SarvamClient | None, cache: AudioCache, confirm: VoiceRideConfirm) -> dict:
    """Blocking (the Monte Carlo, then maybe one TTS call): run it in a worker thread."""
    started = time.perf_counter()
    pickup, drop = _confirmed_places(confirm)  # an impossible confirmation is a 422 even if the driver said no
    timings = {"hear": None, "parse": None, "llm": None, "decide": None, "speak": None}
    if not confirm.confirmed:
        return {"stage": "not_confirmed", "reply_language": confirm.language, "ride_check": None, "speech": None,
                "audio": None, "timings_ms": {**timings, "total": _ms(started)}, "is_simulated": True}

    request = {**DRIVER_DEMO, "soc_pct": confirm.soc_pct,
               "vehicle_id": confirm.vehicle_id or DRIVER_DEMO["vehicle_id"],
               "current": confirm.current.model_dump() if confirm.current else DRIVER_DEMO["current"],
               "pickup": {"lat": pickup.lat, "lon": pickup.lon}, "drop": {"lat": drop.lat, "lon": drop.lon}}
    t = time.perf_counter()
    result = svc.ride_check(request)
    timings["decide"] = _ms(t)

    speech = result.get("speech", {}).get(confirm.language) or None  # none when the engine could not say it honestly
    clip = None
    if speech and confirm.speak:
        t = time.perf_counter()
        clip = try_synthesise(speech, confirm.language, voice, cache)
        timings["speak"] = _ms(t)
    return {"stage": "decided", "reply_language": confirm.language, "ride_check": result, "speech": speech, "audio": clip,
            "timings_ms": {**timings, "total": _ms(started)}, "is_simulated": True}
