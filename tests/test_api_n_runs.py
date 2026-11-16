"""W0.3 (F4): every dispatch, live-update and ride-check response names the number of Monte Carlo runs it used,
so no consumer (landing page, README, deck) has to hardcode "2,000" or "1,500" again."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.main import app, get_service
from api.service import DRIVER_DEMO

DISPATCH = {"vehicle_id": "V12", "origin": {"lat": 28.5355, "lon": 77.3910},
            "destination": {"lat": 27.1767, "lon": 78.0081}, "departure_time": "2026-10-28T14:00:00+05:30",
            "passengers": 4, "ac_on": True, "target_confidence": 0.9, "start_soc_pct": 62}


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def plan(client: TestClient) -> dict:
    r = client.post("/api/dispatch", json=DISPATCH)
    assert r.status_code == 200, r.text
    return r.json()


def test_dispatch_names_the_runs_it_used(plan: dict) -> None:
    assert plan["n_runs"] == get_service().cfg.n_runs and plan["n_runs"] >= 1


def test_the_run_count_is_the_configured_one_not_a_constant(plan: dict) -> None:
    cfg_runs = get_service().cfg.n_runs
    assert isinstance(plan["n_runs"], int) and plan["n_runs"] == cfg_runs


def test_a_live_update_names_the_runs_of_the_plan_it_reweights(client: TestClient, plan: dict) -> None:
    body = {"plan_id": plan["plan_id"], "t": "2026-10-28T14:55:00+05:30", "lat": 28.10, "lon": 77.60, "soc_pct": 45}
    r = client.post("/api/forecast/update", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["n_runs"] == plan["n_runs"]  # the update reweights the SAME runs the plan was scored with


def test_a_ride_check_names_its_runs(client: TestClient) -> None:
    r = client.post("/api/ride-check", json=DRIVER_DEMO)
    assert r.status_code == 200, r.text
    assert r.json()["n_runs"] == get_service().cfg.n_runs
