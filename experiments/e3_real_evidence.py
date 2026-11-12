"""F5: real public charger evidence -> matched chargers, per-channel trust, analyses and E3-real (REAL data).

    uv run python experiments/e3_real_evidence.py                        # writes results/e3_real_evidence.json
    VERITY_GRAPH=memory uv run python experiments/e3_real_evidence.py    # never touch Neo4j

Reads data/corpus/charger_evidence.csv (verified=yes, no validator ERROR), app_status_snapshots.csv (no ERROR)
and charger_match_overrides.csv; matches rows to data/processed/chargers_ncr.json (unmatched rows ->
data/processed/charger_evidence_review.csv for a human); turns them into dated REPORTED edges from one Source per
channel (is_simulated=false) in the in-memory graph and, when it answers, Neo4j; learns per-channel trust with the
Dawid-Skene engine; and reports, always with n: working rate by operator and site type, how often the operator app
showed 'available' within 48 h of a dated failure report, rated kW seen in operator apps, and E3-real (on verified
NCR / corridor trips, how often trust-aware planning on this evidence changes the charger choice vs reliability-blind).
"""

from __future__ import annotations

import csv
import dataclasses
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from verity import evidence as ev
from verity import india_eval as ie
from verity.chargers import CHARGERS_FILE, chargers_near_route, load_chargers
from verity.corpus import APP_SNAPSHOTS, CHARGER_EVIDENCE, MATCH_OVERRIDES, VEHICLES, FileSpec, read_csv, usable_rows
from verity.dispatch import VehicleContext, plan_only
from verity.forecast import ForecastConfig
from verity.paths import CORPUS_DIR, PROCESSED_DIR, RESULTS_DIR
from verity.schemas import ChargerRecord, Report
from verity.trust.engine import TrustEngine, TrustReport
from verity.trust.graph import InMemoryGraph, Neo4jGraph

OUTPUT = RESULTS_DIR / "e3_real_evidence.json"
REVIEW_CSV = PROCESSED_DIR / "charger_evidence_review.csv"
RATED_KW_FILE = PROCESSED_DIR / "charger_rated_kw_from_apps.json"
ENRICHED = PROCESSED_DIR / "trips_india_enriched.json"
CORRIDOR_KM = 3.0  # same corridor width as the API (api/service.py CORRIDOR_KM)
TARGET = 0.9
MIN_OK_TRIPS = 15
CFG = ForecastConfig(n_runs=800)
LABEL = "REAL: hand-collected public charger evidence (verified=yes rows only)"


def _rows(corpus: Path, spec: FileSpec) -> list[dict]:
    path = corpus / spec.filename
    return usable_rows(spec, read_csv(path)[1]) if path.exists() else []


def _round(x: float | None) -> float | None:
    return None if x is None else round(x, 3)


def channel_trust(items: list[ev.Item], reports: list[Report], fit: TrustReport | None) -> list[dict]:
    out = []
    for ch in ev.CHANNELS:
        src = ev.channel_source_id(ch)
        out.append({"channel": ch, "source_id": src, "n_rows": sum(i.channel == ch for i in items),
                    "n_reports": sum(r.source_id == src for r in reports),
                    "sensitivity": None if fit is None else _round(fit.source_sens.get(src)),
                    "specificity": None if fit is None else _round(fit.source_spec.get(src))})
    return out


# ---------------------------------------------------------------- graph
def write_graph(chargers: list[ChargerRecord], reports: list[Report]) -> dict:
    """REPORTED edges from one Source per channel, in memory always and in Neo4j when it answers (no pruning)."""
    involved = {r.charger_id for r in reports}
    used = [c for c in chargers if c.id in involved]
    mem = InMemoryGraph()
    mem.add_chargers(used)
    mem.add_reports(reports)
    status: dict = {"in_memory": {"n_chargers": len(used), "n_reports": len(mem.reports()),
                                  "sources": sorted({r.source_id for r in mem.reports()})}}
    if not reports:
        status["neo4j"] = "skipped: no real evidence rows yet"
    elif os.environ.get("VERITY_GRAPH", "auto") == "memory":
        status["neo4j"] = "skipped: VERITY_GRAPH=memory"
    else:
        status["neo4j"] = _write_neo4j(used, reports)
    return status


