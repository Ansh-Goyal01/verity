"""WP7 spoken trip planner: rules first, the model only fills gaps, places from the geocoder, never a guess.

No network: the geocoder and Sarvam are fakes; call 2 plans on the recorded Connaught Place -> Jaipur route
(tests/fixtures/route_cp_jaipur, real recorded responses)."""

from __future__ import annotations

import shutil
from datetime import datetime

import pytest

from api.service import ApiError, EngineService
from api.voice_plan import decide_plan_stage, hear_plan_stage
from tests.test_any_route import CP, DEPARTURE, FIXTURES, JAIPUR, Offline, replay_factory
from verity.catalog import load_catalog
from verity.plan_voice import IST, parse_departure, parse_trip
from verity.voice import VoiceUnavailable

NOW = datetime(2026, 11, 21, 6, 0, tzinfo=IST)
CARS = list(load_catalog().values())
DEMO = "Kal subah 8 baje Jaipur jaana hai, Nexon 45 mein 70% hai, AC chalega"
PLACES = {"jaipur": [JAIPUR], "connaught place": [CP],
          "aurangabad": [{"name": "Aurangabad, Maharashtra", "lat": 19.88, "lon": 75.34},
                         {"name": "Aurangabad, Bihar", "lat": 24.75, "lon": 84.37}]}  # test fakes, not real lookups


class FakeSvc:
    """What hear_plan_stage reads from EngineService: clock, catalog, geocode, _vehicle_for."""

    def __init__(self, geocoder_up: bool = True) -> None:
        self.catalog = load_catalog()
        self.geocoder_up, self.queries = geocoder_up, []

    def clock(self) -> datetime:
        return NOW

    def geocode(self, q: str, limit: int = 5, near=None) -> dict:
        self.queries.append(q)
        if not self.geocoder_up:
            raise ApiError(503, "geocoder_unavailable", "down")
        return {"results": [{**r, "kind": "city"} for r in PLACES.get(q, [])]}

    def _vehicle_for(self, req: dict):
        car = self.catalog[req["vehicle_id"]]
        return type("Picked", (), {"vehicle": {"name": car.name}})()


class FakeVoice:
    def __init__(self, llm: dict | None = None) -> None:
        self.llm, self.chat_calls, self.tts_calls = llm, 0, 0

    def chat_json(self, system: str, user: str, schema: dict, name: str = "", timeout: float | None = None) -> dict:
        self.chat_calls += 1
        if self.llm is None:
            raise VoiceUnavailable("model down (test)")
        return self.llm

    def tts(self, text: str, language: str) -> dict:
        self.tts_calls += 1
        raise VoiceUnavailable("tts down (test)")


class NoClips:
    def lookup(self, text: str, language: str) -> bytes | None:
        return None


def hear(text: str, voice=None, svc=None, **kw) -> dict:
    return hear_plan_stage(svc or FakeSvc(), voice, NoClips(), transcript=text, language=kw.pop("language", "unknown"),
                           speak=False, position=kw.pop("position", {"lat": CP["lat"], "lon": CP["lon"]}), **kw)


# --- rules ----------------------------------------------------------------------------------------------------------


def test_demo_sentence_parses_by_rules_alone() -> None:
    s = parse_trip(DEMO, NOW, CARS)
    assert (s.destination_phrase, s.origin_phrase, s.soc_pct, s.vehicle_id) == ("jaipur", None, 70, "tata-nexon-ev-45")
    assert s.departure == datetime(2026, 11, 22, 8, 0, tzinfo=IST) and s.ac_on and s.ac_said and s.parsed_by == "rules"


@pytest.mark.parametrize("text,expected", [
    ("kal subah 8 baje", datetime(2026, 11, 22, 8, 0, tzinfo=IST)),
    ("aaj shaam 6 baje", datetime(2026, 11, 21, 18, 0, tzinfo=IST)),
    ("tomorrow 6 pm", datetime(2026, 11, 22, 18, 0, tzinfo=IST)),
    ("parso dopahar 2 baje", datetime(2026, 11, 23, 14, 0, tzinfo=IST)),
    ("abhi", NOW),
])
def test_time_phrases(text: str, expected: datetime) -> None:
    when, said, problem = parse_departure(text, NOW)
    assert when == expected and said and problem is None


@pytest.mark.parametrize("text,problem", [("kal subah", "no hour"), ("kal 8 baje", "am or pm"),
                                          ("aaj subah 5 baje", "in the past")])
def test_unparsed_or_past_time_is_asked_never_guessed(text: str, problem: str) -> None:
    assert parse_departure(text, NOW) == (None, True, problem)


def test_no_time_said_means_now_and_is_read_back() -> None:
    when, said, problem = parse_departure("jaipur jaana hai", NOW)
    assert when == NOW and not said and problem is None


def test_sector_number_is_not_a_battery_percentage() -> None:
    s = parse_trip("Noida Sector 18 se Agra jaana hai abhi", NOW, CARS)
    assert s.origin_phrase == "noida sector 18" and s.destination_phrase == "agra" and s.soc_pct is None


def test_several_car_variants_without_a_size_are_asked() -> None:
    s = parse_trip("Jaipur jaana hai, Nexon mein 70% hai", NOW, CARS)
    assert s.vehicle_id is None and len(s.car_options) >= 2
    hinted = parse_trip("Jaipur jaana hai, Nexon mein 70%", NOW, CARS, vehicle_hint="tata-nexon-ev-45")
    assert hinted.vehicle_id == "tata-nexon-ev-45"  # the app's own car tells the variants apart


