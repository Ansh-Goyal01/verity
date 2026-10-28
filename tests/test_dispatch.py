"""Dispatch: candidate chains, plans scored with backups, Bingo Line, refusal, levers, live update.

Synthetic flat 190 km route and hand-made charger trust records; the vehicle is the class prior (no data),
declared as having 5 calibrated trips so that the evidence rules, not the 'no trips' rule, are under test.
"""

from __future__ import annotations

import itertools

import numpy as np
import pytest

from verity.calibrate import calibrate
from verity.chargers import RouteCharger
from verity.dispatch import (
    CHARGE_CAP,
    RESERVE_PCT,
    Ctx,
    Stop,
    VehicleContext,
    _build_ctx,
    dispatch,
    evaluate_plan,
    execute_trip,
    k_shortest_paths,
    live_update_plan,
    make_nodes,
)
from verity.forecast import ForecastConfig, PlannedRoute
from verity.schemas import ChargerRecord
from verity.trust.engine import ChargerTrust
from verity.vehicles import get_prior

CFG = ForecastConfig(n_runs=800)
PRIOR = get_prior("compact_esuv")
POST = calibrate(PRIOR.u_mean, PRIOR.u_cov, [], PRIOR.base, PRIOR.nominal_kwh)
ROUTE = PlannedRoute("car", np.full(190, 1000.0), np.zeros(190, dtype=int), np.zeros(190), np.full(190, 33.0), np.zeros(190))


def vehicle(trips: int = 5, vid: str = "V1") -> VehicleContext:
    return VehicleContext(vid, "compact_esuv", POST, passengers=2, ac_on=True, trips_calibrated=trips)


def charger(cid: str, km: float, p: float = 0.9, *, kw: float | None = 50.0, level: str = "high", p_lo: float | None = None):
    rec = ChargerRecord(id=cid, name=f"Charger {cid}", lat=28.0, lon=77.5, operator="Op", site_type="highway",
                        connectors=[{"type": "CCS2", "kw": kw}] if kw else [])
    n_rep, days = {"high": (8, 3.0), "medium": (3, 20.0), "low": (1, 50.0), "unknown": (0, None)}[level]
    trust = ChargerTrust(cid, p, p if p_lo is None else p_lo, min(p + 0.05, 1.0), n_rep, days, level, 0.82)
    return RouteCharger(rec, km, 0.2), trust


def scenario(*specs):
    pairs = [charger(*s[:3], **(s[3] if len(s) > 3 else {})) for s in specs]
    return [p[0] for p in pairs], {p[1].charger_id: p[1] for p in pairs}


STANDARD = scenario(("A", 50.0, 0.9), ("B", 58.0, 0.85), ("C", 100.0, 0.9), ("D", 108.0, 0.85),
                    ("E", 150.0, 0.9), ("F", 158.0, 0.85))


def run(start: float = 60.0, specs=STANDARD, **kw):
    chargers, trust = specs
    return dispatch(kw.pop("veh", vehicle()), ROUTE, chargers, trust, start_soc=start, seed=1, cfg=CFG, **kw)


# --- k-shortest paths ---------------------------------------------------------------------------------------


def test_yen_matches_brute_force_enumeration_on_a_small_dag() -> None:
    adj = {"S": {"a": 2.0, "b": 1.0, "T": 9.0}, "a": {"b": 1.0, "T": 3.0}, "b": {"T": 5.0, "c": 1.0}, "c": {"T": 1.0}, "T": {}}
    paths = []
    for r in range(0, 4):
        for mid in itertools.permutations(["a", "b", "c"], r):
            p = ["S", *mid, "T"]
            if all(q in adj[x] for x, q in zip(p[:-1], p[1:])):
                paths.append((sum(adj[x][q] for x, q in zip(p[:-1], p[1:])), p))
    expected = sorted(paths)[:5]
    got = k_shortest_paths(adj, "S", "T", 5)
    assert [c for c, _ in got] == pytest.approx([c for c, _ in expected]) and got[0][1] == expected[0][1]
    assert len({tuple(p) for _, p in got}) == len(got)  # loopless and distinct


# --- plans -------------------------------------------------------------------------------------------------


def test_low_start_soc_needs_a_charging_stop_and_stops_are_ordered() -> None:
    r = run(60.0)
    assert r.stops and [s["km_along_route"] for s in r.stops] == sorted(s["km_along_route"] for s in r.stops)
    assert r.decision in ("GO", "NO_GO") and 0.0 <= r.confidence <= 1.0
    assert all(0 < s["charge_minutes"] < 120 for s in r.stops)


def test_probabilities_are_rounded_to_five_percent() -> None:
    r = run(60.0)
    values = [r.confidence, *[s["p_works"] for s in r.stops], *[a["p_reach"] for a in r.alternates],
              *[a["p_works"] for a in r.alternates], *[lv["new_confidence"] for lv in r.levers]]
    assert all(abs(v * 20 - round(v * 20)) < 1e-9 for v in values)