def _write_neo4j(used: list[ChargerRecord], reports: list[Report]) -> str:
    graph = None
    try:
        graph = Neo4jGraph.from_env()
        if graph is None:
            return "skipped: no Neo4j credentials"
        graph.verify_connectivity()
        graph.ensure_constraints()
        graph.add_chargers(used)
        graph.add_reports(reports)
        return f"written: {len(reports)} REPORTED edges on {len(used)} chargers"
    except Exception as exc:  # noqa: BLE001 - class name only: messages may echo the connection string
        return f"unavailable ({type(exc).__name__}); in-memory only"
    finally:
        if graph is not None:
            try:
                graph.close()
            except Exception:  # noqa: BLE001 - closing a dead link may fail
                pass


# ---------------------------------------------------------------- E3-real
def _inside(lat: float, lon: float, boxes: list) -> bool:
    return any(b[0] <= lat <= b[2] and b[1] <= lon <= b[3] for b in boxes)


def e3_cases(enriched: dict, vehicles: dict, boxes: list) -> tuple[list[tuple[dict, ie.Case]], list[dict]]:
    """Verified, routed soc_trips with an end inside the directory boxes (NCR, Noida-Agra corridor)."""
    keep, skipped = [], []
    for rec in enriched.get("records", []):
        ends = [(rec.get(k) or {}).get("value") for k in ("origin", "destination")]
        if rec["record_type"] != "soc_trip" or None in ends or not (rec.get("route") or {}).get("value"):
            continue
        if not any(_inside(e["lat"], e["lon"], boxes) for e in ends):
            continue
        case, why = ie.case_from_record(rec, vehicles)
        if case is None or case.distance_flag or case.start_soc is None:
            skipped.append({"record_id": rec["record_id"], "why": why or "distance flag or no start SoC"})
            continue
        keep.append((rec, case))
    return keep, skipped


def plan_case(rec: dict, case: ie.Case, chargers: list[ChargerRecord], trust: dict, client: object) -> dict:
    from verity.routing import fetch_route  # cached network call, only when a real case exists

    a, b = rec["origin"]["value"], rec["destination"]["value"]
    route = fetch_route(client, (a["lat"], a["lon"]), (b["lat"], b["lon"]))
    scale = (case.distance_km or route.distance_km) / route.distance_km  # planned_route scales to the reported km
    near = [dataclasses.replace(rc, km_along=rc.km_along * scale)
            for rc in chargers_near_route(chargers, route.coords, route.cum_m / 1000.0, CORRIDOR_KM)]
    temp = case.temp_c if case.temp_c is not None else ie.CALIBRATION_TEMP_C
    planned, _ = ie.planned_route(case, case.speed_band, temp)
    vehicle = VehicleContext(case.vehicle_id, case.prior_key, ie.prior_posterior(case), case.passengers or 1,
                             True if case.ac is None else case.ac, 0)
    stops = {}
    for aware in (False, True):
        chosen, _, _ = plan_only(vehicle, planned, near, trust, start_soc=case.start_soc, target_confidence=TARGET,
                                 cfg=CFG, trust_aware=aware)
        stops[aware] = [s.node.id for s in chosen]
    first = {k: (v[0] if v else None) for k, v in stops.items()}
    return {"record_id": case.record_id, "n_route_chargers": len(near), "stops_blind": stops[False],
            "stops_trust_aware": stops[True], "first_stop_changed": first[False] != first[True],
            "stop_set_changed": stops[False] != stops[True],
            "n_route_chargers_with_real_reports": sum(trust[rc.charger.id].n_reports > 0 for rc in near)}


def run_e3_real(chargers: list[ChargerRecord], trust: dict, boxes: list, enriched_path: Path = ENRICHED) -> dict:
    enriched = json.loads(enriched_path.read_text(encoding="utf-8")) if enriched_path.exists() else {}
    _, vrows = read_csv(CORPUS_DIR / VEHICLES.filename)
    cases, skipped = e3_cases(enriched, {v["vehicle_id"]: v for v in vrows}, boxes)
    base = {"label": "REAL trips + REAL evidence; planner inputs as in F4 (vehicle prior, AC on when unknown)",
            "n_trips": len(cases), "skipped": skipped}
    if not cases:
        return {**base, "status": "NOT_RUN",
                "why": "no verified soc_trip with a routed origin/destination inside the NCR / Noida-Agra boxes yet"}
    from verity.routing import CachedClient

    client = CachedClient()
    trips = [plan_case(rec, case, chargers, trust, client) for rec, case in cases]
    k = sum(t["first_stop_changed"] for t in trips)
    return {**base, "status": "PRELIMINARY" if len(trips) < MIN_OK_TRIPS else "OK", "n_first_stop_changed": k,
            "first_stop_change_rate": round(k / len(trips), 4), "ci95": ev.wilson(k, len(trips)), "trips": trips}


