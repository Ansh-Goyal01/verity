"""WP5 honest bands: split conformal on REAL training trips, never on held-out ones, and how releases carry it."""

from __future__ import annotations

import csv
import json
import shutil
import sys
from pathlib import Path

import numpy as np
import pytest

from api.bands import BandCalibration, load_band_calibration
from verity import conformal as cf
from verity import honest_bands as hb
from verity import india_eval as ie
from verity.forecast import ForecastConfig, simulate_runs
from verity.paths import CORPUS_DIR, PROCESSED_DIR, SPLITS_DIR

ROOT = Path(__file__).resolve().parents[1]
CONFORMAL = PROCESSED_DIR / "conformal.json"
SPLIT = SPLITS_DIR / "india_trips_split.json"


# --- the conformal maths ------------------------------------------------------------------------------------


def test_conformal_quantile_uses_the_finite_sample_rank() -> None:
    scores = list(range(1, 10))  # n = 9: rank ceil(10 * 0.9) = 9 -> the largest
    assert cf.conformal_quantile(scores) == 9
    assert cf.conformal_quantile(scores[:8]) is None  # n = 8: no finite 90 % band
    assert cf.conformal_quantile(list(range(1, 20))) == 18  # n = 19: rank 18


def test_a_calibrated_model_scores_about_one_and_widening_keeps_the_median() -> None:
    rng = np.random.default_rng(0)
    s = rng.normal(50.0, 5.0, 20000)
    obs = rng.normal(50.0, 5.0, 2000)
    k = cf.conformal_quantile([cf.score(s, o) for o in obs])
    assert k == pytest.approx(1.0, abs=0.06)
    w = cf.widen(s, 2.0)
    assert np.median(w) == pytest.approx(np.median(s)) and cf.half_width(w) == pytest.approx(2 * cf.half_width(s))
    assert cf.applied_scale(0.8) == 1.0 and cf.applied_scale(1.3) == 1.3 and cf.applied_scale(None) == 1.0


def test_the_correction_target_is_an_energy_ratio_for_every_record_type() -> None:
    assert cf.energy_log_ratio("consumption", 150.0, 100.0) == pytest.approx(np.log(1.5))
    assert cf.energy_log_ratio("range_test", 200.0, 300.0) == pytest.approx(np.log(1.5))  # less range = more energy
    s = np.array([100.0, 110.0])
    assert cf.apply_correction("range_test", s, np.log(1.5)) == pytest.approx(s / 1.5)
    assert cf.apply_correction("soc_trip", s, np.log(1.5)) == pytest.approx(s * 1.5)


def test_band_scale_widens_the_monte_carlo_around_its_median() -> None:
    from tests.test_dispatch import ROUTE, vehicle

    v = vehicle()
    base = simulate_runs(v.post, ROUTE, mass_kg=1700.0, ac_on=True, rng=np.random.default_rng(3),
                         cfg=ForecastConfig(n_runs=500))
    wide = simulate_runs(v.post, ROUTE, mass_kg=1700.0, ac_on=True, rng=np.random.default_rng(3),
                         cfg=ForecastConfig(n_runs=500, band_scale=1.5))
    assert np.median(wide.total_used) == pytest.approx(np.median(base.total_used))
    assert cf.half_width(wide.total_used) == pytest.approx(1.5 * cf.half_width(base.total_used), rel=1e-6)


# --- the calibration set never sees held-out data -----------------------------------------------------------


def _cases():
    sys.path.insert(0, str(ROOT / "experiments"))
    from e1_india_real import ENRICHED, load_cases

    return load_cases(ENRICHED, CORPUS_DIR)[0]


def test_calibration_set_is_training_split_only() -> None:
    split = json.loads(SPLIT.read_text(encoding="utf-8"))["assignments"]
    cases = _cases()
    cal = hb.calibration_cases(cases, split)
    assert cal and all(split[c.group] == "train" for c in cal)
    assert not {c.record_id for c in cal} & {c.record_id for c in hb.heldout_cases(cases, split)}


