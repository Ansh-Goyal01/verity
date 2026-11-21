"""Rules that read a spoken TRIP request (WP7): where to, from where, battery %, which car, when, AC, how many people.

"Kal subah 8 baje Jaipur jaana hai, Nexon 45 mein 70% hai, AC chalega" -> destination phrase "jaipur", battery 70,
car tata-nexon-ev-45, tomorrow 08:00 IST, AC on. Pure functions, no network: place PHRASES are found here and resolved
to coordinates by the geocoder in ``api/voice_plan.py``.

Never guess: an unparsed time, a car that matches several variants, or no car at all is a question to the driver. Two
things have a stated default instead of a question, and the read-back says them aloud so the driver can correct them:
AC on and 1 person in the car (both make the plan more conservative). No time said at all means "now", read back too.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Protocol, Sequence

from .voice_loop import soc_from_transcript

IST = timezone(timedelta(hours=5, minutes=30))
MAX_DAYS_AHEAD = 4  # the weather forecast covers 5 days from today

_FILLER = frozenset(
    "kal aaj parso subah sabah dopahar shaam sham raat baje bje mujhe mujhko hume humein hamein hai hain ko main mein "
    "me mai i we want wanna need have go going to the a tomorrow today morning evening night afternoon pm am at car "
    "gaadi gadi gaddi chalo chalna jaana jana se tak ab abhi please hello ji bhai yaar reach drive travel visit get "
    "in on by with my battery percent ac and "
    "मुझे हमें कल आज परसों सुबह दोपहर शाम रात बजे है हैं को में से तक अभी".split())
_L = r"a-zऀ-ॿ"
_WORD = rf"[{_L}][{_L}'.-]*"
_MORE = rf"[{_L}0-9][{_L}0-9'.-]*"  # later words may be numbers ("Sector 18")
_PHRASE = rf"(?P<p>{_WORD}(?:\s+{_MORE}){{0,3}})"
_TO_EN = re.compile(rf"\b(?:to|towards|till|until)\s+{_PHRASE}")
_FROM_EN = re.compile(rf"\bfrom\s+{_PHRASE}")
_TO_HI = re.compile(rf"{_PHRASE}\s+(?:jaana|jana|jaanaa|jaenge|jayenge|jaunga|jaungi|tak|जाना|जाएंगे|जाऊंगा|तक)(?![{_L}])")
_FROM_HI = re.compile(rf"{_PHRASE}\s+(?:se|से)(?![{_L}])")

_DAY_WORDS = {"parso": 2, "परसों": 2, "kal": 1, "कल": 1, "tomorrow": 1, "aaj": 0, "आज": 0, "today": 0}
_PERIOD = {"subah": "am", "sabah": "am", "morning": "am", "सुबह": "am", "dopahar": "noon", "afternoon": "noon",
           "दोपहर": "noon", "shaam": "pm", "sham": "pm", "evening": "pm", "शाम": "pm", "raat": "night", "night": "night",
           "रात": "night"}
_NOW = re.compile(rf"(?<![{_L}])(?:abhi|now|अभी)(?![{_L}])")
_HOUR = re.compile(r"\b(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(baje|bje|बजे|o'?\s?clock|a\.?m\.?|p\.?m\.?)(?![a-z])")
_TIME_HINT = re.compile(rf"(?<![{_L}])(?:baje|bje|बजे|o'?clock|am|pm|kal|aaj|parso|tomorrow|today|subah|shaam|sham|"
                        rf"raat|dopahar|morning|evening|night|सुबह|शाम|रात|कल|आज|परसों)(?![{_L}])")
_AC_OFF = re.compile(r"\b(?:ac|एसी)\s+(?:nahi|nahin|nhi|band|off|बंद|नहीं)|\b(?:without|no)\s+ac\b")
_AC_ON = re.compile(r"\b(?:ac|एसी)\s+(?:chalega|chalu|on|chalayenge|चलेगा|चालू)|\bwith\s+ac\b")
_PEOPLE = re.compile(r"\b(\d)\s*(?:log|logon|लोग|passengers?|people|persons?)(?![a-z])")
_ALONE = re.compile(rf"(?<![{_L}])(?:akela|akeli|alone|अकेला|अकेली)(?![{_L}])")
_NUMBER = re.compile(r"(?<![\d.])(\d{2,3}(?:\.\d+)?)(?![\d.])(?!\s*(?:%|percent|per cent|pratishat|प्रतिशत|baje|bje|बजे))")
_GENERIC_MODEL = frozenset({"ev", "electric", "e"})


class CarRow(Protocol):
    vehicle_id: str
    model: str
    battery_kwh: float

    @property
    def name(self) -> str: ...


@dataclass(frozen=True)
class TripSlots:
    destination_phrase: str | None
    origin_phrase: str | None  # None = the phone's position (when sent) or ask
    soc_pct: int | None
    vehicle_id: str | None
    car_options: tuple[str, ...] = ()  # several cars matched: their names, asked back
    departure: datetime | None = None
    departure_said: bool = False  # False = nothing about time was said: "now", read back aloud
    time_problem: str | None = None  # a time was said but not understood, or is in the past / too far ahead
    ac_on: bool = True
    ac_said: bool = False
    passengers: int = 1
    passengers_said: bool = False
    parsed_by: str = "rules"

    def missing(self, have_position: bool) -> list[str]:
        checks = (("destination", self.destination_phrase), ("origin", self.origin_phrase or have_position or None),
                  ("soc_pct", self.soc_pct), ("car", self.vehicle_id), ("departure", self.departure))
        return [name for name, value in checks if value is None]


def normalise(text: str) -> str:
    return " ".join((text or "").casefold().replace(",", " , ").replace("।", " , ").replace("?", " ").split())


def _clean_phrase(raw: str) -> str | None:
    words = raw.split()
    while words and (words[0] in _FILLER or words[0].isdigit()):
        words.pop(0)
    kept = []
    for w in words:  # the place ends at the first filler or time word; a number after a name stays ("sector 18")
        if w in _FILLER:
            break
        kept.append(w)
    phrase = " ".join(kept).strip(" .'-")
    return phrase or None


_AFTER_FROM = re.compile(r"\s(?:se|से|from)\s")


def _last_phrase(pattern: re.Pattern, text: str, after_from: bool = False) -> tuple[str | None, tuple[int, int] | None]:
    """The last match's cleaned phrase. ``after_from``: a destination is what follows the last "se" / "from" in it
    ("Sector 18 se Agra jaana" -> "agra")."""
    found: tuple[str, tuple[int, int]] | None = None
    for m in pattern.finditer(text):
        raw = _AFTER_FROM.split(f" {m.group('p')} ")[-1] if after_from else m.group("p")
        phrase = _clean_phrase(raw)
        if phrase:
            found = (phrase, m.span("p"))
    return found if found else (None, None)


def place_phrases(text: str) -> tuple[str | None, str | None, list[tuple[int, int]]]:
    """(destination phrase, origin phrase, their spans). English "from X to Y" / "to X"; Hinglish "X se Y jaana" /
    "X jaana hai" / "X tak"; Devanagari "X से", "X जाना"."""
    dest, d_span = _last_phrase(_TO_EN, text)
    if dest is None:
        dest, d_span = _last_phrase(_TO_HI, text, after_from=True)
    origin, o_span = _last_phrase(_FROM_EN, text)
    if origin is None:
        origin, o_span = _last_phrase(_FROM_HI, text)
    if dest is not None and origin is not None and (origin == dest or origin.endswith(" " + dest)):
        origin, o_span = None, None  # the "se" phrase swallowed the destination
    return dest, origin, [s for s in (d_span, o_span) if s]


def _has(words: dict, text: str):
    return next((v for w, v in words.items() if re.search(rf"(?<![{_L}]){w}(?![{_L}])", text)), None)


def parse_departure(text: str, now: datetime) -> tuple[datetime | None, bool, str | None]:
    """(departure in IST, whether a time was said, the problem if it could not be used). Supported: "abhi" / "now";
    a day word (aaj, kal, parso, today, tomorrow) with an hour ("8 baje", "6 pm", "at 7 o'clock") and a period word
    (subah, dopahar, shaam, raat, morning, evening, night) or am/pm. An hour with no period and no am/pm is ambiguous."""
    now = now.astimezone(IST)
    m = _HOUR.search(text)
    if _NOW.search(text) and m is None:
        return now.replace(second=0, microsecond=0), True, None
    if not _TIME_HINT.search(text) and m is None:
        return now.replace(second=0, microsecond=0), False, None
    if m is None:
        return None, True, "no hour"
    days, period = _has(_DAY_WORDS, text) or 0, _has(_PERIOD, text)
    hour, minute, unit = int(m.group(1)), int(m.group(2) or 0), re.sub(r"[.\s]", "", m.group(3))
    if unit in ("am", "pm"):
        period = unit
    if hour > 23 or minute > 59:
        return None, True, "not a time"
    if hour <= 12:
        if period is None or (period == "night" and hour < 6):
            return None, True, "am or pm"
        if period == "am":
            hour = 0 if hour == 12 else hour
        elif period in ("pm", "night") or (period == "noon" and hour < 11):
            hour = hour if hour == 12 else hour + 12
    when = (now + timedelta(days=days)).replace(hour=hour, minute=minute, second=0, microsecond=0)
    if when < now - timedelta(minutes=5):
        return None, True, "in the past"
    if when > now + timedelta(days=MAX_DAYS_AHEAD):
        return None, True, "too far ahead"
    return when, True, None


def match_car(text: str, cars: Sequence[CarRow], hint: str | None = None) -> tuple[str | None, tuple[str, ...]]:
    """(vehicle_id, names when several match). The car is named by its model words ("Nexon", "Nexon EV Max", "XEV 9E");
    several variants are told apart by a spoken battery size ("Nexon 45") or by the app's own car (``hint``)."""
    words = set(re.findall(r"[a-z0-9]+", text))
    scored = []
    for car in cars:
        key = {w for w in re.findall(r"[a-z0-9]+", car.model.casefold()) if w not in _GENERIC_MODEL}
        if key and key <= words:
            scored.append((len(key), car))
    if not scored:
        return (hint, ()) if hint else (None, ())
    best = max(n for n, _ in scored)
    options = [c for n, c in scored if n == best]
    if len(options) == 1:
        return options[0].vehicle_id, ()
    sizes = {float(x) for x in _NUMBER.findall(text)}
    by_size = [c for c in options if float(c.battery_kwh) in sizes]
    if len(by_size) == 1:
        return by_size[0].vehicle_id, ()
    by_hint = [c for c in options if c.vehicle_id == hint]
    if by_hint:
        return by_hint[0].vehicle_id, ()
    return None, tuple(c.name for c in options)


