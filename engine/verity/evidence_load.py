"""Real charger evidence -> trust-engine reports, shared by the API and experiment E3 (WP3).

The hand-collected corpus (data/corpus/charger_evidence.csv + app_status_snapshots.csv, verified rows only) is
matched to the charger directory by ``verity.evidence``. Rows without coordinates can be located by their address,
geocoded once by ``scripts/geocode_evidence.py`` into data/processed/evidence_geocoded.json; nothing here touches
the network. Every report produced is REAL (``is_simulated`` false).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from . import evidence as ev
from .corpus import APP_SNAPSHOTS, CHARGER_EVIDENCE, MATCH_OVERRIDES, FileSpec, read_csv, usable_rows
from .paths import CORPUS_DIR, PROCESSED_DIR
from .schemas import ChargerRecord, Report

GEOCODED_FILE = PROCESSED_DIR / "evidence_geocoded.json"

# Photon ``osm_value`` kinds that name an area, not a place: too coarse to put a report on one charger.
AREA_KINDS = frozenset({
    "country", "state", "state_district", "region", "province", "county", "district", "municipality", "city",
    "town", "village", "hamlet", "borough", "suburb", "quarter", "neighbourhood", "locality", "city_block",
    "postcode", "postal_code", "island", "isolated_dwelling", "administrative", "residential", "farm",
})


def is_precise_kind(kind: str) -> bool:
    """A geocode result of this kind is street- or POI-level (usable for a 250 m match)."""
    return bool(kind) and kind.strip().lower() not in AREA_KINDS


@dataclass(frozen=True)
class RealEvidence:
    reports: list[Report]
    chargers: list[ChargerRecord]  # the directory with app-reported rated kW applied
    items: list[ev.Item] = field(default_factory=list)
    matches: list[ev.Match] = field(default_factory=list)
    summary: dict = field(default_factory=dict)


def corpus_rows(corpus: Path, spec: FileSpec) -> list[dict]:
    path = corpus / spec.filename
    return usable_rows(spec, read_csv(path)[1]) if path.exists() else []


def load_geocoded(path: Path = GEOCODED_FILE) -> dict[str, tuple[float, float]]:
    """{evidence_id: (lat, lon)} for rows geocoded to a precise kind; {} when the file is missing."""
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {rid: (float(g["lat"]), float(g["lon"])) for rid, g in (data.get("rows") or {}).items()
            if g and is_precise_kind(str(g.get("kind", "")))}


def real_evidence(directory: Sequence[ChargerRecord], corpus: Path = CORPUS_DIR,
                  geocoded: dict[str, tuple[float, float]] | None = None) -> RealEvidence:
    ev_rows, snap_rows = corpus_rows(corpus, CHARGER_EVIDENCE), corpus_rows(corpus, APP_SNAPSHOTS)
    overrides = {r["row_id"]: r["charger_id"] for r in corpus_rows(corpus, MATCH_OVERRIDES)}
    ev_m = ev.match_evidence(ev_rows, directory, overrides, geocoded)
    snap_m = ev.match_snapshots(snap_rows, ev_rows, ev_m, {c.id for c in directory}, overrides)
    items = ev.items_from(ev_rows, snap_rows, ev_m, snap_m)
    reports = ev.reports_from(items)
    chargers = ev.apply_rated_kw(directory, ev.rated_kw_updates(items))
    matches = [*ev_m.values(), *snap_m.values()]
    summary = {"evidence_rows_usable": len(ev_rows), "snapshot_rows_usable": len(snap_rows),
               "match_overrides": len(overrides), "geocoded_rows": len(geocoded or {}),
               "matched": sum(m.charger_id is not None for m in matches), "n_reports": len(reports),
               "chargers_with_reports": len({r.charger_id for r in reports})}
    return RealEvidence(reports, chargers, items, matches, summary)
