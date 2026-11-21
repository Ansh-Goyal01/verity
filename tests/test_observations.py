"""Calibration capture: a session with intermediate charging is recorded but never eligible (pure screening)."""

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from api.main import app
from api.routes_trips import MeasuredSegment, Observation
from verity.observations import CHARGE_RISE_PCT, MIN_DROP_PCT, screen_session


def test_a_plain_drive_is_eligible() -> None:
    s = screen_session([(0, 80), (1800, 74), (3600, 68)], declared_charging=False)
    assert s.eligible and not s.intermediate_charging and s.delta_soc_pct == 12 and s.n_readings == 3


def test_readings_are_sorted_by_time_before_screening() -> None:
    assert screen_session([(3600, 68), (0, 80)], False).eligible


def test_a_rise_between_readings_means_intermediate_charging() -> None:
    s = screen_session([(0, 40), (1500, 40 + CHARGE_RISE_PCT), (3000, 30)], declared_charging=False)
    assert s.intermediate_charging and not s.eligible and "rose" in s.reason


def test_a_small_wobble_is_not_charging() -> None:
    s = screen_session([(0, 80), (900, 80 + CHARGE_RISE_PCT - 0.5), (1800, 70)], declared_charging=False)
    assert s.eligible and not s.intermediate_charging


def test_the_driver_saying_they_charged_excludes_the_session_even_if_soc_only_fell() -> None:
    s = screen_session([(0, 80), (3600, 60)], declared_charging=True)
    assert s.intermediate_charging and not s.eligible and "driver" in s.reason


def test_a_drop_inside_the_noise_or_a_single_reading_is_not_eligible() -> None:
    assert not screen_session([(0, 80), (600, 80 - MIN_DROP_PCT + 0.5)], False).eligible
    one = screen_session([(0, 80)], False)
    assert not one.eligible and one.delta_soc_pct == 0 and "two" in one.reason


def test_the_observation_model_rejects_unphysical_or_incomplete_input() -> None:
    segment = {"length_m": 1000, "v_mps": 20, "accel_mps2": 0, "grade_rad": 0, "dt_s": 50, "temp_c": 25}
    ok = Observation(soc=[{"t_s": 0, "pct": 80}, {"t_s": 60, "pct": 79}], segments=[segment], charged_during_trip=False)
    assert ok.segments[0].surface == 1.0 and ok.segments[0].headwind_mps == 0.0
    for bad in ({"soc": [{"t_s": 0, "pct": 80}]}, {"segments": []}, {"soc": [{"t_s": 0, "pct": 101}] * 2},
                {"segments": [{**segment, "length_m": 0}]}):
        with pytest.raises(ValidationError):
            Observation(**{"soc": [{"t_s": 0, "pct": 80}, {"t_s": 60, "pct": 79}], "segments": [segment],
                           "charged_during_trip": False, **bad})
    with pytest.raises(ValidationError):
        MeasuredSegment(**{**segment, "v_mps": 200})


def test_the_endpoint_needs_a_sign_in() -> None:
    response = TestClient(app).post("/api/trips/3f6c7a52-1b2d-4c0e-9a6b-0d1e2f3a4b5c/observation", json={})
    assert response.status_code == 401
