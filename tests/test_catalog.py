"""WP2 any Indian EV: the catalog, the held-out guard, option B, and real cars on the recorded CP -> Jaipur route."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest

from api.service import ApiError, EngineService
from verity import catalog as cat
from verity import india_eval as ie
from verity.dispatch import PRIOR_ONLY_MARGIN, prior_only_too_close
from verity.paths import CORPUS_DIR
from verity.routing import CachedClient

FIXTURES = Path(__file__).parent / "fixtures" / "route_cp_jaipur"
CP = {"lat": 28.63177, "lon": 77.21938, "name": "Connaught Place, New Delhi"}  # Photon, 2026-11-20
JAIPUR = {"lat": 26.915458, "lon": 75.818982, "name": "Jaipur"}  # Photon, 2026-11-20
NEXON_MAX = "tata-nexon-ev-max-40.5"


def _offline(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("offline test", request=request)


@pytest.fixture(scope="module")
def svc(tmp_path_factory: pytest.TempPathFactory) -> EngineService:
    cache = tmp_path_factory.mktemp("route_cache")
    for f in FIXTURES.glob("*.json"):
        shutil.copy2(f, cache / f.name)
    factory = lambda: CachedClient(cache_dir=cache, max_requests=1000, retries=0, sleep=lambda _s: None,  # noqa: E731
                                   client=httpx.Client(transport=httpx.MockTransport(_offline)))
    return EngineService(n_runs=300, graph_backend="memory", route_client=factory)


def trip(vehicle_id: str, **extra) -> dict:
    return {"vehicle_id": vehicle_id, "origin": CP, "destination": JAIPUR, "departure_time": "2026-11-21T08:00:00+05:30",
            "start_soc_pct": 90, "passengers": 1, "ac_on": True, "target_confidence": 0.9, **extra}


# --- GET /api/vehicles ------------------------------------------------------------------------------------------------


def test_every_spec_row_and_the_demo_fleet_are_listed(svc: EngineService) -> None:
    rows = svc.vehicles()["vehicles"]
    n_spec = sum(1 for _ in open(CORPUS_DIR / "vehicles_india.csv", encoding="utf-8-sig")) - 1
    real = [v for v in rows if not v["is_simulated"]]
    demo = [v for v in rows if v["is_simulated"]]
    assert len(real) == n_spec and {v["vehicle_id"] for v in demo} == {"V07", "V12", "V19"}
    for v in real:
        assert v["source_quality"] in {"official", "secondary"} and v["verify_note"] == "approximate - verify"
        assert v["calibration_basis"] in {cat.BASIS_TRIPS, cat.BASIS_PRIOR}


def test_an_unknown_vehicle_is_a_404_that_lists_every_valid_id(svc: EngineService) -> None:
    with pytest.raises(ApiError) as err:
        svc.dispatch(trip("no-such-car"))
    assert err.value.status == 404 and NEXON_MAX in err.value.message and "V07" in err.value.message


# --- a real car on a real route ---------------------------------------------------------------------------------------


def test_a_real_car_on_a_real_route_with_real_reports_is_not_simulated(svc: EngineService) -> None:
    r = svc.dispatch(trip(NEXON_MAX))
    assert r["vehicle"]["id"] == NEXON_MAX and r["vehicle"]["is_simulated"] is False
    assert r["vehicle"]["battery_kwh"] == 40.5 and r["vehicle"]["source_count"] >= 1
    assert r["calibration"] == {"trips": 0, "basis": "class prior", "label": "First trip in this car: prior only"}
    assert r["data_sources"]["vehicle"]["status"] == "real"
    assert r["data_sources"]["charger_reports"]["status"] == "real"
    assert r["is_simulated"] is False  # FINALE_BUILD rule 3: every input real -> false
    assert not any(lv["lever_id"].startswith("vehicle_") for lv in r["levers"])  # a lone real car: no fleet lever


def test_the_cars_own_kerb_mass_is_used(svc: EngineService) -> None:
    car = svc.catalog[NEXON_MAX]
    ctx = cat.vehicle_context(car, 2, True)
    assert car.curb_mass_basis == "spec sheet" and ctx.curb_mass_kg == car.curb_mass_kg
    assert ctx.prior_only is True and ctx.post.nominal_kwh == 40.5


def test_a_prior_only_car_too_close_to_its_target_is_refused(svc: EngineService) -> None:
    first = svc.dispatch(trip("mahindra-xev-9e-79"))
    shown = first["confidence"]
    if not 0.5 < shown <= 0.99:
        pytest.skip(f"shown confidence {shown} cannot be used as a target")
    close = svc.dispatch(trip("mahindra-xev-9e-79", target_confidence=shown))
    assert close["decision"] == "INSUFFICIENT_EVIDENCE"
    assert close["refusal_reason"].startswith("Too close to call without this car's trip history")


def test_option_b_margin_arithmetic() -> None:
    assert PRIOR_ONLY_MARGIN == 0.10
    assert prior_only_too_close(0.85, 0.90) and prior_only_too_close(0.95, 0.90)
    assert not prior_only_too_close(1.0, 0.90)  # clearly above: GO stands
    assert not prior_only_too_close(0.80, 0.90)  # clearly below: NO_GO stands


# --- held-out rows never calibrate a car ------------------------------------------------------------------------------


@dataclass
class FakeCase:
    record_id: str
    vehicle_id: str
    group: str
    record_type: str = "soc_trip"
    delta_soc: float | None = 30.0


def _groups() -> tuple[str, str]:
    """A source the fixed split rule puts in train and one it puts in test (found, never chosen by hand)."""
    train = test = None
    for i in range(500):
        g = f"example.test/source-{i}"
        if ie.assign_group(g) == "train":
            train = train or g
        else:
            test = test or g
        if train and test:
            return train, test
    raise AssertionError("no split found in 500 candidates")


def test_held_out_rows_never_reach_calibration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    train_g, test_g = _groups()
    frozen_test_g = f"{train_g}-frozen"  # train by the rule, but the split file says test: the file wins
    while ie.assign_group(frozen_test_g) != "train":
        frozen_test_g += "x"
    records = [{"id": f"R{i}", "group": g} for i, g in enumerate([train_g] * 3 + [test_g] * 3 + [frozen_test_g] * 3)]
    enriched = tmp_path / "enriched.json"
    enriched.write_text(json.dumps({"records": records}), encoding="utf-8")
    split = tmp_path / "split.json"
    split.write_text(json.dumps({"assignments": {frozen_test_g: "test", test_g: "test", train_g: "train"}}),
                     encoding="utf-8")
    monkeypatch.setattr(ie, "case_from_record",
                        lambda rec, vehicles: (FakeCase(rec["id"], NEXON_MAX, rec["group"]), ""))
    seen: list[str] = []

    def spy(cases):
        seen.extend(c.group for c in cases)
        return None

    monkeypatch.setattr(ie, "calibrated_posterior", spy)
    catalog = cat.load_catalog(CORPUS_DIR, enriched, split)
    assert seen == [train_g] * 3
    assert catalog[NEXON_MAX].trips_calibrated == 0  # the spy calibrated nothing: prior only


def test_a_prior_only_car_says_its_range_is_not_measured(svc: EngineService) -> None:
    r = svc.dispatch(trip(NEXON_MAX))
    assert "not measured" in r["data_sources"]["vehicle"]["note"]
    listed = {v["vehicle_id"]: v for v in svc.vehicles()["vehicles"]}
    assert listed[NEXON_MAX]["curb_mass_basis"] == "spec sheet"


def test_above_a_90_percent_target_a_prior_only_refusal_says_it_cannot_be_cleared(svc: EngineService) -> None:
    r = svc.dispatch(trip("mahindra-xev-9e-79", target_confidence=0.95))
    if r["decision"] == "INSUFFICIENT_EVIDENCE" and r["refusal_reason"].startswith("Too close"):
        assert "cannot be cleared" in r["refusal_reason"]
    else:  # far below the target: an honest NO_GO, never a GO
        assert r["decision"] == "NO_GO"


def test_dispatch_and_ride_check_compute_the_same_mass() -> None:
    from verity.dispatch import vehicle_mass_kg
    from verity.physics import total_mass_kg
    from verity.vehicles import get_prior

    car = cat.load_catalog()[NEXON_MAX]
    ctx = cat.vehicle_context(car, 2, True)
    assert vehicle_mass_kg(ctx) == total_mass_kg(car.curb_mass_kg, 2, 0.0)
    demo_like = cat.VehicleContext("V", car.class_key, car.post, 2, True, 3)  # no own mass: the class prior's
    assert vehicle_mass_kg(demo_like) == total_mass_kg(get_prior(car.class_key).base.curb_mass_kg, 2, 0.0)
