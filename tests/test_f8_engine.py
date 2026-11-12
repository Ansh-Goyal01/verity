"""F8 engine upgrades: Site correlation, candidate diversity, the lost-minutes objective, the Neo4j client deadline,
and the ride check engine. Synthetic routes, chargers and trust records only (no network, no real evidence)."""

from __future__ import annotations

import threading
import time

import numpy as np
import pytest

from verity.calibrate import calibrate
from verity.dispatch import STRANDING_PENALTY_MIN, Node, VehicleContext, diverse_paths
from verity.forecast import ForecastConfig, PlannedRoute
from verity.ridecheck import AfterDropOption, RideInput, ride_check, rupees
from verity.schemas import ChargerRecord
from verity.sites import BETA_SITE, WorksSampler, assign_sites, p_all_fail
from verity.trust.engine import ChargerTrust
from verity.trust.graph import InMemoryGraph, Neo4jGraph, Neo4jStalled, run_with_deadline
from verity.trust.graph_build import GraphHandle
from verity.vehicles import get_prior

from graph_fakes import FakeDriver

CFG = ForecastConfig(n_runs=600)
PRIOR = get_prior("compact_esuv")
POST = calibrate(PRIOR.u_mean, PRIOR.u_cov, [], PRIOR.base, PRIOR.nominal_kwh)


# --- Sites ----------------------------------------------------------------------------------------------------------


def test_chargers_within_150_m_share_a_site_and_farther_ones_do_not() -> None:
    sites = assign_sites([("A", 28.5000, 77.1000), ("B", 28.5010, 77.1000), ("C", 28.5100, 77.1000)])  # 111 m, 1.1 km
    assert sites["A"] == sites["B"] == "A" and sites["C"] == "C"


def test_site_sampler_keeps_each_marginal_and_correlates_failures_within_a_site() -> None:
    s = WorksSampler(np.random.default_rng(1), 200_000)
    a, b, c = s.works("S1", 0.7), s.works("S1", 0.7), s.works("S2", 0.7)
    assert abs(a.mean() - 0.7) < 0.01 and abs(b.mean() - 0.7) < 0.01
    both_fail_same, both_fail_apart = np.mean(~a & ~b), np.mean(~a & ~c)
    assert abs(both_fail_apart - 0.09) < 0.01  # independent Sites: 0.3 x 0.3
    assert both_fail_same > both_fail_apart + 0.05  # a common cause makes joint failure far likelier


def test_all_fail_probability_reduces_to_independence_with_beta_zero() -> None:
    reach = np.ones((2, 1), dtype=bool)
    assert p_all_fail([0.8, 0.8], ["A", "B"], reach)[0] == pytest.approx(0.04)
    assert p_all_fail([0.8, 0.8], ["A", "A"], reach, beta=0.0)[0] == pytest.approx(0.04)
    same = p_all_fail([0.8, 0.8], ["A", "A"], reach)[0]
    assert same == pytest.approx(0.2 * (BETA_SITE + (1 - BETA_SITE) * 0.2)) and same > 0.04


# --- candidate diversity ----------------------------------------------------------------------------------------------


def node(cid: str, km: float, p: float, site: str = "") -> Node:
    return Node(cid, km, p, p, p, 50.0, site=site)


def test_diverse_candidates_add_a_reliable_and_a_spatially_distinct_chain() -> None:
    nodes = [node("cheap", 50, 0.3), node("twin", 55, 0.3, "cheap"), node("safe", 90, 0.99), node("far", 130, 0.6)]
    adj = {"START": {"cheap": 40, "twin": 45, "safe": 70, "far": 100}, "cheap": {"END": 100}, "twin": {"END": 100},
           "safe": {"END": 90}, "far": {"END": 80}}
    assert diverse_paths(adj, nodes, k=1, diversity=False) == [["cheap"]]
    rich = diverse_paths(adj, nodes, k=1)
    assert rich[0] == ["cheap"]
    assert ["safe"] in rich  # reliability-ranked: the cheap chain is 70 % likely to fail
    assert ["twin"] not in rich  # same Site as the cheapest chain: neither reliable nor spatially distinct


# --- Neo4j client deadline ---------------------------------------------------------------------------------------------


class StalledDriver(FakeDriver):
    def run(self, query: str, **params: object):  # a socket that never answers
        time.sleep(5.0)
        return []


def test_a_stalled_query_raises_within_the_deadline() -> None:
    t0 = time.perf_counter()
    with pytest.raises(Neo4jStalled):
        Neo4jGraph(driver=StalledDriver(), deadline_s=0.2).chargers()
    assert time.perf_counter() - t0 < 2.0


def test_deadline_passes_values_and_errors_through() -> None:
    assert run_with_deadline(lambda: 7, 1.0) == 7

    def boom() -> None:
        raise ValueError("x")

    with pytest.raises(ValueError):
        run_with_deadline(boom, 1.0)


def test_a_stalled_neo4j_falls_back_to_the_mirror_instead_of_hanging() -> None:
    handle = GraphHandle(Neo4jGraph(driver=StalledDriver(), deadline_s=0.2), "neo4j", InMemoryGraph(), "connected")
    t0 = time.perf_counter()
    assert handle.call(lambda g: g.chargers()) == []
    assert handle.backend == "memory" and time.perf_counter() - t0 < 2.0
    assert all(t.daemon for t in threading.enumerate() if t.name.startswith("neo4j-"))


