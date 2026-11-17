"""Spoken charger reports (W3.3): what is heard, what is read back, what is said after the write."""

from __future__ import annotations

import pytest

from verity.names import decide
from verity.report_voice import interpret_report, name_query, prompt_for, recorded_sentence
from verity.trust.graph_types import NameHit


def _hit(cid: str, name: str, score: float = 1.0) -> NameHit:
    return NameHit(cid, name, name, score)


def test_name_query_drops_the_status_and_the_filler() -> None:
    assert name_query("Radisson wala charger band hai") == "Radisson wala charger"
    assert name_query("Radisson ka charger bijli nahi hai") == "Radisson ka charger"


@pytest.mark.parametrize("said,status", [("Radisson wala charger band hai", "broken"),
                                         ("Radisson charger chal raha hai", "working"),
                                         ("Radisson charger pe lambi line hai", "occupied")])
def test_status_comes_from_the_rule_parser(said: str, status: str) -> None:
    assert interpret_report(said, [_hit("CH-1", "Radisson")]).status == status


def test_one_charger_and_a_status_is_read_back_and_waits_for_yes() -> None:
    reading = interpret_report("Radisson wala charger band hai", [_hit("CH-1", "Radisson Blu")])
    text, waits = prompt_for(reading, "en-IN")
    assert waits and text == "Radisson Blu: not working. Shall I record that?"


def test_two_equal_chargers_ask_which_one_and_never_wait_for_yes() -> None:
    reading = interpret_report("Tata charger band hai", [_hit("A", "Tata One"), _hit("B", "Tata Two")])
    assert reading.charger.kind == "which_one"
    text, waits = prompt_for(reading, "en-IN")
    assert not waits and "Tata One" in text and "Tata Two" in text


def test_no_charger_found_asks_for_the_name() -> None:
    text, waits = prompt_for(interpret_report("charger band hai", []), "hi-IN")
    assert not waits and "नाम" in text


def test_a_charger_without_a_status_asks_how_it_is() -> None:
    reading = interpret_report("Radisson", [_hit("CH-1", "Radisson")])
    assert reading.status is None
    text, waits = prompt_for(reading, "en-IN")
    assert not waits and "working" in text


def test_the_reading_agrees_with_names_decide() -> None:
    hits = [_hit("CH-1", "Radisson")]
    assert interpret_report("Radisson band", hits).charger == decide(hits)


BEFORE = {"p_works": 0.7, "n_reports": 3}
AFTER = {"p_works": 0.65, "n_reports": 4}


def test_recorded_sentence_gives_trust_before_after_and_its_evidence() -> None:
    text = recorded_sentence("Radisson Blu", BEFORE, AFTER, [], "en-IN")
    assert "70 percent before, 65 percent now, from 4 recent reports" in text
    assert text.endswith("No plan you asked for uses this charger.")


def test_a_charger_nobody_reported_on_says_no_estimate_not_a_number() -> None:
    text = recorded_sentence("X", {"p_works": None, "n_reports": 0}, AFTER, [], "en-IN")
    assert "no estimate before" in text


def test_the_plan_line_says_same_or_what_moved() -> None:
    same = {"moved": False, "changed": []}
    moved = {"moved": True, "changed": ["backups", "decision"]}
    assert recorded_sentence("X", BEFORE, AFTER, [same], "en-IN").endswith("Your plan did not change.")
    assert recorded_sentence("X", BEFORE, AFTER, [same, moved], "en-IN").endswith(
        "Your plan changed: the decision, the backup charger.")


def test_every_sentence_exists_in_hindi_too() -> None:
    moved = {"moved": True, "changed": ["stops"]}
    assert "योजना बदली" in recorded_sentence("X", BEFORE, AFTER, [moved], "hi-IN")
    assert "प्रतिशत" in recorded_sentence("X", BEFORE, AFTER, [], "hi-IN")
