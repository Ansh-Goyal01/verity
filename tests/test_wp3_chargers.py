"""WP3: India-wide directory (ids frozen), geocoded evidence matching, operator pooling and the trust block."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

from verity import evidence as ev
from verity.chargers import chargers_near_route, default_chargers_file, extend_directory
from verity.evidence_load import is_precise_kind, load_geocoded
from verity.schemas import ChargerRecord, Report
from verity.trips import haversine_m
from verity.trust.engine import TrustEngine, operator_pool
from verity.trust.priors import operator_key, pooled_prior, prior_for

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[1]
FROZEN = [[28.2, 76.8, 29.0, 77.8]]


def rec(cid: str, lat: float, lon: float, op: str = "", src: str | None = None) -> ChargerRecord:
    return ChargerRecord(id=cid, name=f"Charger {cid}", lat=lat, lon=lon, operator=op, site_type="unknown",
                         connectors=[], source_ids=[src or f"osm:node/{cid}"])


def rep(cid: str, status: str = "working", days: float = 1.0, *, sim: bool = False, src: str = "s1") -> Report:
    return Report(charger_id=cid, source_id=src, status=status, t=NOW - timedelta(days=days), is_simulated=sim)


# --- directory -------------------------------------------------------------------------------------------------------

def test_extend_directory_keeps_every_base_record_and_id() -> None:
    base = [rec("CH-0001", 28.5, 77.3), rec("CH-0002", 28.6, 77.4)]
    new = [rec("x1", 28.55, 77.35, src="ocm:1"),             # inside the frozen box: not added
           rec("x2", 26.9, 75.8, src="ocm:2"),               # Jaipur: added
           rec("x3", 26.90001, 75.80001, src="osm:node/3"),  # within 50 m of x2: merged
           rec("x4", 19.0, 72.8, src="ocm:4")]               # Mumbai: added
    out, stats = extend_directory(base, new, FROZEN)
    assert out[:2] == base
    assert [c.id for c in out[2:]] == ["CH-0003", "CH-0004"]  # numbering continues, lat/lon order
    assert stats["inside_frozen_boxes_dropped"] == 1 and stats["merged_away_within_50m"] == 1 and stats["added"] == 2


def test_extend_directory_drops_a_new_record_that_duplicates_a_base_charger() -> None:
    base = [rec("CH-0001", 20.0, 78.0)]
    out, stats = extend_directory(base, [rec("x", 20.0001, 78.0001, src="ocm:9")], FROZEN)
    assert out == base and stats["duplicates_of_base_dropped"] == 1


def test_the_bbox_prefilter_changes_nothing() -> None:
    rng = np.random.default_rng(3)
    chargers = [rec(f"C{i}", float(lat), float(lon)) for i, (lat, lon) in
                enumerate(zip(rng.uniform(26.5, 28.8, 400), rng.uniform(75.5, 77.5, 400)))]
    poly = np.column_stack([np.linspace(28.6, 26.9, 300), np.linspace(77.2, 75.8, 300)])
    poly_km = np.linspace(0.0, 270.0, 300)
    got = chargers_near_route(chargers, poly, poly_km, 5.0)
    brute = [c.id for c in chargers  # the old exhaustive check
             if haversine_m(np.full(len(poly), c.lat), np.full(len(poly), c.lon), poly[:, 0], poly[:, 1]).min() <= 5000.0]
    assert got and sorted(rc.charger.id for rc in got) == sorted(brute)


def test_verity_chargers_names_the_directory(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("VERITY_CHARGERS", str(tmp_path / "x.json"))
    assert default_chargers_file() == tmp_path / "x.json"


# --- geocoded evidence -----------------------------------------------------------------------------------------------

def test_area_level_geocodes_are_never_used() -> None:
    assert not is_precise_kind("city") and not is_precise_kind("suburb") and not is_precise_kind("postcode")
    assert is_precise_kind("charging_station") and is_precise_kind("fuel")


def test_load_geocoded_keeps_only_precise_rows(tmp_path: Path) -> None:
    f = tmp_path / "g.json"
    f.write_text(json.dumps({"rows": {"E1": {"lat": 1.0, "lon": 2.0, "kind": "fuel"},
                                      "E2": {"lat": 3.0, "lon": 4.0, "kind": "city"}, "E3": None}}), encoding="utf-8")
    assert load_geocoded(f) == {"E1": (1.0, 2.0)}


def _row(rid: str, name: str) -> dict:
    return {"evidence_id": rid, "charger_name": name, "operator": "", "lat": "", "lon": "", "place_url": ""}


def test_a_geocoded_row_matches_within_250_m_only_with_a_similar_name() -> None:
    directory = [rec("CH-0001", 26.9, 75.8).model_copy(update={"name": "Zeon Charging Highway Hub"})]
    near = (26.9 + 200 / 111_000, 75.8)  # ~200 m north
    far = (26.9 + 400 / 111_000, 75.8)   # ~400 m north
    m = ev.match_evidence([_row("E1", "Zeon Highway Hub"), _row("E2", "Zeon Highway Hub"),
                           _row("E3", "Visa office")], directory, {}, {"E1": near, "E2": far, "E3": near})
    assert m["E1"].charger_id == "CH-0001" and m["E1"].method == "geocoded_name"
    assert m["E2"].charger_id is None and "250 m" in m["E2"].reason
    assert m["E3"].charger_id is None and "similar name" in m["E3"].reason


def test_a_typed_location_beats_the_geocode() -> None:
    row = {**_row("E1", "x"), "lat": "10.0", "lon": "20.0"}
    assert ev.row_location(row, {"E1": (1.0, 2.0)}) == ((10.0, 20.0), "lat_lon")


# --- operator pooling ------------------------------------------------------------------------------------------------

def test_operator_key_drops_placeholders() -> None:
    assert operator_key("(Unknown Operator)") == "" and operator_key("") == "" and operator_key("Zeon") == "zeon"


def test_pooled_prior_shrinks_toward_the_operator_and_is_capped() -> None:
    p0 = prior_for("", "unknown")
    few, many = pooled_prior(p0, 0, 2), pooled_prior(p0, 0, 50)
    assert many.mean < few.mean < p0.mean  # more failing reports move it further
    assert many.a + many.b == pytest.approx(8.0)  # capped at MAX_STRENGTH
    assert pooled_prior(p0, 0, 0) == p0


def test_pool_uses_only_recent_real_reports() -> None:
    chargers = [rec("A", 1, 1, "Zeon"), rec("B", 2, 2, "Zeon")]
    reports = [rep("A", "broken"), rep("A", "working", sim=True), rep("A", "broken", days=200)]
    assert operator_pool(chargers, reports, NOW) == {"zeon": (0, 1)}


def test_own_reports_always_win_and_basis_is_named() -> None:
    chargers = [rec("A", 1, 1, "Zeon"), rec("B", 2, 2, "Zeon"), rec("C", 3, 3, "Other"), rec("D", 4, 4, "")]
    reports = [rep("A", "broken", src=f"s{i}") for i in range(4)]
    on = TrustEngine(chargers, use_rings=False, pool_operators=True).estimate(reports, NOW).chargers
    off = TrustEngine(chargers, use_rings=False, pool_operators=False).estimate(reports, NOW).chargers
    assert on["A"].basis == "own reports" and on["A"].prior_p == off["A"].prior_p  # own evidence untouched
    assert on["A"].p_works == pytest.approx(off["A"].p_works)
    assert on["B"].basis == "operator pool" and on["B"].pool_n == 4 and on["B"].prior_p < off["B"].prior_p
    assert on["C"].basis == "prior only" and on["D"].basis == "prior only"
    assert on["C"].prior_p == off["C"].prior_p


def test_simulated_reports_never_feed_a_pool() -> None:
    chargers = [rec("A", 1, 1, "Zeon"), rec("B", 2, 2, "Zeon")]
    on = TrustEngine(chargers, pool_operators=True).estimate([rep("A", "broken", sim=True)], NOW).chargers
    assert on["B"].basis == "prior only"


# --- the release (saved example == today's engine output, checked by test_snapshot) -----------------------------------

def test_every_stop_and_backup_names_its_trust_basis_and_a_refusal_lists_what_to_verify() -> None:
    for vid in ("V07", "V12", "V19"):
        body = json.loads((ROOT / "docs" / "api_examples" / f"dispatch_{vid}.json").read_text(encoding="utf-8"))
        for item in [*body["stops"], *body["alternates"]]:
            assert item["trust"]["basis"] in ("own reports", "operator pool", "prior only")
        if body["decision"] == "INSUFFICIENT_EVIDENCE" and "pessimistic reading" in (body["refusal_reason"] or ""):
            assert body["verify_chargers"], vid
            assert all(v["name"] in body["refusal_reason"] for v in body["verify_chargers"])
        else:
            assert body["verify_chargers"] == []
