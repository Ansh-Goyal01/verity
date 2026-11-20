"""Charger directory: parse OpenStreetMap / Open Charge Map records, merge duplicates, load, and place on a route.

Pure functions (no network): ``scripts/import_chargers.py`` does the fetching through ``routing.CachedClient``.
Records follow the charger contract in CLAUDE.md. Unknown values stay unknown: a connector whose power is
not tagged is dropped (the schema needs kW > 0) rather than given an invented number.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np

from .paths import PROCESSED_DIR
from .schemas import ChargerRecord, Connector
from .trips import haversine_m

CHARGERS_FILE = PROCESSED_DIR / "chargers_ncr.json"  # the frozen NCR + Noida-Agra snapshot (ids CH-0001..)
NCR_CHARGERS_FILE = CHARGERS_FILE
INDIA_CHARGERS_FILE = PROCESSED_DIR / "chargers_india.json"  # NCR snapshot + the rest of India (WP3)
CHARGERS_ENV = "VERITY_CHARGERS"
MERGE_RADIUS_M = 50.0
KM_PER_DEG_LAT = 111.0

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


def default_chargers_file() -> Path:
    """VERITY_CHARGERS if set, else the India directory when it has been built, else the NCR snapshot."""
    if env := os.environ.get(CHARGERS_ENV, "").strip():
        return Path(env)
    return INDIA_CHARGERS_FILE if INDIA_CHARGERS_FILE.exists() else NCR_CHARGERS_FILE


def load_chargers(path: Path | None = None) -> tuple[list[ChargerRecord], dict]:
    data = json.loads(Path(path or default_chargers_file()).read_text(encoding="utf-8"))
    return [ChargerRecord.model_validate(c) for c in data["chargers"]], {k: v for k, v in data.items() if k != "chargers"}


def in_any_box(lat: float, lon: float, boxes: Iterable[Iterable[float]]) -> bool:
    """Inside any (south, west, north, east) box."""
    return any(s <= lat <= n and w <= lon <= e for s, w, n, e in boxes)


def _id_number(cid: str) -> int:
    m = re.fullmatch(r"CH-(\d+)", cid)
    return int(m.group(1)) if m else 0


def extend_directory(base: list[ChargerRecord], new: Iterable[ChargerRecord], frozen_boxes: Iterable[Iterable[float]],
                     radius_m: float = MERGE_RADIUS_M) -> tuple[list[ChargerRecord], dict]:
    """The base directory UNCHANGED (same records, same ids) plus the new records outside ``frozen_boxes``, merged
    within ``radius_m`` among themselves; a new record within ``radius_m`` of a base charger is dropped (the base
    already has it). New ids continue after the base's highest ``CH-`` number, in lat/lon order (WP3)."""
    boxes = [tuple(b) for b in frozen_boxes]
    incoming = list(new)
    outside = [r for r in incoming if not in_any_box(r.lat, r.lon, boxes)]
    merged, n_merged = merge_duplicates(outside, radius_m)
    if base and merged:
        b_lat, b_lon = np.array([c.lat for c in base]), np.array([c.lon for c in base])
        keep = [r for r in merged if float(haversine_m(np.full(len(base), r.lat), np.full(len(base), r.lon),
                                                         b_lat, b_lon).min()) > radius_m]
    else:
        keep = merged
    start = max((_id_number(c.id) for c in base), default=0) + 1
    ordered = sorted(keep, key=lambda r: (round(r.lat, 6), round(r.lon, 6), r.source_ids))
    added = [r.model_copy(update={"id": f"CH-{i:04d}"}) for i, r in enumerate(ordered, start=start)]
    stats = {"new_records": len(incoming), "inside_frozen_boxes_dropped": len(incoming) - len(outside),
             "merged_away_within_50m": n_merged, "duplicates_of_base_dropped": len(merged) - len(keep),
             "added": len(added), "base": len(base)}
    return [*base, *added], stats


@dataclass(frozen=True)
class RouteCharger:
    charger: ChargerRecord
    km_along: float
    offset_km: float


def chargers_near_route(chargers: Iterable[ChargerRecord], poly_latlon: np.ndarray, poly_km: np.ndarray,
                        max_offset_km: float = 5.0) -> list[RouteCharger]:
    """Chargers within ``max_offset_km`` of the route polyline, with their position along it (km). A charger outside
    the route's bounding box widened by ``max_offset_km`` cannot qualify, so it is skipped before the haversine."""
    out = []
    recs = list(chargers)
    if not recs or len(poly_latlon) == 0:
        return out
    margin_lat = max_offset_km / KM_PER_DEG_LAT * 1.01
    max_abs_lat = float(np.abs(poly_latlon[:, 0]).max()) + margin_lat
    margin_lon = margin_lat / max(np.cos(np.radians(min(max_abs_lat, 89.0))), 1e-6)
    lo_lat, hi_lat = float(poly_latlon[:, 0].min()) - margin_lat, float(poly_latlon[:, 0].max()) + margin_lat
    lo_lon, hi_lon = float(poly_latlon[:, 1].min()) - margin_lon, float(poly_latlon[:, 1].max()) + margin_lon
    for c in recs:
        if not (lo_lat <= c.lat <= hi_lat and lo_lon <= c.lon <= hi_lon):
            continue
        d = haversine_m(np.full(len(poly_latlon), c.lat), np.full(len(poly_latlon), c.lon),
                        poly_latlon[:, 0], poly_latlon[:, 1])
        i = int(np.argmin(d))
        if d[i] <= max_offset_km * 1000.0:
            out.append(RouteCharger(c, float(poly_km[i]), float(d[i] / 1000.0)))
    return sorted(out, key=lambda rc: rc.km_along)


def chargers_near_point(chargers: Iterable[ChargerRecord], lat: float, lon: float, radius_km: float,
                        limit: int) -> list[tuple[ChargerRecord, float]]:
    """Up to ``limit`` chargers within ``radius_km`` (straight line) of a point, nearest first, with that distance."""
    recs = list(chargers)
    if not recs:
        return []
    d = haversine_m(np.full(len(recs), lat), np.full(len(recs), lon), np.array([c.lat for c in recs]),
                    np.array([c.lon for c in recs])) / 1000.0
    order = [int(i) for i in np.argsort(d) if d[i] <= radius_km]
    return [(recs[i], float(d[i])) for i in order[:limit]]
