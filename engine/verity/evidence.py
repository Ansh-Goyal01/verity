"""Real charger evidence (F5): hand-collected public rows -> matched chargers -> dated reports per channel.

Inputs are data/corpus/charger_evidence.csv (dated public evidence: Google Maps reviews, PlugShare check-ins,
operator-app screens, posts on X, forums, news) and data/corpus/app_status_snapshots.csv (what an operator app
showed at one moment). Pure functions only: the experiment script reads the files and writes the results.

Matching a row to the charger directory (data/processed/chargers_ncr.json), first rule that applies wins:
  1. a human-confirmed line in charger_match_overrides.csv;
  2. a place_url that is an OpenStreetMap / Open Charge Map link to a directory record;
  3. a location (lat/lon, or the pin inside a Google Maps place_url) within MATCH_RADIUS_M of a directory charger
     whose name/operator is similar enough (NAME_MIN_SCORE);
  4. the same place_url as rows matched by rules 1-3 (only when they all agree on one charger).
Snapshots carry no location: they match through an override, the exact charger_name of a matched evidence row, or
the (app, charger_ref_in_app) of a matched snapshot. Anything else goes to a review list with the reason and the
nearest candidate, for a human to confirm in the override file. Nothing is guessed.

Each channel (google_review, plugshare, operator_app, x_post, forum, news) is ONE source for the Dawid-Skene trust
engine, so the engine learns how far each channel can be believed. Every report here is is_simulated=False.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import numpy as np

from .corpus import CHARGER_EVIDENCE, Row
from .schemas import ChargerRecord, Connector, Report
from .trips import haversine_m

IST = timezone(timedelta(hours=5, minutes=30), "IST")
MATCH_RADIUS_M = 100.0
NAME_MIN_SCORE = 0.5  # share of the shorter name's tokens found in the other name (approximate - verify)
DEFAULT_HOUR_IST = 12  # evidence with a date but no time is placed at noon IST (+-12 h, disclosed)
APP_WINDOW_H = 48.0  # "operator app showed available within 48 h of a dated failure report" (either side)
MIN_GROUP_N = 5  # groups with fewer reports are reported but marked thin
SOURCE_PREFIX = "public:"
EVIDENCE_FILE = CHARGER_EVIDENCE.filename
SNAPSHOT_FILE = "app_status_snapshots.csv"
CHANNELS: tuple[str, ...] = next(c.allowed for c in CHARGER_EVIDENCE.columns if c.name == "source_kind")

# Corpus status -> trust-engine report status. None = not evidence about whether the charger works.
EVIDENCE_STATUS = {"working": "working", "partially_working": "working", "queue": "occupied", "broken": "broken",
                   "no_power": "no_power", "access_blocked": "blocked", "payment_failed": "blocked", "unknown": None}
SNAPSHOT_STATUS = {"available": "working", "busy": "occupied", "offline": "broken", "faulty": "broken",
                   "unknown": None}
FAILURE_STATUSES = ("broken", "no_power")  # a "dated failure report" for the app-vs-failure analysis

_STOP = frozenset("ev charging charge charger chargers station stations point hub the and of at pvt ltd limited "
                  "private india electric openstreetmap ocm".split())
_OSM = re.compile(r"openstreetmap\.org/(node|way|relation)/(\d+)")
_OCM = re.compile(r"openchargemap\.(?:io|org)/\D*(\d+)")
_PIN = re.compile(r"!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)")
_AT = re.compile(r"@(-?\d+\.\d+),(-?\d+\.\d+)")
_Q = re.compile(r"[?&](?:q|query|ll)=(-?\d+\.\d+)(?:,|%2C)(-?\d+\.\d+)", re.IGNORECASE)


# ---------------------------------------------------------------- names and places
def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", (text or "").lower()) if t not in _STOP}


def name_similarity(a: str, b: str) -> float:
    """Share of the shorter name's meaningful tokens that also appear in the other one (0 when either is empty)."""
    ta, tb = _tokens(a), _tokens(b)
    return len(ta & tb) / min(len(ta), len(tb)) if ta and tb else 0.0


def directory_link(url: str) -> str | None:
    """``osm:node/123`` / ``ocm:456`` when the place_url points at an OSM or Open Charge Map record."""
    if m := _OSM.search(url or ""):
        return f"osm:{m.group(1)}/{m.group(2)}"
    if m := _OCM.search(url or ""):
        return f"ocm:{m.group(1)}"
    return None


def url_location(url: str) -> tuple[float, float] | None:
    """The coordinates inside a map link: the place pin (!3d..!4d..) first, then ?q=lat,lon, then @lat,lon."""
    for pattern in (_PIN, _Q, _AT):
        if m := pattern.search(url or ""):
            return float(m.group(1)), float(m.group(2))
    return None


