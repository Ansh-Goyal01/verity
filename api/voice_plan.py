"""The spoken trip planner (WP7): say a trip in Hindi, Hinglish or English, hear it read back, confirm, hear the answer.

Same two-call design as the spoken ride-check (``api/voice_ride.py``):

* Call 1, ``hear_plan_stage``: audio (one Sarvam STT call) or the app's own transcript -> slots by RULES
  (``verity.plan_voice``) -> the language model ONLY if a slot is missing (4 s limit; the rule result stands if it fails)
  -> places resolved through the WP1 geocoder (India only, biased to the phone) -> a read-back to confirm, or a question.
  It never plans.
* Call 2, ``decide_plan_stage``: the driver's yes -> ``EngineService.plan`` (WP4, safest option) on the confirmed slots ->
  the release's own spoken sentence (a fixed template filled with engine numbers), spoken. A "no" plans nothing.

A place is asked back when two of the geocoder's matches for it are more than ``AMBIGUOUS_KM`` apart ("Did you mean
Aurangabad, Maharashtra or Aurangabad, Bihar?"); a car is asked back when several variants match. Never a guess.
"""

from __future__ import annotations

import logging
import time

import numpy as np

from verity.audio_cache import AudioCache
from verity.plan_voice import LLM_SCHEMA, LLM_SYSTEM, TripSlots, clarify, merge_llm, parse_trip, read_back, spoken_when
from verity.ride_voice import LLM_TIMEOUT_S, reply_language
from verity.trips import haversine_m
from verity.voice import SarvamClient, VoiceUnavailable

from .service import ApiError, EngineService
from .speech import try_synthesise

log = logging.getLogger(__name__)
AMBIGUOUS_KM = 50.0  # two matches of one spoken place this far apart are different places: ask which


def _ms(since: float) -> int:
    return int(round(1000 * (time.perf_counter() - since)))


def _km(a: dict, b: dict) -> float:
    return float(haversine_m(np.array([a["lat"]]), np.array([a["lon"]]), np.array([b["lat"]]),
                             np.array([b["lon"]]))[0]) / 1000.0


def resolve_place(svc: EngineService, phrase: str, near: tuple[float, float] | None) -> tuple[dict | None, tuple[str, ...]]:
    """(place {name, lat, lon}, the names to ask between when ambiguous). No result, or the geocoder down: (None, ())."""
    try:
        results = svc.geocode(phrase, 5, near)["results"]
    except ApiError as exc:
        log.warning("voice plan: place search failed for a spoken place (%s)", exc.code)
        return None, ()
    if not results:
        return None, ()
    # Only results that ARE a place of that name count ("Jaipur, Rajasthan"); "Jaipur Column" is another thing named
    # after it. Two such places far apart ("Aurangabad, Haryana" / "Aurangabad, Uttar Pradesh"): ask which.
    named = [r for r in results if r["name"].casefold() == phrase or r["name"].casefold().startswith(phrase + ",")]
    if len(named) >= 2 and _km(named[0], named[1]) > AMBIGUOUS_KM:
        return None, (named[0]["name"], named[1]["name"])
    first = results[0]
    return {"name": first["name"], "lat": first["lat"], "lon": first["lon"]}, ()


def _with_llm(slots: TripSlots, transcript: str, voice: SarvamClient | None, now, cars, hint: str | None,
              have_position: bool) -> tuple[TripSlots, int | None]:
    """The model fills ONLY what the rules missed; never called when nothing is missing."""
    if voice is None or not (slots.missing(have_position) or slots.car_options):
        return slots, None
    t = time.perf_counter()
    try:
        data = voice.chat_json(LLM_SYSTEM, transcript, LLM_SCHEMA, name="trip_slots", timeout=LLM_TIMEOUT_S)
    except (VoiceUnavailable, ValueError, KeyError, TypeError) as exc:
        log.warning("voice plan: the language-model fallback failed (%s); keeping the rule result", type(exc).__name__)
        return slots, _ms(t)
    return merge_llm(slots, data, now, cars, hint), _ms(t)