# ---------------------------------------------------------------- main
def run(corpus: Path = CORPUS_DIR, now: datetime | None = None, e3: bool = True,
        chargers_file: Path = CHARGERS_FILE, write_side_files: bool = True) -> dict:
    now = now or datetime.now(timezone.utc)
    directory, meta = load_chargers(chargers_file)
    ev_rows, snap_rows = _rows(corpus, CHARGER_EVIDENCE), _rows(corpus, APP_SNAPSHOTS)
    overrides = {r["row_id"]: r["charger_id"] for r in _rows(corpus, MATCH_OVERRIDES)}
    ev_m = ev.match_evidence(ev_rows, directory, overrides)
    snap_m = ev.match_snapshots(snap_rows, ev_rows, ev_m, {c.id for c in directory}, overrides)
    items = ev.items_from(ev_rows, snap_rows, ev_m, snap_m)
    reports = ev.reports_from(items)
    updates = ev.rated_kw_updates(items)
    chargers = ev.apply_rated_kw(directory, updates)
    # Rings are off: public channels co-report on popular chargers by nature, which is not collusion.
    fit = TrustEngine(chargers, use_rings=False, use_usage=False).estimate(reports, now) if reports else None
    trust = fit.chargers if fit else TrustEngine(chargers).estimate([], now).chargers
    review = ev.review_list([*ev_m.values(), *snap_m.values()])
    result = {
        "experiment": "F5 real charger evidence + E3-real", "is_simulated": False, "label": LABEL,
        "generated_at": now.isoformat(timespec="seconds"),
        "inputs": {"evidence_rows_usable": len(ev_rows), "snapshot_rows_usable": len(snap_rows),
                   "match_overrides": len(overrides), "directory_chargers": len(directory)},
        "matching": {"evidence": ev.match_summary(list(ev_m.values())),
                     "snapshots": ev.match_summary(list(snap_m.values())), "n_review": len(review),
                     "review_file": "data/processed/charger_evidence_review.csv"},
        "n_reports": len(reports), "channels": channel_trust(items, reports, fit),
        "charger_trust": [{"charger_id": cid, "p_works": round(t.p_works, 3), "n_reports_60d": t.n_reports,
                           "trust_level": t.trust_level} for cid, t in sorted(trust.items()) if t.n_reports > 0],
        "working_rates": ev.working_rates(items, {c.id: c for c in chargers}),
        "app_vs_failures": ev.app_vs_failures(items),
        "rated_kw_from_apps": {"n_chargers": len(updates), "file": "data/processed/charger_rated_kw_from_apps.json"},
        "graph": write_graph(chargers, reports),
        "notes": ["Rows with verified != yes and rows with validator ERRORs are excluded.",
                  "Evidence with no time is placed at 12:00 IST (+-12 h).",
                  "Groups with n_reports < 5 are marked thin: quote them as anecdotes, not rates."],
    }
    result["e3_real"] = (run_e3_real(chargers, trust, meta.get("bboxes", [])) if e3
                         else {"status": "NOT_RUN", "why": "disabled", "n_trips": 0})
    if write_side_files:
        _write_side_files(review, updates)
    return result


def _write_side_files(review: list[dict], updates: dict) -> None:
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    with REVIEW_CSV.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, ["file", "row_id", "reason", "nearest_candidate", "nearest_distance_m", "name_score"])
        writer.writeheader()
        writer.writerows(review)
    RATED_KW_FILE.write_text(json.dumps({"is_simulated": False, "source": "operator apps via the hand-collected corpus",
                                         "updates": updates}, indent=1), encoding="utf-8")


def main() -> int:
    result = run()
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(result, indent=1), encoding="utf-8")
    m, e3 = result["matching"], result["e3_real"]
    print(f"{LABEL}\n evidence rows {result['inputs']['evidence_rows_usable']}, snapshots "
          f"{result['inputs']['snapshot_rows_usable']}, matched {m['evidence']['n_matched']}+"
          f"{m['snapshots']['n_matched']}, review list {m['n_review']}, reports {result['n_reports']}\n"
          f" graph: {result['graph']['neo4j']}\n E3-real: {e3['status']} (n={e3['n_trips']})\n -> {OUTPUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
