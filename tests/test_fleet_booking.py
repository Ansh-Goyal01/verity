"""WP6 fleet booking acceptance: rank every car for one booking, recommend one GO at the full count, share its release.

The two depot -> Connaught Place -> Jaipur routes are REAL recorded responses (tests/fixtures/route_fleet_jaipur,
recorded 2026-11-21 through CachedClient); no test here touches the network. The demo fleet's battery % and depots are
SIMULATED scenario inputs (data/fleet/demo_fleet.json)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api import main
from api.fleet import FLEET_FILE, confirm_recommended, load_demo_fleet, rank_key, ranked, share_summary
from api.service import ApiError, EngineService, plan_key
from tests.test_any_route import CP, DEPARTURE, JAIPUR, Offline, replay_factory
from verity.catalog import load_catalog
from verity.voice import VoiceUnavailable

FIXTURES = Path(__file__).parent / "fixtures" / "route_fleet_jaipur"
BOOKING = {"origin": CP, "destination": JAIPUR, "departure_time": DEPARTURE, "passengers": 1, "ac_on": True,
           "target_confidence": 0.9}
FULL_RUNS, RANK_RUNS = 400, 150


def row(fid: str, decision: str, conf: float, minutes: int) -> dict:
    return {"fleet_id": fid, "decision": decision, "confidence": conf, "total_minutes": minutes, "rank": 0,
            "recommended": False}


# --- pure ranking rules -----------------------------------------------------------------------------------------


def test_rank_puts_go_first_then_confidence_then_minutes() -> None:
    rows = [row("a", "NO_GO", 0.99, 100), row("b", "GO", 0.91, 300), row("c", "GO", 0.95, 400),
            row("d", "GO", 0.95, 350), row("e", "INSUFFICIENT_EVIDENCE", 0.9, 50)]
    assert [r["fleet_id"] for r in ranked(rows)] == ["d", "c", "b", "a", "e"]
    assert [r["rank"] for r in ranked(rows)] == [1, 2, 3, 4, 5]
    assert rank_key(rows[1]) < rank_key(rows[0])  # any GO beats any NO_GO, whatever the confidence


def test_only_a_go_confirmed_at_the_full_count_is_recommended() -> None:
    rows = [row("a", "GO", 0.95, 300), row("b", "GO", 0.92, 320), row("c", "NO_GO", 0.5, 300)]
    flips = {"a": row("a", "NO_GO", 0.85, 300), "b": row("b", "GO", 0.93, 320)}  # a: GO when ranked, NO_GO at full
    out, reruns = confirm_recommended(rows, lambda r: flips[r["fleet_id"]])
    assert reruns == 2 and [r["fleet_id"] for r in out if r["recommended"]] == ["b"]
    assert out[0]["fleet_id"] == "b"
    none_go, n = confirm_recommended([row("c", "NO_GO", 0.5, 300)], lambda r: pytest.fail("no GO: nothing to re-run"))
    assert n == 0 and not any(r["recommended"] for r in none_go)


def test_reruns_are_bounded() -> None:
    rows = [row(c, "GO", 0.99 - i / 100, 300) for i, c in enumerate("abcdef")]
    out, reruns = confirm_recommended(rows, lambda r: {**r, "decision": "NO_GO"}, max_extra=2)
    assert reruns == 3 and not any(r["recommended"] for r in out)  # three flips: no GO left confirmed


# --- the demo fleet file ------------------------------------------------------------------------------------------


def test_demo_fleet_file_is_simulated_real_models_with_sourced_depots() -> None:
    fleet = load_demo_fleet(load_catalog())
    assert fleet is not None and fleet["is_simulated"] is True and 5 <= len(fleet["vehicles"]) <= 6
    assert all("Photon" in d["source"] and d["query"] for d in fleet["depots"].values())
    assert not any("trips_calibrated" in v for v in fleet["vehicles"])  # read from the catalog, never claimed here


def test_a_fleet_file_that_is_not_marked_simulated_is_refused(tmp_path: Path) -> None:
    bad = json.loads(FLEET_FILE.read_text(encoding="utf-8")) | {"is_simulated": False}
    (tmp_path / "f.json").write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ValueError, match="is_simulated"):
        load_demo_fleet(load_catalog(), tmp_path / "f.json")


# --- the booking on recorded routes ---------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def svc(tmp_path_factory: pytest.TempPathFactory) -> EngineService:
    cache = tmp_path_factory.mktemp("fleet_cache")
    for f in FIXTURES.glob("*.json"):
        shutil.copy2(f, cache / f.name)
    return EngineService(n_runs=FULL_RUNS, graph_backend="memory", route_client=replay_factory(cache, Offline()))


@pytest.fixture(scope="module")
def booking(svc: EngineService) -> dict:
    mp = pytest.MonkeyPatch()
    mp.setenv("VERITY_FLEET_RANK_RUNS", str(RANK_RUNS))
    try:
        return svc.fleet_booking(BOOKING)
    finally:
        mp.undo()


def test_every_car_is_ranked_with_a_go_and_a_reasoned_no(booking: dict) -> None:
    rows = booking["rows"]
    assert len(rows) == 6 and [r["rank"] for r in rows] == list(range(1, 7))
    assert any(r["decision"] == "GO" for r in rows)
    nos = [r for r in rows if r["decision"] != "GO"]
    assert nos and all(r["why_not"] for r in nos)
    assert rows == sorted(rows, key=rank_key)


def test_one_recommended_go_at_the_full_count(booking: dict, svc: EngineService) -> None:
    rec = [r for r in booking["rows"] if r["recommended"]]
    assert len(rec) == 1 and rec[0]["decision"] == "GO" and rec[0]["n_runs"] == FULL_RUNS
    assert booking["recommended_plan_id"] == rec[0]["plan_id"] and booking["n_runs_rank"] == RANK_RUNS
    assert 1 <= booking["full_reruns"] <= 3
    full = sum(r["n_runs"] == FULL_RUNS for r in booking["rows"])
    assert full == booking["full_reruns"]  # every other row is the reduced ranking count
    assert all(r["n_runs"] in (RANK_RUNS, FULL_RUNS) for r in booking["rows"])
    assert svc.release(rec[0]["plan_id"])["n_runs"] == FULL_RUNS


def test_recommended_plan_works_with_the_live_update_and_starts_at_the_depot(booking: dict, svc: EngineService) -> None:
    rec = next(r for r in booking["rows"] if r["recommended"])
    release = svc.release(rec["plan_id"])
    first = release["route"]["polyline"][0]
    assert first == pytest.approx([rec["start"]["lat"], rec["start"]["lon"]], abs=0.01)  # the car's depot, not CP
    assert rec["deadhead_km"] is not None and rec["deadhead_km"] > 1.0
    upd = svc.forecast_update({"plan_id": rec["plan_id"], "lat": first[0], "lon": first[1], "soc_pct": rec["soc_pct"]})
    assert upd["plan_id"] == rec["plan_id"]


def test_demo_fleet_releases_say_simulated_because_of_the_battery_state(booking: dict, svc: EngineService) -> None:
    assert booking["is_simulated"] is True and booking["fleet_source"].endswith("demo_fleet.json")
    for r in booking["rows"]:
        release = svc.release(r["plan_id"])
        assert release["data_sources"]["vehicle_state"]["status"] == "simulated"
        assert release["is_simulated"] is True and release["vehicle"]["is_simulated"] is False  # a real model


def test_entered_cars_are_not_marked_by_the_demo_fleet(svc: EngineService) -> None:
    depot = svc.demo_fleet["depots"]["okhla"]
    out = svc.fleet_booking({**BOOKING, "vehicles": [{"vehicle_id": "tata-curvv-ev-55", "soc_pct": 90,
                                                      "location": {"lat": depot["lat"], "lon": depot["lon"]}}]})
    assert out["fleet_source"] == "request" and len(out["rows"]) == 1
    assert "vehicle_state" not in svc.release(out["rows"][0]["plan_id"])["data_sources"]


def test_unknown_car_fails_before_planning(svc: EngineService) -> None:
    with pytest.raises(ApiError) as err:
        svc.fleet_booking({**BOOKING, "vehicles": [{"vehicle_id": "no-such-car", "soc_pct": 50}]})
    assert err.value.code == "unknown_vehicle"


def test_wp6_defaults_leave_earlier_plan_ids_unchanged() -> None:
    req = {"vehicle_id": "V12", "origin": CP, "destination": JAIPUR}
    assert plan_key({**req, "start": None, "state_source": "entered"}) == plan_key(req)
    assert plan_key({**req, "start": {"lat": 1.0, "lon": 2.0}}) != plan_key(req)


# --- share ------------------------------------------------------------------------------------------------------


class FailingVoice:
    def __init__(self) -> None:
        self.calls = 0

    def tts(self, text: str, language: str) -> dict:
        self.calls += 1
        raise VoiceUnavailable("Sarvam is down (test)")


class OneClipCache:
    def __init__(self, text: str, language: str) -> None:
        self.key = (text, language)

    def lookup(self, text: str, language: str) -> bytes | None:
        return b"RIFF-test-clip" if (text, language) == self.key else None


def test_share_text_uses_engine_fields_and_falls_back_to_a_cached_clip(booking: dict, svc: EngineService) -> None:
    pid = booking["recommended_plan_id"]
    release = svc.release(pid)
    voice = FailingVoice()
    out = svc.share(pid, voice, OneClipCache(release["speech"]["en-IN"], "en-IN"))
    assert out["link"].endswith(f"/api/release/{pid}") and out["is_simulated"] is True
    assert release["confidence_display"] in out["text"]["en-IN"] and out["text"]["hi-IN"] == release["speech"]["hi-IN"]
    assert out["audio"]["en-IN"]["cached"] is True and out["audio"]["hi-IN"] is None and out["audio_note"]
    assert voice.calls == 2
    assert share_summary(release) == out["text"]["en-IN"]


def test_share_without_audio_makes_no_voice_call(booking: dict, svc: EngineService) -> None:
    voice = FailingVoice()
    out = svc.share(booking["recommended_plan_id"], voice, OneClipCache("", ""), audio=False)
    assert voice.calls == 0 and out["audio"] == {"en-IN": None, "hi-IN": None}


def test_http_endpoints_validate_against_the_contract(booking: dict, svc: EngineService) -> None:
    main.app.dependency_overrides[main.get_service] = lambda: svc
    main.app.dependency_overrides[main.get_voice_client] = lambda: None
    try:
        client = TestClient(main.app)
        pid = booking["recommended_plan_id"]
        assert client.get(f"/api/release/{pid}").json()["plan_id"] == pid
        share = client.get(f"/api/release/{pid}/share", params={"audio": "false"}).json()
        assert share["plan_id"] == pid and share["audio_note"]
        assert client.get("/api/release/PL-nope").status_code == 404
        bad = client.post("/api/fleet/booking", json={**BOOKING, "vehicles": [{"vehicle_id": "x", "soc_pct": 5}]})
        assert bad.status_code == 404
    finally:
        main.app.dependency_overrides.clear()
