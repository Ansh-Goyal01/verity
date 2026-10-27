"""Physics sanity tests (CLAUDE.md) plus hand calculations. All inputs are synthetic."""

from __future__ import annotations

import numpy as np
import pytest

from verity.physics import (
    G,
    TERMS,
    Segments,
    VehicleParams,
    air_density,
    energy_terms,
    joules_to_soc_pct,
    total_energy_j,
    total_mass_kg,
    trip_soc_drop_pct,
)
from verity.vehicles import VEHICLE_CLASSES, N_THETA, classify, get_prior, to_params, to_physical

CAR = VEHICLE_CLASSES["compact_esuv"].nominal_params()
MASS = total_mass_kg(CAR.curb_mass_kg, passengers=2, cargo_kg=0.0)


def _flat(speed_mps: float, distance_m: float = 10_000.0, temp_c: float = 25.0, n: int = 10) -> Segments:
    length = np.full(n, distance_m / n)
    return Segments.build(length, np.full(n, speed_mps), temp_c=temp_c)


def _total(seg: Segments, params: VehicleParams = CAR, **kw) -> float:
    kw = {"mass_kg": MASS, "ac_on": False, **kw}
    return float(total_energy_j(energy_terms(params, seg, **kw)))


def test_zero_distance_means_zero_energy() -> None:
    seg = Segments.build(np.zeros(3), np.zeros(3))
    terms = energy_terms(CAR, seg, mass_kg=MASS, ac_on=True)
    assert all(np.all(t == 0.0) for t in terms.values())
    empty = Segments.build(np.zeros(0), np.zeros(0))
    assert float(total_energy_j(energy_terms(CAR, empty, mass_kg=MASS, ac_on=True))) == 0.0


def test_more_payload_means_more_energy() -> None:
    seg = _flat(25.0)
    light, heavy = _total(seg, mass_kg=MASS), _total(seg, mass_kg=MASS + 300.0)
    assert heavy > light


def test_ac_on_means_more_aux_energy() -> None:
    seg = _flat(25.0, temp_c=38.0)
    off = energy_terms(CAR, seg, mass_kg=MASS, ac_on=False)["aux"].sum()
    on = energy_terms(CAR, seg, mass_kg=MASS, ac_on=True)["aux"].sum()
    assert off == 0.0 and on > 0.0


def test_regen_never_exceeds_available_braking_energy() -> None:
    rng = np.random.default_rng(0)
    n = 400
    seg = Segments.build(
        rng.uniform(50, 500, n),
        rng.uniform(1, 30, n),
        accel_mps2=rng.uniform(-4, 1, n),
        grade_rad=rng.uniform(-0.12, 0.05, n),
    )
    terms = energy_terms(CAR, seg, mass_kg=MASS, ac_on=False)
    recovered_j = -terms["regen"]  # >= 0
    assert np.all(recovered_j >= 0)
    # wheel-level power in each step
    cos_t, sin_t = np.cos(seg.grade_rad), np.sin(seg.grade_rad)
    f = (CAR.crr * seg.surface * MASS * G * cos_t + MASS * G * sin_t
         + 0.5 * air_density(seg.temp_c) * CAR.cda_m2 * seg.v_mps**2
         + CAR.dyn_k * MASS * (1 + CAR.rot_inertia_delta) * seg.accel_mps2)
    p_wheel = f * seg.v_mps
    braking_j = np.maximum(-p_wheel, 0.0) * seg.dt_s
    assert np.all(recovered_j <= CAR.eta_regen * braking_j + 1e-6)
    assert np.all(recovered_j <= CAR.max_regen_kw * 1000.0 * seg.dt_s + 1e-6)
    assert recovered_j.sum() > 0  # the random trip does include regeneration


def test_flat_constant_speed_matches_hand_calculation() -> None:
    v, d = 20.0, 10_000.0
    seg = _flat(v, d, temp_c=25.0)
    rho = 101325.0 / (287.05 * (25.0 + 273.15))
    force = CAR.crr * MASS * G + 0.5 * rho * CAR.cda_m2 * v**2
    dt = d / v
    expected = force * d / CAR.eta_drive + CAR.base_w * dt
    assert _total(seg) == pytest.approx(expected, rel=1e-9)


def test_energy_per_km_rises_with_speed_above_30_kmh() -> None:
    per_km = [_total(_flat(kmh / 3.6)) / 10.0 for kmh in (40, 60, 80, 100, 120)]
    assert all(b > a for a, b in zip(per_km, per_km[1:]))


def test_terms_sum_to_piecewise_battery_power_integral() -> None:
    rng = np.random.default_rng(1)
    n = 200
    seg = Segments.build(
        rng.uniform(50, 400, n), rng.uniform(2, 28, n),
        accel_mps2=rng.uniform(-2.5, 1.5, n), grade_rad=rng.uniform(-0.08, 0.08, n),
        temp_c=rng.uniform(10, 40, n), headwind_mps=rng.uniform(-3, 5, n),
    )
    terms = energy_terms(CAR, seg, mass_kg=MASS, ac_on=True)
    total = sum(float(t.sum()) for t in terms.values())
    assert set(terms) == set(TERMS)
    expected = 0.0
    for i in range(n):  # explicit scalar re-implementation of the CLAUDE.md formulas
        rho = air_density(seg.temp_c[i])
        f = (CAR.crr * seg.surface[i] * MASS * G * np.cos(seg.grade_rad[i])
             + MASS * G * np.sin(seg.grade_rad[i])
             + 0.5 * rho * CAR.cda_m2 * (seg.v_mps[i] + seg.headwind_mps[i]) ** 2
             + CAR.dyn_k * MASS * (1 + CAR.rot_inertia_delta) * seg.accel_mps2[i])
        p_wheel = f * seg.v_mps[i]
        p_batt = p_wheel / CAR.eta_drive if p_wheel >= 0 else max(p_wheel * CAR.eta_regen, -CAR.max_regen_kw * 1000)
        p_aux = CAR.aux_w_per_k * abs(seg.temp_c[i] - 22.0)
        expected += (p_batt + p_aux + CAR.base_w) * seg.dt_s[i]
    assert total == pytest.approx(expected, rel=1e-9)


