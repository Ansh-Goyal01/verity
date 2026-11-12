"""F3: enrich verified public Indian trips -> data/processed/trips_india_enriched.json (REAL, with provenance).

    uv run python scripts/enrich_trips.py                       # verified=yes rows only (the real output)
    uv run python scripts/enrich_trips.py --include-unverified  # plumbing check -> results/dev/ (never a result)

Only rows that are verified=yes and free of validator ERRORs are enriched for the real output. Every
response is cached in data/cache/ and counts towards the persistent request budget.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from verity.corpus import ERROR, TRIPS, VEHICLES, Row, read_csv, usable_rows, validate_row
from verity.enrich import SCHEMA, enrich_record
from verity.paths import CORPUS_DIR, PROCESSED_DIR, RESULTS_DIR
from verity.routing import CachedClient

OUTPUT = PROCESSED_DIR / "trips_india_enriched.json"
DEV_OUTPUT = RESULTS_DIR / "dev" / "trips_india_enriched_UNVERIFIED.json"


def select_rows(rows: list[Row], include_unverified: bool) -> list[Row]:
    if not include_unverified:
        return usable_rows(TRIPS, rows)
    cleaned = [{k: (v or "").strip() for k, v in row.items() if isinstance(k, str)} for row in rows]
    return [r for r in cleaned if not any(level == ERROR for level, _ in validate_row(TRIPS, r))]


def summarise(records: list[dict]) -> dict:
    def ok(rec: dict, key: str) -> bool:
        return isinstance(rec.get(key), dict) and rec[key].get("value") is not None

    return {"n_records": len(records), "n_routed": sum(ok(r, "route") for r in records),
            "n_with_weather": sum(ok(r, "weather") for r in records),
            "n_with_segments": sum(ok(r, "segments") for r in records),
            "n_distance_flagged": sum(bool(r.get("distance_check", {}).get("flag")) for r in records),
            "by_record_type": {t: sum(r["record_type"] == t for r in records)
                               for t in ("soc_trip", "consumption", "range_test")}}


def run(corpus: Path, output: Path, include_unverified: bool, client: CachedClient | None = None,
        sleep: Callable[[float], None] = time.sleep) -> dict:
    _, trips = read_csv(corpus / TRIPS.filename)
    _, vehicles = read_csv(corpus / VEHICLES.filename)
    rows = select_rows(trips, include_unverified)
    client = client or CachedClient()
    records = [enrich_record(client, row, vehicles, sleep) for row in rows]
    label = ("PLUMBING CHECK ONLY: includes unverified rows; never used in any result" if include_unverified
             else "REAL: public self-reported Indian trips (verified=yes rows only)")
    doc = {"schema": SCHEMA, "is_simulated": False, "label": label,
           "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "n_rows_in_corpus": len(trips), "n_selected": len(rows), "summary": summarise(records),
           "requests_made_total": client.requests_made(), "records": records}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(doc, indent=1, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    return doc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Enrich verified Indian trips with route, elevation, weather.")
    parser.add_argument("--include-unverified", action="store_true",
                        help="plumbing check on draft rows too; writes results/dev/ (never a result)")
    args = parser.parse_args(argv)
    output = DEV_OUTPUT if args.include_unverified else OUTPUT
    doc = run(CORPUS_DIR, output, args.include_unverified)
    print(json.dumps({"output": str(output), "n_rows_in_corpus": doc["n_rows_in_corpus"],
                      "n_selected": doc["n_selected"], **doc["summary"],
                      "requests_made_total": doc["requests_made_total"]}, indent=1))
    for rec in doc["records"]:
        check = rec.get("distance_check", {})
        if check.get("flag"):
            print(f"FLAG {rec['record_id']}: reported distance differs from the route by "
                  f"{check['mismatch_frac']:.0%} (expected {check['expected_km']} km) {check['note']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
