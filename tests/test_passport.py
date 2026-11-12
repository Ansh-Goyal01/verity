"""F9 Range Passport: certificate structure and honesty labels, and the BMW i3 adapter's ground truth and dashboard
readings. Synthetic in-test data only (no raw BMW files, no network)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from verity.datasets.bmw_i3 import BmwTrip
from verity.datasets.bmw_passport import i3_prior, measured, observation, truth_kwh_per_100, usable_for_passport
from verity.forecast import ForecastConfig, PlannedRoute
from verity.passport import SCHEMA, ReferenceRoute, certificate, markdown
from verity.sim import load_manifest
from verity.trips import load_sim_observation
from verity.vehicles import get_prior

CFG = ForecastConfig(n_runs=400)
FLAT = ReferenceRoute("flat", "Flat test road", PlannedRoute("car", np.full(50, 1000.0), np.zeros(50, dtype=int),
                                                             np.zeros(50), np.full(50, 32.0), np.zeros(50)), "synthetic")


def demo_obs(k: int) -> list:
    veh = load_manifest("demo")["vehicles"][1]
    return [load_sim_observation("demo", t) for t in veh["trip_ids"][:k]]


def test_certificate_has_capacity_intervals_range_and_labels() -> None:
    cert = certificate({"id": "X"}, get_prior("compact_esuv"), demo_obs(4), [FLAT], label="SIMULATED: test", cfg=CFG)
    cap = cert["capacity"]
    assert cert["schema"] == SCHEMA and cert["is_simulated"] and cert["status"] == "OK"
    assert cap["ci95_kwh"][0] <= cap["ci80_kwh"][0] <= cap["usable_kwh_p50"] <= cap["ci80_kwh"][1] <= cap["ci95_kwh"][1]
    r = cert["range_in_indian_conditions"][0]
    assert 0 < r["range_km"]["p10"] <= r["range_km"]["p50"] <= r["range_km"]["p90"]
    assert cert["evidence"]["trips"] == 4 and cert["evidence"]["km_logged"] > 0
    md = markdown(cert)
    assert "SIMULATED: test" in md and "Flat test road" in md and "Not a warranty" in md


def test_few_trips_make_a_preliminary_passport_with_a_wider_interval() -> None:
    prior = get_prior("compact_esuv")
    one = certificate({"id": "X"}, prior, demo_obs(1), [FLAT], label="SIMULATED: test", cfg=CFG)
    six = certificate({"id": "X"}, prior, demo_obs(6), [FLAT], label="SIMULATED: test", cfg=CFG)

    def width(c: dict) -> float:
        return c["capacity"]["ci80_kwh"][1] - c["capacity"]["ci80_kwh"][0]

    assert one["status"] == "PRELIMINARY" and six["status"] == "OK" and width(six) < width(one)


def test_real_trips_are_not_labelled_simulated() -> None:
    cert = certificate({"id": "X"}, get_prior("compact_esuv"), demo_obs(3), [FLAT], label="REAL trips (test)", cfg=CFG)
    assert cert["is_simulated"] is False


# --- BMW adapter ------------------------------------------------------------------------------------------------------


def bmw_trip(flags: tuple[str, ...] = (), soc_drop: float = 10.0) -> BmwTrip:
    n = 6001  # 600 s at 0.1 s
    t = np.arange(n) * 0.1
    soc = np.linspace(80.0, 80.0 - soc_drop, n)
    soc[:3] = np.nan  # leading NaN rows, as in the real files
    data = pd.DataFrame({"time_s": t, "speed_mps": np.full(n, 15.0), "elevation_m": np.full(n, 500.0),
                         "ambient_c": np.full(n, 20.0), "voltage_v": np.full(n, 360.0), "current_a": np.full(n, -50.0),
                         "soc_pct": soc, "heating_w": np.zeros(n)})
    return BmwTrip("TripA99", "A", data, flags)


def test_measured_energy_is_the_integral_of_voltage_times_current() -> None:
    m = measured(bmw_trip())
    duration_h = (6000 - 3) * 0.1 / 3600.0  # over the valid-SoC window
    assert m.energy_kwh == pytest.approx(360.0 * 50.0 * duration_h / 1000.0, rel=1e-6)
    assert m.charge_ah == pytest.approx(50.0 * duration_h, rel=1e-6)
    assert truth_kwh_per_100([m]) == pytest.approx(100.0 * m.energy_kwh / m.soc_drop_pct)


def test_the_observation_uses_whole_percent_dashboard_readings_and_no_battery_signals() -> None:
    obs = observation(bmw_trip(soc_drop=9.6), i3_prior())
    assert obs.soc_pct[0] == 80.0 and obs.soc_pct[1] == 70.0  # the BMS 70.4 reads as 70 on the dashboard
    assert obs.distance_km == pytest.approx(15.0 * 599.7 / 1000.0, rel=0.01) and obs.ac_on


def test_flagged_or_tiny_trips_are_excluded_by_the_fixed_rule() -> None:
    assert usable_for_passport(bmw_trip())
    assert not usable_for_passport(bmw_trip(("charging_session",)))
    assert not usable_for_passport(bmw_trip(("soc_copy_of:TripB03",)))
    assert not usable_for_passport(bmw_trip(soc_drop=2.0))


def test_the_i3_prior_carries_the_cited_battery_and_mass() -> None:
    p = i3_prior()
    assert p.nominal_kwh == 21.6 and p.base.curb_mass_kg == 1195.0
    assert float(p.base.usable_kwh) == pytest.approx(18.8)