# --- ride check engine ----------------------------------------------------------------------------------------------------


RIDE = PlannedRoute("car", np.full(40, 1000.0), np.ones(40, dtype=int), np.zeros(40), np.full(40, 25.0), np.zeros(40))


def option(cid: str, lat: float, p: float, level: str = "high", *, leg_km: float = 4.0, failure_days: float | None = None,
           site: str = "") -> AfterDropOption:
    rec = ChargerRecord(id=cid, name=f"Charger {cid}", lat=lat, lon=77.1, connectors=[{"type": "CCS2", "kw": 30.0}])
    n_rep, days = {"high": (8, 2.0), "unknown": (0, None)}[level]
    lo = max(p - 0.3, 0.05) if level == "unknown" else p - 0.05
    trust = ChargerTrust(cid, p, lo, min(p + 0.05, 1.0), n_rep, days, level, 0.82)
    return AfterDropOption(Node(cid, 0.0, p, trust.p_lo, trust.p_hi, 30.0, rec, trust, site or cid),
                           leg_km, leg_km / 25 * 60, failure_days)


def ride_input(soc: float, options: tuple[AfterDropOption, ...], trips: int = 5) -> RideInput:
    vehicle = VehicleContext("V1", "compact_esuv", POST, passengers=1, ac_on=True, trips_calibrated=trips)
    return RideInput(vehicle, RIDE, 10.0, soc, 0.9, 60.0, options)


GOOD = option("GOOD", 28.55, 0.95, leg_km=5.0)
BROKEN_NEAR = option("NEAR", 28.56, 0.35, leg_km=2.0, failure_days=1.0)
BACKUP = option("BACK", 28.54, 0.9, leg_km=6.0)


def test_ride_check_avoids_the_charger_with_recent_failure_evidence() -> None:
    out = ride_check(ride_input(45.0, (BROKEN_NEAR, GOOD, BACKUP)), cfg=CFG)
    assert out["decision"] == "GO"
    assert out["after_drop"]["charger"]["charger_id"] == "GOOD"
    near = next(c for c in out["considered"] if c["charger_id"] == "NEAR")
    assert not near["chosen"] and "failure was reported 1 days ago" in near["note"]


def test_more_battery_never_lowers_ride_confidence() -> None:
    lo = ride_check(ride_input(12.0, (GOOD, BACKUP)), cfg=CFG, compute_levers=False)
    hi = ride_check(ride_input(45.0, (GOOD, BACKUP)), cfg=CFG, compute_levers=False)
    assert lo["decision"] == "NO_GO" and hi["confidence"] >= lo["confidence"]


def test_minutes_add_up_and_become_rupees_at_the_earning_rate() -> None:
    out = ride_check(ride_input(45.0, (GOOD, BACKUP)), cfg=CFG, compute_levers=False)
    parts = out["after_drop"]["minutes"]
    assert sum(p["minutes"] for p in parts) == pytest.approx(out["after_drop"]["expected_lost_min"], abs=1.0)
    assert out["after_drop"]["expected_lost_rs"] == rupees(out["after_drop"]["expected_lost_min"], 60.0)
    assert rupees(90, 60.0) == 90 and rupees(30, 120.0) == 60
    assert next(p for p in parts if p["part"] == "stranding")["minutes"] <= STRANDING_PENALTY_MIN


def test_ride_check_refuses_without_evidence_or_without_calibration() -> None:
    # two unreported chargers whose prior (0.75 each) meets the target only if read optimistically
    unknown = (option("U1", 28.55, 0.75, "unknown"), option("U2", 28.54, 0.75, "unknown"))
    assert ride_check(ride_input(45.0, unknown), cfg=CFG)["decision"] == "INSUFFICIENT_EVIDENCE"
    assert ride_check(ride_input(45.0, (GOOD,), trips=0), cfg=CFG)["decision"] == "INSUFFICIENT_EVIDENCE"
    none = ride_check(ride_input(45.0, ()), cfg=CFG)
    assert none["decision"] == "NO_GO" and none["after_drop"] is None and none["ride"]["distance_km"] == 40.0


def test_a_backup_at_the_same_site_counts_for_less_than_an_independent_one() -> None:
    a = option("A", 28.550, 0.7, site="S")
    twin = option("T", 28.5505, 0.7, site="S")
    apart = option("P", 28.540, 0.7, site="P")
    cfg = ForecastConfig(n_runs=3000)
    same = ride_check(ride_input(45.0, (a, twin)), cfg=cfg, compute_levers=False)
    indep = ride_check(ride_input(45.0, (a, apart)), cfg=cfg, compute_levers=False)
    assert same["confidence"] < indep["confidence"]


def test_ride_bingo_line_covers_the_ride_and_falls_towards_the_drop() -> None:
    out = ride_check(ride_input(45.0, (GOOD, BACKUP)), cfg=CFG, compute_levers=False)
    line = out["bingo_line"]
    assert line[0]["km"] == 0.0 and line[-1]["km"] == pytest.approx(40.0)
    assert line[-1]["bingo_soc_pct"] <= line[0]["bingo_soc_pct"]
