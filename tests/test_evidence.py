"""F5 real charger evidence: matching, channel reports, trust, analyses. Synthetic in-test rows only."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

import experiments.e3_real_evidence as e3r
from verity import evidence as ev
from verity.corpus import APP_SNAPSHOTS, CHARGER_EVIDENCE, MATCH_OVERRIDES
from verity.schemas import ChargerRecord, Connector
from verity.trust.engine import TrustEngine

NOW = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)
DIRECTORY = [
    ChargerRecord(id="CH-0001", name="Tata Power Sector 18", lat=28.5700, lon=77.3200, operator="Tata Power",
                  site_type="mall", connectors=[Connector(type="CCS2", kw=30.0)], source_ids=["osm:node/11"]),
    ChargerRecord(id="CH-0002", name="Statiq Highway Plaza", lat=28.0000, lon=77.6000, operator="Statiq",
                  site_type="highway", connectors=[], source_ids=["ocm:22"]),
    ChargerRecord(id="CH-0003", name="EV charging station (OpenStreetMap)", lat=28.4000, lon=77.1000, operator="",
                  site_type="unknown", connectors=[], source_ids=["osm:node/33"]),
]


def ev_row(rid: str, **kw: str) -> dict:
    base = {"evidence_id": rid, "charger_name": "Tata Power Sector 18", "operator": "Tata Power",
            "landmark_or_address": "", "lat": "", "lon": "", "place_url": "", "source_kind": "google_review",
            "evidence_date": "2026-11-01", "evidence_time": "", "status": "working", "connector": "", "rated_kw": "",
            "queue_count": "", "source_url": "https://example.org/r", "paraphrase": "", "collected_by": "t",
            "verified": "yes"}
    return {**base, **kw}


def snap_row(rid: str, **kw: str) -> dict:
    base = {"snapshot_id": rid, "app": "tata_ez_charge", "charger_name": "Tata Power Sector 18",
            "charger_ref_in_app": "", "datetime_ist": "2026-11-01 18:00", "status_shown": "available",
            "connectors_available": "", "rated_kw": "", "collected_by": "t"}
    return {**base, **kw}


def test_name_similarity_ignores_generic_words() -> None:
    assert ev.name_similarity("Tata Power EZ Charge Sector 18", "Tata Power") == 1.0
    assert ev.name_similarity("Statiq hub", "EV charging station (OpenStreetMap)") == 0.0


def test_place_url_links_and_coordinates_are_read() -> None:
    assert ev.directory_link("https://www.openstreetmap.org/node/11") == "osm:node/11"
    assert ev.directory_link("https://openchargemap.org/site/poi/details/22") == "ocm:22"
    assert ev.url_location("https://www.google.com/maps/place/X/@28.1,77.1,17z/data=!3d28.57!4d77.32") == (28.57, 77.32)
    assert ev.url_location("https://maps.google.com/?q=28.5,77.3") == (28.5, 77.3)
    assert ev.url_location("https://example.org") is None


def test_matching_rules_and_review_list() -> None:
    rows = [ev_row("E1", lat="28.5703", lon="77.3201"),  # ~35 m, same name
            ev_row("E2", place_url="https://www.openstreetmap.org/node/33", charger_name="Unnamed"),
            ev_row("E3", lat="28.4002", lon="77.1001", charger_name="Jio-bp pulse"),  # near CH-0003, name differs
            ev_row("E4", lat="28.9", lon="77.9"),  # nothing near
            ev_row("E5", place_url="https://maps.google.com/?q=28.5703,77.3201"),
            ev_row("E6", place_url="https://maps.google.com/?q=28.5703,77.3201", charger_name="zzz",
                   operator=""),  # same url as E5
            ev_row("E7", charger_name="anything")]
    m = ev.match_evidence(rows, DIRECTORY, {"E7": "CH-0002"})
    assert (m["E1"].charger_id, m["E1"].method) == ("CH-0001", "location_name") and m["E1"].distance_m < 100
    assert (m["E2"].charger_id, m["E2"].method) == ("CH-0003", "directory_link")
    assert m["E3"].charger_id is None and m["E3"].candidate_id == "CH-0003" and "similar name" in m["E3"].reason
    assert m["E4"].charger_id is None and "no directory charger" in m["E4"].reason
    assert m["E6"].method == "same_place_url" and m["E6"].charger_id == "CH-0001"
    assert (m["E7"].charger_id, m["E7"].method) == ("CH-0002", "manual")
    assert {r["row_id"] for r in ev.review_list(list(m.values()))} == {"E3", "E4"}
    bad = ev.match_evidence([ev_row("E8")], DIRECTORY, {"E8": "CH-9999"})
    assert bad["E8"].charger_id is None and "unknown charger" in bad["E8"].reason


def test_snapshots_match_by_evidence_name_then_app_ref() -> None:
    rows = [ev_row("E1", lat="28.5703", lon="77.3201")]
    em = ev.match_evidence(rows, DIRECTORY, {})
    snaps = [snap_row("S1", charger_ref_in_app="TP-9"),
             snap_row("S2", charger_name="other name", charger_ref_in_app="TP-9"), snap_row("S3", charger_name="nobody")]
    sm = ev.match_snapshots(snaps, rows, em, {c.id for c in DIRECTORY}, {})
    assert sm["S1"].method == "same_name_as_evidence" and sm["S2"].method == "same_app_ref"
    assert sm["S3"].charger_id is None


def _items(rows: list[dict], snaps: list[dict]) -> list[ev.Item]:
    em = ev.match_evidence(rows, DIRECTORY, {})
    sm = ev.match_snapshots(snaps, rows, em, {c.id for c in DIRECTORY}, {})
    return ev.items_from(rows, snaps, em, sm)


def test_reports_are_real_one_source_per_channel_and_never_collide() -> None:
    rows = [ev_row(f"E{i}", lat="28.5703", lon="77.3201", status=s)
            for i, s in enumerate(["working", "broken", "unknown", "payment_failed"])]
    reports = ev.reports_from(_items(rows, []))
    assert len(reports) == 3  # 'unknown' says nothing about the charger
    assert {r.source_id for r in reports} == {"public:google_review"} and not any(r.is_simulated for r in reports)
    assert len({r.t for r in reports}) == 3  # same day, no time: moved by seconds instead of replacing each other
    assert {r.status for r in reports} == {"working", "broken", "blocked"}


def test_dawid_skene_learns_a_channel_that_always_says_working() -> None:
    rows, snaps = [], []
    for d in range(1, 13):  # public reviews say broken on even days; the app always says available
        status = "broken" if d % 2 == 0 else "working"
        rows.append(ev_row(f"E{d}", lat="28.5703", lon="77.3201", status=status, evidence_date=f"2026-11-{d:02d}"))
        snaps.append(snap_row(f"S{d}", datetime_ist=f"2026-11-{d:02d} 13:00"))
    items = _items(rows, snaps)
    fit = TrustEngine(DIRECTORY, use_rings=False, use_usage=False).estimate(ev.reports_from(items), NOW)
    assert fit.source_spec["public:operator_app"] < fit.source_spec["public:google_review"]
    out = ev.app_vs_failures(items)
    assert out["n_failure_reports"] == 6 and out["n_with_snapshots_in_window"] == 6
    assert out["rate"] == 1.0 and out["ci95"][1] == 1.0


def test_working_rates_by_operator_and_site_with_n() -> None:
    rows = [ev_row("E1", lat="28.5703", lon="77.3201"), ev_row("E2", lat="28.5703", lon="77.3201", status="broken"),
            ev_row("E3", place_url="https://openchargemap.org/site/poi/details/22", operator="Statiq")]
    rates = ev.working_rates(_items(rows, [snap_row("S1")]), {c.id: c for c in DIRECTORY})
    assert rates["n_reports"] == 3  # the snapshot is not counted
    tata = next(r for r in rates["by_operator"] if r["group"] == "tata power")
    assert (tata["n_reports"], tata["n_working"], tata["thin"]) == (2, 1, True) and tata["ci95"] is not None
    assert {r["group"] for r in rates["by_site_type"]} == {"mall", "highway"}


def test_rated_kw_from_operator_app_updates_new_records_only() -> None:
    rows = [ev_row("E1", lat="28.5703", lon="77.3201", source_kind="operator_app", connector="CCS2", rated_kw="60"),
            ev_row("E2", place_url="https://openchargemap.org/site/poi/details/22", source_kind="operator_app",
                   rated_kw="25")]
    updates = ev.rated_kw_updates(_items(rows, []))
    new = {c.id: c for c in ev.apply_rated_kw(DIRECTORY, updates)}
    assert new["CH-0001"].connectors[0].kw == 60.0 and DIRECTORY[0].connectors[0].kw == 30.0
    assert new["CH-0002"].connectors[0].type == "unspecified" and new["CH-0002"].connectors[0].kw == 25.0
    assert new["CH-0003"] is DIRECTORY[2]


def _write(path: Path, header: list[str], rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, header)
        writer.writeheader()
        writer.writerows(rows)


def _directory_file(tmp_path: Path) -> Path:
    path = tmp_path / "chargers.json"
    path.write_text(json.dumps({"bboxes": [], "chargers": [c.model_dump() for c in DIRECTORY]}), encoding="utf-8")
    return path


def test_run_end_to_end_excludes_unverified_and_reports_n(tmp_path: Path) -> None:
    _write(tmp_path / CHARGER_EVIDENCE.filename, CHARGER_EVIDENCE.header,
           [ev_row("E1", lat="28.5703", lon="77.3201", status="broken"),
            ev_row("E2", lat="28.5703", lon="77.3201", verified="no")])
    _write(tmp_path / APP_SNAPSHOTS.filename, APP_SNAPSHOTS.header, [snap_row("S1", datetime_ist="2026-11-01 20:00")])
    _write(tmp_path / MATCH_OVERRIDES.filename, MATCH_OVERRIDES.header, [])
    out = e3r.run(tmp_path, NOW, e3=False, chargers_file=_directory_file(tmp_path), write_side_files=False)
    assert out["is_simulated"] is False and out["inputs"]["evidence_rows_usable"] == 1
    assert out["n_reports"] == 2 and out["app_vs_failures"]["n_app_showed_available"] == 1
    assert out["graph"]["in_memory"]["n_reports"] == 2 and out["graph"]["neo4j"] == "skipped: VERITY_GRAPH=memory"
    assert out["charger_trust"][0]["charger_id"] == "CH-0001"


def test_run_on_empty_corpus_is_honest(tmp_path: Path) -> None:
    out = e3r.run(tmp_path, NOW, e3=False, chargers_file=_directory_file(tmp_path), write_side_files=False)
    assert out["n_reports"] == 0 and out["graph"]["neo4j"].startswith("skipped")
    assert out["app_vs_failures"]["rate"] is None and out["working_rates"]["n_reports"] == 0
