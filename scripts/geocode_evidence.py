"""Locate verified charger-evidence rows that have no coordinates by geocoding their address (WP3).

Each row's address (then its charger name + address) goes to Photon through the WP1 geocoder, which has its own
cache and request budget. Only a street- or POI-level result is kept (never a city, suburb or postcode), so a report
lands on a charger only when the geocode is within 250 m of it AND the names agree (verity.evidence). Rows that
already carry lat/lon or a map-link pin are skipped: the collector's location always wins.

Output: data/processed/evidence_geocoded.json (coordinates are Photon's, never typed in).
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import datetime, timezone

from verity.corpus import CHARGER_EVIDENCE
from verity.evidence import row_location
from verity.evidence_load import GEOCODED_FILE, corpus_rows, is_precise_kind
from verity.geocode import MAX_QUERY_CHARS, geocode, geocode_client
from verity.paths import CORPUS_DIR
from verity.routing import NetworkUnavailable, RequestBudgetExceeded

CANDIDATES = 3  # results looked at per query


def queries_for(row: dict) -> list[str]:
    addr, name = row.get("landmark_or_address", "").strip(), row.get("charger_name", "").strip()
    out = [q for q in (addr, f"{name}, {addr}" if name and addr else name) if q]
    return [q[:MAX_QUERY_CHARS] for q in dict.fromkeys(out)]


def main() -> int:
    client = geocode_client()
    rows = [r for r in corpus_rows(CORPUS_DIR, CHARGER_EVIDENCE) if row_location(r)[0] is None]
    found: dict[str, dict | None] = {}
    kinds: Counter = Counter()
    stopped = False
    for row in rows:
        if stopped:
            break
        hit = None
        for q in queries_for(row):
            try:
                places = geocode(client, q, CANDIDATES)
            except (NetworkUnavailable, RequestBudgetExceeded) as exc:
                print(f"geocoder unavailable ({type(exc).__name__}); stopping, {len(found)} rows done")
                stopped = True
                break
            except ValueError:
                continue
            kinds.update(p.kind for p in places)
            hit = next((p for p in places if is_precise_kind(p.kind)), None)
            if hit:
                hit = {"lat": hit.lat, "lon": hit.lon, "kind": hit.kind, "label": hit.name, "query": q}
                break
        if not stopped:
            found[row["evidence_id"]] = hit
    out = {"is_simulated": False, "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "source": "Photon (komoot), OpenStreetMap data; street/POI-level results only",
           "rows": found}
    GEOCODED_FILE.write_text(json.dumps(out, indent=1), encoding="utf-8")
    n_hit = sum(v is not None for v in found.values())
    print(f"rows without a location: {len(found)} | geocoded precisely: {n_hit} | result kinds seen: "
          f"{kinds.most_common(12)} | requests made: {client.requests_made()} -> {GEOCODED_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
