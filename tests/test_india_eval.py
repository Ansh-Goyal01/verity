"""F4 E1-India: fixed source-grouped split, planner-path predictions, baselines and honest labelling.

All records here are SYNTHETIC test inputs built in the test; nothing is written outside tmp_path.
"""

from __future__ import annotations

import csv
import json

import numpy as np
import pytest

import experiments.e1_india_real as e1
from verity import india_eval as ie
from verity.corpus import VEHICLES

VEHICLE = {"vehicle_id": "test-ev-40", "make": "Test", "model": "EV", "variant": "40", "vehicle_type": "car",
           "battery_nominal_kwh": "40.5", "battery_basis": "unstated", "battery_source_url": "https://example.org/v",
           "curb_mass_kg_low": "1450", "curb_mass_kg_high": "1550", "curb_mass_source_url": "https://example.org/v",
           "claimed_range_km": "437", "range_test_cycle": "ARAI", "range_source_url": "https://example.org/v",
           "source_quality": "secondary", "engine_prior_class": "compact_esuv", "notes": "approximate - verify"}


def _v(x):
    return {"value": x, "provenance": "reported" if x is not None else "missing: test"}


def record(rid: str, rtype: str = "soc_trip", url: str | None = None, **reported) -> dict:
    fields = {"distance_km": None, "start_soc_pct": None, "end_soc_pct": None, "energy_wh_per_km": None,
              "speed_band": None, "ac": None, "passengers": None}
    fields.update(reported)
    return {"record_id": rid, "record_type": rtype, "source_url": url or f"https://example.org/{rid}",
            "vehicle_id": {"value": "test-ev-40", "provenance": "test"}, "is_simulated": True,
            "reported": {k: _v(v) for k, v in fields.items()}, "weather": {"value": {"temperature_c": 30.0}}}


def case(rid: str = "R1", rtype: str = "soc_trip", **reported) -> ie.Case:
    c, reason = ie.case_from_record(record(rid, rtype, **reported), {"test-ev-40": VEHICLE})
    assert c is not None, reason
    return c


def _width(samples: np.ndarray) -> float:
    lo, hi = np.quantile(samples, [0.05, 0.95])
    return float(hi - lo)


# ---------------------------------------------------------------- split
def test_source_group_ignores_www_query_fragment_and_trailing_slash():
    assert ie.source_group("https://www.Example.org/a/b/?x=1#c") == ie.source_group("http://example.org/a/b")


def test_split_assignments_are_frozen_and_never_rewritten(tmp_path):
    path = tmp_path / "split.json"
    first = ie.update_split(path, {"a.org/1", "a.org/2"})["assignments"]
    doc = json.loads(path.read_text(encoding="utf-8"))
    flipped = {g: ("test" if s == "train" else "train") for g, s in first.items()}
    path.write_text(json.dumps({**doc, "assignments": flipped}), encoding="utf-8")
    again = ie.update_split(path, {"a.org/1", "a.org/2", "a.org/3"})["assignments"]
    assert {g: again[g] for g in flipped} == flipped  # existing entries kept even if they disagree with the rule
    assert "a.org/3" in again


def test_split_rule_change_is_refused(tmp_path):
    path = tmp_path / "split.json"
    ie.update_split(path, {"a.org/1"})
    doc = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps({**doc, "test_fraction": 0.5}), encoding="utf-8")
    with pytest.raises(ValueError, match="fixed"):
        ie.update_split(path, {"a.org/1"})


def test_hash_rule_puts_roughly_30_percent_in_test():
    share = np.mean([ie.assign_group(f"example.org/{i}") == "test" for i in range(2000)])
    assert 0.27 < share < 0.33


# ---------------------------------------------------------------- planner-path predictions
def test_soc_used_grows_with_distance():
    rng = np.random.default_rng(0)
    short = case("S", distance_km=50.0, start_soc_pct=90, end_soc_pct=70, ac="on", passengers=2, speed_band="mixed")
    long = case("L", distance_km=150.0, start_soc_pct=90, end_soc_pct=30, ac="on", passengers=2, speed_band="mixed")
    a = ie.predictive_samples(short, ie.prior_posterior(short), rng, 400)
    b = ie.predictive_samples(long, ie.prior_posterior(long), rng, 400)
    assert np.median(b) > 2.5 * np.median(a)


def test_unknown_context_widens_the_band():
    known = case("K", "consumption", energy_wh_per_km=150.0, ac="on", passengers=2, speed_band="highway_80_100")
    unknown = case("U", "consumption", energy_wh_per_km=150.0)
    a = ie.predictive_samples(known, ie.prior_posterior(known), np.random.default_rng(1), 800)
    b = ie.predictive_samples(unknown, ie.prior_posterior(unknown), np.random.default_rng(1), 800)
    assert _width(b) > _width(a)
    assert 60.0 < np.median(a) < 300.0  # physically plausible Wh/km for a ~1.6 t car at 90 km/h


