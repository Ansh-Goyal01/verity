"""Find the places a driver names in a spoken ride request, and which one is the pickup (W3.2).

A small, committed gazetteer (data/gazetteer/ncr_places.json: only places whose coordinates the project already pins)
and plain rules over the transcript, in English, Hinglish or Devanagari. No model is involved: the spoken-ride
pipeline reads the answer back to the driver and runs nothing until they confirm, so a rule that is sometimes wrong is
safe and one that guesses silently is not. Anything the gazetteer does not know is simply not found. S6 (W2.5)
built the full-text index over CHARGER names (``names.py``, ``trust/graph_structure.py``); it is deliberately NOT used
here, because ride places are not chargers and charger names would match ordinary words (docs/DECISIONS.md, W2.5).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

from .paths import DATA_DIR

GAZETTEER_FILE = DATA_DIR / "gazetteer" / "ncr_places.json"
_DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")
_WORDLIKE = r"\wऀ-ॿ"  # letters, digits and every Devanagari sign (matras are not \w)
# Cue words around a place. Hinglish puts "se" (from) AFTER the pickup and "tak/ko/jaana" after the drop; English
# puts "from" / "to" BEFORE it. "X ka ride" is a ride to X.
PICKUP_BEFORE = {"from", "pickup"}
PICKUP_AFTER = {"se", "से", "pickup", "पिकअप"}
DROP_BEFORE = {"to", "towards", "till", "until", "se", "से"}  # "A se B" is "from A to B": B follows the "se"
DROP_AFTER = {"tak", "तक", "ko", "को", "jana", "jaana", "जाना", "जाने", "ke", "के"}
RIDE_OF = {"ka", "ki", "ke", "का", "की", "के"}
RIDE_WORDS = {"ride", "rides", "trip", "booking", "राइड", "सवारी", "बुकिंग", "ट्रिप"}
FILLERS = {"the", "a", "an"}


def normalise(text: str) -> str:
    """Lower case, Devanagari digits as ASCII, punctuation (and the danda) as spaces, single spaces."""
    t = (text or "").translate(_DEVANAGARI_DIGITS).lower().replace("%", " percent ")  # "45%" keeps its unit
    t = t.replace("।", " ").replace("॥", " ")
    t = re.sub(rf"[^{_WORDLIKE}\s]", " ", t).replace("_", " ")
    return re.sub(r"\s+", " ", t).strip()


@dataclass(frozen=True, eq=False)
class Place:
    id: str
    name: str
    lat: float
    lon: float
    aliases: tuple[str, ...]  # already normalised
    spoken: dict[str, str]
    source: str


class Mention(NamedTuple):
    place: Place
    start: int  # offsets into the normalised text
    end: int


@dataclass(frozen=True)
class Gazetteer:
    places: tuple[Place, ...]

    @classmethod
    def from_file(cls, path: Path | None = None) -> Gazetteer:
        raw = json.loads((path or GAZETTEER_FILE).read_text(encoding="utf-8"))
        return cls(tuple(Place(p["id"], p["name"], float(p["lat"]), float(p["lon"]),
                               tuple(dict.fromkeys(normalise(a) for a in p["aliases"])), dict(p["spoken"]), p["source"])
                         for p in raw["places"]))

    def by_id(self, place_id: str) -> Place | None:
        return next((p for p in self.places if p.id == place_id), None)


def find_mentions(norm: str, gazetteer: Gazetteer) -> list[Mention]:
    """Every place named in ``norm`` (already normalised), longest alias first, none overlapping, in spoken order.
    An alias only counts as a whole word: "sector 18" is not found inside "sector 180" or "sector 18a"."""
    found: list[Mention] = []
    for place in gazetteer.places:
        for alias in place.aliases:
            for m in re.finditer(rf"(?<![{_WORDLIKE}]){re.escape(alias)}(?![{_WORDLIKE}])", norm):
                found.append(Mention(place, m.start(), m.end()))
    chosen: list[Mention] = []
    for mention in sorted(found, key=lambda m: (-(m.end - m.start), m.start)):
        if all(mention.end <= c.start or mention.start >= c.end for c in chosen):
            chosen.append(mention)
    return sorted(chosen, key=lambda m: m.start)


def mask(norm: str, mentions: list[Mention]) -> str:
    """``norm`` with every place's span blanked (same length), so "sector 18" can never be read as a battery of 18."""
    chars = list(norm)
    for m in mentions:
        chars[m.start:m.end] = " " * (m.end - m.start)
    return "".join(chars)


def _cue(norm: str, mention: Mention) -> str | None:
    before, after = norm[:mention.start].split()[-3:], norm[mention.end:].split()[:3]
    if before and before[-1] in FILLERS:
        before = before[:-1]
    if (before and before[-1] in PICKUP_BEFORE) or (after and after[0] in PICKUP_AFTER) or after[:2] == ["pick", "up"]:
        return "pickup"
    if (before and before[-1] in DROP_BEFORE) or (after and after[0] in DROP_AFTER) \
            or (len(after) > 1 and after[0] in RIDE_OF and after[1] in RIDE_WORDS) \
            or (len(after) > 1 and after[1] in DROP_AFTER and after[0] in {"pe", "par", "पर", "पे"}):
        return "drop"
    return None


def assign_roles(norm: str, mentions: list[Mention]) -> tuple[Place | None, Place | None, str]:
    """(pickup, drop, how). ``how``: "cues" (cue words decided it), "order" (none did: the spoken order), "single"
    (one place, its role known), "ambiguous" (one place, role unknown: nothing assumed) or "none"."""
    if not mentions:
        return None, None, "none"
    cues = [_cue(norm, m) for m in mentions]
    if len(mentions) == 1:
        place = mentions[0].place
        if cues[0] == "pickup":
            return place, None, "single"
        if cues[0] == "drop":
            return None, place, "single"
        return None, None, "ambiguous"
    pickup_i = next((i for i, c in enumerate(cues) if c == "pickup"), None)
    drop_i = next((i for i, c in enumerate(cues) if c == "drop" and i != pickup_i), None)
    how = "cues" if pickup_i is not None or drop_i is not None else "order"
    rest = [i for i in range(len(mentions)) if i not in (pickup_i, drop_i)]
    if pickup_i is None and rest:
        pickup_i = rest.pop(0)
    if drop_i is None and rest:
        drop_i = rest.pop(0)
    return (mentions[pickup_i].place if pickup_i is not None else None,
            mentions[drop_i].place if drop_i is not None else None, how)
