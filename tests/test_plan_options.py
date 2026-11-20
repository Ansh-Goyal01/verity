"""WP4 trip options: one scoring pass, up to three full releases (fastest, fewest stops, safest).

Engine tests use the synthetic corridor of test_dispatch; the API tests replay the recorded Connaught Place -> Jaipur
route of test_any_route (no network)."""

from __future__ import annotations

import shutil
from dataclasses import replace

import numpy as np
import pytest

from api.models import DEFAULT_ARRIVAL_BUFFER_PCT
from api.service import EngineService
from tests.test_any_route import FIXTURES, Offline, replay_factory, trip
from tests.test_dispatch import CFG, ROUTE, STANDARD, vehicle
from verity import dispatch as D
from verity.forecast import RESERVE_PCT
from verity.options import OBJECTIVES, Preferences, filter_nodes, operator_of, plan_options, plan_signature


def with_operators(specs, ops: dict[str, str]):
    chargers, trust = specs
    return [replace(rc, charger=rc.charger.model_copy(update={"operator": ops.get(rc.charger.id, "")})) for rc in chargers], trust


OPS = {"A": "Statiq (IN)", "B": "Tata Power", "C": "Statiq (IN)", "D": "Tata Power", "E": "Statiq (IN)", "F": "Tata Power"}


def options(start: float = 45.0, specs=STANDARD, **prefs):
    chargers, trust = specs
    return plan_options(vehicle(), ROUTE, chargers, trust, start_soc=start, seed=1, cfg=CFG, prefs=Preferences(**prefs))


@pytest.fixture(scope="module")
def default_options():
    return options()


def test_every_objective_is_answered_once_and_options_are_distinct(default_options) -> None:
    labels = [lab for o in default_options.options for lab in o.labels]
    assert sorted(labels) == sorted(OBJECTIVES)  # merged options carry every label that chose them
    sigs = [plan_signature(o.result.state.stops) for o in default_options.options]
    assert len(sigs) == len(set(sigs))
    assert 1 <= len(default_options.options) <= 3 and default_options.n_candidates >= len(default_options.options)


def test_each_option_is_a_full_release_with_the_dispatch_rules(default_options) -> None:
    for o in default_options.options:
        r = o.result
        assert r.decision in {"GO", "NO_GO", "INSUFFICIENT_EVIDENCE"} and r.bingo_line and r.state is not None
        assert o.meets_target == bool(D.meets_target(r.confidence_raw, 0.9))
        if r.decision == "GO":  # a released GO never contradicts its own Bingo Line (W0.1)
            assert all(p["expected_soc_pct"] >= p["bingo_soc_pct"] for p in r.bingo_line)
        assert not any(lv["lever_id"] == "cruise_80" or lv["lever_id"].startswith("vehicle_") for lv in r.levers)


def test_each_objective_gets_what_it_asks_for(default_options) -> None:
    by = {lab: o for o in default_options.options for lab in o.labels}
    top = max(round(o.result.confidence_raw / 0.02) for o in default_options.options)
    assert round(by["safest"].result.confidence_raw / 0.02) == top  # safest: best at the Monte Carlo's resolution
    meeting = [o.result for o in default_options.options if o.meets_target]
    if by["fastest"].meets_target:
        assert by["fastest"].result.state.ev.mean_minutes <= min(r.state.ev.mean_minutes for r in meeting) + 1e-6
    if by["fewest_stops"].meets_target:
        assert len(by["fewest_stops"].result.stops) <= min(len(r.stops) for r in meeting)


def test_avoided_operator_is_never_a_stop_or_a_backup() -> None:
    out = options(specs=with_operators(STANDARD, OPS), avoid_operators=("tata power",))
    assert out.avoided_chargers == 3
    for o in out.options:
        used = [s.node for s in o.result.state.stops] + [a for s in o.result.state.stops for a in s.alts]
        assert all(operator_of(n) != "tata power" for n in used)


def test_preferred_operator_limits_planned_stops_only_and_matches_without_the_country_tag() -> None:
    nodes = D.make_nodes(*with_operators(STANDARD, OPS))
    kept, stop_nodes, unsupported, removed = filter_nodes(nodes, Preferences(preferred_operators=("Statiq",)))
    assert removed == 0 and len(kept) == len(nodes) and not unsupported
    assert {n.id for n in stop_nodes} == {"A", "C", "E"}
    out = options(specs=with_operators(STANDARD, OPS), preferred_operators=("statiq",))
    assert all(operator_of(s.node) == "statiq" for o in out.options for s in o.result.state.stops)