def _mask(text: str, spans: Sequence[tuple[int, int]]) -> str:
    chars = list(text)
    for a, b in spans:
        chars[a:b] = " " * (b - a)
    return "".join(chars)


def parse_trip(transcript: str, now: datetime, cars: Sequence[CarRow], language_code: str | None = None,
               vehicle_hint: str | None = None) -> TripSlots:
    text = normalise(transcript)
    dest, origin, spans = place_phrases(text)
    hour_spans = [m.span() for m in _HOUR.finditer(text)]
    soc, _ = soc_from_transcript(_mask(text, [*spans, *hour_spans]), None, language_code)
    vehicle_id, options = match_car(text, cars, vehicle_hint)
    departure, said, problem = parse_departure(text, now)
    ac_off, ac_on = bool(_AC_OFF.search(text)), bool(_AC_ON.search(text))
    people = _PEOPLE.search(text)
    n = int(people.group(1)) if people else (1 if _ALONE.search(text) else None)
    ok_n = n is not None and 1 <= n <= 8
    return TripSlots(dest, origin, soc, vehicle_id, options, departure, said, problem, ac_on=not ac_off,
                     ac_said=ac_off or ac_on, passengers=n if ok_n else 1, passengers_said=ok_n)


def merge_llm(slots: TripSlots, data: dict, now: datetime, cars: Sequence[CarRow], vehicle_hint: str | None) -> TripSlots:
    """Fill ONLY the slots the rules left empty, from the model's strings, through the same rules (the model's battery %
    must be one whole number 0-100, and the driver confirms everything before anything is planned)."""
    out = slots
    for slot, key in (("destination_phrase", "destination"), ("origin_phrase", "origin")):
        if getattr(out, slot) is None and isinstance(data.get(key), str):
            out = replace(out, **{slot: _clean_phrase(normalise(data[key]))})
    soc = data.get("soc_pct")
    if out.soc_pct is None and isinstance(soc, int) and not isinstance(soc, bool) and 0 <= soc <= 100:
        out = replace(out, soc_pct=soc)
    if out.vehicle_id is None and not out.car_options and isinstance(data.get("car"), str):
        vid, options = match_car(normalise(data["car"]), cars, vehicle_hint)
        out = replace(out, vehicle_id=vid, car_options=options)
    if out.departure is None and isinstance(data.get("departure"), str):
        when, said, _ = parse_departure(normalise(data["departure"]), now)
        if when is not None:
            out = replace(out, departure=when, departure_said=said, time_problem=None)
    return replace(out, parsed_by="rules+llm") if out != slots else out