def test_uphill_costs_more_downhill_less_and_headwind_costs_more() -> None:
    flat = _total(_flat(25.0))
    up = _total(Segments.build(np.full(10, 1000.0), np.full(10, 25.0), grade_rad=0.03))
    down = _total(Segments.build(np.full(10, 1000.0), np.full(10, 25.0), grade_rad=-0.03))
    wind = _total(Segments.build(np.full(10, 1000.0), np.full(10, 25.0), headwind_mps=6.0))
    assert down < flat < up
    assert wind > flat


def test_monte_carlo_broadcasting_matches_scalar_loop() -> None:
    prior = get_prior("compact_esuv")
    rng = np.random.default_rng(2)
    u = prior.u_mean + rng.normal(0, prior.u_sd, size=(5, N_THETA))
    seg = _flat(27.0, temp_c=35.0)
    batch = to_params(prior.base, prior.nominal_kwh, u)
    soc = trip_soc_drop_pct(batch, seg, mass_kg=MASS, ac_on=True)
    assert soc.shape == (5,)  # one value per run, never an (N, N) cross product
    for i in range(5):
        single = to_params(prior.base, prior.nominal_kwh, u[i])
        assert float(trip_soc_drop_pct(single, seg, mass_kg=MASS, ac_on=True)) == pytest.approx(
            float(soc[i]), rel=1e-9
        )


def test_per_term_soc_breakdown_has_one_value_per_run_and_sums_to_total() -> None:
    from verity.physics import soc_drop_by_term_pct

    prior = get_prior("compact_esuv")
    u = prior.u_mean + np.random.default_rng(4).normal(0, prior.u_sd, size=(7, N_THETA))
    params = to_params(prior.base, prior.nominal_kwh, u)
    seg = _flat(27.0, temp_c=35.0)
    terms = energy_terms(params, seg, mass_kg=MASS, ac_on=True)
    by_term = soc_drop_by_term_pct(terms, params.usable_kwh)
    assert all(v.shape == (7,) for v in by_term.values())
    total = trip_soc_drop_pct(params, seg, mass_kg=MASS, ac_on=True)
    assert sum(by_term.values()) == pytest.approx(total, rel=1e-9)


def test_segments_validation_rejects_bad_input() -> None:
    with pytest.raises(ValueError):
        Segments.build(np.array([-1.0]), np.array([10.0]))
    with pytest.raises(ValueError):
        Segments.build(np.array([100.0]), np.array([10.0]), surface=0.5)
    with pytest.raises(ValueError):
        Segments.build(np.array([100.0, 100.0]), np.array([10.0, np.nan]))


def test_joules_to_soc_is_linear_in_energy_and_inverse_in_capacity() -> None:
    assert joules_to_soc_pct(3.6e6, 1.0) == pytest.approx(100.0)
    assert joules_to_soc_pct(3.6e6, 40.0) == pytest.approx(2.5)


# --- vehicle priors -----------------------------------------------------------------------------


def test_three_generic_classes_exist_and_are_labelled_generic() -> None:
    assert set(VEHICLE_CLASSES) == {"small_ecar", "compact_esuv", "e_scooter"}
    for prior in VEHICLE_CLASSES.values():
        assert np.all(prior.u_sd > 0)
        assert prior.u_mean.shape == (N_THETA,)


def test_prior_median_reproduces_base_parameters() -> None:
    prior = get_prior("compact_esuv")
    phys = to_physical(prior.u_mean)
    assert phys["cda_mult"] == pytest.approx(1.0) and phys["k"] == pytest.approx(1.0)
    assert phys["eta_drive"] == pytest.approx(prior.base.eta_drive)
    assert phys["usable_frac"] == pytest.approx(0.93)


def test_parameter_draws_stay_inside_physical_bounds() -> None:
    prior = get_prior("small_ecar")
    u = prior.u_mean + np.random.default_rng(3).normal(0, 4 * prior.u_sd, size=(2000, N_THETA))
    phys = to_physical(u)
    assert np.all((phys["eta_drive"] > 0.60) & (phys["eta_drive"] < 0.98))
    assert np.all((phys["usable_frac"] > 0.50) & (phys["usable_frac"] < 1.05))


def test_classify_picks_scooter_by_type_and_cars_by_battery_size() -> None:
    assert classify("2w", 3.0).key == "e_scooter"
    assert classify("car", 24.0).key == "small_ecar"
    assert classify("car", 50.0).key == "compact_esuv"


@pytest.mark.parametrize(
    "key, kmh, low, high",
    [("small_ecar", 90, 80, 220), ("compact_esuv", 90, 100, 260), ("e_scooter", 40, 10, 60)],
)
def test_energy_per_km_is_physically_plausible_for_each_class(key: str, kmh: int, low: float, high: float) -> None:
    prior = get_prior(key)
    params = prior.nominal_params()
    mass = total_mass_kg(prior.base.curb_mass_kg, passengers=1, cargo_kg=0.0)
    seg = _flat(kmh / 3.6)
    wh_per_km = float(total_energy_j(energy_terms(params, seg, mass_kg=mass, ac_on=False))) / 3600.0 / 10.0
    assert low < wh_per_km < high