@pytest.mark.skipif(not CONFORMAL.exists(), reason="conformal.json not built yet")
def test_conformal_file_holds_no_heldout_or_bmw_trip_and_k_is_reproducible() -> None:
    doc = json.loads(CONFORMAL.read_text(encoding="utf-8"))
    split = json.loads(SPLIT.read_text(encoding="utf-8"))["assignments"]
    with (CORPUS_DIR / "trips_india.csv").open(encoding="utf-8") as f:
        india_ids = {r["record_id"] for r in csv.DictReader(f)}
    rows = doc["rows"] + doc["correction"]["rows"]
    assert {r["record_id"] for r in rows} <= india_ids  # only India corpus rows: no BMW trip, no VED trip
    assert all(split[r["group"]] == "train" for r in rows)
    assert doc["n"] == len(doc["rows"])
    assert cf.conformal_quantile([r["score"] for r in doc["rows"]]) == pytest.approx(doc["k"], abs=1e-5)
    assert cf.conformal_quantile([r["score"] for r in doc["correction"]["rows"]]) == pytest.approx(
        doc["correction"]["k"], abs=1e-5)
    assert doc["k_applied"] == cf.applied_scale(doc["k"]) and doc["correction"]["in_releases"] is False


def test_a_trip_is_never_predicted_from_its_own_source_page(monkeypatch: pytest.MonkeyPatch) -> None:
    cases = _cases()
    seen = []
    monkeypatch.setattr(ie, "calibrated_posterior", lambda pool: seen.append(list(pool)) or None)
    target = cases[0]
    hb.posterior_for(target, cases)
    assert seen and all(c.group != target.group for c in seen[0])


def test_the_bmw_heldout_split_is_not_reachable_from_the_calibration_code() -> None:
    src = (ROOT / "engine" / "verity" / "honest_bands.py").read_text(encoding="utf-8") + (
        ROOT / "experiments" / "e7_honest_bands.py").read_text(encoding="utf-8")
    assert "import bmw" not in src and "datasets" not in src and "load_split_part" not in src


# --- releases ---------------------------------------------------------------------------------------------------


def test_band_blocks_for_real_cars_and_demo_cars(tmp_path: Path) -> None:
    cal = BandCalibration(71, 0.95, 1.0, ("trips_india.csv training split",), "2026-11-21T00:00:00+00:00")
    real, demo = cal.block(vehicle_simulated=False), cal.block(vehicle_simulated=True)
    assert real["name"] == "split conformal" and real["n"] == 71 and "never narrowed" in real["note"]
    assert "not a guarantee for any single trip" in real["note"] and "guaranteed" not in real["note"].lower()
    assert demo["name"] == "model only" and demo["n"] is None and cal.scale_for(vehicle_simulated=True) == 1.0
    wide = BandCalibration(71, 1.4, 1.4, (), None)
    assert wide.scale_for(vehicle_simulated=False) == 1.4
    assert "widened by 1.40" in wide.block(vehicle_simulated=False)["note"]
    assert load_band_calibration(tmp_path / "missing.json").block(vehicle_simulated=False)["name"] == "model only"
    (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
    assert load_band_calibration(tmp_path / "bad.json").k_applied == 1.0


@pytest.fixture(scope="module")
def svc(tmp_path_factory: pytest.TempPathFactory):
    from api.service import EngineService
    from tests.test_any_route import FIXTURES, Offline, replay_factory

    cache = tmp_path_factory.mktemp("route_cache_wp5")
    for f in FIXTURES.glob("*.json"):
        shutil.copy2(f, cache / f.name)
    return EngineService(n_runs=300, graph_backend="memory", route_client=replay_factory(cache, Offline()))


def test_every_release_says_how_its_bands_were_made(svc) -> None:
    from api.service import demo_dispatch_request
    from tests.test_any_route import trip

    demo = svc.dispatch(demo_dispatch_request("V12"))
    assert demo["band_method"]["name"] == "model only"
    car = next(iter(svc.catalog))
    real = svc.dispatch(trip(vehicle_id=car))
    expected = "split conformal" if svc.bands.available else "model only"
    assert real["band_method"]["name"] == expected
    if svc.bands.available:
        assert real["band_method"]["n"] == svc.bands.n
    health = svc.health()["bands"]
    assert health["method"] == expected and health["k_applied"] >= 1.0
