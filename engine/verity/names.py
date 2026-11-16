"""Spoken charger names: which charger did the driver mean? (W2.5)

The graph retrieves candidates (Neo4j full-text index, or its in-memory twin); the decision to answer or to ask
"Which one?" is made HERE, from the words alone, so both backends decide identically. Golden rule 6: when the
words do not pick one charger, the answer is a short list, never a guess.

Words that name no place ("charger", "station", "wala", ...) are ignored: 77 of the 207 directory chargers are
called "EV charging station (OpenStreetMap)", which names nothing, so they can never be matched by name.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import NamedTuple

from .places import normalise
from .trust.graph_types import NameHit

# Words that say "a charger" or "the one near", not which one (English, Hinglish, Devanagari; approximate - verify).
GENERIC_WORDS = frozenset({
    "charger", "chargers", "charging", "charge", "station", "stations", "ev", "point", "points", "openstreetmap",
    "the", "a", "an", "near", "at", "of", "wala", "wali", "wale", "waala", "waali", "ka", "ki", "ke", "ek", "pe", "par",
    "me", "mein", "se", "to", "tak", "चार्जर", "चार्जिंग", "स्टेशन", "वाला", "वाली", "वाले", "का", "की", "के", "एक", "पर",
    "में", "से", "तक",
})

# The one tunable decision (docs/DECISIONS.md W2.5): how sure the words must be before one charger is named.
MATCH_MIN_SCORE = 1.0  # every distinctive spoken word is in the name
WHICH_ONE_MIN_SCORE = 0.5  # at least half of them: worth showing as a candidate
MAX_CANDIDATES = 3


class NameDecision(NamedTuple):
    kind: str  # "match" (one charger) | "which_one" (ask, with candidates) | "none" (nothing to go on)
    hits: tuple[NameHit, ...]


def spoken_tokens(text: str) -> list[str]:
    """The distinctive words of what was said or written, in order, without duplicates."""
    return list(dict.fromkeys(t for t in normalise(text).split() if t not in GENERIC_WORDS))


def text_tokens(text: str) -> set[str]:
    return set(normalise(text).split())


def coverage(spoken: Sequence[str], text: str) -> float:
    """Share of the distinctive spoken words that appear, as whole words, in ``text``. 0.0 with nothing to match."""
    if not spoken:
        return 0.0
    have = text_tokens(text)
    return sum(1 for t in spoken if t in have) / len(spoken)


def rank_hits(spoken_text: str, candidates: Iterable[tuple[str, str, str]]) -> list[NameHit]:
    """``candidates`` = (charger id, charger name, text that matched). One hit per charger (its best text), best first;
    ties go to the shorter text (fewer extra words), then the id. Scores below WHICH_ONE_MIN_SCORE are dropped."""
    spoken = spoken_tokens(spoken_text)
    best: dict[str, NameHit] = {}
    for cid, name, matched in candidates:
        score = coverage(spoken, matched)
        if score < WHICH_ONE_MIN_SCORE:
            continue
        hit = NameHit(cid, name, matched, round(score, 4))
        old = best.get(cid)
        if old is None or (-hit.score, len(text_tokens(hit.matched))) < (-old.score, len(text_tokens(old.matched))):
            best[cid] = hit
    return sorted(best.values(), key=lambda h: (-h.score, len(text_tokens(h.matched)), h.charger_id))


def decide(hits: Sequence[NameHit]) -> NameDecision:
    """One charger only when it alone reaches MATCH_MIN_SCORE; otherwise the best few, to ask which."""
    if not hits:
        return NameDecision("none", ())
    top = hits[0]
    if top.score >= MATCH_MIN_SCORE and (len(hits) == 1 or hits[1].score < top.score):
        return NameDecision("match", (top,))
    return NameDecision("which_one", tuple(hits[:MAX_CANDIDATES]))
