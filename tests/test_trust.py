"""Trust engine: priors, Dawid-Skene source learning, ring detection, and recovery in a hostile world."""

from __future__ import annotations

from datetime import timedelta

import numpy as np
import pytest

from verity.schemas import ChargerRecord, Report
from verity.trust.engine import TrustEngine, trust_level
from verity.trust.priors import NATIONAL, prior_for, region_of
from verity.trust.reports_sim import NOW, WorldConfig, simulate_world
from verity.trust.rings import detect_rings
from verity.trust.source_trust import ReportSet, average_rating_baseline, fit_trust


def _world_error(world, **engine_kw) -> float:
    rep = TrustEngine(world.chargers, **engine_kw).estimate(world.reports, NOW, world.usage)
    est = np.array([rep.chargers[c.id].p_works for c in world.chargers])
    return float(np.mean(np.abs(est - world.true_r)))


# --- priors ---------------------------------------------------------------------------------------


def test_prior_defaults_to_the_national_figure_and_cites_its_sources() -> None:
    p = prior_for(None, None)
    assert p.mean == pytest.approx(NATIONAL.p_works)
    assert all("PLAN s3" in e.source and "approximate - verify" in e.source for e in p.evidence)


def test_operator_site_and_region_evidence_lower_the_prior_and_stay_bounded() -> None:
    base = prior_for("Some Network", "mall").mean
    assert prior_for("BPCL", "mall").mean < base
    assert prior_for("Some Network", "highway").mean < base
    assert prior_for("Some Network", "mall", "delhi").mean < base
    worst = prior_for("Bharat Petroleum", "highway", "delhi")
    assert 2.0 <= worst.a + worst.b <= 8.0 and 0.0 < worst.mean < 0.55


def test_region_of_separates_delhi_from_noida() -> None:
    assert region_of(28.61, 77.21) == "delhi"
    assert region_of(28.5355, 77.3910) is None  # Noida


# --- Dawid-Skene ------------------------------------------------------------------------------------


def test_honest_world_beats_the_prior_alone_and_learns_source_accuracy() -> None:
    # Each report is one Bernoulli sample of an intermittent charger, so ~9 reports/charger leaves an
    # irreducible error near 0.15; the claim is only that evidence clearly beats the prior.
    world = simulate_world(WorldConfig(seed=1, n_honest=80, reports_per_honest=(8, 20)))
    rep = TrustEngine(world.chargers, use_rings=False, use_usage=False).estimate(world.reports, NOW)
    est = np.array([rep.chargers[c.id].p_works for c in world.chargers])
    prior = np.array([rep.chargers[c.id].prior_p for c in world.chargers])
    assert np.mean(np.abs(est - world.true_r)) < 0.90 * np.mean(np.abs(prior - world.true_r))
    assert np.mean(list(rep.source_sens.values())) == pytest.approx(0.88, abs=0.10)


def test_a_lying_operator_feed_is_learned_and_does_not_distort_estimates() -> None:
    clean = simulate_world(WorldConfig(seed=2, n_honest=60, reports_per_honest=(6, 16)))
    lying = simulate_world(WorldConfig(seed=2, n_honest=60, reports_per_honest=(6, 16), operator_feed=True))
    rep = TrustEngine(lying.chargers, use_rings=False, use_usage=False).estimate(lying.reports, NOW)
    assert rep.source_spec["feed:OpD"] < 0.55  # says "working" regardless -> no evidence of catching failures
    err_clean = _world_error(clean, use_rings=False, use_usage=False)
    err_lying = _world_error(lying, use_rings=False, use_usage=False)
    assert err_lying < err_clean + 0.05  # a feed that claims "working" everywhere barely moves the estimates


def test_recent_reports_outweigh_old_ones() -> None:
    charger = ChargerRecord(id="C1", name="x", lat=28.5, lon=77.4, operator="X", site_type="mall")
    old_ok = [Report(charger_id="C1", source_id=f"u{i}", status="working", t=NOW - timedelta(days=120)) for i in range(4)]
    new_bad = [Report(charger_id="C1", source_id=f"v{i}", status="broken", t=NOW - timedelta(days=1)) for i in range(4)]
    rep = TrustEngine([charger], use_rings=False, use_usage=False).estimate(old_ok + new_bad, NOW)
    assert rep.chargers["C1"].p_works < 0.5


def test_no_reports_falls_back_to_the_prior_and_says_unknown() -> None:
    charger = ChargerRecord(id="C1", name="x", lat=28.5, lon=77.4, operator="BPCL", site_type="highway")
    t = TrustEngine([charger]).estimate([], NOW).chargers["C1"]
    assert t.p_works == pytest.approx(t.prior_p) and t.trust_level == "unknown" and t.n_reports == 0


def test_trust_levels_follow_report_count_and_recency() -> None:
    assert trust_level(0, None) == "unknown"
    assert trust_level(7, 3) == "high" and trust_level(3, 30) == "medium" and trust_level(1, 50) == "low"
    assert trust_level(7, 30) == "medium"  # many reports but stale


# --- ring detection ---------------------------------------------------------------------------------


def _rs(world) -> ReportSet:
    return ReportSet.from_reports(world.reports, [c.id for c in world.chargers], NOW)


def test_ring_detection_finds_the_ring_and_nobody_else() -> None:
    for seed in range(5):
        world = simulate_world(WorldConfig(seed=seed, ring_size=8, operator_feed=True))
        rs = _rs(world)
        flagged = set(detect_rings(rs).flagged_ids(rs.source_ids))
        assert flagged == world.ring_sources, (seed, flagged ^ world.ring_sources)


def test_honest_world_has_no_rings() -> None:
    for seed in range(5):
        rs = _rs(simulate_world(WorldConfig(seed=seed, n_honest=60, reports_per_honest=(6, 20))))
        assert detect_rings(rs).flagged == ()


def test_engine_beats_average_rating_in_a_hostile_world_and_ring_detection_matters() -> None:
    full, no_rings, base_err = [], [], []
    for seed in (3, 4, 5):  # averaged over worlds: a single world is dominated by sampling noise
        world = simulate_world(WorldConfig(seed=seed, n_honest=100, reports_per_honest=(6, 24), ring_size=12,
                                           ring_targets=30, operator_feed=True))
        full.append(_world_error(world))
        no_rings.append(_world_error(world, use_rings=False))
        baseline = average_rating_baseline(_rs(world), world.stars, 0.5)
        base_err.append(float(np.mean(np.abs(baseline - world.true_r))))
    assert np.mean(full) < 0.8 * np.mean(base_err)  # clearly better than the average star rating
    assert np.mean(full) < np.mean(no_rings)  # and ring detection is what helps


def test_estimation_is_deterministic() -> None:
    world = simulate_world(WorldConfig(seed=4, ring_size=5))
    assert _world_error(world) == _world_error(world)


def test_fit_trust_converges_and_probabilities_are_valid() -> None:
    world = simulate_world(WorldConfig(seed=5))
    rs = _rs(world)
    fit = fit_trust(rs, np.full(len(world.chargers), 4.0), np.full(len(world.chargers), 1.0))
    assert fit.n_iter < 300 and np.all((fit.r_mean > 0) & (fit.r_mean < 1))
    assert np.all((fit.sens > 0) & (fit.sens < 1) & (fit.spec > 0) & (fit.spec < 1))