LLM_SYSTEM = ("You read what an electric-car driver in India said about a trip they want to make, in any Indian language "
              "or a mix with English. Return only JSON with the words as spoken (English letters if you can), or null "
              "when not said: destination, origin, car (make and model words), departure (the time words, e.g. "
              "'tomorrow 8 am'), soc_pct (the battery percentage as a whole number 0-100, or null). Extract only what "
              "was said; do not add anything.")
LLM_SCHEMA = {"type": "object", "additionalProperties": False,
              "required": ["destination", "origin", "car", "departure", "soc_pct"],
              "properties": {"destination": {"type": ["string", "null"]}, "origin": {"type": ["string", "null"]},
                             "car": {"type": ["string", "null"]}, "departure": {"type": ["string", "null"]},
                             "soc_pct": {"type": ["integer", "null"], "minimum": 0, "maximum": 100}}}


# --- the sentences the driver hears (the Hindi must be checked by a native speaker before the demo) -----------------

def spoken_when(when: datetime, said: bool, now: datetime, language: str) -> str:
    if not said:
        return "leaving now" if language == "en-IN" else "अभी निकलना"
    days = (when.astimezone(IST).date() - now.astimezone(IST).date()).days
    clock = when.astimezone(IST).strftime("%H:%M")
    if language == "en-IN":
        day = {0: "today", 1: "tomorrow", 2: "the day after tomorrow"}.get(days, when.strftime("%d %b"))
        return f"{day} at {clock}"
    day = {0: "आज", 1: "कल", 2: "परसों"}.get(days, when.strftime("%d-%m"))
    return f"{day} {clock} बजे"