def row_location(row: Row) -> tuple[tuple[float, float] | None, str]:
    if row.get("lat") and row.get("lon"):
        return (float(row["lat"]), float(row["lon"])), "lat_lon"
    loc = url_location(row.get("place_url", ""))
    return (loc, "place_url_coordinates") if loc else (None, "")


# ---------------------------------------------------------------- matching
@dataclass(frozen=True)
class Match:
    file: str
    row_id: str
    charger_id: str | None
    method: str  # manual | directory_link | location_name | same_place_url | same_name_as_evidence | same_app_ref
    reason: str = ""  # why it is unmatched
    distance_m: float | None = None
    name_score: float | None = None
    candidate_id: str | None = None  # nearest directory charger, for the review list


def _distances(chargers: Sequence[ChargerRecord], lat: float, lon: float) -> np.ndarray:
    return haversine_m(np.full(len(chargers), lat), np.full(len(chargers), lon),
                       np.array([c.lat for c in chargers]), np.array([c.lon for c in chargers]))


def _by_location(row: Row, chargers: Sequence[ChargerRecord]) -> Match:
    rid = row["evidence_id"]
    loc, how = row_location(row)
    if loc is None:
        return Match(EVIDENCE_FILE, rid, None, "", "no lat/lon and no coordinates in place_url")
    if not chargers:
        return Match(EVIDENCE_FILE, rid, None, "", "charger directory is empty")
    label = f"{row.get('charger_name', '')} {row.get('operator', '')}"
    dist = _distances(chargers, *loc)
    near = [(name_similarity(label, f"{chargers[i].name} {chargers[i].operator}"), float(dist[i]), chargers[i])
            for i in np.flatnonzero(dist <= MATCH_RADIUS_M)]
    good = sorted((s for s in near if s[0] >= NAME_MIN_SCORE), key=lambda s: (-s[0], s[1]))
    if good:
        score, d, best = good[0]
        return Match(EVIDENCE_FILE, rid, best.id, "location_name", "", round(d, 1), round(score, 2))
    i = int(np.argmin(dist))
    nearest = chargers[i]
    reason = (f"directory chargers within {MATCH_RADIUS_M:.0f} m but none with a similar name (via {how})" if near
              else f"no directory charger within {MATCH_RADIUS_M:.0f} m (via {how})")
    return Match(EVIDENCE_FILE, rid, None, "", reason, round(float(dist[i]), 1),
                 round(name_similarity(label, f"{nearest.name} {nearest.operator}"), 2), nearest.id)


def _override(file: str, rid: str, cid: str, ids: set[str]) -> Match:
    return (Match(file, rid, cid, "manual") if cid in ids
            else Match(file, rid, None, "", f"override names unknown charger {cid!r}"))


def _agreeing(keys_to_ids: dict[object, set[str]]) -> dict[object, str]:
    """Keep only keys whose matched rows all point at one charger."""
    return {k: next(iter(v)) for k, v in keys_to_ids.items() if len(v) == 1}


def match_evidence(rows: Sequence[Row], chargers: Sequence[ChargerRecord],
                   overrides: dict[str, str]) -> dict[str, Match]:
    ids = {c.id for c in chargers}
    by_source = {s: c.id for c in chargers for s in c.source_ids}
    out: dict[str, Match] = {}
    for row in rows:
        rid = row["evidence_id"]
        if rid in overrides:
            out[rid] = _override(EVIDENCE_FILE, rid, overrides[rid], ids)
        elif (link := directory_link(row.get("place_url", ""))) and link in by_source:
            out[rid] = Match(EVIDENCE_FILE, rid, by_source[link], "directory_link")
        else:
            out[rid] = _by_location(row, chargers)
    seen: dict[object, set[str]] = defaultdict(set)
    for r in rows:
        if r.get("place_url") and (cid := out[r["evidence_id"]].charger_id):
            seen[r["place_url"]].add(cid)
    place = _agreeing(seen)
    for row in rows:
        m = out[row["evidence_id"]]
        if m.charger_id is None and row.get("place_url") in place:
            out[m.row_id] = Match(EVIDENCE_FILE, m.row_id, place[row["place_url"]], "same_place_url")
    return out


