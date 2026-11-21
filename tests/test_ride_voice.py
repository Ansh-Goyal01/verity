"""W3.2: from a transcript to the ride's slots, the read-back, and the spoken answer.

Rules first; a language model only when the rules miss something, with a strict schema and a 4 s limit, and it only ever
fills a gap: it cannot override a rule-found value, cannot invent a place the gazetteer lacks, and never sees the
decision. Fake clients only: no test here is a paid call.
"""

from __future__ import annotations

import copy
import json
import re

import pytest

from verity.paths import DOCS_DIR
from verity.places import Gazetteer
from verity.ride_voice import (
    LLM_TIMEOUT_S,
    RideSlots,
    clarify,
    complete_with_llm,
    parse_ride_request,
    read_back,
    reply_language,
    spoken_ride_answer,
)
from verity.voice import VoiceUnavailable

GAZ = Gazetteer.from_file()
RIDE = json.loads((DOCS_DIR / "api_examples" / "ride_check.json").read_text(encoding="utf-8"))
DEVANAGARI = re.compile(r"[ऀ-ॿ]")


def slots(text: str, language_code: str | None = None) -> RideSlots:
    return parse_ride_request(text, GAZ, language_code)


# --- the rules ---------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("text", [
    "Battery 45 percent hai, Sector 18 se airport ka ride mila hai",
    "Sector 18 se airport ka ride mila hai, battery 45",  # no "percent": a sector number must not be read as the battery
    "बैटरी 45 प्रतिशत है, सेक्टर 18 से एयरपोर्ट का राइड मिला है",
    "बैटरी ४५ प्रतिशत, सेक्टर १८ से एयरपोर्ट तक",
    "ride from sector 18 to the airport, battery forty five percent",
])
def test_the_demo_request_is_parsed_by_rules_in_every_script(text: str) -> None:
    s = slots(text)
    assert (s.soc_pct, s.pickup.id, s.drop.id, s.parsed_by) == (45, "noida-sector-18", "igi-airport-t3", "rules")
    assert s.missing == [] and s.unresolved == ()


def test_hindi_number_words_are_not_parsed_by_rules_so_the_battery_is_missing() -> None:
    s = slots("बैटरी पैंतालीस प्रतिशत है, सेक्टर 18 से एयरपोर्ट")
    assert s.soc_pct is None and s.missing == ["soc_pct"] and s.pickup is not None


def test_what_the_rules_cannot_find_is_listed_as_missing() -> None:
    assert slots("hello there").missing == ["soc_pct", "pickup", "drop"]
    assert slots("battery 30 percent, going to the airport").missing == ["pickup"]


def test_a_place_the_gazetteer_does_not_know_is_not_guessed() -> None:
    s = slots("battery 45 percent, Sector 6 se airport")
    assert s.pickup is None and s.drop.id == "igi-airport-t3" and s.missing == ["pickup"]


# --- the language-model fallback ---------------------------------------------------------------------------------------------


class FakeChat:
    def __init__(self, reply: dict | Exception) -> None:
        self.reply, self.calls = reply, []

    def chat_json(self, system: str, user: str, schema: dict, name: str = "labels", timeout: float | None = None) -> dict:
        self.calls.append({"system": system, "user": user, "schema": schema, "timeout": timeout})
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


HINDI_WORDS = "बैटरी पैंतालीस प्रतिशत है, सेक्टर 18 से एयरपोर्ट का राइड"


def test_the_model_fills_only_the_gap_the_rules_left() -> None:
    chat = FakeChat({"soc_pct": 45, "pickup": "sector 18", "drop": "airport"})
    done = complete_with_llm(slots(HINDI_WORDS), HINDI_WORDS, chat, GAZ)
    assert (done.soc_pct, done.pickup.id, done.drop.id, done.parsed_by) == (45, "noida-sector-18", "igi-airport-t3", "rules+llm")
    assert len(chat.calls) == 1 and chat.calls[0]["timeout"] == LLM_TIMEOUT_S == 4.0


def test_the_model_never_overrides_a_value_the_rules_found() -> None:
    text = "battery 45 percent, sector 18 se airport, bas yahi"
    chat = FakeChat({"soc_pct": 80, "pickup": "sector 62", "drop": "airport"})
    done = complete_with_llm(slots(text), text, chat, GAZ)
    assert chat.calls == [] and done.soc_pct == 45 and done.pickup.id == "noida-sector-18"  # nothing missing: not even called


def test_a_rule_found_battery_survives_a_model_that_disagrees_about_the_places() -> None:
    text = "battery 45 percent, kahin jaana hai"
    chat = FakeChat({"soc_pct": 80, "pickup": "sector 18", "drop": "airport"})
    done = complete_with_llm(slots(text), text, chat, GAZ)
    assert done.soc_pct == 45 and (done.pickup.id, done.drop.id) == ("noida-sector-18", "igi-airport-t3")


def test_a_place_the_model_names_but_the_gazetteer_lacks_is_reported_unresolved_not_invented() -> None:
    text = "battery 45 percent, radisson se airport"
    done = complete_with_llm(slots(text), text, FakeChat({"soc_pct": 45, "pickup": "Radisson", "drop": "airport"}), GAZ)
    assert done.pickup is None and done.unresolved == ("Radisson",) and done.missing == ["pickup"]


@pytest.mark.parametrize("bad", [250, -3, "forty", 45.5, None, True])
def test_a_battery_the_model_returns_must_be_a_whole_percent_from_0_to_100(bad) -> None:
    text = "pata nahi kitni battery hai, sector 18 se airport"
    done = complete_with_llm(slots(text), text, FakeChat({"soc_pct": bad, "pickup": None, "drop": None}), GAZ)
    assert done.soc_pct is None and done.missing == ["soc_pct"]


