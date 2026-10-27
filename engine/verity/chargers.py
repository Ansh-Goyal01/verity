"""Charger directory: parse OpenStreetMap / Open Charge Map records, merge duplicates, load, and place on a route.

Pure functions (no network): ``scripts/import_chargers.py`` does the fetching through ``routing.CachedClient``.
Records follow the charger contract in CLAUDE.md. Unknown values stay unknown: a connector whose power is
not tagged is dropped (the schema needs kW > 0) rather than given an invented number.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np

from .paths import PROCESSED_DIR
from .schemas import ChargerRecord, Connector
from .trips import haversine_m

CHARGERS_FILE = PROCESSED_DIR / "chargers_ncr.json"
MERGE_RADIUS_M = 50.0

# OSM socket tag -> connector type label. Approximate - verify against the OSM wiki before relying on it.
OSM_SOCKETS = {
    "socket:type2": "Type2", "socket:type2_combo": "CCS2", "socket:ccs": "CCS2", "socket:chademo": "CHAdeMO",
    "socket:type1": "Type1", "socket:type1_combo": "CCS1", "socket:gb_dc": "GB/T DC", "socket:gb_ac": "GB/T AC",
    "socket:schuko": "Schuko", "socket:tesla_supercharger": "Tesla",
}
SITE_KEYWORDS = (  # (site_type, regex over operator + brand + name); heuristic, approximate - verify
    ("fuel_station", r"petrol|indian ?oil|iocl|bpcl|bharat petroleum|hpcl|hindustan petroleum|jio-?bp|shell|reliance"),
    ("mall", r"\bmall\b|plaza mall|shopping"),
    ("highway", r"expressway|highway|\bnh-?\d+|toll|food ?court|dhaba|rest ?area|service area"),
    ("hotel", r"hotel|resort|inn\b"),
    ("metro", r"metro"),
)


def _kw(value: str | None) -> float | None:
    if not value:
        return None
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*(k?w)?", value.strip().lower())
    if not m:
        return None
    number = float(m.group(1).replace(",", "."))
    kw = number / 1000.0 if (m.group(2) == "w" and number > 1000) else number
    return kw if kw > 0 else None


def site_type_of(tags: dict[str, str]) -> str:
    text = " ".join(tags.get(k, "") for k in ("operator", "brand", "network", "name")).lower()
    for site, pattern in SITE_KEYWORDS:
        if re.search(pattern, text):
            return site
    return "unknown"


def parse_osm_element(el: dict) -> tuple[ChargerRecord | None, int]:
    """(record or None, number of connectors dropped for lack of a tagged power)."""
    tags = el.get("tags", {})
    lat = el.get("lat", (el.get("center") or {}).get("lat"))
    lon = el.get("lon", (el.get("center") or {}).get("lon"))
    if lat is None or lon is None:
        return None, 0
    connectors: list[Connector] = []
    dropped = 0
    for key, label in OSM_SOCKETS.items():
        if key in tags:
            power = _kw(tags.get(f"{key}:output"))
            if power is None:
                dropped += 1
            else:
                connectors.append(Connector(type=label, kw=power))
    operator = tags.get("operator") or tags.get("brand") or tags.get("network") or ""
    name = tags.get("name") or operator or "EV charging station (OpenStreetMap)"
    return ChargerRecord(id=f"osm:{el['type']}/{el['id']}", name=name, lat=float(lat), lon=float(lon),
                         operator=operator, site_type=site_type_of(tags), connectors=connectors,
                         source_ids=[f"osm:{el['type']}/{el['id']}"]), dropped


def parse_ocm_poi(poi: dict) -> ChargerRecord | None:
    """Open Charge Map POI -> record (``AddressInfo``, ``Connections``, ``OperatorInfo``)."""
    addr = poi.get("AddressInfo") or {}
    if addr.get("Latitude") is None or addr.get("Longitude") is None:
        return None
    connectors = []
    for c in poi.get("Connections") or []:
        kw, kind = c.get("PowerKW"), (c.get("ConnectionType") or {}).get("Title")
        if kw and kw > 0 and kind:
            connectors.append(Connector(type=kind, kw=float(kw)))
    operator = (poi.get("OperatorInfo") or {}).get("Title") or ""
    return ChargerRecord(id=f"ocm:{poi['ID']}", name=addr.get("Title") or operator or "EV charging station (OCM)",
                         lat=float(addr["Latitude"]), lon=float(addr["Longitude"]), operator=operator,
                         site_type=site_type_of({"operator": operator, "name": addr.get("Title") or ""}),
                         connectors=connectors, source_ids=[f"ocm:{poi['ID']}"])


def merge_duplicates(records: Iterable[ChargerRecord], radius_m: float = MERGE_RADIUS_M) -> tuple[list[ChargerRecord], int]:
    """Merge records within ``radius_m`` of each other (union-find, grid-bucketed). Returns (merged, n_merged_away)."""
    recs = list(records)
    parent = list(range(len(recs)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    cell = 0.001  # ~111 m buckets; neighbours are looked up in the 3 x 3 block around each record
    buckets: dict[tuple[int, int], list[int]] = {}
    for i, r in enumerate(recs):
        buckets.setdefault((int(r.lat // cell), int(r.lon // cell)), []).append(i)
    for i, r in enumerate(recs):
        ci, cj = int(r.lat // cell), int(r.lon // cell)
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                for j in buckets.get((ci + di, cj + dj), []):
                    if j > i and float(haversine_m(np.array([r.lat]), np.array([r.lon]),
                                                   np.array([recs[j].lat]), np.array([recs[j].lon]))[0]) <= radius_m:
                        parent[find(j)] = find(i)
    groups: dict[int, list[ChargerRecord]] = {}
    for i, r in enumerate(recs):
        groups.setdefault(find(i), []).append(r)
    merged = [_merge_group(g) for g in groups.values()]
    return merged, len(recs) - len(merged)


def _merge_group(group: list[ChargerRecord]) -> ChargerRecord:
    best = max(group, key=lambda r: (len(r.connectors), bool(r.operator), r.name != "EV charging station (OpenStreetMap)"))
    seen: set[tuple[str, float]] = set()
    connectors = []
    for r in group:
        for c in r.connectors:
            if (c.type, c.kw) not in seen:
                seen.add((c.type, c.kw))
                connectors.append(c)
    return ChargerRecord(
        id=best.id, name=best.name, lat=best.lat, lon=best.lon,
        operator=next((r.operator for r in [best, *group] if r.operator), ""),
        site_type=next((r.site_type for r in [best, *group] if r.site_type != "unknown"), "unknown"),
        connectors=connectors, source_ids=sorted({s for r in group for s in r.source_ids}),
    )


def assign_ids(records: list[ChargerRecord]) -> list[ChargerRecord]:
    """Stable ``CH-0001`` style ids, ordered by position (the source ids stay in ``source_ids``)."""
    ordered = sorted(records, key=lambda r: (round(r.lat, 6), round(r.lon, 6), r.source_ids))
    return [r.model_copy(update={"id": f"CH-{i:04d}"}) for i, r in enumerate(ordered, start=1)]


def source_of(rec: ChargerRecord) -> str:
    """API ``source`` field: osm | ocm | simulated (merged OSM+OCM records report osm)."""
    ids = rec.source_ids
    if any(s.startswith("synthetic") for s in ids):
        return "simulated"
    if any(s.startswith("osm:") for s in ids):
        return "osm"
    return "ocm" if any(s.startswith("ocm:") for s in ids) else "simulated"


def load_chargers(path: Path = CHARGERS_FILE) -> tuple[list[ChargerRecord], dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return [ChargerRecord.model_validate(c) for c in data["chargers"]], {k: v for k, v in data.items() if k != "chargers"}


@dataclass(frozen=True)
class RouteCharger:
    charger: ChargerRecord
    km_along: float
    offset_km: float


def chargers_near_route(chargers: Iterable[ChargerRecord], poly_latlon: np.ndarray, poly_km: np.ndarray,
                        max_offset_km: float = 5.0) -> list[RouteCharger]:
    """Chargers within ``max_offset_km`` of the route polyline, with their position along it (km)."""
    out = []
    for c in chargers:
        d = haversine_m(np.full(len(poly_latlon), c.lat), np.full(len(poly_latlon), c.lon),
                        poly_latlon[:, 0], poly_latlon[:, 1])
        i = int(np.argmin(d))
        if d[i] <= max_offset_km * 1000.0:
            out.append(RouteCharger(c, float(poly_km[i]), float(d[i] / 1000.0)))
    return sorted(out, key=lambda rc: rc.km_along)
