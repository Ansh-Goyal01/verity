"""The spoken ride-check, minus the network: from a transcript to the ride's slots, the read-back, and the answer (W3.2).

1. ``parse_ride_request``: rules only. Places by gazetteer (``places.py``) FIRST, then the battery read from what is
   left, so "Sector 18" can never be taken for a battery of 18.
2. ``complete_with_llm``: only when the rules missed something, with a strict schema and a 4 s limit (the repo's own
   smoke test recorded 22.9 s for one ``sarvam-105b`` call). The model only FILLS GAPS: it cannot override a value the
   rules found, cannot name a place the gazetteer lacks, and never sees the decision or any engine number.
3. ``read_back`` / ``clarify``: the sentence the driver confirms ("45 percent battery, Sector 18 to IGI Airport
   Terminal 3. Is that right?"). Nothing runs on an unconfirmed parse.
4. ``spoken_ride_answer``: the decision as a FIXED template filled with the engine's numbers (confidence as
   ``confidence_display`` says it, minutes, rupees, the after-drop charger). The language model never writes a number.

Hindi sentences were written for this project and are not yet checked by a native speaker (Ansh to verify).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, replace
from typing import Protocol

from .places import Gazetteer, Place, assign_roles, find_mentions, mask, normalise
from .voice import VoiceUnavailable
from .voice_loop import soc_from_transcript, spoken_confidence

log = logging.getLogger(__name__)
LLM_TIMEOUT_S = 4.0
MAX_SPOKEN_NAME_CHARS = 40
_DEVANAGARI = re.compile(r"[ऀ-ॿ]")
_NUMBER_WITH_UNIT = re.compile(r"%|percent|per cent|pratishat|प्रतिशत|फीसदी|फ़ीसदी", re.IGNORECASE)

LLM_SYSTEM = ("You read what an electric cab driver in Delhi-NCR said about a ride request, in any Indian language or a "
              "mix with English. Return only JSON. soc_pct: the battery percentage as a whole number from 0 to 100, or "
              "null if it was not said. pickup and drop: the place names exactly as spoken (English letters if you can), "
              "or null. Extract only what was said; do not add anything.")
LLM_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["soc_pct", "pickup", "drop"],
              "properties": {"soc_pct": {"type": ["integer", "null"], "minimum": 0, "maximum": 100},
                             "pickup": {"type": ["string", "null"]}, "drop": {"type": ["string", "null"]}}}


class ChatJson(Protocol):
    def chat_json(self, system: str, user: str, schema: dict, name: str = ..., timeout: float | None = ...) -> dict: ...


@dataclass(frozen=True)
class RideSlots:
    soc_pct: int | None
    pickup: Place | None
    drop: Place | None
    parsed_by: str = "rules"  # "rules" | "llm" | "rules+llm"
    unresolved: tuple[str, ...] = ()  # place names the model heard that the gazetteer does not know
    roles_how: str = "none"  # how pickup/drop were told apart (places.assign_roles)

    @property
    def missing(self) -> list[str]:
        return [name for name, value in (("soc_pct", self.soc_pct), ("pickup", self.pickup), ("drop", self.drop))
                if value is None]


def parse_ride_request(transcript: str, gazetteer: Gazetteer, language_code: str | None = None) -> RideSlots:
    norm = normalise(transcript)
    mentions = find_mentions(norm, gazetteer)
    pickup, drop, how = assign_roles(norm, mentions)
    soc, _ = soc_from_transcript(mask(norm, mentions), None, language_code)  # Hindi number WORDS are left to the model
    if pickup is not None and drop is not None and pickup.id == drop.id:
        drop = None  # a ride from a place to itself is not a ride: ask again (call 2 would refuse it anyway)
    return RideSlots(soc, pickup, drop, "rules", (), how)


def _whole_percent(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 100 else None


def _resolve(name: object, gazetteer: Gazetteer) -> tuple[Place | None, str | None]:
    if not isinstance(name, str) or not name.strip():
        return None, None
    mentions = find_mentions(normalise(name), gazetteer)
    if mentions:
        return mentions[0].place, None
    name = name.strip()
    # An unknown name is spoken back to the driver ("I do not know the place X yet"), so it must look like a place the
    # driver named: never long, never a number-with-unit the model composed itself (a model never writes a number).
    if len(name) > MAX_SPOKEN_NAME_CHARS or _NUMBER_WITH_UNIT.search(name):
        return None, None
    return None, name


def complete_with_llm(slots: RideSlots, transcript: str, client: ChatJson | None, gazetteer: Gazetteer,
                      timeout: float = LLM_TIMEOUT_S) -> RideSlots:
    """Fill the slots the rules missed. Never called when nothing is missing; a failure or a timeout leaves the rule
    result as it was (the driver is simply asked again)."""
    if client is None or not slots.missing:
        return slots
    try:
        data = client.chat_json(LLM_SYSTEM, transcript, LLM_SCHEMA, name="ride_slots", timeout=timeout)
    except (VoiceUnavailable, ValueError, KeyError, TypeError) as exc:
        log.warning("ride slots: the language-model fallback failed (%s); keeping the rule result", type(exc).__name__)
        return slots
    soc, pickup, drop, unresolved = slots.soc_pct, slots.pickup, slots.drop, list(slots.unresolved)
    if soc is None:
        soc = _whole_percent(data.get("soc_pct"))
    if pickup is None:
        pickup, unknown = _resolve(data.get("pickup"), gazetteer)
        unresolved += [unknown] if unknown else []
    if drop is None:
        drop, unknown = _resolve(data.get("drop"), gazetteer)
        unresolved += [unknown] if unknown else []
        if drop is not None and pickup is not None and drop.id == pickup.id:
            drop = None  # a ride from a place to itself is not a ride: ask again
    filled = (soc, pickup, drop) != (slots.soc_pct, slots.pickup, slots.drop)
    had_rules = any(v is not None for v in (slots.soc_pct, slots.pickup, slots.drop))
    return replace(slots, soc_pct=soc, pickup=pickup, drop=drop, unresolved=tuple(unresolved),
                   parsed_by=("rules+llm" if had_rules else "llm") if filled else slots.parsed_by)


def reply_language(language_code: str | None, transcript: str) -> str:
    """The language to answer in: the one detected when the demo speaks it, else English (S12 adds more languages)."""
    if language_code:
        return "hi-IN" if language_code.startswith("hi") else "en-IN"
    return "hi-IN" if _DEVANAGARI.search(transcript or "") else "en-IN"


# --- the sentences the driver hears -----------------------------------------------------------------------------------------

READ_BACK = {"en-IN": "{soc} percent battery, {pickup} to {drop}. Is that right?",
             "hi-IN": "बैटरी {soc} प्रतिशत, {pickup} से {drop}। सही है?"}
SLOT_WORDS = {"en-IN": {"soc_pct": "the battery percentage", "pickup": "the pickup place", "drop": "the drop place"},
              "hi-IN": {"soc_pct": "बैटरी प्रतिशत", "pickup": "पिकअप की जगह", "drop": "ड्रॉप की जगह"}}
AND = {"en-IN": "and", "hi-IN": "और"}
CLARIFY = {"en-IN": "I did not catch {what}. Please say it again.", "hi-IN": "मुझे {what} समझ नहीं आया। कृपया फिर से बोलें।"}
UNKNOWN_PLACE = {"en-IN": "I do not know the place {name} yet. ", "hi-IN": "मुझे जगह {name} अभी नहीं पता। "}


def read_back(slots: RideSlots, language: str) -> str:
    if slots.missing:
        raise ValueError(f"nothing to read back: missing {slots.missing}")
    return READ_BACK[language].format(soc=slots.soc_pct, pickup=slots.pickup.spoken[language],
                                      drop=slots.drop.spoken[language])


def clarify(slots: RideSlots, language: str) -> str:
    words = [SLOT_WORDS[language][name] for name in slots.missing]
    what = words[0] if len(words) == 1 else f"{', '.join(words[:-1])} {AND[language]} {words[-1]}"
    unknown = "".join(UNKNOWN_PLACE[language].format(name=name) for name in slots.unresolved)
    return unknown + CLARIFY[language].format(what=what)


RIDE_ANSWER = {
    "en-IN": {"GO": "Take the ride. The chance of completing it and reaching a working charger is {confidence}.",
              "GO_charge": " After the drop, charge at {charger}: about {minutes}, roughly {rupees}{rate}.",
              "NO_GO": "Do not take this ride. The chance of completing it and reaching a working charger is only {confidence}.",
              "INSUFFICIENT_EVIDENCE": "There is not enough evidence to promise this ride yet, so we will not guess.",
              "minutes": ("{n} minute", "{n} minutes"), "rupees": ("{n} rupee", "{n} rupees"),
              "example_rate": " at an example earning rate"},
    "hi-IN": {"GO": "राइड ले लीजिए। राइड पूरी करके चालू चार्जर तक पहुँचने की संभावना {confidence} है।",
              "GO_charge": " ड्रॉप के बाद {charger} पर चार्ज करें: लगभग {minutes}, करीब {rupees}{rate}।",
              "NO_GO": "यह राइड न लें। राइड पूरी करके चालू चार्जर तक पहुँचने की संभावना केवल {confidence} है।",
              "INSUFFICIENT_EVIDENCE": "इस राइड का वादा करने के लिए अभी पर्याप्त जानकारी नहीं है, इसलिए हम कोई अंदाज़ा नहीं लगा रहे।",
              "minutes": ("{n} मिनट", "{n} मिनट"), "rupees": ("{n} रुपये", "{n} रुपये"),
              "example_rate": " (उदाहरण कमाई दर पर)"},
}


def _count(forms: tuple[str, str], n: int) -> str:
    return forms[0 if n == 1 else 1].format(n=n)


def spoken_ride_answer(result: dict, language: str) -> str:
    """The ride-check decision as a fixed sentence in ``language`` (a ``POST /api/ride-check`` response in, text out).
    Empty when the engine did not give what the template needs: refuse rather than guess."""
    t, decision = RIDE_ANSWER[language], result.get("decision")
    if decision == "INSUFFICIENT_EVIDENCE":
        return t["INSUFFICIENT_EVIDENCE"]
    if decision not in ("GO", "NO_GO") or result.get("confidence") is None:
        return ""
    text = t[decision].format(confidence=spoken_confidence(result["confidence"], language))
    drop = result.get("after_drop") or {}
    charger = (drop.get("charger") or {}).get("name")
    minutes, rupees = drop.get("expected_lost_min"), drop.get("expected_lost_rs")
    if decision == "GO" and charger and minutes is not None and rupees is not None:
        rate = t["example_rate"] if (result.get("earning_rate") or {}).get("is_example") else ""
        text += t["GO_charge"].format(charger=charger, minutes=_count(t["minutes"], int(round(minutes))),
                                      rupees=_count(t["rupees"], int(round(rupees))), rate=rate)
    return text
