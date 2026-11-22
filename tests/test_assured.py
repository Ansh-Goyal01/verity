"""WP9 Verity Assured: an illustrative cover price and report rewards, with no rupee figure without a sourced input."""

from __future__ import annotations

import json

import pytest

from api.assured import (DEFAULT_DAILY_REPORT_CAP, DEFAULT_MIN_CONFIDENCE, assured_block, contradicts_strong_evidence,
                         load_config, reward_block)
from api.demo_ring import ring_attack, ring_reset
from api.service import EngineService, demo_dispatch_request

SOURCED = {"tow_cost_rs": 3000, "tow_cost_source_url": "https://example.org/tow-tariff", "margin": 1.2,
           "min_confidence": 0.95, "report_reward_rs": 10, "report_daily_cap": 2}


def cfg_from(tmp_path, content) -> dict:
    path = tmp_path / "assured.json"
    path.write_text(content if isinstance(content, str) else json.dumps(content), encoding="utf-8")
    return load_config(path)


def test_a_missing_or_broken_config_invents_nothing(tmp_path) -> None:
    for cfg in (load_config(tmp_path / "absent.json"), cfg_from(tmp_path, "{not json"), cfg_from(tmp_path, "[1, 2]")):
        assert cfg["tow_cost_rs"] is None and cfg["tow_cost_source"] is None and cfg["margin"] is None
        assert cfg["report_reward_rs"] is None
        assert cfg["min_confidence"] == DEFAULT_MIN_CONFIDENCE and cfg["report_daily_cap"] == DEFAULT_DAILY_REPORT_CAP


def test_a_tow_cost_without_a_source_url_is_dropped(tmp_path) -> None:
    cfg = cfg_from(tmp_path, {**SOURCED, "tow_cost_source_url": ""})
    assert cfg["tow_cost_rs"] is None and cfg["tow_cost_source"] is None
    assert cfg_from(tmp_path, SOURCED)["tow_cost_source"] == "https://example.org/tow-tariff"
    assert cfg_from(tmp_path, {**SOURCED, "margin": 0.5})["margin"] is None


def test_a_go_at_threshold_with_calibrated_bands_is_priced_to_the_nearest_five_rupees(tmp_path) -> None:
    block = assured_block(decision="GO", confidence=0.95, confidence_raw=0.96, band_method="split conformal",
                          cfg=cfg_from(tmp_path, SOURCED))
    assert block["eligible"] and block["reason"] is None and block["is_illustrative"] is True
    assert block["p_strand"] == 0.05 and block["price_rs"] == 145  # 0.04 x 3000 x 1.2 = 144 -> 145
    assert block["tow_cost_source"] == "https://example.org/tow-tariff" and "Illustrative" in block["note"]


def test_no_rupee_price_without_a_sourced_input_but_the_formula_is_shown(tmp_path) -> None:
    block = assured_block(decision="GO", confidence=0.95, confidence_raw=0.96, band_method="split conformal",
                          cfg=load_config(tmp_path / "absent.json"))
    assert block["eligible"] and block["price_rs"] is None and block["tow_cost_rs"] is None
    assert "p_strand x tow cost x margin" in block["formula"]


@pytest.mark.parametrize("decision,confidence,bands,reason", [
    ("NO_GO", 0.99, "split conformal", "GO releases only"),
    ("INSUFFICIENT_EVIDENCE", 0.99, "split conformal", "GO releases only"),
    ("GO", 0.90, "split conformal", "below the 95%"),
    ("GO", 0.99, "model only", "bands not yet calibrated on real data"),
])
def test_cover_is_not_offered_unless_go_confident_and_calibrated(tmp_path, decision, confidence, bands, reason) -> None:
    block = assured_block(decision=decision, confidence=confidence, confidence_raw=confidence,
                          band_method=bands, cfg=cfg_from(tmp_path, SOURCED))
    assert not block["eligible"] and reason in block["reason"] and block["price_rs"] is None


def test_contradicting_strong_evidence_needs_a_strong_basis() -> None:
    assert contradicts_strong_evidence({"p_works": 0.9, "n_reports": 5}, "broken")
    assert contradicts_strong_evidence({"p_works": 0.1, "n_reports": 4}, "working")
    assert not contradicts_strong_evidence({"p_works": 0.9, "n_reports": 2}, "broken")  # too few reports
    assert not contradicts_strong_evidence({"p_works": 0.9, "n_reports": 5}, "working")  # agrees
    assert not contradicts_strong_evidence({"p_works": None, "n_reports": 0}, "broken")  # unknown charger


def test_reward_is_withheld_for_the_first_reason_that_applies(tmp_path) -> None:
    cfg = cfg_from(tmp_path, SOURCED)

    def reason(**flags) -> str | None:
        return reward_block(**{"is_simulated": False, "in_ring": False, "contradicts": False, "over_cap": False,
                               **flags}, cfg=cfg)["withheld_reason"]

    clean = reward_block(is_simulated=False, in_ring=False, contradicts=False, over_cap=False, cfg=cfg)
    assert clean == {"eligible": True, "amount_rs": 10.0, "withheld_reason": None, "is_illustrative": True}
    ring = reward_block(is_simulated=True, in_ring=True, contradicts=True, over_cap=True, cfg=cfg)
    assert not ring["eligible"] and ring["amount_rs"] is None
    assert "simulated" in reason(is_simulated=True, in_ring=True, contradicts=True, over_cap=True)
    assert "ring" in reason(in_ring=True, contradicts=True, over_cap=True)
    assert "contradicts" in reason(contradicts=True, over_cap=True)
    assert "daily cap of 2" in reason(over_cap=True)


@pytest.fixture(scope="module")
def svc() -> EngineService:
    service = EngineService(n_runs=200, graph_backend="memory")
    yield service
    service.close()


def test_every_release_carries_an_illustrative_assured_block(svc: EngineService) -> None:
    release = svc.dispatch(demo_dispatch_request("V12"))
    block = release["assured"]
    assert block["is_illustrative"] is True and block["formula"] and block["p_strand_basis"].startswith("upper bound")
    assert block["eligible"] == (release["decision"] == "GO" and release["confidence"] >= DEFAULT_MIN_CONFIDENCE
                                 and release["band_method"]["name"] != "model only")
    if not block["eligible"]:
        assert block["price_rs"] is None and block["reason"]


def test_a_real_report_earns_a_reward_until_the_daily_cap_and_the_ring_attack_earns_nothing(
        svc: EngineService, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    path = tmp_path / "assured.json"
    path.write_text(json.dumps(SOURCED), encoding="utf-8")
    monkeypatch.setattr("api.assured.CONFIG_PATH", path)
    cid = svc.chargers[0].id
    body = {"charger_id": cid, "status": "working", "channel": "driver_voice", "consent": True}
    first = svc.add_report(body, reporter="addr-1")["reward"]
    second = svc.add_report(body, reporter="addr-1")["reward"]
    third = svc.add_report(body, reporter="addr-1")["reward"]
    assert first["eligible"] and first["amount_rs"] == 10 and second["eligible"]
    assert not third["eligible"] and "daily cap of 2" in third["withheld_reason"]
    assert svc.add_report(body, reporter="addr-2")["reward"]["eligible"]  # another reporter has its own cap
    scripted = svc.add_report(body, is_simulated=True)["reward"]
    assert not scripted["eligible"] and "simulated" in scripted["withheld_reason"]
    attack = ring_attack(svc)
    try:
        assert attack["reward"]["eligible"] is False and "simulated" in attack["reward"]["withheld_reason"]
    finally:
        ring_reset(svc)