def _what_is_missing(slots: TripSlots, destination: dict | None, origin: dict | None, dest_asked: bool,
                     origin_asked: bool, have_position: bool) -> list[str]:
    """What to ask for. A place or car being asked as a choice ("Did you mean A or B?") is not also asked as missing."""
    places = [name for name, place, asked in (("destination", destination, dest_asked), ("origin", origin, origin_asked))
              if place is None and not asked]
    skip = {"destination", "origin"} | ({"car"} if slots.car_options else set())
    return places + [m for m in slots.missing(have_position) if m not in skip]


def hear_plan_stage(svc: EngineService, voice: SarvamClient | None, cache: AudioCache, *, transcript: str | None,
                    language: str, speak: bool, position: dict | None = None, vehicle_hint: str | None = None,
                    audio: bytes | None = None, filename: str = "", content_type: str = "") -> dict:
    """Blocking (STT, maybe the model, the geocoder, maybe TTS): run it in a worker thread."""
    started = time.perf_counter()
    hear_ms, language_code = None, (None if language == "unknown" else language)
    if transcript is None:
        if voice is None:
            raise VoiceUnavailable("no voice client")
        t = time.perf_counter()
        heard = voice.stt_full(audio, filename, content_type, language)  # raises VoiceUnavailable
        transcript, language_code, hear_ms = heard["transcript"], heard.get("language_code"), _ms(t)
    now, cars = svc.clock(), list(svc.catalog.values())
    have_position = position is not None
    t = time.perf_counter()
    slots = parse_trip(transcript, now, cars, language_code, vehicle_hint)
    parse_ms = _ms(t)
    slots, llm_ms = _with_llm(slots, transcript, voice, now, cars, vehicle_hint, have_position)

    near = (position["lat"], position["lon"]) if position else None
    t = time.perf_counter()
    destination, dest_choices = resolve_place(svc, slots.destination_phrase, near) if slots.destination_phrase else (
        None, ())
    if slots.origin_phrase:
        origin, origin_choices = resolve_place(svc, slots.origin_phrase, near)
    else:
        origin, origin_choices = ({"name": None, **position} if position else None), ()
    geocode_ms = _ms(t)

    place_choices = [c for c in (dest_choices, origin_choices) if c]
    choices = place_choices + ([slots.car_options] if slots.car_options else [])
    missing = _what_is_missing(slots, destination, origin, bool(dest_choices), bool(origin_choices), have_position)
    reply = reply_language(language_code, transcript)
    confirming = not missing and not choices
    car_name = (svc._vehicle_for({"vehicle_id": slots.vehicle_id, "passengers": 1, "ac_on": True}).vehicle["name"]
                if confirming else None)
    if confirming:
        when = spoken_when(slots.departure, slots.departure_said, now, reply)
        prompt = read_back(origin["name"], destination["name"], when, car_name, slots.soc_pct, slots.ac_on,
                           slots.passengers, reply)
    else:
        prompt = clarify(missing, choices, reply)
    clip, speak_ms = None, None
    if speak:
        t = time.perf_counter()
        clip = try_synthesise(prompt, reply, voice, cache)
        speak_ms = _ms(t)
    return {"stage": "confirm" if confirming else "clarify", "transcript": transcript, "language_code": language_code,
            "reply_language": reply, "needs_confirmation": confirming, "prompt": prompt, "prompt_audio": clip,
            "slots": {"destination": destination, "origin": origin, "vehicle_id": slots.vehicle_id,
                      "vehicle_name": car_name, "soc_pct": slots.soc_pct,
                      "departure_time": slots.departure.isoformat() if slots.departure else None,
                      "departure_said": slots.departure_said, "time_problem": slots.time_problem,
                      "ac_on": slots.ac_on, "ac_said": slots.ac_said, "passengers": slots.passengers,
                      "passengers_said": slots.passengers_said, "missing": missing,
                      "choices": [list(c) for c in choices], "parsed_by": slots.parsed_by},
            "timings_ms": {"hear": hear_ms, "parse": parse_ms, "llm": llm_ms, "geocode": geocode_ms, "decide": None,
                           "speak": speak_ms, "total": _ms(started)},
            "is_simulated": False}