def test_unknown_or_contradictory_operators_are_reported_not_silently_dropped() -> None:
    nodes = D.make_nodes(*with_operators(STANDARD, OPS))
    _, stop_nodes, unsupported, _ = filter_nodes(nodes, Preferences(preferred_operators=("Nobody",),
                                                                   avoid_operators=("Ghost",)))
    assert stop_nodes is None  # nothing to prefer: every operator stays a candidate
    assert {u["preference"] for u in unsupported} == {"preferred_operators", "avoid_operators"}
    _, _, both, _ = filter_nodes(nodes, Preferences(preferred_operators=("Tata Power",), avoid_operators=("tata power",)))
    assert any("both preferred and avoided" in u["reason"] for u in both)


def test_a_bigger_arrival_buffer_never_raises_confidence() -> None:
    ctx10 = D._build_ctx(vehicle(), ROUTE, 60.0, 0.9, np.random.default_rng(1), CFG)
    ctx25 = replace(ctx10, reserve_pct=25.0)
    nodes = D.make_nodes(*STANDARD)
    stops = D.make_stops(ctx10, [nodes[2]], nodes)
    ev10 = D.evaluate_plan(ctx10, stops, np.random.default_rng(5))
    ev25 = D.evaluate_plan(ctx25, stops, np.random.default_rng(5))
    assert np.all(ev25.success <= ev10.success) and ev25.confidence < ev10.confidence
    assert D.make_stops(ctx25, [nodes[2]], nodes)[0].charge_to > stops[0].charge_to  # and it charges more for it


def test_max_stops_caps_every_option() -> None:
    out = options(start=30.0, max_stops=1)
    assert all(len(o.result.stops) <= 1 for o in out.options)


def test_the_api_default_buffer_is_the_engine_reserve() -> None:
    assert DEFAULT_ARRIVAL_BUFFER_PCT == RESERVE_PCT


# --- API, on the recorded Connaught Place -> Jaipur route --------------------------------------------------------


@pytest.fixture(scope="module")
def svc(tmp_path_factory: pytest.TempPathFactory) -> EngineService:
    cache = tmp_path_factory.mktemp("route_cache_wp4")
    for f in FIXTURES.glob("*.json"):
        shutil.copy2(f, cache / f.name)
    return EngineService(n_runs=300, graph_backend="memory", route_client=replay_factory(cache, Offline()))


@pytest.fixture(scope="module")
def jaipur_plan(svc: EngineService) -> dict:
    return svc.plan(trip())


def test_jaipur_returns_full_releases_with_their_own_plan_ids(svc: EngineService, jaipur_plan: dict) -> None:
    opts = jaipur_plan["options"]
    assert 1 <= len(opts) <= 3
    assert sorted(lab for o in opts for lab in o["labels"]) == sorted(OBJECTIVES)
    assert len({o["plan_id"] for o in opts}) == len(opts)
    for o in opts:
        assert o["plan_id"] in svc.plans and o["stop_count"] == len(o["stops"])
        assert o["route"]["polyline"] and o["data_sources"]["route"]["status"] == "real"
    assert svc.plan(trip()) is jaipur_plan  # the same request is served from the cache


def test_forecast_update_works_on_every_option(svc: EngineService, jaipur_plan: dict) -> None:
    for o in jaipur_plan["options"]:
        lat, lon = o["route"]["polyline"][len(o["route"]["polyline"]) // 3]
        upd = svc.forecast_update({"plan_id": o["plan_id"], "t": "2026-11-21T09:00:00+05:30", "lat": lat, "lon": lon,
                                   "soc_pct": 60})
        assert upd["action"] in {"CONTINUE", "DIVERT", "SLOW_DOWN"} and upd["plan_id"] == o["plan_id"]


def test_avoiding_an_operator_holds_in_every_option_on_a_real_route(svc: EngineService, jaipur_plan: dict) -> None:
    ops = {c.id: c.operator for c in svc.chargers}
    used = [ops[s["charger_id"]] for o in jaipur_plan["options"] for s in o["stops"] if ops.get(s["charger_id"])]
    if not used:
        pytest.skip("no planned stop has a named operator on this route")
    avoid = used[0]
    out = svc.plan(trip(preferences={"avoid_operators": [avoid]}))
    assert out["avoided_chargers"] > 0
    for o in out["options"]:
        ids = [s["charger_id"] for s in o["stops"]] + [a["charger_id"] for a in o["alternates"]]
        assert all(ops.get(i) != avoid for i in ids)


def test_a_report_triggered_replan_of_an_option_replans_that_option(svc: EngineService, jaipur_plan: dict) -> None:
    o = jaipur_plan["options"][-1]
    again = svc._refresh(svc.plans[o["plan_id"]].request)
    assert set(o["labels"]) & set(again["labels"])