def test_breakdown_adds_up_exactly_to_the_arrival_median() -> None:
    for start in (60.0, 75.0, 90.0):
        r = run(start)
        total = sum(row["soc_pct"] for row in r.breakdown)
        assert round(start) + total == r.arrival_soc["p50"], (start, r.breakdown)
        assert r.arrival_soc["p10"] <= r.arrival_soc["p50"] <= r.arrival_soc["p90"]
        assert {row["term"] for row in r.breakdown} == {"rolling", "aero", "grade", "inertia", "regen", "aux", "charging"}


def test_alternates_raise_the_success_probability_when_chargers_fail() -> None:
    chargers, trust = scenario(("A", 50.0, 0.6), ("B", 58.0, 0.6), ("C", 100.0, 0.6), ("D", 108.0, 0.6))
    ctx = _build_ctx(vehicle(), ROUTE, 60.0, 0.9, np.random.default_rng(3), CFG)
    nodes = make_nodes(chargers, trust)
    from verity.dispatch import _choose, evaluate_plan as ev

    stops, _, _ = _choose(ctx, nodes, np.random.default_rng(4), trust_aware=True)
    with_alts = ev(ctx, stops, np.random.default_rng(5), use_alts=True).confidence
    without = ev(ctx, stops, np.random.default_rng(5), use_alts=False).confidence
    assert with_alts > without + 0.05


def test_trust_aware_planner_avoids_the_unreliable_charger() -> None:
    # one stop at a 95 % charger cannot reach 90 % overall (0.95 x the energy margin), so Y must be 99 % here
    specs = scenario(("X", 80.0, 0.30, {"level": "high"}), ("Y", 86.0, 0.99, {"level": "high"}))
    aware = run(60.0, specs, trust_aware=True)
    assert aware.decision == "GO" and [s["charger_id"] for s in aware.stops] == ["Y"]
    assert aware.confidence >= 0.9


# --- planning through an external graph (Neo4j in the API) -----------------------------------------------------

from verity.trust.graph import InMemoryGraph  # noqa: E402 - kept next to the only tests that need it


class SpyGraph(InMemoryGraph):
    """In-memory graph that records the scopes LEGs were written under, and how many legs each held when dropped."""

    def __init__(self) -> None:
        super().__init__()
        self.written: list[str] = []
        self.dropped: list[str] = []
        self.legs_at_drop: list[int] = []

    def add_legs(self, legs, scope: str = "") -> None:
        self.written.append(scope)
        super().add_legs(legs, scope)

    def drop_legs(self, scope: str) -> None:
        self.legs_at_drop.append(len(self.legs(scope)))
        self.dropped.append(scope)
        super().drop_legs(scope)


def test_planning_through_an_external_graph_gives_the_same_plan_and_leaves_no_legs_behind() -> None:
    graph = SpyGraph()
    plain, via_graph = run(60.0), run(60.0, graph=graph)
    assert (via_graph.decision, via_graph.confidence, via_graph.stops) == (plain.decision, plain.confidence, plain.stops)
    assert len(graph.written) >= 2 and all(graph.written)  # the plan and its re-plans each got their own scope
    assert len(set(graph.written)) == len(graph.written) and graph.dropped == graph.written
    assert all(n > 0 for n in graph.legs_at_drop) and graph._legs == {}


def test_legs_are_dropped_even_when_the_write_fails() -> None:
    class Exploding(SpyGraph):
        def add_legs(self, legs, scope: str = "") -> None:
            self.written.append(scope)
            raise ConnectionError("link dropped mid-write")

    graph = Exploding()
    with pytest.raises(ConnectionError):
        run(60.0, graph=graph)
    assert graph.dropped == graph.written and len(graph.written) == 1


def test_a_failing_cleanup_does_not_fail_or_change_the_plan() -> None:
    class CleanupFails(SpyGraph):
        def drop_legs(self, scope: str) -> None:
            raise ConnectionError("link gone before the cleanup")

    plain, via_graph = run(60.0), run(60.0, graph=CleanupFails())
    assert (via_graph.decision, via_graph.confidence, via_graph.stops) == (plain.decision, plain.confidence, plain.stops)


def test_charging_longer_never_lowers_confidence() -> None:
    ctx = _build_ctx(vehicle(), ROUTE, 55.0, 0.9, np.random.default_rng(6), CFG)
    chargers, trust = scenario(("C", 100.0, 0.95))
    node = make_nodes(chargers, trust)[0]
    conf = [evaluate_plan(ctx, [Stop(node, target, ())], np.random.default_rng(7)).confidence
            for target in (30.0, 45.0, 60.0, CHARGE_CAP)]
    assert conf == sorted(conf) and conf[-1] > conf[0]


# --- Bingo Line, point of no return -----------------------------------------------------------------------------


def test_bingo_line_has_one_point_per_km_and_sensible_values() -> None:
    r = run(60.0)
    kms = [p["km"] for p in r.bingo_line]
    assert kms == sorted(kms) and len(r.bingo_line) == 191 and kms[0] == 0.0 and kms[-1] == pytest.approx(190.0, abs=0.1)
    assert all(0 <= p["bingo_soc_pct"] <= 100 for p in r.bingo_line)
    assert all(p["expected_soc_p10"] <= p["expected_soc_pct"] <= p["expected_soc_p90"] for p in r.bingo_line)
    assert RESERVE_PCT - 2 <= r.bingo_line[-1]["bingo_soc_pct"] <= RESERVE_PCT + 6  # near the end only the reserve matters