@pytest.mark.parametrize("failure", [VoiceUnavailable("voice service error: ReadTimeout"), ValueError("bad"), KeyError("x")])
def test_a_slow_or_broken_model_leaves_the_rule_result_and_asks_again(failure: Exception) -> None:
    before = slots(HINDI_WORDS)
    assert complete_with_llm(before, HINDI_WORDS, FakeChat(failure), GAZ) == before


def test_without_a_client_the_rule_result_stands() -> None:
    before = slots(HINDI_WORDS)
    assert complete_with_llm(before, HINDI_WORDS, None, GAZ) == before


def test_the_model_sees_the_transcript_and_a_strict_schema_and_nothing_about_the_decision() -> None:
    chat = FakeChat({"soc_pct": 45, "pickup": "sector 18", "drop": "airport"})
    complete_with_llm(slots(HINDI_WORDS), HINDI_WORDS, chat, GAZ)
    call = chat.calls[0]
    assert call["user"] == HINDI_WORDS
    assert call["schema"]["additionalProperties"] is False
    assert set(call["schema"]["properties"]) == {"soc_pct", "pickup", "drop"} == set(call["schema"]["required"])
    prompt = (call["system"] + call["user"]).lower()
    assert not any(w in prompt for w in ("go/no", "confidence", "decide", "probability"))


# --- the language the driver gets back ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("code, text, expected", [
    ("hi-IN", "anything", "hi-IN"), ("en-IN", "anything", "en-IN"), (None, "सेक्टर 18 से", "hi-IN"),
    (None, "sector 18 se airport", "en-IN"), ("ta-IN", "வணக்கம்", "en-IN"),  # S12 adds more languages
])
def test_the_reply_is_in_the_detected_language_when_the_demo_speaks_it(code: str | None, text: str, expected: str) -> None:
    assert reply_language(code, text) == expected


# --- read back and ask again -------------------------------------------------------------------------------------------------


def test_the_read_back_says_the_battery_and_both_places_and_asks() -> None:
    s = slots("battery 45 percent, sector 18 se airport")
    assert read_back(s, "en-IN") == "45 percent battery, Sector 18 to IGI Airport Terminal 3. Is that right?"
    assert read_back(s, "hi-IN") == "बैटरी 45 प्रतिशत, सेक्टर 18 से आईजीआई एयरपोर्ट टर्मिनल 3। सही है?"


def test_the_clarification_names_exactly_what_was_not_understood() -> None:
    assert clarify(slots("sector 18 se"), "en-IN") == "I did not catch the battery percentage and the drop place. Please say it again."
    assert "ड्रॉप की जगह" in clarify(slots("battery 45 percent, sector 18 se"), "hi-IN")


def test_the_clarification_admits_a_place_it_does_not_know() -> None:
    s = RideSlots(45, None, None, "rules+llm", unresolved=("Radisson",))
    assert "I do not know the place Radisson yet" in clarify(s, "en-IN")
    assert "Radisson" in clarify(s, "hi-IN")


# --- the spoken answer -------------------------------------------------------------------------------------------------------


def test_a_go_is_spoken_with_the_charger_the_minutes_and_the_rupees_flagged_as_an_example() -> None:
    en, hi = spoken_ride_answer(RIDE, "en-IN"), spoken_ride_answer(RIDE, "hi-IN")
    assert en.startswith("Take the ride.") and "95 percent or more" in en
    minutes = round(RIDE["after_drop"]["expected_lost_min"])
    assert "Radisson Blu Plaza Delhi Airport" in en and f"{minutes} minutes" in en and "80 rupees" in en
    assert "example" in en  # the rupee figure uses an example earning rate and says so
    assert DEVANAGARI.search(hi) and "95 प्रतिशत या उससे अधिक" in hi and "80 रुपये" in hi and "उदाहरण" in hi


def test_a_driver_who_entered_their_own_rate_is_not_told_it_is_an_example() -> None:
    mine = copy.deepcopy(RIDE)
    mine["earning_rate"]["is_example"] = False
    assert "example" not in spoken_ride_answer(mine, "en-IN") and "उदाहरण" not in spoken_ride_answer(mine, "hi-IN")


def test_no_spoken_ride_answer_ever_says_certainty() -> None:
    for confidence in (0.0, 0.05, 0.5, 0.95, 1.0):
        for decision in ("GO", "NO_GO"):
            changed = {**copy.deepcopy(RIDE), "decision": decision, "confidence": confidence}
            for language in ("en-IN", "hi-IN"):
                text = spoken_ride_answer(changed, language)
                assert text and "%" not in text and "100" not in text and not re.search(r"\b0 (percent|प्रतिशत)", text)


def test_a_no_go_and_a_refusal_are_fixed_sentences() -> None:
    no_go = {**copy.deepcopy(RIDE), "decision": "NO_GO", "confidence": 0.6, "after_drop": None}
    assert spoken_ride_answer(no_go, "en-IN").startswith("Do not take this ride.") and "60 percent" in spoken_ride_answer(no_go, "en-IN")
    refusal = {**copy.deepcopy(RIDE), "decision": "INSUFFICIENT_EVIDENCE"}
    assert spoken_ride_answer(refusal, "en-IN") == "There is not enough evidence to promise this ride yet, so we will not guess."


def test_a_go_with_no_after_drop_plan_does_not_invent_a_charger() -> None:
    bare = {**copy.deepcopy(RIDE), "after_drop": None}
    text = spoken_ride_answer(bare, "en-IN")
    assert text.startswith("Take the ride.") and "charge at" not in text and "rupees" not in text


def test_an_answer_without_its_confidence_is_not_spoken() -> None:
    assert spoken_ride_answer({"decision": "GO"}, "en-IN") == ""
