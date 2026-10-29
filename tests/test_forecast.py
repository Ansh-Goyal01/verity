"""Monte Carlo forecast: shared vs independent factors, levers, live update, rounding. Synthetic routes."""

from __future__ import annotations

import numpy as np
import pytest

from verity.calibrate import calibrate
from verity.forecast import (
    ForecastConfig,
    build_route,
    live_update,
    round_prob,
    simulate_runs,
)
from verity.physics import total_mass_kg
from verity.vehicles import get_prior

PRIOR = get_prior("compact_esuv")
POST = calibrate(PRIOR.u_mean, PRIOR.u_cov, [], PRIOR.base, PRIOR.nominal_kwh)  # class prior only
WEATHER = {"temperature_c": 33.0, "wind_speed_mps": 3.0, "wind_from_deg": 90.0}
FLAT = {"step_m": 100.0, "z_m": [0.0] * 4000}
MASS = total_mass_kg(PRIOR.base.curb_mass_kg, passengers=2, cargo_kg=0.0)


def route(km: float = 200.0, kind: int = 0, weather: dict = WEATHER, elevation: dict = FLAT):
    return build_route("car", km, np.array([km * 1000.0 + 1000.0]), np.array([kind]), elevation, weather, 0.0)


def runs(rt=None, *, seed: int = 0, n: int = 1500, shared: bool = True, **kw):
    return simulate_runs(POST, rt or route(), mass_kg=MASS, ac_on=True, rng=np.random.default_rng(seed),
                         cfg=ForecastConfig(n_runs=n), shared=shared, **kw)


def test_same_seed_reproduces_and_different_seed_differs() -> None:
    a, b, c = runs(seed=3), runs(seed=3), runs(seed=4)
    np.testing.assert_array_equal(a.soc_used_cum, b.soc_used_cum)
    assert not np.array_equal(a.soc_used_cum, c.soc_used_cum)


def test_shared_factors_keep_long_trips_honestly_uncertain() -> None:
    shared, indep = runs(shared=True), runs(shared=False)
    # same per-segment marginal spread (first kilometre) ...
    first = (shared.soc_used_cum[:, 0].std(), indep.soc_used_cum[:, 0].std())
    assert first[0] == pytest.approx(first[1], rel=0.35)
    # ... but over 200 independent segments the strawman averages the uncertainty away
    assert shared.total_used.std() > 3.0 * indep.total_used.std()
    assert shared.total_used.mean() == pytest.approx(indep.total_used.mean(), rel=0.12)


def test_confidence_rises_with_start_soc_and_terms_sum_to_total() -> None:
    r = runs(route(120.0))
    med = float(np.median(r.total_used))
    # an uncalibrated vehicle (class prior only) is honestly wide, so the margins vs the 10 % reserve are large
    conf = [r.confidence(med + 10.0 + off) for off in (-25, -8, 0, 8, 25)]
    assert conf == sorted(conf) and conf[0] < 0.1 and conf[-1] > 0.9
    summed = sum(r.terms_soc.values())
    np.testing.assert_allclose(summed, r.total_used, rtol=1e-9, atol=1e-9)


def test_payload_heat_and_speed_lever_move_energy_the_right_way() -> None:
    base = runs(seed=1).total_used.mean()
    heavy = simulate_runs(POST, route(), mass_kg=MASS + 400, ac_on=True, rng=np.random.default_rng(1),
                          cfg=ForecastConfig(n_runs=1500)).total_used.mean()
    no_ac = simulate_runs(POST, route(), mass_kg=MASS, ac_on=False, rng=np.random.default_rng(1),
                          cfg=ForecastConfig(n_runs=1500)).total_used.mean()
    slow = runs(seed=1, highway_speed_mps=22.0).total_used.mean()
    assert heavy > base > no_ac
    assert slow < base  # cruising slower on the highway saves energy


def test_stop_and_go_energy_appears_only_in_congestion_prone_stretches() -> None:
    arterial = runs(route(60.0, kind=1)).terms_soc["inertia"].mean()
    jam = runs(route(60.0, kind=2)).terms_soc["inertia"].mean()
    assert arterial == pytest.approx(0.0, abs=1e-9)
    assert jam > 0.5  # SoC points lost to cycling kinetic energy over 60 km of stop-and-go


def test_used_at_km_interpolates_and_reaches_the_total() -> None:
    r = runs(route(50.0))
    np.testing.assert_allclose(r.used_at_km(0.0), 0.0)
    np.testing.assert_allclose(r.used_at_km(50.0), r.total_used)
    half = r.used_at_km(25.0)
    assert np.all((half > 0) & (half < r.total_used))
    np.testing.assert_allclose(r.used_at_km(999.0), r.total_used)  # clipped to the route end


def test_live_update_reacts_to_a_battery_reading() -> None:
    r = runs(route(150.0), n=3000)
    # Put median arrival at the 10 % reserve so confidence does not saturate near 1.
    start = float(np.median(r.total_used)) + 10.0
    assert start < 100.0
    expected = start - float(np.median(r.used_at_km(100.0)))
    base = live_update(r, start, 100.0, expected)
    faster = live_update(r, start, 100.0, expected - 5.0)  # draining faster than planned
    slower = live_update(r, start, 100.0, expected + 5.0)
    assert faster.confidence < base.confidence < slower.confidence
    assert faster.arrival_p50 < base.arrival_p50 < slower.arrival_p50
    assert base.arrival_p50 == pytest.approx(start - float(np.median(r.total_used)), abs=1.5)
    assert min(base.ess, faster.ess, slower.ess) >= 30  # never trusts a handful of runs


def test_round_prob_is_nearest_five_percent() -> None:
    assert [round_prob(p) for p in (0.0, 0.02, 0.03, 0.83, 0.9, 0.971, 0.98, 1.0)] == [
        0.0, 0.0, 0.05, 0.85, 0.9, 0.95, 1.0, 1.0]


def test_build_route_cuts_segments_assigns_road_classes_and_grade() -> None:
    elev = {"step_m": 100.0, "z_m": [0.1 * i * 100 for i in range(4000)]}  # 10 % climb
    rt = build_route("car", 10.5, np.array([4000.0, 12000.0]), np.array([0, 2]), elev, WEATHER, 0.0)
    assert rt.n == 11 and rt.distance_km == pytest.approx(10.5)
    assert list(rt.road_class[:3]) == [0, 0, 0] and rt.road_class[-1] == 2
    assert np.all(rt.grade_rad[1:-2] > 0.09)  # the last interior segment is smoothed with the route end
    assert rt.headwind_mps[0] == pytest.approx(3.0 * np.cos(np.radians(90.0)), abs=1e-9)
