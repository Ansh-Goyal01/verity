"""The graph's structure beyond chargers and reports (W2.1, W2.5, W2.6), on the in-memory backend and with a fake
Neo4j driver. The live twin of these checks is tests/test_graph_parity.py."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from tests.graph_fakes import FakeDriver
from verity.chargers import RouteCharger
from verity.names import MATCH_MIN_SCORE, decide, rank_hits, spoken_tokens
from verity.schemas import ChargerRecord, Report
from verity.trust.channels import channel_of
from verity.trust.graph import InMemoryGraph, Neo4jGraph
from verity.trust.graph_build import ALIAS_FILE, avoided_reason, derive_aliases, plan_record, usable_alternate
from verity.trust.graph_structure import build_site_recs, lucene_query
from verity.trust.graph_types import (
    AVOIDED_LOW_P_WORKS,
    AVOIDED_NO_REPORTS,
    AliasRec,
    PlanAvoidedRec,
    PlanBackupRec,
    PlanRec,
    PlanStopRec,
)

IST = timezone(timedelta(hours=5, minutes=30))
T0 = datetime(2026, 10, 27, 10, 0, tzinfo=IST)


def charger(cid: str, name: str = "", lat: float = 28.5, lon: float = 77.4, operator: str = "", site_type: str = "unknown"):
    return ChargerRecord(id=cid, name=name or cid, lat=lat, lon=lon, operator=operator, site_type=site_type)


def report(cid: str, sid: str, status: str = "working", minutes: int = 0) -> Report:
    return Report(charger_id=cid, source_id=sid, status=status, t=T0 + timedelta(minutes=minutes))


# --- channels (W2.1) ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("source,channel", [
    ("public:google_review", "google_review"), ("driver:driver_voice", "driver_voice"), ("feed:OpD", "operator_feed"),
    ("user-003", "app_user"), ("demo-user-1", "app_user"), ("ride-demo-user-CH-0006-2", "app_user"),
    ("ring-04", "app_user"), ("public:", "other"), ("s1", "other"), ("", "other")])
def test_a_source_id_maps_to_one_channel(source: str, channel: str) -> None:
    assert channel_of(source) == channel


# --- physical sites (W2.1) ---------------------------------------------------------------------------------------


def test_sites_are_150_m_clusters_with_their_commonest_type() -> None:
    near = [charger("A", lat=28.5000, site_type="mall"), charger("B", lat=28.5010, site_type="mall"),
            charger("C", lat=28.5012, site_type="hotel")]  # A-B 111 m, B-C 22 m: one site by single linkage
    far = charger("D", lat=28.6, site_type="highway")
    sites = build_site_recs([*near, far, charger("TEST-X", lat=0.0)])
    assert [(s.site_id, s.site_type, s.charger_ids) for s in sites] == [
        ("A", "mall", ("A", "B", "C")), ("D", "highway", ("D",))]  # the TEST- charger is never part of the structure


def test_a_site_whose_chargers_have_no_known_type_is_unknown() -> None:
    assert [s.site_type for s in build_site_recs([charger("A"), charger("B", lat=28.5001)])] == ["unknown"]


def test_the_in_memory_graph_derives_sites_from_its_chargers() -> None:
    g = InMemoryGraph()
    g.add_chargers([charger("A"), charger("B", lat=28.6)])
    assert [s.site_id for s in g.sites()] == ["A", "B"]


def test_the_neo4j_site_rebuild_drops_the_old_sites_first_and_writes_rows() -> None:
    drv = FakeDriver([[{"id": "A", "name": "a", "lat": 28.5, "lon": 77.4, "operator": "", "site_type": "mall",
                        "connectors": "[]", "source_ids": []}]])
    Neo4jGraph(driver=drv).sync_sites()
    queries = [q for q, _ in drv.log]
    assert "MATCH (s:Site) DETACH DELETE s" in queries[1] and "SiteType" in queries[2] and "OF_TYPE" in queries[3]
    assert drv.log[3][1]["rows"] == [{"id": "A", "site_type": "mall", "chargers": ["A"]}]


# --- plans (W2.1) ------------------------------------------------------------------------------------------------


def plan(pid: str = "PL-1", ts: float = 1000.0, **kw) -> PlanRec:
    return PlanRec(pid, "V12", "GO", 0.95, 300, ts, **kw)


def test_a_plan_is_stored_replaced_by_id_and_swept_by_count_and_age() -> None:
    g = InMemoryGraph()
    g.add_plan(plan("PL-1", 1000.0, stops=(PlanStopRec(0, "A"),)))
    g.add_plan(plan("PL-1", 1000.0, stops=(PlanStopRec(0, "B"),)))  # same id: replaced, not duplicated
    assert g.plan_ids() == ["PL-1"] and g.plan("PL-1").stops == (PlanStopRec(0, "B"),)
    for i, ts in enumerate((2000.0, 3000.0, 4000.0)):
        g.add_plan(plan(f"PL-{i + 2}", ts))
    g.sweep_plans(keep=3, max_age_s=10_000.0, now=5000.0)  # newest 3 stay: the oldest (PL-1) goes
    assert g.plan_ids() == ["PL-2", "PL-3", "PL-4"]
    g.sweep_plans(keep=3, max_age_s=2500.0, now=5000.0)  # PL-2 is 3000 s old
    assert g.plan_ids() == ["PL-3", "PL-4"]


def test_neo4j_writes_a_plan_in_one_statement_with_the_three_edge_kinds() -> None:
    drv = FakeDriver()
    Neo4jGraph(driver=drv).add_plan(plan(stops=(PlanStopRec(0, "A"),), backups=(PlanBackupRec(0, "B", 0.9, 0.7),),
                                         avoided=(PlanAvoidedRec(0, "C", AVOIDED_NO_REPORTS),)))
    (query, params), = drv.log
    assert all(kind in query for kind in ("STOPS_AT", "BACKUP_FOR", "AVOIDED")) and "DELETE old" in query
    assert params["stops"] == [{"order": 0, "cid": "A"}] and params["avoided"][0]["reason"] == "no_reports"


def test_the_avoided_rule_is_the_coverage_rule() -> None:
    assert avoided_reason(0.9, "unknown") == AVOIDED_NO_REPORTS  # however good its prior looks
    assert avoided_reason(0.4, "low") == AVOIDED_LOW_P_WORKS
    assert avoided_reason(0.5, "medium") is None
    assert all(usable_alternate(p, lvl) == (avoided_reason(p, lvl) is None)
               for p in (0.1, 0.5, 0.9) for lvl in ("unknown", "low", "medium", "high"))


def test_a_plan_record_lists_stops_backups_and_what_was_passed_over() -> None:
    def rc(cid: str, km: float) -> RouteCharger:
        return RouteCharger(charger(cid), km, 0.0)

    chargers = [rc("S", 100.0), rc("B", 120.0), rc("U", 130.0), rc("L", 140.0), rc("OK", 150.0), rc("FAR", 400.0)]
    trust = {c: SimpleNamespace(p_works=p, trust_level=lvl) for c, p, lvl in
             [("S", 0.9, "high"), ("B", 0.9, "high"), ("U", 0.8, "unknown"), ("L", 0.3, "low"), ("OK", 0.9, "high"),
              ("FAR", 0.8, "unknown")]}
    response = {"vehicle_id": "V12", "decision": "GO", "confidence": 0.95, "n_runs": 300, "is_simulated": True,
                "stops": [{"charger_id": "S"}],
                "alternates": [{"for_stop_index": 0, "charger_id": "B", "p_reach": 0.9, "p_works": 0.9}]}
    rec = plan_record("PL-x", response, chargers, trust, alt_ahead_km=60.0, created_ts=5.0)
    assert rec.stops == (PlanStopRec(0, "S"),) and rec.backups == (PlanBackupRec(0, "B", 0.9, 0.9),)
    # U (no reports) and L (low p) lie in S's 60 km window and are refused; OK would count as coverage but is simply
    # not needed; FAR is outside the window. (A plan's edges are a set: stored in one canonical order.)
    assert rec.avoided == (PlanAvoidedRec(0, "L", "low_p_works"), PlanAvoidedRec(0, "U", "no_reports"))
    assert rec.avoided_total == 2


def test_a_direct_plan_with_no_stop_has_no_edges() -> None:
    rec = plan_record("PL-d", {"vehicle_id": "V07", "decision": "GO", "confidence": 0.95, "n_runs": 10, "stops": [],
                               "alternates": []}, [], {}, 60.0, 1.0)
    assert (rec.stops, rec.backups, rec.avoided, rec.avoided_total) == ((), (), (), 0)


# --- names (W2.5) ------------------------------------------------------------------------------------------------


def named_graph() -> InMemoryGraph:
    g = InMemoryGraph()
    g.add_chargers([charger("C1", "Tata Power Yamuna Expressway Mathura Exit", operator="Tata Power"),
                    charger("C2", "Tata Power Agra Fort", lat=27.2), charger("C3", "Haldiram", lat=28.0, operator="Haldiram's"),
                    charger("C4", "EV charging station (OpenStreetMap)", lat=28.1),
                    charger("C5", "EV charging station (OpenStreetMap)", lat=28.2), charger("C6", "Yuma", lat=28.3),
                    charger("C7", "Yuma", lat=28.4)])
    return g


def test_one_distinctive_word_in_one_name_is_a_match() -> None:
    d = decide(named_graph().search_names("haldiram wala charger"))
    assert d.kind == "match" and d.hits[0].charger_id == "C3"


def test_two_chargers_with_the_same_name_are_asked_about_never_guessed() -> None:
    d = decide(named_graph().search_names("Yuma"))
    assert d.kind == "which_one" and {h.charger_id for h in d.hits} == {"C6", "C7"}


def test_a_shared_brand_word_asks_which_one_with_the_best_first() -> None:
    d = decide(named_graph().search_names("Tata Power Mathura"))
    assert d.kind == "match" and d.hits[0].charger_id == "C1"  # all three words only in C1
    d = decide(named_graph().search_names("Tata Power"))
    assert d.kind == "which_one" and [h.charger_id for h in d.hits] == ["C2", "C1"]  # shorter name first on a tie


def test_a_name_made_only_of_generic_words_can_never_be_found() -> None:
    g = named_graph()
    assert spoken_tokens("the EV charging station") == []
    assert g.search_names("EV charging station") == [] and g.search_names("openstreetmap charger") == []


def test_an_unknown_place_is_nothing_never_a_guess() -> None:
    d = decide(named_graph().search_names("Sector 62 wala charger"))  # F7: no directory name says "sector"
    assert d.kind == "none" and d.hits == ()


def test_a_number_must_match_exactly() -> None:
    g = InMemoryGraph()
    g.add_chargers([charger("S6", "Sector 6 charger hub"), charger("S62", "Sector 62 plaza", lat=28.6)])
    hits = g.search_names("sector 62")
    assert [h.charger_id for h in hits if h.score >= MATCH_MIN_SCORE] == ["S62"]  # "6" is not "62"


def test_aliases_name_chargers_and_one_alias_can_name_several() -> None:
    g = named_graph()
    g.add_aliases([AliasRec("Jaypee wala", "C2", "alias_file"), AliasRec("Jaypee wala", "C3", "alias_file")])
    assert decide(g.search_names("jaypee")).kind == "which_one"
    with pytest.raises(KeyError):
        g.add_aliases([AliasRec("x", "NOPE", "alias_file")])


def test_the_operator_becomes_an_alias_only_when_it_adds_a_word() -> None:
    recs = derive_aliases([charger("C1", "Tata Power Mathura", operator="Tata Power"),
                           charger("C2", "Quick Stop", operator="Zoom"), charger("C3", "Zoom", operator="Zoom"),
                           charger("TEST-1", "t", operator="Zoom")])
    assert [(a.text, a.charger_id) for a in recs] == [("Tata Power", "C1"), ("Zoom", "C2")]


def test_the_alias_file_is_read_and_unknown_chargers_are_skipped(tmp_path) -> None:
    path = tmp_path / "aliases.json"
    path.write_text(json.dumps({"aliases": [{"text": "Mathura exit", "charger_id": "C1", "source": "visited"},
                                            {"text": "Ghost", "charger_id": "NOPE", "source": "?"}]}), encoding="utf-8")
    recs = derive_aliases([charger("C1", "Tata Power", operator="")], alias_file=path)
    assert recs == [AliasRec("Mathura exit", "C1", "alias_file")]


def test_the_committed_alias_file_holds_no_unverified_alias() -> None:
    assert json.loads(ALIAS_FILE.read_text(encoding="utf-8"))["aliases"] == []  # fill it only from verified places


def test_the_lucene_query_escapes_and_ors_only_distinctive_words() -> None:
    assert lucene_query("the Tata-Power (Mathura) wala charger!") == "tata OR power OR mathura"
    assert lucene_query("charger wala") == ""


def test_rank_hits_keeps_one_hit_per_charger_and_drops_weak_ones() -> None:
    hits = rank_hits("tata power mathura", [("A", "Tata Power", "Tata Power"), ("A", "Tata Power", "Mathura Hub"),
                                            ("B", "Other", "Tata only")])
    assert [(h.charger_id, h.score) for h in hits] == [("A", 0.6667)]  # B has 1 of 3 words: below the "worth asking" bar


# --- audit views (W2.6) ------------------------------------------------------------------------------------------


def viewed_graph() -> InMemoryGraph:
    g = InMemoryGraph()
    g.add_chargers([charger("A", "Alpha", operator="OpA", site_type="mall"), charger("B", "Beta", lat=28.5001, operator="OpA"),
                    charger("C", "Gamma", lat=28.9)])
    g.add_reports([report("A", "public:google_review", "broken", 1), report("A", "driver:driver_voice", "working", 5),
                   report("C", "demo-user-1", "working", 2)])
    g.add_plan(plan("PL-v", 100.0, stops=(PlanStopRec(0, "A"),), backups=(PlanBackupRec(0, "C", 0.9, 0.8),),
                    avoided=(PlanAvoidedRec(0, "B", "no_reports"),)))
    g.add_aliases([AliasRec("Alpha point", "A", "alias_file")])
    return g


def test_the_why_view_is_a_path_from_the_plan_to_reports_sources_and_channels() -> None:
    v = viewed_graph().why("PL-v")
    assert sorted({n["label"] for n in v.nodes}) == ["Channel", "Charger", "Operator", "Plan", "Site", "Source"]
    assert {"STOPS_AT", "BACKUP_FOR", "AVOIDED", "REPORTED", "VIA", "AT", "RUN_BY"} <= {e["type"] for e in v.edges}
    assert not v.executed and v.backend == "memory" and "Plan {id: $plan}" in v.cypher and v.query_ms >= 0
    avoided = next(e for e in v.edges if e["type"] == "AVOIDED")
    assert avoided["to"] == "Charger:B" and avoided["props"] == {"stop": 0, "reason": "no_reports"}
    # A and B are one 150 m site: the shared site is ONE node with two charger edges
    assert [e["to"] for e in v.edges if e["type"] == "AT" and e["from"] in ("Charger:A", "Charger:B")] == ["Site:A", "Site:A"]


def test_the_why_view_of_a_plan_without_stops_is_just_the_plan() -> None:
    g = InMemoryGraph()
    g.add_plan(plan("PL-direct"))
    v = g.why("PL-direct")
    assert [n["id"] for n in v.nodes] == ["Plan:PL-direct"] and v.edges == () and g.why("PL-missing") is None


def test_the_charger_view_shows_reports_channels_aliases_plans_and_site_mates() -> None:
    v = viewed_graph().charger_neighbourhood("A")
    ids = {n["id"] for n in v.nodes}
    assert {"Charger:A", "Charger:B", "Site:A", "Operator:OpA", "Alias:alpha point", "Plan:PL-v", "Source:driver:driver_voice",
            "Channel:driver_voice", "Channel:google_review"} <= ids
    assert next(e for e in v.edges if e["type"] == "STOPS_AT")["props"] == {"order": 0}
    assert viewed_graph().charger_neighbourhood("NOPE") is None


def test_a_view_shows_only_the_latest_reports_per_charger() -> None:
    g = InMemoryGraph()
    g.add_chargers([charger("A")])
    g.add_reports([report("A", f"user-{i:03d}", minutes=i) for i in range(9)])
    reported = [e for e in g.charger_neighbourhood("A").edges if e["type"] == "REPORTED"]
    assert len(reported) == 5 and {e["from"] for e in reported} == {f"Source:user-{i:03d}" for i in range(4, 9)}


def test_the_in_memory_counts_include_the_new_structure() -> None:
    nodes, rels = viewed_graph().structure_counts()
    # sites A (A+B) and C = 2; SiteType unknown + mall = 2; channels google_review, driver_voice, app_user = 3; plan 1; alias 1
    assert nodes == 2 + 2 + 3 + 1 + 1
    # AT 3 + OF_TYPE 2 + VIA 3 sources + plan edges 3 + NAMES 1
    assert rels == 3 + 2 + 3 + 3 + 1
