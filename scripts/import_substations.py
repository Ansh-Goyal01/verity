"""Import OpenStreetMap ``power=substation`` around the charger directory (WP8c) -> data/processed/substations.json.

Only where chargers are: each 0.25-degree grid cell holding a directory charger, padded by about 5 km (the link radius),
asked of Overpass in batches through CachedClient (cached; Overpass asks for fair use). A substation is linked to a
charger later only by PROXIMITY (``verity.substations``): India's feeder maps are not public, so the link never claims
that a substation feeds a charger.

    uv run python scripts/import_substations.py
"""

from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone

from verity.chargers import load_chargers
from verity.paths import DATA_DIR
from verity.routing import OVERPASS_URL, CachedClient, NetworkUnavailable

OUT = DATA_DIR / "processed" / "substations.json"
CELL_DEG = 0.25
PAD_DEG = 0.05  # ~5.5 km of latitude: covers the 5 km link radius at a cell's edge
BOXES_PER_QUERY = 30


def cells(points: list[tuple[float, float]]) -> list[tuple[float, float, float, float]]:
    """(south, west, north, east) per occupied grid cell, padded."""
    seen = sorted({(math.floor(lat / CELL_DEG), math.floor(lon / CELL_DEG)) for lat, lon in points})
    return [(round(i * CELL_DEG - PAD_DEG, 4), round(j * CELL_DEG - PAD_DEG, 4), round((i + 1) * CELL_DEG + PAD_DEG, 4),
             round((j + 1) * CELL_DEG + PAD_DEG, 4)) for i, j in seen]


def query(boxes: list[tuple[float, float, float, float]]) -> str:
    parts = "".join(f'nwr["power"="substation"]({s},{w},{n},{e});' for s, w, n, e in boxes)
    return f"[out:json][timeout:180];({parts});out center tags;"


def record(el: dict) -> dict | None:
    lat = el.get("lat", (el.get("center") or {}).get("lat"))
    lon = el.get("lon", (el.get("center") or {}).get("lon"))
    if lat is None or lon is None:
        return None
    tags = el.get("tags") or {}
    return {"id": f"osm:{el['type']}/{el['id']}", "lat": round(float(lat), 6), "lon": round(float(lon), 6),
            "name": tags.get("name"), "voltage": tags.get("voltage"), "operator": tags.get("operator")}


MIN_SPLIT = 4


def fetch(client: CachedClient, boxes: list) -> list[dict]:
    """One Overpass call for ``boxes``; a dense batch that times out or fails is split in half (each half cached)."""
    try:
        payload, _ = client.post_json(OVERPASS_URL, {"data": query(boxes)})
        return payload.get("elements", [])
    except NetworkUnavailable:
        if len(boxes) <= MIN_SPLIT:
            raise
        half = len(boxes) // 2
        return fetch(client, boxes[:half]) + fetch(client, boxes[half:])


def main() -> int:
    chargers, _ = load_chargers()
    boxes = cells([(c.lat, c.lon) for c in chargers])
    client = CachedClient()
    found: dict[str, dict] = {}
    for i in range(0, len(boxes), BOXES_PER_QUERY):
        for el in fetch(client, boxes[i:i + BOXES_PER_QUERY]):
            rec = record(el)
            if rec is not None:
                found[rec["id"]] = rec
        print(f"batch {i // BOXES_PER_QUERY + 1}: {len(found)} substations so far")
    out = {"is_simulated": False, "source": "OpenStreetMap power=substation via Overpass (cached)",
           "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "cell_deg": CELL_DEG,
           "pad_deg": PAD_DEG, "n_cells": len(boxes), "n_chargers": len(chargers),
           "note": "Positions only. Linked to chargers by proximity; never the feeding substation (no public feeder maps).",
           "substations": sorted(found.values(), key=lambda r: r["id"])}
    OUT.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {OUT} ({len(found)} substations, {len(boxes)} cells)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
