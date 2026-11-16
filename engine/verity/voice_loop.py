"""Driver voice loop (F7): battery % and spoken charger reports from a transcript, and spoken decisions.

Pure functions over transcripts (the Sarvam calls live in voice.SarvamClient):
* ``soc_from_transcript`` reads a battery % from English, Hindi or Hinglish text; Hindi number WORDS are not
  parsed locally, so when nothing is found in Devanagari text a translate function (Sarvam Translate) may be passed.
* ``voice_report`` turns a note like "Sector 62 wala charger band hai" into {charger match, status}. The charger is
  matched only when the spoken words cover most of ONE directory name (else: unmatched, with candidates). A report
  is ``is_simulated=False`` only when the caller says it came from a real person; synthetic speech (our own TTS
  round trip) is always simulated. source_kind is ``driver_voice``.
* ``render_decision`` speaks a decision from FIXED templates (hi-IN, en-IN); every number in it comes from the
  engine's output (confidence already rounded to 5 %), never from the template. Confidence is spoken as
  ``confidence_display`` shows it (W0.2): "95 percent or more", "under 5 percent", never certainty.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Sequence
from datetime import datetime

import numpy as np

from .dispatch import REASON_TEMPLATES_EN, ROLE_EN, render_reason, reports_en
from .forecast import confidence_display
from .schemas import ChargerRecord, Report
from .trips import haversine_m
from .voice import parse_soc_percent

log = logging.getLogger(__name__)
SOURCE_KIND = "driver_voice"
SPEECH_LANGUAGES = ("en-IN", "hi-IN")  # the languages the demo answers in (W0.5); both are in the audio cache
MIN_NAME_COVERAGE = 0.5  # share of a charger name's meaningful words that must be spoken (approximate - verify)
NEAR_RADIUS_KM = 2.0
_DEVANAGARI = re.compile(r"[ऀ-ॿ]")

# Status words, most specific first. Hinglish (Latin), Hindi (Devanagari) and English.
STATUS_WORDS: tuple[tuple[str, str], ...] = (
    ("no_power", r"bijli nahi|light nahi|power nahi|no power|power cut|बिजली नहीं|लाइट नहीं"),
    ("blocked", r"blocked|block hai|gaadi khadi|car parked|parked|रास्ता बंद|गाड़ी खड़ी"),
    ("occupied", r"\bline\b|queue|lambi line|\bwait|busy|occupied|लाइन|भीड़"),
    ("broken", r"\bband\b|kharab|nahi chal|kaam nahi|not working|broken|dead|faulty|बंद|खराब|नहीं चल|काम नहीं"),
    ("working", r"chal raha|chalu|theek|working|kaam kar raha|चल रहा|चालू|ठीक"),
)
# Place words that never identify a charger on their own (a match needs at least one other spoken name word).
GENERIC_PLACE = frozenset("sector noida delhi new gurugram gurgaon ghaziabad greater road marg phase block market "
                          "mall metro station nagar city near highway expressway toll plaza".split())
_SPOKEN_STOP = frozenset("ev charging charge charger chargers station point hub the and of at wala wale wali vala "
                         "hai tha ka ki ke par pe me mein main yahan wahan bhai sir please private ltd india "
                         "openstreetmap battery percent per cent pratishat bayalis is was the off on band kharab "
                         "closed broken working not".split())  # words every note contains never name a charger

DECISION_TEMPLATES: dict[str, dict[str, str]] = {
    "hi-IN": {
        "GO": "जाइए। {destination} तक पहुँचने की संभावना {confidence} है। "
              "बैटरी {bingo} प्रतिशत से नीचे जाए तो रास्ता बदलें।",
        "NO_GO": "अभी न जाएँ। {destination} तक पहुँचने की संभावना केवल {confidence} है।",
        "INSUFFICIENT_EVIDENCE": "पक्का कहने के लिए अभी पर्याप्त जानकारी नहीं है, इसलिए हम कोई वादा नहीं कर रहे।",
    },
    "en-IN": {
        "GO": "Go. The chance of reaching {destination} is {confidence}. "
              "Divert if the battery falls below {bingo} percent.",
        "NO_GO": "Do not start yet. The chance of reaching {destination} is only {confidence}.",
        "INSUFFICIENT_EVIDENCE": "There is not enough evidence to promise this trip yet, so we will not guess.",
    },
}
# How each language says confidence_display's three forms; {p} is the engine's rounded percent.
SPOKEN_CONFIDENCE: dict[str, dict[str, str]] = {
    "hi-IN": {"95%+": "95 प्रतिशत या उससे अधिक", "under 5%": "5 प्रतिशत से कम", "pct": "{p} प्रतिशत"},
    "en-IN": {"95%+": "95 percent or more", "under 5%": "under 5 percent", "pct": "{p} percent"},
}


# The dispatch reason (W0.6) spoken: en-IN reuses the response's own templates; Hindi written for this project
# (not yet checked by a native speaker - Ansh to verify before the demo).
REASON_TEMPLATES: dict[str, dict[str, str]] = {
    "en-IN": REASON_TEMPLATES_EN,
    "hi-IN": {
        "GO": "{role} {name} के काम करने की संभावना {p_works} है ({reports}), और वहाँ {p_reach} बार पहुँचा जा सकता है।",
        "GO_direct": "बिना चार्जिंग के, {confidence} बार रिज़र्व बैटरी के साथ पहुँचा जा सकता है।",
        "NO_GO": "सबसे अच्छी योजना {role} {name} पर निर्भर है ({p_works} काम करने की संभावना, {reports}), "
                 "फिर भी संभावना केवल {confidence} है (लक्ष्य {target})।",
        "NO_GO_direct": "कोई भी चार्जिंग योजना {target} के लक्ष्य तक नहीं पहुँचती; सबसे अच्छी {confidence} तक पहुँचती है।",
    },
}
ROLES = {"en-IN": ROLE_EN, "hi-IN": {"stop": "तय चार्जिंग स्टॉप", "backup": "बैकअप चार्जर"}}
REPORTS = {"en-IN": reports_en, "hi-IN": lambda n: "कोई रिपोर्ट नहीं" if n == 0 else f"{n} रिपोर्ट"}


def spoken_confidence(confidence: float, language: str) -> str:
    """``confidence_display`` in words for ``language`` (W0.2): the same three forms, never "100 percent"."""
    shown, forms = confidence_display(confidence), SPOKEN_CONFIDENCE[language]
    return forms[shown] if shown in forms else forms["pct"].format(p=shown.rstrip("%"))


def soc_from_transcript(text: str, translate: Callable[[str], str] | None = None,
                        language_code: str | None = None) -> tuple[int | None, str]:
    """(battery %, how it was read). Hindi number words (Devanagari, or romanized such as "bayalis" from STT
    translit mode) are not parsed locally: they are read through ``translate`` (to English) when given."""
    soc = parse_soc_percent(text)
    if soc is not None:
        return soc, "transcript"
    hindi = bool(_DEVANAGARI.search(text or "")) or (language_code or "").startswith("hi")
    if translate is not None and hindi:
        soc = parse_soc_percent(translate(text))
        if soc is not None:
            return soc, "translated"
    return None, "not found"


def parse_status(text: str) -> str | None:
    low = (text or "").lower()
    return next((status for status, pattern in STATUS_WORDS if re.search(pattern, low)), None)


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if w not in _SPOKEN_STOP}


def _pool(chargers: Sequence[ChargerRecord], near: tuple[float, float] | None, radius_km: float) -> list[ChargerRecord]:
    pool = list(chargers)
    if near is None or not pool:
        return pool
    d = haversine_m(np.full(len(pool), near[0]), np.full(len(pool), near[1]),
                    np.array([c.lat for c in pool]), np.array([c.lon for c in pool]))
    return [c for c, dist in zip(pool, d) if dist <= radius_km * 1000.0]


def match_spoken_charger(text: str, chargers: Sequence[ChargerRecord], near: tuple[float, float] | None = None,
                         radius_km: float = NEAR_RADIUS_KM) -> dict:
    """{charger_id | None, coverage, candidates, reason}. ``near`` (lat, lon) limits the search to radius_km."""
    spoken = _words(text)
    scored = []
    for c in _pool(chargers, near, radius_km):
        name = _words(c.name) | _words(c.operator)
        shared = name & spoken
        if name and shared - GENERIC_PLACE:  # "sector" alone (or "noida") never identifies a charger
            scored.append((len(shared) / len(name), c.id))
    scored.sort(key=lambda s: (-s[0], s[1]))
    top = [s for s in scored if s[0] >= MIN_NAME_COVERAGE]
    candidates = [{"charger_id": cid, "coverage": round(cov, 2)} for cov, cid in scored[:3] if cov > 0]
    if not top:
        return {"charger_id": None, "coverage": None, "candidates": candidates,
                "reason": "no directory charger name was spoken clearly enough"}
    if len(top) > 1 and top[0][0] == top[1][0]:
        return {"charger_id": None, "coverage": round(top[0][0], 2), "candidates": candidates,
                "reason": "several chargers match equally; ask the driver which one"}
    return {"charger_id": top[0][1], "coverage": round(top[0][0], 2), "candidates": candidates, "reason": ""}


def voice_report(transcript: str, chargers: Sequence[ChargerRecord], t: datetime, driver_id: str, *,
                 from_real_person: bool, near: tuple[float, float] | None = None,
                 translation: str = "") -> tuple[dict, Report | None]:
    """Structured record of a spoken charger note, plus a trust-engine Report when charger and status are known.
    ``translation`` (English, e.g. from Sarvam Translate) is also searched for the charger name: STT writes spoken
    numbers as Hindi words ("basath"), the translation as digits ("62")."""
    match = match_spoken_charger(f"{transcript} {translation}", chargers, near)
    status = parse_status(transcript)
    record = {"source_kind": SOURCE_KIND, "driver_id": driver_id, "t": t.isoformat(), "status": status,
              **match, "is_simulated": not from_real_person}
    if match["charger_id"] is None or status is None:
        return record, None
    return record, Report(charger_id=match["charger_id"], source_id=f"{SOURCE_KIND}:{driver_id}", status=status,
                          t=t, evidence="driver voice note", is_simulated=not from_real_person)


def spoken_reason(facts: dict, language: str) -> str:
    """The dispatch reason sentence (DispatchResult.reason_facts) in ``language``, numbers in spoken words."""
    return render_reason(facts, REASON_TEMPLATES[language], lambda p: spoken_confidence(p, language),
                         REPORTS[language], ROLES[language],
                         target_text=lambda t: SPOKEN_CONFIDENCE[language]["pct"].format(p=int(round(100 * t))))


def render_decision(decision: str, language: str, *, destination: str = "", confidence: float | None = None,
                    bingo: float | None = None, reason_facts: dict | None = None) -> str:
    """Fill a fixed template. ``confidence`` is the engine's probability (0-1, already rounded to 5 %).
    ``reason_facts`` (from dispatch) appends the reason sentence naming the charger that carries the decision."""
    template = DECISION_TEMPLATES[language][decision]
    needs = {"confidence": confidence, "bingo": bingo}
    missing = [k for k, v in needs.items() if "{" + k + "}" in template and v is None]
    if missing:
        raise ValueError(f"{decision} needs {', '.join(missing)} from the engine")
    text = template.format(destination=destination,
                           confidence=None if confidence is None else spoken_confidence(confidence, language),
                           bingo=None if bingo is None else int(round(bingo)))
    return text if reason_facts is None else f"{text} {spoken_reason(reason_facts, language)}"


def spoken_answers(decision: str, *, destination: str, confidence: float | None, bingo: float | None,
                   reason_facts: dict | None) -> dict[str, str]:
    """The sentence the demo speaks for a decision, in every demo language (W0.5): the dispatch response carries
    these exact strings, and the audio cache is keyed on them, so what is spoken offline is what was spoken online.
    Empty when the engine did not supply what the template needs (a GO without its Bingo Line is not spoken:
    refuse rather than guess)."""
    try:
        return {language: render_decision(decision, language, destination=destination, confidence=confidence,
                                          bingo=bingo, reason_facts=reason_facts) for language in SPEECH_LANGUAGES}
    except ValueError as exc:
        log.warning("not speaking %s: %s", decision, exc)
        return {}
