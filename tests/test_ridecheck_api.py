"""POST /api/ride-check (contract v2): shape (strict models), refusals and errors, on the committed ride route
(data/processed/ride_demo_noida_t3.json, real OSRM route and forecast), so no network is needed."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.main import app
from api.ride import EXAMPLE_EARNING_RS_PER_HOUR

RIDE = {"vehicle_id": "V12", "soc_pct": 45, "current": {"lat": 28.6280, "lon": 77.3649},
        "pickup": {"lat": 28.5708, "lon": 77.3261}, "drop": {"lat": 28.5562, "lon": 77.0999},
        "departure_time": "2026-11-13T19:00:00+05:30"}


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def checked(client: TestClient) -> dict:
    r = client.post("/api/ride-check", json=RIDE)
    assert r.status_code == 200, r.text
    return r.json()


def test_ride_check_answers_with_the_contract_fields(checked: dict) -> None:
    assert checked["decision"] in ("GO", "NO_GO", "INSUFFICIENT_EVIDENCE") and checked["is_simulated"] is True
    assert abs(checked["confidence"] * 20 - round(checked["confidence"] * 20)) < 1e-9
    assert checked["ride"]["distance_km"] > 30 and 0 < checked["ride"]["pickup_km"] < checked["ride"]["distance_km"]
    assert checked["route"]["source"] == "osrm" and checked["bingo_line"][0]["km"] == 0.0
    assert any("forecast for 2026-11-13T19:00" in n for n in checked["data_notes"])


def test_the_default_earning_rate_is_the_cited_example(checked: dict) -> None:
    rate = checked["earning_rate"]
    assert rate["is_example"] and rate["rs_per_hour"] == EXAMPLE_EARNING_RS_PER_HOUR and "cis-india.org" in rate["source"]


def test_brand_reserved_chargers_are_not_after_drop_options(checked: dict) -> None:
    assert all("tesla" not in c["name"].lower() for c in checked["considered"])


def test_an_entered_earning_rate_scales_the_rupees(client: TestClient, checked: dict) -> None:
    out = client.post("/api/ride-check", json={**RIDE, "earning_rs_per_hour": 120}).json()
    assert not out["earning_rate"]["is_example"]
    if out["after_drop"] and checked["after_drop"]:
        assert out["after_drop"]["expected_lost_rs"] >= checked["after_drop"]["expected_lost_rs"]


@pytest.mark.parametrize(("change", "status", "code"), [
    ({"vehicle_id": "NOPE"}, 404, "unknown_vehicle"),
    ({"drop": {"lat": 19.09, "lon": 72.87}}, 422, "unsupported_area"),  # Mumbai: outside the charger directory
    ({"soc_pct": 140}, 422, "invalid_request"),
])
def test_ride_check_errors_use_the_contract_shape(client: TestClient, change: dict, status: int, code: str) -> None:
    r = client.post("/api/ride-check", json={**RIDE, **change})
    assert r.status_code == status and r.json()["error"] == code
