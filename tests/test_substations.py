"""WP8c likely shared grid cause: nearest substation within 5 km (proximity, never "feeds"), the weakest common-cause
tier in the Monte Carlo, and the shared-cause query on both graph backends. No network; substations here are test fakes
placed relative to real directory chargers."""

from __future__ import annotations

import numpy as np
import pytest

from verity.chargers import load_chargers
from verity.sites import BETA_OPERATOR, BETA_SITE, BETA_SUBSTATION, WorksSampler, p_all_fail
from verity.substations import LINK, SUBSTATION_RADIUS_KM, nearest_substations, shared_only
from verity.trust.graph import InMemoryGraph
from verity.trust.graph_structure import SHARED_CAUSES_CYPHER


@pytest.fixture(scope="module")
def chargers():
    return load_chargers()[0][:6]


def fake_subs(chargers, offset_deg: float) -> list[dict]:
    return [{"id": f"osm:node/T{i}", "lat": c.lat + offset_deg, "lon": c.lon, "name": None, "voltage": None,
             "operator": None} for i, c in enumerate(chargers)]


def test_only_a_substation_within_the_radius_is_linked(chargers) -> None:
    near = nearest_substations(chargers, fake_subs(chargers, 0.02))  # ~2.2 km north of each charger
    one = chargers[:1]  # alone: a fake placed for one charger can sit near another directory charger
    far = nearest_substations(one, fake_subs(one, 0.06))  # ~6.7 km: beyond 5 km
    assert SUBSTATION_RADIUS_KM == 5.0 and len(near) == len(chargers) and far == {}
    assert all(0.0 < km <= 5.0 for _, km in near.values())


def test_the_link_says_nearest_and_never_feeds() -> None:
    assert LINK == "NEAREST_SUBSTATION" and "FEED" not in SHARED_CAUSES_CYPHER.upper()


def test_a_substation_alone_adds_nothing() -> None:
    assert shared_only({"a": "S1", "b": "S1", "c": "S2"}, ["a", "b", "c"]) == {"a": "S1", "b": "S1"}


def test_marginals_stay_exact_with_all_three_tiers() -> None:
    assert BETA_SITE + BETA_OPERATOR + BETA_SUBSTATION <= 1.0 and BETA_SUBSTATION < BETA_OPERATOR < BETA_SITE
    for args in [("A", 0.8, "", "S"), ("A", 0.8, "X", "S"), ("A", 0.55, "X", "S")]:
        works = WorksSampler(np.random.default_rng(1), 200_000).works(*args)
        assert works.mean() == pytest.approx(args[1], abs=0.005)


def test_no_substation_draws_the_same_random_stream_as_before() -> None:
    old = WorksSampler(np.random.default_rng(3), 1000).works("A", 0.7, "X")
    new = WorksSampler(np.random.default_rng(3), 1000).works("A", 0.7, "X", "")
    assert np.array_equal(old, new)


def test_a_shared_substation_makes_joint_failure_likelier() -> None:
    s = WorksSampler(np.random.default_rng(5), 400_000)
    a, b = s.works("A", 0.8, "", "S"), s.works("B", 0.8, "", "S")
    both = float((~a & ~b).mean())
    assert both > 0.2 * 0.2 + 0.005  # above independence
    reach = np.ones((2, 1), dtype=bool)
    linked = p_all_fail([0.8, 0.8], ["A", "B"], reach, substations=["S", "S"])[0]
    plain = p_all_fail([0.8, 0.8], ["A", "B"], reach)[0]
    assert linked == pytest.approx(0.2 * (BETA_SUBSTATION + (1 - BETA_SUBSTATION) * 0.2)) and linked > plain


def test_the_memory_graph_reports_a_shared_substation(chargers) -> None:
    g = InMemoryGraph()
    g.add_chargers(chargers)
    a, b, c = chargers[:3]
    g.add_substation_links([{"id": "S1", "lat": 0, "lon": 0}, {"id": "S2", "lat": 0, "lon": 0}],
                           {a.id: ("S1", 1.0), b.id: ("S1", 2.0), c.id: ("S2", 3.0)})
    shared = {(sc.a, sc.b): sc.shared_substation for sc in g.shared_causes([a.id, b.id, c.id])}
    assert shared[tuple(sorted((a.id, b.id)))] == "S1"
    assert all(v != "S2" for v in shared.values())  # c shares its substation with nobody here
    assert g.substation_links()[a.id] == ("S1", 1.0)