def _norm(name: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", (name or "").lower()))


def match_snapshots(rows: Sequence[Row], evidence_rows: Sequence[Row], evidence_matches: dict[str, Match],
                    charger_ids: set[str], overrides: dict[str, str]) -> dict[str, Match]:
    names: dict[str, set[str]] = defaultdict(set)
    for r in evidence_rows:
        if (cid := evidence_matches[r["evidence_id"]].charger_id) is not None:
            names[_norm(r.get("charger_name", ""))].add(cid)
    out: dict[str, Match] = {}
    for row in rows:
        rid = row["snapshot_id"]
        if rid in overrides:
            out[rid] = _override(SNAPSHOT_FILE, rid, overrides[rid], charger_ids)
            continue
        cands = names.get(_norm(row.get("charger_name", "")), set())
        out[rid] = (Match(SNAPSHOT_FILE, rid, next(iter(cands)), "same_name_as_evidence") if len(cands) == 1 else
                    Match(SNAPSHOT_FILE, rid, None, "", "charger_name matches no matched evidence row" if not cands
                          else "charger_name matches evidence rows at different chargers"))
    refs: dict[object, set[str]] = defaultdict(set)
    for row in rows:
        if row.get("charger_ref_in_app") and (cid := out[row["snapshot_id"]].charger_id):
            refs[(row["app"], row["charger_ref_in_app"])].add(cid)
    by_ref = _agreeing(refs)
    for row in rows:
        m, key = out[row["snapshot_id"]], (row["app"], row.get("charger_ref_in_app", ""))
        if m.charger_id is None and key in by_ref:
            out[m.row_id] = Match(SNAPSHOT_FILE, m.row_id, by_ref[key], "same_app_ref")
    return out


# ---------------------------------------------------------------- dated items and reports
@dataclass(frozen=True)
class Item:
    """One row as a dated observation about a charger (charger_id None when unmatched)."""

    file: str
    row_id: str
    channel: str
    corpus_status: str
    report_status: str | None
    t: datetime
    time_known: bool
    charger_id: str | None
    operator: str
    rated_kw: float | None
    connector: str


def evidence_moment(row: Row) -> tuple[datetime, bool]:
    day = date.fromisoformat(row["evidence_date"])
    if row.get("evidence_time"):
        hh, mm = (int(x) for x in row["evidence_time"].split(":"))
        return datetime(day.year, day.month, day.day, hh, mm, tzinfo=IST), True
    return datetime(day.year, day.month, day.day, DEFAULT_HOUR_IST, tzinfo=IST), False


def snapshot_moment(row: Row) -> datetime:
    return datetime.strptime(row["datetime_ist"], "%Y-%m-%d %H:%M").replace(tzinfo=IST)


def _kw(text: str) -> float | None:
    return float(text) if text else None


def items_from(evidence_rows: Sequence[Row], snapshot_rows: Sequence[Row], ev_matches: dict[str, Match],
               snap_matches: dict[str, Match]) -> list[Item]:
    out = []
    for r in evidence_rows:
        t, known = evidence_moment(r)
        out.append(Item(EVIDENCE_FILE, r["evidence_id"], r["source_kind"], r["status"], EVIDENCE_STATUS[r["status"]],
                        t, known, ev_matches[r["evidence_id"]].charger_id, r.get("operator", ""),
                        _kw(r.get("rated_kw", "")), r.get("connector", "")))
    for r in snapshot_rows:
        out.append(Item(SNAPSHOT_FILE, r["snapshot_id"], "operator_app", r["status_shown"],
                        SNAPSHOT_STATUS[r["status_shown"]], snapshot_moment(r), True,
                        snap_matches[r["snapshot_id"]].charger_id, "", _kw(r.get("rated_kw", "")), ""))
    return out


def channel_source_id(channel: str) -> str:
    return SOURCE_PREFIX + channel


def reports_from(items: Sequence[Item]) -> list[Report]:
    """One real Report per matched item with a usable status. Same channel, charger and instant would share one
    report key (and replace each other), so later colliding items are moved by whole seconds (disclosed)."""
    used: set[tuple[str, str, datetime]] = set()
    out = []
    for it in sorted(items, key=lambda i: (i.t, i.file, i.row_id)):
        if it.charger_id is None or it.report_status is None:
            continue
        src, t = channel_source_id(it.channel), it.t
        while (src, it.charger_id, t) in used:
            t += timedelta(seconds=1)
        used.add((src, it.charger_id, t))
        out.append(Report(charger_id=it.charger_id, source_id=src, status=it.report_status, t=t,
                          evidence=f"{it.file}:{it.row_id}", is_simulated=False))
    return out


# ---------------------------------------------------------------- rated power from operator apps
def rated_kw_updates(items: Sequence[Item]) -> dict[str, dict]:
    """Latest operator-app rated kW per (charger, connector), with the row it came from."""
    latest: dict[tuple[str, str], Item] = {}
    for it in items:
        if it.charger_id and it.channel == "operator_app" and it.rated_kw:
            key = (it.charger_id, it.connector.strip())
            if key not in latest or it.t > latest[key].t:
                latest[key] = it
    out: dict[str, dict] = defaultdict(dict)
    for (cid, conn), it in sorted(latest.items()):
        out[cid][conn] = {"rated_kw": it.rated_kw, "from": f"{it.file}:{it.row_id}", "t": it.t.isoformat()}
    return dict(out)


def apply_rated_kw(chargers: Sequence[ChargerRecord], updates: dict[str, dict]) -> list[ChargerRecord]:
    """New records with the app's rated kW. A named connector type replaces that type's kW (or is added); an
    unnamed one replaces the kW of a charger's only connector, else is added as type 'unspecified'."""
    out = []
    for c in chargers:
        conns = list(c.connectors)
        for conn, u in updates.get(c.id, {}).items():
            same = [i for i, k in enumerate(conns) if conn and k.type.lower() == conn.lower()]
            if not conn and len(conns) == 1:
                same = [0]
            if same:
                conns[same[0]] = Connector(type=conns[same[0]].type, kw=u["rated_kw"])
            else:
                conns.append(Connector(type=conn or "unspecified", kw=u["rated_kw"]))
        out.append(c.model_copy(update={"connectors": conns}) if conns != list(c.connectors) else c)
    return out


# ---------------------------------------------------------------- analyses
def wilson(k: int, n: int) -> list[float] | None:
    """Wilson 95 % interval for k successes in n."""
    if n <= 0:
        return None
    z2, p = 1.96**2, k / n
    centre = (p + z2 / (2 * n)) / (1 + z2 / n)
    half = 1.96 * float(np.sqrt(p * (1 - p) / n + z2 / (4 * n**2))) / (1 + z2 / n)
    return [round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4)]


