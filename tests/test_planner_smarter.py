"""WP3b: the smarter planner. Every change must keep the answer honest: more candidate plans, physics that is priced
(taper above 80 %, detour energy), and an evidence check that is a real credible bound, never a looser target."""

from __future__ import annotations

import numpy as np
import pytest

from tests.test_dispatch import CFG, ROUTE, run, scenario, vehicle
from verity import dispatch as D
from verity.chargers import RouteCharger
from verity.schemas import ChargerRecord


def _ctx(start: float = 60.0) -> D.Ctx:
    return D._build_ctx(vehicle(), ROUTE, start, 0.9, np.random.default_rng(1), CFG)


def test_charging_above_80_percent_is_allowed_but_slower() -> None:
    ctx = _ctx()
    node = D.make_nodes(*scenario(("A", 50.0, 0.9)))[0]
    per_pp = ctx.min_per_pp(node)
    assert float(ctx.charge_minutes(node, 40.0, 30.0)) == pytest.approx(30.0 * per_pp)  # below the cap: linear
    assert float(ctx.charge_minutes(node, 80.0, 10.0)) == pytest.approx(10.0 * per_pp * D.ABOVE_CAP_SLOWDOWN)
    assert float(ctx.charge_minutes(node, 70.0, 20.0)) == pytest.approx((10.0 + 10.0 * D.ABOVE_CAP_SLOWDOWN) * per_pp)


def _route_charger(offset_km: float) -> list[RouteCharger]:
    rec = ChargerRecord(id="X", name="X", lat=0.0, lon=0.0, operator="", site_type="unknown", connectors=[],
                        source_ids=["osm:node/1"])
    return [RouteCharger(rec, 60.0, offset_km)]


def test_an_off_route_charger_costs_battery_as_well_as_minutes() -> None:
    ctx = _ctx()
    on, off = (D.make_nodes(_route_charger(k), {})[0] for k in (0.0, 5.0))
    assert off.detour_km == pytest.approx(2 * 5.0 * D.DETOUR_ROAD_FACTOR) and on.detour_km == 0.0
    ev_on = D.evaluate_plan(ctx, [D.Stop(on, 90.0, ())], np.random.default_rng(3), p_override={"X": 1.0})
    ev_off = D.evaluate_plan(ctx, [D.Stop(off, 90.0, ())], np.random.default_rng(3), p_override={"X": 1.0})
    half_detour_pp = float(np.median(off.detour_km / 2.0 * ctx.pp_per_km))
    gap = float(np.nanmedian(ev_on.stop_arrival[0]) - np.nanmedian(ev_off.stop_arrival[0]))
    assert gap == pytest.approx(half_detour_pp, abs=0.5)  # arriving costs half the detour...
    assert ev_off.minutes.mean() > ev_on.minutes.mean()  # ...and the trip takes longer


def test_the_lenient_proposer_never_loses_a_chain_the_strict_rule_finds() -> None:
    ctx = _ctx()
    nodes = D.make_nodes(*scenario(("A", 50.0, 0.9), ("B", 58.0, 0.85), ("C", 100.0, 0.9), ("D", 150.0, 0.9)))
    table = D.leg_table(ctx, nodes, trust_aware=True)
    strict_graph, _ = D.build_leg_graph(ctx, nodes, trust_aware=True, lenient=False, table=table)
    strict = D.diverse_paths(D._adjacency(strict_graph, ["START", *[n.id for n in nodes], "END"]), nodes)
    proposed = [[n.id for n in p] for p in D.propose_paths(ctx, nodes, trust_aware=True)[0]]
    assert strict and all(chain in proposed for chain in strict)


def test_a_first_leg_must_be_reachable_at_the_target_quantile() -> None:
    ctx = _ctx(start=30.0)
    nodes = D.make_nodes(*scenario(("A", 50.0, 0.9), ("B", 150.0, 0.9)))
    km = {n.id: n.km for n in nodes}
    for a, b, _, ok_lenient, _, _ in D.leg_table(ctx, nodes, trust_aware=True)[1]:
        if a == "START":
            energy = ctx.runs.total_used if b == "END" else ctx.runs.used_at_km(km[b])
            need = D.RESERVE_PCT if b == "END" else D.RES_STOP
            assert ok_lenient == (float(np.quantile(energy, 0.9)) + need <= 30.0 + 1e-9)


def test_the_credible_bound_is_a_lower_bound_on_the_plan_confidence() -> None:
    r = run(start=60.0)
    ctx, stops = r.state.ctx, list(r.state.stops)
    conf = D.evaluate_plan(ctx, stops, np.random.default_rng(2)).confidence
    bound = D.credible_confidence(ctx, stops, [s.node for s in stops], seed=2)
    assert 0.0 <= bound <= conf + 0.03  # up to Monte Carlo noise


def test_robust_stops_charge_at_least_as_much_as_plain_ones() -> None:
    ctx = _ctx()
    nodes = D.make_nodes(*scenario(("A", 50.0, 0.9), ("C", 100.0, 0.9), ("E", 150.0, 0.9)))
    plain, robust = D.make_stops(ctx, nodes, nodes), D.make_stops(ctx, nodes, nodes, robust=True)
    assert all(r.charge_to >= p.charge_to - 1e-9 for r, p in zip(robust, plain))
    assert robust[-1].charge_to == plain[-1].charge_to  # the last stop has nothing to skip
    assert all(s.charge_to <= D.MAX_CHARGE_PCT for s in robust)


def test_thinning_keeps_the_best_few_per_stretch_only_on_dense_routes() -> None:
    sparse = D.make_nodes(*scenario(*[(f"S{i}", 10.0 + i, 0.9) for i in range(D.THIN_ABOVE)]))
    assert D.thin_nodes(sparse, 50.0) == sparse
    dense = D.make_nodes(*scenario(*[(f"D{i}", 10.0 + i * 0.5, 0.5 + (i % 5) * 0.1) for i in range(D.THIN_ABOVE + 20)]))
    kept = D.thin_nodes(dense, 50.0)
    per_bin: dict[int, list] = {}
    for n in kept:
        per_bin.setdefault(int(n.km // D.THIN_BIN_KM), []).append(n)
    assert all(len(v) <= D.THIN_PER_BIN for v in per_bin.values())
    for b, group in per_bin.items():  # the kept ones are the most reliable of their stretch
        dropped = [n for n in dense if int(n.km // D.THIN_BIN_KM) == b and n not in group]
        assert all(min(k.p_works for k in group) >= d.p_works - 1e-9 for d in dropped)