def test_english_from_to_and_people() -> None:
    s = parse_trip("from Connaught Place to Jaipur tomorrow at 7 am, Curvv 55, 90 percent, no AC, 3 people", NOW, CARS)
    assert (s.origin_phrase, s.destination_phrase, s.vehicle_id, s.soc_pct) == ("connaught place", "jaipur",
                                                                                "tata-curvv-ev-55", 90)
    assert not s.ac_on and s.passengers == 3 and s.departure.hour == 7


# --- call 1 ---------------------------------------------------------------------------------------------------------


def test_demo_sentence_reads_back_in_hindi_without_the_model() -> None:
    voice = FakeVoice({"destination": "x"})
    out = hear(DEMO, voice, language="hi-IN")
    assert out["stage"] == "confirm" and voice.chat_calls == 0 and out["reply_language"] == "hi-IN"
    assert out["slots"]["destination"]["name"] == "Jaipur" and out["slots"]["origin"]["name"] is None
    assert "Jaipur" in out["prompt"] and "70" in out["prompt"] and "सही है" in out["prompt"]


def test_ambiguous_place_is_a_question() -> None:
    out = hear("kal subah 8 baje Aurangabad jaana hai, Curvv 55, 60%")
    assert out["stage"] == "clarify" and out["slots"]["choices"] == [["Aurangabad, Maharashtra", "Aurangabad, Bihar"]]
    assert "Aurangabad, Maharashtra or Aurangabad, Bihar" in out["prompt"]


def test_unknown_car_is_a_question() -> None:
    out = hear("kal subah 8 baje Jaipur jaana hai, 70%")
    assert out["stage"] == "clarify" and out["slots"]["missing"] == ["car"] and "which car" in out["prompt"]


def test_model_fills_only_missing_slots_and_a_failing_model_keeps_the_rules() -> None:
    voice = FakeVoice({"destination": None, "origin": None, "car": "Curvv 55", "departure": None, "soc_pct": 99})
    out = hear("kal subah 8 baje Jaipur jaana hai, 70%", voice)
    assert voice.chat_calls == 1 and out["slots"]["vehicle_id"] == "tata-curvv-ev-55"
    assert out["slots"]["soc_pct"] == 70 and out["slots"]["parsed_by"] == "rules+llm"  # the rule value stands
    down = FakeVoice(None)
    out = hear("kal subah 8 baje Jaipur jaana hai, 70%", down)
    assert down.chat_calls == 1 and out["stage"] == "clarify" and out["slots"]["parsed_by"] == "rules"


def test_geocoder_down_asks_for_the_place() -> None:
    out = hear(DEMO, svc=FakeSvc(geocoder_up=False))
    assert out["stage"] == "clarify" and out["slots"]["missing"] == ["destination"]


# --- call 2 ---------------------------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def svc(tmp_path_factory: pytest.TempPathFactory) -> EngineService:
    cache = tmp_path_factory.mktemp("voice_plan_cache")
    for f in FIXTURES.glob("*.json"):
        shutil.copy2(f, cache / f.name)
    return EngineService(n_runs=300, graph_backend="memory", route_client=replay_factory(cache, Offline()))


def confirm(**kw) -> dict:
    return {"confirmed": True, "language": "hi-IN", "speak": True, "origin": CP, "destination": JAIPUR,
            "vehicle_id": "tata-curvv-ev-55", "soc_pct": 90.0, "departure_time": DEPARTURE, "ac_on": True,
            "passengers": 1, "target_confidence": 0.9, **kw}


def test_no_plans_nothing(svc: EngineService) -> None:
    out = decide_plan_stage(svc, None, NoClips(), confirm(confirmed=False))
    assert out["stage"] == "not_confirmed" and out["release"] is None


def test_yes_plans_the_safest_option_and_speaks_its_own_sentence(svc: EngineService) -> None:
    voice = FakeVoice()
    out = decide_plan_stage(svc, voice, NoClips(), confirm())
    release = out["release"]
    assert out["stage"] == "decided" and "safest" in release["labels"]
    assert out["speech"] == release["speech"]["hi-IN"] and out["audio"] is None and voice.tts_calls == 1
    assert svc.plans.get(release["plan_id"]) is not None  # opens in the live-drive view like any plan


def test_yes_with_voice_down_serves_the_cached_clip(svc: EngineService) -> None:
    speech = decide_plan_stage(svc, None, NoClips(), confirm(speak=False))["speech"]

    class OneClip:
        def lookup(self, text: str, language: str) -> bytes | None:
            return b"RIFF-clip" if (text, language) == (speech, "hi-IN") else None

    out = decide_plan_stage(svc, FakeVoice(), OneClip(), confirm())
    assert out["audio"]["cached"] is True


def test_http_both_calls_validate_against_the_contract(svc: EngineService) -> None:
    from fastapi.testclient import TestClient

    from api import main

    main.app.dependency_overrides[main.get_voice_client] = lambda: None
    try:
        main.app.dependency_overrides[main.get_service] = FakeSvc
        heard = TestClient(main.app).post("/api/voice/plan", json={"transcript": DEMO, "language": "hi-IN",
                                                                   "speak": False, "lat": CP["lat"], "lon": CP["lon"]})
        assert heard.status_code == 200 and heard.json()["stage"] == "confirm"
        main.app.dependency_overrides[main.get_service] = lambda: svc
        decided = TestClient(main.app).post("/api/voice/plan", json=confirm(speak=False))
        assert decided.status_code == 200 and decided.json()["stage"] == "decided"
        bad = TestClient(main.app).post("/api/voice/plan", json={"confirmed": True})
        assert bad.status_code == 422
    finally:
        main.app.dependency_overrides.clear()
