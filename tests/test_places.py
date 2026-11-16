"""W3.2: a small gazetteer of NCR places and the rule that finds them in a spoken ride request.

The gazetteer holds only places whose coordinates the project already pins (the driver scenario); S6 swaps in a
full-text index over the OSM names. A place it does not know is reported as unknown, never guessed.
"""

from __future__ import annotations

import json

import pytest

from api.service import DRIVER_DEMO
from verity.paths import DATA_DIR
from verity.places import Gazetteer, assign_roles, find_mentions, mask, normalise

GAZ = Gazetteer.from_file()
S18, S62, IGI = "noida-sector-18", "noida-sector-62", "igi-airport-t3"


def roles(text: str) -> tuple[str | None, str | None, str]:
    norm = normalise(text)
    pickup, drop, how = assign_roles(norm, find_mentions(norm, GAZ))
    return (pickup.id if pickup else None), (drop.id if drop else None), how


# --- the data is grounded ---------------------------------------------------------------------------------------------------


def test_the_gazetteer_is_the_pinned_driver_scenarios_places_and_nothing_invented() -> None:
    by_id = {p.id: p for p in GAZ.places}
    assert set(by_id) == {S18, S62, IGI}
    for place_id, key in ((S62, "current"), (S18, "pickup"), (IGI, "drop")):
        assert (by_id[place_id].lat, by_id[place_id].lon) == (DRIVER_DEMO[key]["lat"], DRIVER_DEMO[key]["lon"])
    assert all(p.source and p.spoken.keys() >= {"en-IN", "hi-IN"} for p in GAZ.places)


def test_the_file_says_what_it_covers_and_that_it_is_temporary() -> None:
    raw = json.loads((DATA_DIR / "gazetteer" / "ncr_places.json").read_text(encoding="utf-8"))
    assert "S6" in raw["coverage"] and raw["places"]


# --- normalising and finding ------------------------------------------------------------------------------------------------


def test_normalise_lowercases_maps_devanagari_digits_and_drops_punctuation() -> None:
    assert normalise("Sector 18, Noida!") == "sector 18 noida"
    assert normalise("सेक्टर १८ से") == "सेक्टर 18 से"
    assert normalise("  IGI   Airport  ") == "igi airport"


@pytest.mark.parametrize("text", ["sector 18", "Sector 18", "sec 18", "SECTOR EIGHTEEN", "सेक्टर 18", "सेक्टर १८", "सेक्टर अठारह"])
def test_a_place_is_found_in_english_hinglish_and_hindi_spellings(text: str) -> None:
    mentions = find_mentions(normalise(text), GAZ)
    assert [m.place.id for m in mentions] == [S18]


def test_the_longest_alias_wins_and_one_place_is_one_mention() -> None:
    assert [m.place.id for m in find_mentions(normalise("IGI airport terminal 3"), GAZ)] == [IGI]
    assert [m.place.id for m in find_mentions(normalise("airport terminal 3 pe jaana hai"), GAZ)] == [IGI]


def test_a_number_inside_another_word_is_not_a_sector() -> None:
    assert find_mentions(normalise("sector 180 and sector 6"), GAZ) == []  # not 18, not 62: unknown, so not guessed
    assert find_mentions(normalise("sector 18a"), GAZ) == []


def test_mentions_come_in_the_order_they_were_spoken() -> None:
    mentions = find_mentions(normalise("airport se sector 62"), GAZ)
    assert [m.place.id for m in mentions] == [IGI, S62]


# --- who is the pickup and who is the drop -----------------------------------------------------------------------------------


@pytest.mark.parametrize("text, pickup, drop, how", [
    ("Battery 45 percent hai, Sector 18 se airport ka ride mila hai", S18, IGI, "cues"),
    ("सेक्टर 18 से एयरपोर्ट का राइड मिला है", S18, IGI, "cues"),
    ("ride from Sector 62 to airport terminal 3", S62, IGI, "cues"),
    ("airport se sector 18 ka ride", IGI, S18, "cues"),  # "se" after the airport: that is the pickup, whatever the order
    ("sector 18 airport", S18, IGI, "order"),  # no cue words: spoken order, and the read-back will say so
])
def test_pickup_and_drop_follow_the_cue_words_then_the_spoken_order(text: str, pickup: str, drop: str, how: str) -> None:
    assert roles(text) == (pickup, drop, how)


def test_the_drop_cue_to_makes_a_single_place_a_drop() -> None:
    assert roles("take me to the airport") == (None, IGI, "single")
    assert roles("airport se pickup hai") == (IGI, None, "single")


def test_a_single_place_with_no_cue_is_ambiguous_and_nothing_is_assumed() -> None:
    assert roles("sector 18") == (None, None, "ambiguous")


def test_no_places_means_no_roles() -> None:
    assert roles("battery forty five percent") == (None, None, "none")


# --- masking ---------------------------------------------------------------------------------------------------------------


def test_masking_removes_place_numbers_so_a_sector_is_never_read_as_the_battery() -> None:
    norm = normalise("sector 18 to airport terminal 3, battery 45")
    masked = mask(norm, find_mentions(norm, GAZ))
    assert "18" not in masked and "3" not in masked.split() and "45" in masked
    assert len(masked) == len(norm)  # spans are blanked, not cut, so offsets stay valid