READ_BACK = {
    "en-IN": "{origin} to {destination}, {when}, {car}, battery {soc} percent, AC {ac}, {people}. Is that right?",
    "hi-IN": "{origin} से {destination}, {when}, {car}, बैटरी {soc} प्रतिशत, AC {ac}, {people}। सही है?",
}
HERE = {"en-IN": "From your location", "hi-IN": "आपकी जगह"}
AC_WORD = {"en-IN": {True: "on", False: "off"}, "hi-IN": {True: "चालू", False: "बंद"}}
SLOT_WORDS = {"en-IN": {"destination": "where you are going", "origin": "where you are starting from",
                        "soc_pct": "the battery percentage", "car": "which car", "departure": "when you are leaving"},
              "hi-IN": {"destination": "आपको कहाँ जाना है", "origin": "आप कहाँ से निकलेंगे", "soc_pct": "बैटरी प्रतिशत",
                        "car": "कौन सी गाड़ी", "departure": "आप कब निकलेंगे"}}
CLARIFY = {"en-IN": "I did not catch {what}. Please say it again.", "hi-IN": "मुझे {what} समझ नहीं आया। कृपया फिर से बोलें।"}
WHICH = {"en-IN": "Did you mean {options}?", "hi-IN": "क्या आपका मतलब {options} है?"}
OR = {"en-IN": " or ", "hi-IN": " या "}
AND = {"en-IN": " and ", "hi-IN": " और "}


def people_words(n: int, language: str) -> str:
    if language == "en-IN":
        return "1 person" if n == 1 else f"{n} people"
    return "1 व्यक्ति" if n == 1 else f"{n} लोग"


def read_back(origin: str | None, destination: str, when: str, car: str, soc: int, ac_on: bool, people: int,
              language: str) -> str:
    return READ_BACK[language].format(origin=origin or HERE[language], destination=destination, when=when, car=car,
                                      soc=soc, ac=AC_WORD[language][ac_on], people=people_words(people, language))


def clarify(missing: Sequence[str], choices: Sequence[tuple[str, ...]], language: str) -> str:
    """Ask about what is missing; when one thing matched several (places, cars), ask which one."""
    parts = [WHICH[language].format(options=OR[language].join(c)) for c in choices if c]
    asked = [SLOT_WORDS[language][m] for m in missing if m in SLOT_WORDS[language]]
    if asked:
        parts.append(CLARIFY[language].format(what=AND[language].join(asked)))
    return " ".join(parts)