def test_bingo_is_higher_on_a_long_gap_than_next_to_a_charger() -> None:
    specs = scenario(("A", 40.0, 0.95), ("B", 150.0, 0.95))
    r = run(85.0, specs)
    at = {round(p["km"]): p["bingo_soc_pct"] for p in r.bingo_line}
    assert at[100] > at[38]  # deep in the 110 km gap versus right before a charger


def test_point_of_no_return_is_inside_the_route() -> None:
    r = run(60.0)
    assert 0 < r.point_of_no_return_km <= r.distance_km


# --- refusal and levers -----------------------------------------------------------------------------------------


def test_a_vehicle_with_no_calibrated_trips_is_refused_not_promised() -> None:
    r = run(60.0, veh=vehicle(trips=0))
    assert r.decision == "INSUFFICIENT_EVIDENCE" and "calibrated" in r.refusal_reason


def test_a_decision_that_hinges_on_a_stale_charger_is_refused_with_a_verify_lever() -> None:
    specs = scenario(("S", 70.0, 0.97, {"level": "unknown", "p_lo": 0.40}))
    r = run(60.0, specs)
    assert r.decision == "INSUFFICIENT_EVIDENCE" and "stale or missing" in r.refusal_reason
    assert any(lv["lever_id"] == "verify_S" for lv in r.levers)
    assert any(rs["kind"] == "unknown" and "no recent reports" in rs["label"] for rs in r.risk_sources)


def test_a_well_trusted_plan_is_not_refused_for_evidence() -> None:
    specs = scenario(("S", 70.0, 0.97, {"level": "high"}))
    assert run(60.0, specs).decision == "GO"


def test_other_vehicle_lever_appears_when_a_better_vehicle_exists() -> None:
    chargers, trust = STANDARD
    r = dispatch(vehicle(vid="V-low"), ROUTE, chargers, trust, start_soc=18.0, seed=1, cfg=CFG,
                 other_vehicles=[(vehicle(vid="V-full"), 95.0)])
    assert r.decision == "NO_GO" and any(lv["lever_id"] == "vehicle_V-full" for lv in r.levers)


def test_unlisted_charger_power_is_flagged_as_assumed() -> None:
    specs = scenario(("S", 100.0, 0.97, {"kw": None, "level": "high"}))
    r = run(60.0, specs)
    assert r.stops[0]["power_assumed"] is True and any("assumed" in rs["label"] for rs in r.risk_sources)


def test_dispatch_is_deterministic_and_reports_compute_time() -> None:
    a, b = run(60.0, compute_levers=False), run(60.0, compute_levers=False)
    assert a.confidence_raw == b.confidence_raw and a.arrival_soc == b.arrival_soc
    assert 0 < a.compute_ms < 60_000


# --- live update ---------------------------------------------------------------------------------------------------


def test_live_update_continues_diverts_or_slows_depending_on_the_reading() -> None:
    r = run(60.0, compute_levers=False)
    state = r.state
    km = 40.0
    expected = float(np.median(state.ev.trace[:, int(np.searchsorted(state.ctx.runs.km_end, km))]))
    on_plan = live_update_plan(state, km, expected)
    assert on_plan["action"] == "CONTINUE" and on_plan["divert_to"] is None
    low = live_update_plan(state, km, 8.0)
    assert low["action"] in ("DIVERT", "SLOW_DOWN") and low["confidence"] <= on_plan["confidence"]
    nxt = next(s for s in r.stops if s["km_along_route"] > km)
    broken = live_update_plan(state, km, expected, broken={nxt["charger_id"]})
    assert broken["action"] == "DIVERT" and broken["divert_to"]["charger_id"] != nxt["charger_id"]
    assert "reported broken" in broken["message"]


# --- executing a plan in a simulated world ------------------------------------------------------------------------


def test_execute_trip_strands_without_alternates_and_survives_with_one() -> None:
    chargers, trust = scenario(("A", 60.0, 0.9), ("B", 66.0, 0.9))
    nodes = make_nodes(chargers, trust)
    ctx = _build_ctx(vehicle(), ROUTE, 50.0, 0.9, np.random.default_rng(9), CFG)
    used = lambda km: 0.40 * km  # noqa: E731 - 0.4 SoC points per km: 76 points for the 190 km route
    stops = [Stop(nodes[0], 75.0, (nodes[1],))]
    down = {"A": False, "B": True}
    stranded = execute_trip(stops, used, 50.0, down, 100.0, ctx.min_per_pp, 190.0, use_alts=False)
    saved = execute_trip(stops, used, 50.0, down, 100.0, ctx.min_per_pp, 190.0, use_alts=True)
    assert stranded.stranded and not saved.stranded and saved.stops_used == 1 and saved.minutes > 100.0