def decide_plan_stage(svc: EngineService, voice: SarvamClient | None, cache: AudioCache, confirm: dict) -> dict:
    """Blocking (the Monte Carlo, then maybe one TTS call). ``confirm`` is a validated ``VoicePlanConfirm`` dump."""
    started = time.perf_counter()
    timings = {"hear": None, "parse": None, "llm": None, "geocode": None, "decide": None, "speak": None}
    if not confirm["confirmed"]:
        return {"stage": "not_confirmed", "reply_language": confirm["language"], "release": None, "speech": None,
                "audio": None, "timings_ms": {**timings, "total": _ms(started)}, "is_simulated": False}
    req = {"vehicle_id": confirm["vehicle_id"], "origin": confirm["origin"], "destination": confirm["destination"],
           "departure_time": confirm["departure_time"], "start_soc_pct": confirm["soc_pct"],
           "passengers": confirm["passengers"], "ac_on": confirm["ac_on"],
           "target_confidence": confirm["target_confidence"], "preferences": {"objectives": ["safest"]}}
    t = time.perf_counter()
    release = svc.plan(req)["options"][0]
    timings["decide"] = _ms(t)
    speech = release["speech"].get(confirm["language"]) or None  # none when the engine could not say it honestly
    clip = None
    if speech and confirm["speak"]:
        t = time.perf_counter()
        clip = try_synthesise(speech, confirm["language"], voice, cache)
        timings["speak"] = _ms(t)
    return {"stage": "decided", "reply_language": confirm["language"], "release": release, "speech": speech,
            "audio": clip, "timings_ms": {**timings, "total": _ms(started)}, "is_simulated": release["is_simulated"]}


DEMO_TRIP = "Kal subah 8 baje Jaipur jaana hai, Nexon 45 mein 70% hai, AC chalega"  # FINALE_BUILD WP7's demo sentence
DEMO_ORIGIN_QUERY = "Connaught Place"  # where the demo phone is; its coordinates come from the geocoder, never typed


def demo_trip_sentences(svc: EngineService) -> list[tuple[str, str, str]]:
    """(language, text, source) the demo trip speaks: its read-back and its answer, in both languages, made by the
    engine itself (rules + geocoder + plan; no paid call). The answer clip only matches while the engine's answer for
    tomorrow 08:00 is unchanged, and "kal" moves with the date, so rebuild the cache on the day of the demo."""
    here = svc.geocode(DEMO_ORIGIN_QUERY, 1)["results"][0]
    position, out = {"lat": here["lat"], "lon": here["lon"]}, []
    for language in ("hi-IN", "en-IN"):
        heard = hear_plan_stage(svc, None, AudioCache(), transcript=DEMO_TRIP, language=language, speak=False,
                                position=position)
        out.append((heard["reply_language"], heard["prompt"], "voice plan read-back (demo trip)"))
        if heard["stage"] != "confirm":
            continue
        s = heard["slots"]
        decided = decide_plan_stage(svc, None, AudioCache(), {
            "confirmed": True, "language": heard["reply_language"], "speak": False, "origin": s["origin"],
            "destination": s["destination"], "vehicle_id": s["vehicle_id"], "soc_pct": float(s["soc_pct"]),
            "departure_time": s["departure_time"], "ac_on": s["ac_on"], "passengers": s["passengers"],
            "target_confidence": 0.9})
        if decided["speech"]:
            out.append((decided["reply_language"], decided["speech"], "voice plan answer (demo trip)"))
    return out
