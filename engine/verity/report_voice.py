"""A spoken charger report, in words (W3.3): what was said, what we heard, what we read back, and what changed.

Pure functions, no I/O. The pipeline lives in ``api/voice_report.py``:

    transcript -> ``interpret_report`` (status by rule + charger by the name index) -> read-back (nothing is written)
    driver says yes -> the report is written -> ``recorded_sentence`` (trust before/after, did the plan move)

Golden rule 6: when the status or the charger is not clear, the answer is a question, never a guess. A report changes
what other drivers are told, so it is only written after the driver has heard it read back and said yes.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import NamedTuple

from .names import NameDecision, decide
from .trust.graph_types import NameHit
from .voice_loop import STATUS_WORDS, parse_status

# Words around the charger name that say how it is, or are just talk; removed before the name search so that
# "Radisson wala charger band hai" looks for "radisson" and not for "radisson band hai" (approximate - verify).
_FILLER = frozenset({"hai", "hain", "tha", "thi", "ho", "gaya", "gayi", "abhi", "bhai", "sir", "please", "yaar",
                     "ye", "yeh", "wo", "woh", "bilkul", "aaj", "है", "हैं", "था", "थी", "हो", "गया", "गई", "अभी", "यह", "वह"})
_STATUS_RE = re.compile("|".join(f"(?:{pattern})" for _, pattern in STATUS_WORDS), re.IGNORECASE)

STATUS_SPOKEN = {
    "en-IN": {"working": "working", "broken": "not working", "blocked": "blocked", "occupied": "busy",
              "no_power": "without power"},
    "hi-IN": {"working": "चल रहा है", "broken": "बंद है", "blocked": "रास्ता रुका है", "occupied": "व्यस्त है",
              "no_power": "बिजली नहीं है"},
}
STATUS_CHOICES = {"en-IN": "working, not working, busy, blocked or no power",
                  "hi-IN": "चल रहा है, बंद है, व्यस्त है, रास्ता रुका है या बिजली नहीं है"}

READ_BACK = {"en-IN": "{charger}: {status}. Shall I record that?", "hi-IN": "{charger}: {status}। दर्ज करूँ?"}
WHICH_ONE = {"en-IN": "Which charger do you mean: {names}?", "hi-IN": "कौन सा चार्जर: {names}?"}
NO_CHARGER = {"en-IN": "I could not tell which charger you mean. Please say its name.",
              "hi-IN": "मैं समझ नहीं पाया कि कौन सा चार्जर। कृपया उसका नाम बताइए।"}
NO_STATUS = {"en-IN": "I did not hear how the charger is. Say: {choices}.",
             "hi-IN": "मैंने नहीं सुना कि चार्जर कैसा है। बोलिए: {choices}।"}
NOT_RECORDED = {"en-IN": "Okay, nothing was recorded.", "hi-IN": "ठीक है, कुछ दर्ज नहीं किया गया।"}

TRUST_LINE = {
    "en-IN": "Recorded. Chance that {charger} works: {before} before, {after} now, from {n} recent reports.",
    "hi-IN": "दर्ज किया। {charger} के चलने की संभावना: पहले {before}, अब {after}, {n} हालिया रिपोर्ट के आधार पर।",
}
NO_EARLIER = {"en-IN": "no estimate", "hi-IN": "कोई अनुमान नहीं"}
PERCENT = {"en-IN": "{p} percent", "hi-IN": "{p} प्रतिशत"}
PLAN_LINE = {
    "en-IN": {"none": "No plan you asked for uses this charger.",
              "same": "Your plan did not change.",
              "moved": "Your plan has changed since it was made: {changes}.",
              "pending": "{n} more plans use this charger and will be re-checked when you ask again.",
              "failed": "I could not re-check your plans just now."},
    "hi-IN": {"none": "आपकी किसी योजना में यह चार्जर नहीं है।",
              "same": "आपकी योजना नहीं बदली।",
              "moved": "योजना बनने के बाद से बदली है: {changes}।",
              "pending": "{n} और योजनाओं में यह चार्जर है; दोबारा पूछने पर उन्हें फिर जाँचा जाएगा।",
              "failed": "अभी मैं आपकी योजनाएँ दोबारा नहीं जाँच पाया।"},
}
CHANGE_WORDS = {"en-IN": {"decision": "the decision", "stops": "the charging stop", "backups": "the backup charger"},
                "hi-IN": {"decision": "फ़ैसला", "stops": "चार्जिंग स्टॉप", "backups": "बैकअप चार्जर"}}


class ReportReading(NamedTuple):
    """What was heard. ``status`` is None when no status word was found; ``charger`` decides answer-or-ask."""

    status: str | None
    charger: NameDecision


def name_query(transcript: str) -> str:
    """The transcript with status words and filler removed: the words that can name a charger."""
    without_status = _STATUS_RE.sub(" ", transcript or "")
    return " ".join(w for w in without_status.split() if w.lower().strip(".,!?।") not in _FILLER)


def interpret_report(transcript: str, hits: Sequence[NameHit]) -> ReportReading:
    """Status by rule, charger from the name index's hits (``names.decide``: one match, or a short list, or nothing)."""
    return ReportReading(parse_status(transcript), decide(hits))


def _percent(p: float | None, language: str) -> str:
    return NO_EARLIER[language] if p is None else PERCENT[language].format(p=int(round(p * 100)))


def prompt_for(reading: ReportReading, language: str) -> tuple[str, bool]:
    """(the sentence to say, whether it is the read-back that waits for a yes). A question when anything is unclear."""
    if reading.charger.kind == "which_one":
        return WHICH_ONE[language].format(names="; ".join(h.name for h in reading.charger.hits)), False
    if reading.charger.kind == "none":
        return NO_CHARGER[language], False
    if reading.status is None:
        return NO_STATUS[language].format(choices=STATUS_CHOICES[language]), False
    charger = reading.charger.hits[0].name
    return READ_BACK[language].format(charger=charger, status=STATUS_SPOKEN[language][reading.status]), True


def recorded_sentence(charger_name: str, trust_before: dict, trust_after: dict, plans: Sequence[dict],
                      language: str, *, pending: int = 0, failed: bool = False) -> str:
    """The reply after the write: the charger's trust before and after (rounded to 5 %, with how many recent reports it
    rests on) and whether the plans that use this charger moved. Only what the engine measured is said."""
    trust = TRUST_LINE[language].format(charger=charger_name, before=_percent(trust_before["p_works"], language),
                                        after=_percent(trust_after["p_works"], language), n=trust_after["n_reports"])
    words = PLAN_LINE[language]
    if failed:
        return f"{trust} {words['failed']}"
    tail = f" {words['pending'].format(n=pending)}" if pending else ""
    if not plans:
        return f"{trust} {words['pending'].format(n=pending) if pending else words['none']}"
    moved = [p for p in plans if p["moved"]]
    if not moved:
        return f"{trust} {words['same']}{tail}"
    changed = {c for p in moved for c in p["changed"]}
    ordered = [c for c in CHANGE_WORDS[language] if c in changed]  # the decision first, then the stop, then the backup
    return f"{trust} " + words["moved"].format(changes=", ".join(CHANGE_WORDS[language][c] for c in ordered)) + tail