def test_range_test_predicts_kilometres_below_the_route_length():
    c = case("RT", "range_test", distance_km=300.0, start_soc_pct=100, end_soc_pct=5, speed_band="mixed")
    km = ie.predictive_samples(c, ie.prior_posterior(c), np.random.default_rng(2), 400)
    assert 100.0 < np.median(km) < ie.RANGE_ROUTE_FACTOR * 437.0


def test_range_test_without_both_soc_ends_is_not_evaluable():
    c, reason = ie.case_from_record(record("X", "range_test", distance_km=293.3, end_soc_pct=1),
                                    {"test-ev-40": VEHICLE})
    assert c is None and reason == "observation incomplete"


def test_calibration_moves_predictions_towards_training_reports():
    kw = dict(distance_km=100.0, start_soc_pct=90, end_soc_pct=40, ac="on", passengers=2, speed_band="highway_80_100")
    hungry = [case(f"T{i}", **kw) for i in range(4)]
    target = case("H", **kw)
    prior = ie.predictive_samples(target, ie.prior_posterior(target), np.random.default_rng(3), 400)
    post = ie.calibrated_posterior(hungry)
    assert post is not None and ie.calibrated_posterior(hungry[:2]) is None
    calibrated = ie.predictive_samples(target, post, np.random.default_rng(3), 400)
    assert abs(np.median(calibrated) - 50.0) < abs(np.median(prior) - 50.0)


# ---------------------------------------------------------------- baselines and scoring
def test_claimed_range_baselines():
    c = case(distance_km=100.0, start_soc_pct=90, end_soc_pct=60)
    assert ie.claimed_point(c, 1.0) == pytest.approx(100.0 * 100.0 / 437.0)
    assert ie.claimed_point(c, 0.75) == pytest.approx(100.0 * 100.0 / (437.0 * 0.75))


def test_regression_refuses_to_fit_on_too_few_records():
    few = [case(f"T{i}", distance_km=100.0, start_soc_pct=90, end_soc_pct=60) for i in range(6)]
    assert ie.fit_regression(few) is None


def test_score_reports_mae_coverage_and_wilson():
    c = case(distance_km=100.0, start_soc_pct=90, end_soc_pct=60)  # observed 30
    out = ie.score([c], {"R1": np.linspace(20.0, 40.0, 101)})
    assert out["mae_by_type"]["soc_trip"]["mae"] == pytest.approx(0.0)
    assert out["coverage"]["90%"]["k"] == 1 and out["coverage"]["90%"]["wilson95"][1] == 1.0
    assert ie.score([c], {"R1": 31.0})["coverage"] is None  # a point prediction has no band


# ---------------------------------------------------------------- end-to-end labelling
def _setup(tmp_path, records):
    (tmp_path / "corpus").mkdir()
    with (tmp_path / "corpus" / VEHICLES.filename).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=VEHICLES.header)
        writer.writeheader()
        writer.writerow({c: VEHICLE.get(c, "") for c in VEHICLES.header})
    (tmp_path / "enriched.json").write_text(json.dumps({"records": records}), encoding="utf-8")


def test_no_records_gives_not_run(tmp_path):
    _setup(tmp_path, [])
    result = e1.run(tmp_path / "enriched.json", tmp_path / "corpus", tmp_path / "split.json", n_runs=200)
    assert result["status"] == "NOT_RUN" and result["label"].endswith("n=0") and result["is_simulated"] is False


def test_small_held_out_set_is_preliminary_and_sources_never_straddle(tmp_path):
    urls = (f"https://example.org/s{i}" for i in range(60))
    test_urls = [u for u in urls if ie.assign_group(ie.source_group(u)) == "test"]
    records = [record(f"R{i}", url=test_urls[i % 2], distance_km=80.0, start_soc_pct=90, end_soc_pct=65, ac="on")
               for i in range(4)]
    _setup(tmp_path, records)
    result = e1.run(tmp_path / "enriched.json", tmp_path / "corpus", tmp_path / "split.json", n_runs=200)
    assert result["status"] == "PRELIMINARY" and result["counts"]["test"] == 4
    assert result["counts"]["source_groups_test"] == 2 and result["counts"]["train"] == 0
    assert result["methods"]["verity_prior"]["coverage"]["90%"]["n"] == 4
    assert result["methods"]["verity_calibrated"]["coverage"] is None  # no training data: no calibration claim
    e1.plot(result, tmp_path / "plot.png")
    assert (tmp_path / "plot.png").stat().st_size > 0
