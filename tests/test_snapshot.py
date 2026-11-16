"""W0.3 (F4): nothing a screen shows can differ from the file or the engine it came from, unnoticed.

F4 found a snapshot showing E4 stranding 33 % vs 6 % while the result file said 37.5 % vs 3.5 %, and V07 at 0.85 when
the engine said 0.80. Three layers guard that now: the committed snapshot equals what the saved files say; each
section equals its source file; and the saved API examples equal what the live engine returns today.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.main import app, get_service
from api.service import DRIVER_DEMO
from verity.paths import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "scripts"))
import export_snapshot  # noqa: E402
import export_web_snapshot  # noqa: E402

COMMITTED = REPO_ROOT / export_snapshot.SNAPSHOT_FILE
BASE = {"origin": {"lat": 28.5355, "lon": 77.3910}, "destination": {"lat": 27.1767, "lon": 78.0081},
        "departure_time": "2026-10-28T14:00:00+05:30", "passengers": 4, "ac_on": True, "target_confidence": 0.9}
DEMO_SOC = {"V07": 70, "V12": 62, "V19": 40}  # the demo scenario's starting charge (api/service.py FLEET_META)
SELECTED = {"results.e1", "results.e2", "results.e3", "results.e4", "results.e5"}  # copy only some of their file
COPIED_SECTIONS = ({"api.health", "api.scenario", "api.chargers", "api.forecastUpdate", "api.rideCheck",
                    "api.driverDemo", "results.run_all", "results.real_data"}
                   | {f"api.dispatch.{v}" for v in DEMO_SOC} | {f"api.replay.{v}" for v in DEMO_SOC})


def at(snapshot: dict, dotted: str):
    node = snapshot
    for part in dotted.split("."):
        node = node[part]
    return node


def saved(relative: str) -> dict:
    return json.loads((REPO_ROOT / relative).read_text(encoding="utf-8"))


# --- layer 1: the committed snapshot is what the saved files say now -------------------------------------------------


def test_the_committed_snapshot_is_current() -> None:
    assert COMMITTED.is_file(), "results/snapshot.json is missing: run  uv run python scripts/export_snapshot.py"
    assert export_snapshot.is_current(), (
        "results/snapshot.json is STALE: a saved result or API example changed after it was exported. "
        "Run  uv run python scripts/export_snapshot.py  and commit it (F4: the screens must show the files' numbers).")


# --- layer 2: every section equals the file it came from -------------------------------------------------------------


def test_every_section_names_an_existing_source_file() -> None:
    sources = json.loads(COMMITTED.read_text(encoding="utf-8"))["sources"]
    assert sources and all((REPO_ROOT / rel).is_file() for rel in sources.values())
    assert {"api.rideCheck", "api.driverDemo", "results.real_data"} <= set(sources)  # what W0.3 added


def test_copied_sections_equal_their_source_file_exactly() -> None:
    snapshot = json.loads(COMMITTED.read_text(encoding="utf-8"))
    copied = {k: v for k, v in snapshot["sources"].items() if k not in SELECTED}
    assert set(copied) == COPIED_SECTIONS  # no section silently dropped or swapped
    for dotted, relative in copied.items():
        assert at(snapshot, dotted) == saved(relative), f"{dotted} differs from {relative}"


def test_selected_sections_carry_their_files_values_unchanged() -> None:
    snapshot = json.loads(COMMITTED.read_text(encoding="utf-8"))
    for dotted in ("results.e3", "results.e4", "results.e5"):
        source = saved(snapshot["sources"][dotted])
        for key, value in at(snapshot, dotted).items():
            assert value == source[key], f"{dotted}.{key}"
    e1, e1_source = snapshot["results"]["e1"], saved(snapshot["sources"]["results.e1"])
    for method, rows in e1["methods"].items():
        for row in rows:
            cell = e1_source["methods"][method][str(row["k"])]
            assert row["mae_pp"] == cell["mae_pp"] and row["coverage90"] == cell["bands"]["90"]["coverage"]
    e2, e2_source = snapshot["results"]["e2"], saved(snapshot["sources"]["results.e2"])
    for method, body in e2["methods"].items():
        assert body["ece"] == e2_source["methods"][method]["ece"]
        assert body["brier"] == e2_source["methods"][method]["brier"]


def test_the_two_numbers_f4_caught_are_the_files_numbers() -> None:
    snapshot = json.loads(COMMITTED.read_text(encoding="utf-8"))
    e4 = saved("results/e4_dispatch_vs_fixed_simulated.json")
    assert snapshot["results"]["e4"]["scenarios"] == e4["scenarios"]
    v07 = saved("docs/api_examples/dispatch_V07.json")
    assert snapshot["api"]["dispatch"]["V07"]["confidence"] == v07["confidence"]


# --- layer 3: the saved API examples are what the engine returns today ------------------------------------------------


def without_timing(body: dict) -> dict:
    return {k: v for k, v in body.items() if k != "compute_ms"}


@pytest.fixture(scope="module")
def live():
    with TestClient(app) as client:
        yield client


@pytest.mark.parametrize("vehicle", sorted(DEMO_SOC))
def test_the_saved_dispatch_example_is_what_the_engine_returns_today(live: TestClient, vehicle: str) -> None:
    request = {**BASE, "vehicle_id": vehicle, "start_soc_pct": DEMO_SOC[vehicle]}
    response = live.post("/api/dispatch", json=request)
    assert response.status_code == 200, response.text
    example = saved(f"docs/api_examples/dispatch_{vehicle}.json")
    assert without_timing(response.json()) == without_timing(example), (
        f"docs/api_examples/dispatch_{vehicle}.json is stale: run scripts/export_api_examples.py "
        "(with VERITY_GRAPH=memory), then scripts/export_snapshot.py")


def test_the_saved_ride_check_and_driver_demo_examples_are_what_the_engine_returns_today(live: TestClient) -> None:
    ride = live.post("/api/ride-check", json=DRIVER_DEMO)
    assert ride.status_code == 200, ride.text
    assert without_timing(ride.json()) == without_timing(saved("docs/api_examples/ride_check.json"))
    assert live.get("/api/demo/driver").json() == saved("docs/api_examples/driver_demo.json")


def test_the_saved_live_update_example_is_what_the_engine_returns_today(live: TestClient) -> None:
    plan = live.post("/api/dispatch", json={**BASE, "vehicle_id": "V12", "start_soc_pct": DEMO_SOC["V12"]}).json()
    update = {"plan_id": plan["plan_id"], "t": "2026-10-28T14:55:00+05:30", "lat": 28.10, "lon": 77.60, "soc_pct": 45}
    assert live.post("/api/forecast/update", json=update).json() == saved("docs/api_examples/forecast_update.json")


def test_the_demo_runs_the_number_of_runs_the_examples_report() -> None:
    assert saved("docs/api_examples/dispatch_V12.json")["n_runs"] == get_service().cfg.n_runs


# --- the frontend bundle --------------------------------------------------------------------------------------------------


def test_the_web_wrapper_renders_exactly_the_bundle() -> None:
    bundle = export_snapshot.build_snapshot()
    text = export_web_snapshot.render_js(bundle)
    assert text.splitlines()[1].startswith("window.VERITY_SNAPSHOT=") and text.endswith(";\n")
    body = text.splitlines()[1].removeprefix("window.VERITY_SNAPSHOT=").removesuffix(";")
    assert json.loads(body) == bundle


def test_the_frontends_snapshot_file_is_current_once_its_owner_regenerates_it() -> None:
    path = REPO_ROOT / "web" / "js" / "snapshot.js"  # read only: engine work never writes into web/
    if not path.is_file():
        pytest.skip("web/js/snapshot.js does not exist")
    if path.read_text(encoding="utf-8") != export_web_snapshot.render_js(export_snapshot.build_snapshot()):
        pytest.skip("web/js/snapshot.js is stale: Ansh runs  uv run python scripts/export_web_snapshot.py")