def _rate_row(group: str, its: list[Item]) -> dict:
    n = len(its)
    k = sum(it.report_status in ("working", "occupied") for it in its)
    return {"group": group, "n_reports": n, "n_chargers": len({it.charger_id for it in its}), "n_working": k,
            "working_rate": round(k / n, 4), "ci95": wilson(k, n), "thin": n < MIN_GROUP_N}


def working_rates(items: Sequence[Item], chargers: dict[str, ChargerRecord]) -> dict:
    """Share of public evidence rows saying the charger worked, by operator and by site type. App snapshots are
    excluded: what an app shows is the claim under test in ``app_vs_failures``, not an observation."""
    usable = [it for it in items if it.file == EVIDENCE_FILE and it.charger_id and it.report_status]
    by_op: dict[str, list[Item]] = defaultdict(list)
    by_site: dict[str, list[Item]] = defaultdict(list)
    for it in usable:
        rec = chargers[it.charger_id]
        by_op[(it.operator or rec.operator or "unknown").strip().lower()].append(it)
        by_site[rec.site_type or "unknown"].append(it)
    return {"n_reports": len(usable),
            "by_operator": sorted((_rate_row(g, v) for g, v in by_op.items()), key=lambda r: -r["n_reports"]),
            "by_site_type": sorted((_rate_row(g, v) for g, v in by_site.items()), key=lambda r: -r["n_reports"])}


def app_vs_failures(items: Sequence[Item], window_h: float = APP_WINDOW_H) -> dict:
    """Dated failure reports (broken / no_power, not from the operator app itself) that have at least one app
    snapshot of the same charger within +-window_h hours: how often did the app show 'available' then?"""
    snaps: dict[str, list[Item]] = defaultdict(list)
    for it in items:
        if it.file == SNAPSHOT_FILE and it.charger_id:
            snaps[it.charger_id].append(it)
    failures = [it for it in items if it.file == EVIDENCE_FILE and it.charger_id
                and it.channel != "operator_app" and it.corpus_status in FAILURE_STATUSES]
    window = timedelta(hours=window_h)
    cases = []
    for f in failures:
        near = [s for s in snaps.get(f.charger_id, []) if abs(s.t - f.t) <= window]
        if near:
            cases.append({"evidence_id": f.row_id, "charger_id": f.charger_id, "failure_time_known": f.time_known,
                          "n_snapshots": len(near),
                          "app_showed_available": any(s.corpus_status == "available" for s in near)})
    k = sum(c["app_showed_available"] for c in cases)
    return {"window_hours": window_h, "n_failure_reports": len(failures), "n_with_snapshots_in_window": len(cases),
            "n_app_showed_available": k, "rate": round(k / len(cases), 4) if cases else None,
            "ci95": wilson(k, len(cases)), "thin": len(cases) < MIN_GROUP_N, "cases": cases}


def review_list(matches: Sequence[Match]) -> list[dict]:
    return [{"file": m.file, "row_id": m.row_id, "reason": m.reason, "nearest_candidate": m.candidate_id,
             "nearest_distance_m": m.distance_m, "name_score": m.name_score} for m in matches if m.charger_id is None]


def match_summary(matches: Sequence[Match]) -> dict:
    return {"n_rows": len(matches), "n_matched": sum(m.charger_id is not None for m in matches),
            "by_method": dict(Counter(m.method for m in matches if m.charger_id is not None))}
