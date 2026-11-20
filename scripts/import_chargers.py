"""Build the NCR + Noida-Agra corridor charger directory (Prompt 7): OpenStreetMap + Open Charge Map.

* OpenStreetMap via the Overpass API (keyless). One request, cached in data/cache/.
* Open Charge Map needs a free API key (OCM_API_KEY in .env). No key -> that source is reported BLOCKED
  and skipped; nothing is invented to fill the gap.
* Duplicates within 50 m are merged; every record keeps which source(s) it came from.
* Output: data/processed/chargers_ncr.json (charger-record contract). If the network fails, a clearly
  labelled SYNTHETIC corridor list is written instead (is_simulated = true).
* Loading into Neo4j happens only when NEO4J_* credentials exist.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import datetime, timezone

import numpy as np

from verity.chargers import (
    CHARGERS_FILE,
    INDIA_CHARGERS_FILE,
    NCR_CHARGERS_FILE,
    assign_ids,
    extend_directory,
    load_chargers,
    merge_duplicates,
    parse_ocm_poi,
    parse_osm_element,
    site_type_of,
)
from verity.routing import OVERPASS_URL, CachedClient, NetworkUnavailable, RequestBudgetExceeded
from verity.schemas import ChargerRecord, Connector
from verity.trust.graph import Neo4jGraph, load_env

# NCR (Delhi, Noida, Gurugram, Faridabad, Ghaziabad) and the corridor down to Agra.
BBOXES = ((28.2, 76.8, 29.0, 77.8), (27.1, 77.3, 28.2, 78.1))  # south, west, north, east
OCM_URL = "https://api.openchargemap.io/v3/poi/"


def overpass_query() -> str:
    parts = "".join(f'node["amenity"="charging_station"]({s},{w},{n},{e});way["amenity"="charging_station"]({s},{w},{n},{e});'
                    for s, w, n, e in BBOXES)
    return f"[out:json][timeout:90];({parts});out center tags;"


def fetch_osm(client: CachedClient) -> list[dict]:
    payload, _ = client.post_json(OVERPASS_URL, {"data": overpass_query()})
    return payload.get("elements", [])


def fetch_ocm(client: CachedClient, key: str) -> list[dict]:
    """Open Charge Map points in India inside the two boxes. The key travels in a header: never cached/logged."""
    pois: list[dict] = []
    for s, w, n, e in BBOXES:
        payload, _ = client.get_json(OCM_URL, {"output": "json", "countrycode": "IN", "maxresults": 2000,
                                               "boundingbox": f"({s},{w}),({n},{e})", "compact": "true", "verbose": "false"},
                                     headers={"X-API-Key": key})
        pois.extend(payload)
    return pois


INDIA_TIMEOUT_S = 240.0
OCM_PAGE = 5000  # Open Charge Map results per request for all of India


def overpass_india_query() -> str:
    """Every amenity=charging_station inside India's national boundary (OSM admin_level 2, ISO3166-1 = IN)."""
    return ('[out:json][timeout:180];area["ISO3166-1"="IN"][admin_level=2]->.in;'
            '(node["amenity"="charging_station"](area.in);way["amenity"="charging_station"](area.in););out center tags;')


def fetch_ocm_india(client: CachedClient, key: str) -> list[dict]:
    payload, _ = client.get_json(OCM_URL, {"output": "json", "countrycode": "IN", "maxresults": OCM_PAGE,
                                           "compact": "false", "verbose": "false"}, headers={"X-API-Key": key})
    return payload


def main_india() -> int:
    """WP3: the frozen NCR snapshot (ids unchanged) + every other Indian charger. No synthetic fallback: on a network
    failure nothing is written and the API keeps using the NCR file."""
    base, base_meta = load_chargers(NCR_CHARGERS_FILE)
    if base_meta.get("is_simulated"):
        print("The NCR base file is SYNTHETIC; rebuild it with the plain import first.")
        return 1
    client = CachedClient(timeout=INDIA_TIMEOUT_S)
    try:
        payload, _ = client.post_json(OVERPASS_URL, {"data": overpass_india_query()})
    except (NetworkUnavailable, RequestBudgetExceeded) as exc:
        print(f"OpenStreetMap fetch failed ({exc}); nothing written, the API keeps {NCR_CHARGERS_FILE.name}")
        return 1
    parsed = [parse_osm_element(el) for el in payload.get("elements", [])]
    osm = [r for r, _ in parsed if r is not None]
    records, ocm_n, ocm_status = list(osm), None, ""
    key = load_env().get("OCM_API_KEY", "")
    if not key:
        ocm_status = "BLOCKED: Open Charge Map needs a free API key (OCM_API_KEY in .env); not fetched"
    else:
        try:
            ocm = [r for r in (parse_ocm_poi(p) for p in fetch_ocm_india(client, key)) if r is not None]
            ocm_n, ocm_status = len(ocm), ("fetched" if len(ocm) < OCM_PAGE else f"fetched; hit the {OCM_PAGE} page cap")
            records += ocm
        except (NetworkUnavailable, RequestBudgetExceeded, KeyError, TypeError) as exc:
            ocm_status = f"FAILED: {type(exc).__name__}"
    frozen = base_meta.get("bboxes") or [list(b) for b in BBOXES]
    final, stats = extend_directory(base, records, frozen)
    out = {
        "is_simulated": False, "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "scope": "India (OSM area ISO3166-1=IN)", "base_file": NCR_CHARGERS_FILE.name,
        "base_generated_at": base_meta.get("generated_at"), "frozen_bboxes": frozen, "bboxes": frozen,
        "sources": {"osm_records": len(osm), "ocm_records": ocm_n, "ocm_status": ocm_status},
        "extend": stats, "connectors_dropped_no_tagged_power": sum(d for _, d in parsed),
        "note": ("NCR + Noida-Agra are the frozen base snapshot (ids CH-0001.. unchanged); new chargers inside those "
                 "boxes are not added. site_type is a keyword heuristic; untagged connector power is dropped."),
        "chargers": [r.model_dump(mode="json") for r in final],
    }
    INDIA_CHARGERS_FILE.write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
    print(f"OSM {len(osm)} | OCM {ocm_n} ({ocm_status}) | {stats} | total {len(final)} -> {INDIA_CHARGERS_FILE}")
    print("top operators:", Counter(r.operator or "(none)" for r in final).most_common(10))
    print(f"requests made in total: {client.requests_made()}")
    return 0


