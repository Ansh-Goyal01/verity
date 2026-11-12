"""The second demo (F10), pinned: an EV cab driver in Noida at 19:00 with 45 % battery gets a ride to IGI Airport T3.

Real route, weather and chargers (committed bundle + directory); SIMULATED vehicle and charger reports (api/service.py
DRIVER_PINS). The ride check must say GO, avoid the nearest charger (failure reported a day ago) and pick the
well-evidenced one. If the directory or the engine changes one of these, the demo story is broken: re-pin the scenario
and record it in docs/DECISIONS.md. Do not loosen these assertions or change the engine to pass them.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.main import app
from api.service import DRIVER_DEMO, DRIVER_PINS, PointPin, driver_demo_reports
from verity.schemas import ChargerRecord


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def answer(client: TestClient) -> dict:
    scenario = client.get("/api/demo/driver")
    assert scenario.status_code == 200, scenario.text
    r = client.post("/api/ride-check", json=scenario.json()["request"])
    assert r.status_code == 200, r.text
    return r.json()


def test_the_scenario_endpoint_serves_the_pinned_request(client: TestClient) -> None:
    body = client.get("/api/demo/driver").json()
    assert body["is_simulated"] and body["request"]["drop"] == DRIVER_DEMO["drop"]
    assert [p["charger_id"] for p in body["pinned_chargers"]] == [p.charger_id for p in DRIVER_PINS]


def test_the_driver_can_take_the_airport_ride(answer: dict) -> None:
    assert answer["decision"] == "GO" and answer["confidence"] >= 0.9
    assert answer["ride"]["p_complete"] >= 0.9 and answer["route"]["source"] == "osrm"


def test_after_the_drop_it_picks_the_trusted_charger(answer: dict) -> None:
    chosen = answer["after_drop"]["charger"]
    assert chosen["charger_id"] == "CH-0056" and chosen["evidence"]["trust_level"] == "high"


def test_it_avoids_the_nearest_charger_because_of_recent_failure_evidence(answer: dict) -> None:
    near = next(c for c in answer["considered"] if c["charger_id"] == "CH-0089")
    assert not near["chosen"] and near["recent_failure_days"] == 1 and near["note"].startswith("Avoided")
    assert near["road_km"] < answer["after_drop"]["charger"]["road_km"]  # it IS the nearer one


def test_simulated_evidence_is_flagged_and_counted(answer: dict) -> None:
    assert answer["is_simulated"] is True
    n_sim = sum(len(p.reports) for p in DRIVER_PINS)
    assert f"0 real, {n_sim} SIMULATED" in " ".join(answer["data_notes"])
    assert answer["earning_rate"]["is_example"]


def test_pullman_and_aloft_are_one_site(answer: dict) -> None:
    sites = {c["charger_id"]: c["site_id"] for c in answer["considered"]}
    assert sites["CH-0073"] == sites["CH-0071"]


def test_a_driver_pin_that_no_longer_matches_the_directory_stops_the_service() -> None:
    far = [ChargerRecord(id="CH-0089", name="x", lat=27.0, lon=78.0, source_ids=["osm:node/12973875562"])]
    with pytest.raises(ValueError, match="km from the drop"):
        driver_demo_reports(far, DRIVER_PINS[:1])
    renumbered = [ChargerRecord(id="CH-0089", name="x", lat=28.55, lon=77.10, source_ids=["osm:node/1"])]
    with pytest.raises(ValueError, match="directory changed"):
        driver_demo_reports(renumbered, [PointPin("CH-0089", "osm:node/12973875562", "t", ((1, "broken"),))])