def synthetic_corridor() -> list[ChargerRecord]:
    """SYNTHETIC fallback chargers along the Noida-Agra line, used only when the network is unavailable."""
    out = []
    for i, f in enumerate(np.linspace(0.05, 0.95, 12)):
        out.append(ChargerRecord(id=f"synthetic-{i}", name=f"Synthetic corridor charger {i + 1}", lat=28.5355 + f * (27.1767 - 28.5355),
                                 lon=77.391 + f * (78.0081 - 77.391), operator="SimOp", site_type="highway",
                                 connectors=[Connector(type="CCS2", kw=60.0)], source_ids=["synthetic"]))
    return out


def main() -> int:
    client = CachedClient()
    stats: dict = {"osm": None, "ocm": None, "ocm_status": ""}
    records: list[ChargerRecord] = []
    dropped_connectors = 0
    simulated = False
    try:
        elements = fetch_osm(client)
        parsed = [parse_osm_element(el) for el in elements]
        osm_records = [r for r, _ in parsed if r is not None]
        dropped_connectors = sum(d for _, d in parsed)
        stats["osm"] = len(osm_records)
        records += osm_records
    except (NetworkUnavailable, RequestBudgetExceeded) as exc:
        print(f"OpenStreetMap fetch failed ({exc}); using the SYNTHETIC fallback list")
        records, simulated = synthetic_corridor(), True

    key = load_env().get("OCM_API_KEY", "")
    if not key:
        stats["ocm_status"] = "BLOCKED: Open Charge Map needs a free API key (OCM_API_KEY in .env); not fetched"
    else:
        try:
            ocm = [r for r in (parse_ocm_poi(p) for p in fetch_ocm(client, key)) if r is not None]
            stats["ocm"], stats["ocm_status"] = len(ocm), "fetched"
            records += ocm
        except (NetworkUnavailable, RequestBudgetExceeded, KeyError, TypeError) as exc:
            stats["ocm_status"] = f"FAILED: {type(exc).__name__}"

    merged, n_merged = merge_duplicates(records)
    final = assign_ids(merged)
    n_both = sum(1 for r in final if any(s.startswith("osm:") for s in r.source_ids) and any(s.startswith("ocm:") for s in r.source_ids))
    out = {
        "is_simulated": simulated, "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "bboxes": [list(b) for b in BBOXES],
        "sources": {"osm_records": stats["osm"], "ocm_records": stats["ocm"], "ocm_status": stats["ocm_status"]},
        "merged_away_within_50m": n_merged, "records_in_both_sources": n_both,
        "connectors_dropped_no_tagged_power": dropped_connectors,
        "note": "site_type is a keyword heuristic; connectors without a tagged power are dropped, not guessed.",
        "chargers": [r.model_dump(mode="json") for r in final],
    }
    CHARGERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    CHARGERS_FILE.write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
    operators = Counter(r.operator or "(none)" for r in final)
    print(f"OSM records: {stats['osm']} | OCM records: {stats['ocm']} ({stats['ocm_status']})")
    print(f"merged away (within 50 m): {n_merged} | in both sources: {n_both} | final chargers: {len(final)} "
          f"| with a tagged connector: {sum(1 for r in final if r.connectors)} | simulated: {simulated}")
    print("top operators:", operators.most_common(8))
    print("site types:", dict(Counter(r.site_type for r in final)))
    print(f"requests made in total: {client.requests_made()} (limit 300) -> {CHARGERS_FILE}")

    graph = Neo4jGraph.from_env()
    if graph is None:
        print("Neo4j load SKIPPED: no NEO4J_* credentials in .env")
    else:
        try:
            graph.ensure_constraints()
            for r in final:
                graph.add_charger(r)
            print(f"loaded {len(final)} chargers into Neo4j")
        finally:
            graph.close()
    return 0


if __name__ == "__main__":
    sys.exit(main_india() if "--india" in sys.argv[1:] else main())
