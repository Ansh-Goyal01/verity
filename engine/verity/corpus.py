"""Hand-collected public corpus (data/corpus/): column specs and row validation.

This module is the single source of truth for the CSV layouts a human collects by hand from public
desk sources (see docs/DATA_COLLECTION_GUIDE.md). Later pipelines (enrichment, E1-India, charger evidence,
app-review classification) read the same specs, so a template header can never drift from the code.

Validation is pure: ``validate_table(spec, header, rows)`` returns a list of ``Issue``. Rows still marked
``verified=no`` are drafts: missing fields there are WARNings (a to-do list). A row marked ``verified=yes``
must be complete, so the same gaps become ERRORs. Rows with verified != yes are excluded from every result.
"""

from __future__ import annotations

import csv
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

ERROR = "ERROR"
WARN = "WARN"
MAX_PARAPHRASE_WORDS = 15

Row = dict[str, str]
Finding = tuple[str, str]  # (level, message)


@dataclass(frozen=True)
class Issue:
    file: str
    sheet_row: int  # spreadsheet row number: header is row 1, first data row is row 2; 0 = whole file
    row_id: str
    level: str
    message: str

    def render(self) -> str:
        if self.sheet_row == 0:
            where = "file"
        else:
            where = f"row {self.sheet_row}" + (f" ({self.row_id})" if self.row_id else "")
        return f"{self.level:5} {self.file} {where}: {self.message}"


@dataclass(frozen=True)
class Col:
    """One column. kind: text | enum | number | int | date | month_or_date | time | datetime | url."""

    name: str
    kind: str = "text"
    allowed: tuple[str, ...] = ()
    lo: float | None = None
    hi: float | None = None
    always: bool = False  # required on every row, draft or verified


def _none(row: Row) -> tuple[str, ...]:
    return ()


@dataclass(frozen=True)
class FileSpec:
    filename: str
    id_column: str
    columns: tuple[Col, ...]
    # Fields required once a row is verified=yes (a function so it can depend on record_type).
    needed_when_verified: Callable[[Row], tuple[str, ...]] = _none
    # Fields that help later pipelines; missing ones only ever give a WARN.
    recommended: Callable[[Row], tuple[str, ...]] = _none
    has_verified: bool = True
    paraphrase_columns: tuple[str, ...] = ()
    extra_checks: tuple[Callable[[Row], list[Finding]], ...] = field(default_factory=tuple)

    @property
    def header(self) -> list[str]:
        return [c.name for c in self.columns]


YES_NO = ("yes", "no")
APPS = ("tata_ez_charge", "statiq", "jiobp_pulse", "chargezone", "other")
# Asensio et al., Nature Sustainability 2020: eight review categories, plus "other".
ASENSIO_CATEGORIES = ("functionality", "availability", "cost", "location", "dealership",
                      "user_interaction", "service_time", "range_anxiety", "other")


# ---------------------------------------------------------------- trips_india.csv
def _trip_needed(row: Row) -> tuple[str, ...]:
    by_type = {
        "soc_trip": ("distance_km", "start_soc_pct", "end_soc_pct"),
        "consumption": ("energy_wh_per_km",),
        "range_test": ("distance_km", "start_soc_pct", "end_soc_pct"),
    }
    return ("source_published_date", "verified_by") + by_type.get(row.get("record_type", ""), ())


def _trip_recommended(row: Row) -> tuple[str, ...]:
    common = ("speed_band", "ac", "trip_date_or_month")
    if row.get("record_type") == "soc_trip":
        return common + ("origin", "destination", "one_way_or_round")
    return common


def _soc_order(row: Row) -> list[Finding]:
    start, end = _num(row.get("start_soc_pct", "")), _num(row.get("end_soc_pct", ""))
    if start is not None and end is not None and not start > end:
        return [(ERROR, f"start_soc_pct ({row['start_soc_pct']}) must be larger than end_soc_pct "
                        f"({row['end_soc_pct']}); check they are not swapped")]
    return []


TRIPS = FileSpec(
    filename="trips_india.csv",
    id_column="record_id",
    columns=(
        Col("record_id", always=True),
        Col("record_type", "enum", ("soc_trip", "consumption", "range_test"), always=True),
        Col("source_url", "url", always=True),
        Col("source_kind", "enum", ("owner_forum", "magazine_test", "owner_story", "video"), always=True),
        Col("source_published_date", "date"),
        Col("trip_date_or_month", "month_or_date"),
        Col("make", always=True),
        Col("model", always=True),
        Col("variant"),
        Col("battery_kwh_nominal", "number", lo=1, hi=200),
        Col("origin"),
        Col("destination"),
        Col("route_notes"),
        Col("one_way_or_round", "enum", ("one_way", "round_trip", "unknown")),
        Col("distance_km", "number", lo=1, hi=2000),
        Col("start_soc_pct", "number", lo=0, hi=100),
        Col("end_soc_pct", "number", lo=0, hi=100),
        Col("energy_wh_per_km", "number", lo=20, hi=500),
        Col("avg_speed_kmh", "number", lo=1, hi=200),
        Col("speed_band", "enum", ("city", "mixed", "highway_80_100", "highway_100_plus")),
        Col("ac", "enum", ("on", "off", "unknown")),
        Col("passengers", "int", lo=1, hi=9),
        Col("drive_mode"),
        Col("regen_mode"),
        Col("ambient_temp_c", "number", lo=-20, hi=55),
        Col("notes"),
        Col("entered_by", always=True),
        Col("verified", "enum", YES_NO, always=True),
        Col("verified_by"),
    ),
    needed_when_verified=_trip_needed,
    recommended=_trip_recommended,
    extra_checks=(_soc_order,),
)


# ---------------------------------------------------------------- charger_evidence.csv
def _location_given(row: Row) -> list[Finding]:
    has_lat, has_lon = bool(row.get("lat")), bool(row.get("lon"))
    out: list[Finding] = []
    if has_lat != has_lon:
        out.append((ERROR, "fill both lat and lon, or neither"))
    if not row.get("place_url") and not (has_lat and has_lon):
        level = ERROR if row.get("verified") == "yes" else WARN
        out.append((level, "give place_url or lat+lon so the row can be matched to a charger"))
    return out


CHARGER_EVIDENCE = FileSpec(
    filename="charger_evidence.csv",
    id_column="evidence_id",
    columns=(
        Col("evidence_id", always=True),
        Col("charger_name", always=True),
        Col("operator"),
        Col("landmark_or_address"),
        Col("lat", "number", lo=6, hi=38),  # India bounding box
        Col("lon", "number", lo=68, hi=98),
        Col("place_url", "url"),
        Col("source_kind", "enum", ("google_review", "plugshare", "operator_app", "x_post", "forum", "news"),
            always=True),
        Col("evidence_date", "date", always=True),
        Col("evidence_time", "time"),
        Col("status", "enum", ("working", "broken", "partially_working", "queue", "no_power",
                               "access_blocked", "payment_failed", "unknown"), always=True),
        Col("connector"),
        Col("rated_kw", "number", lo=1, hi=400),
        Col("queue_count", "int", lo=0, hi=50),
        Col("source_url", "url", always=True),
        Col("paraphrase"),
        Col("collected_by", always=True),
        Col("verified", "enum", YES_NO, always=True),
    ),
    needed_when_verified=lambda row: ("operator",),
    recommended=lambda row: ("paraphrase",),
    paraphrase_columns=("paraphrase",),
    extra_checks=(_location_given,),
)


# ---------------------------------------------------------------- app_status_snapshots.csv
APP_SNAPSHOTS = FileSpec(
    filename="app_status_snapshots.csv",
    id_column="snapshot_id",
    columns=(
        Col("snapshot_id", always=True),
        Col("app", "enum", APPS, always=True),
        Col("charger_name", always=True),
        Col("charger_ref_in_app"),
        Col("datetime_ist", "datetime", always=True),
        Col("status_shown", "enum", ("available", "busy", "offline", "faulty", "unknown"), always=True),
        Col("connectors_available", "int", lo=0, hi=50),
        Col("rated_kw", "number", lo=1, hi=400),
        Col("collected_by", always=True),
    ),
    has_verified=False,
)


# ---------------------------------------------------------------- app_reviews.csv (no review text)
def _claim_needs_category(row: Row) -> list[Finding]:
    if row.get("human_claim") and not row.get("human_category"):
        return [(WARN, "human_claim is filled but human_category is empty; label both or neither")]
    return []


APP_REVIEWS = FileSpec(
    filename="app_reviews.csv",
    id_column="review_id",
    columns=(
        Col("review_id", always=True),
        Col("app", "enum", APPS, always=True),
        Col("review_date", "date", always=True),
        Col("stars", "int", lo=1, hi=5, always=True),
        Col("language", "enum", ("en", "hi", "hinglish", "other"), always=True),
        Col("paraphrase"),
        Col("human_category", "enum", ASENSIO_CATEGORIES),
        Col("human_claim", "enum", ("charger_not_working", "wrong_status", "payment", "app_bug", "queue", "other")),
        Col("collected_by", always=True),
    ),
    has_verified=False,
    paraphrase_columns=("paraphrase",),
    extra_checks=(_claim_needs_category,),
)

# ---------------------------------------------------------------- vehicles_india.csv (cited specs)
_VALUE_SOURCES = (("battery_nominal_kwh", "battery_source_url"), ("curb_mass_kg_low", "curb_mass_source_url"),
                  ("claimed_range_km", "range_source_url"), ("motor_kw", "motor_source_url"),
                  ("drag_or_efficiency_hint", "hint_source_url"))


def _every_value_cited(row: Row) -> list[Finding]:
    out: list[Finding] = [(ERROR, f"{value} is filled but {url} is empty; every spec value needs its source")
                          for value, url in _VALUE_SOURCES if row.get(value) and not row.get(url)]
    low, high = _num(row.get("curb_mass_kg_low", "")), _num(row.get("curb_mass_kg_high", ""))
    if low is not None and high is not None and high < low:
        out.append((ERROR, "curb_mass_kg_high is smaller than curb_mass_kg_low"))
    return out


VEHICLES = FileSpec(
    filename="vehicles_india.csv",
    id_column="vehicle_id",
    columns=(
        Col("vehicle_id", always=True),
        Col("make", always=True),
        Col("model", always=True),
        Col("variant", always=True),
        Col("vehicle_type", "enum", ("car", "2w", "3w"), always=True),
        Col("battery_nominal_kwh", "number", lo=1, hi=200, always=True),
        Col("battery_basis", "enum", ("gross", "usable", "unstated"), always=True),
        Col("battery_usable_kwh", "number", lo=1, hi=200),
        Col("battery_source_url", "url", always=True),
        Col("curb_mass_kg_low", "number", lo=50, hi=4000),
        Col("curb_mass_kg_high", "number", lo=50, hi=4000),
        Col("curb_mass_source_url", "url"),
        Col("claimed_range_km", "number", lo=10, hi=1500),
        Col("range_test_cycle", "enum", ("MIDC", "ARAI", "WLTP", "unstated")),
        Col("range_source_url", "url"),
        Col("motor_kw", "number", lo=1, hi=1000),
        Col("motor_source_url", "url"),
        Col("drag_or_efficiency_hint"),
        Col("hint_source_url", "url"),
        Col("source_quality", "enum", ("official", "secondary"), always=True),
        Col("engine_prior_class", always=True),
        Col("notes"),
    ),
    has_verified=False,
    extra_checks=(_every_value_cited,),
)

SPECS: tuple[FileSpec, ...] = (TRIPS, CHARGER_EVIDENCE, APP_SNAPSHOTS, APP_REVIEWS, VEHICLES)
PRIVATE_REVIEW_TEXT = "app_reviews_text.csv"  # lives in data/private/ (gitignored), never data/corpus/
PRIVATE_REVIEW_HEADER = ["review_id", "text"]


# ---------------------------------------------------------------- value checks
def _num(value: str) -> float | None:
    try:
        return float(value.strip())
    except (ValueError, AttributeError):
        return None


_DATE_HINT = "write dates as YYYY-MM-DD, for example 2026-11-14 (in Google Sheets: Format > Number > Plain text)"


def _check_number(col: Col, value: str) -> str | None:
    number = _num(value)
    if number is None:
        return f"{col.name} is '{value}', it must be a plain number (no units, no ranges like 65-70)"
    if col.kind == "int" and not number.is_integer():
        return f"{col.name} is '{value}', it must be a whole number"
    if (col.lo is not None and number < col.lo) or (col.hi is not None and number > col.hi):
        return f"{col.name} is {value}, outside the plausible range {col.lo:g} to {col.hi:g}"
    return None


def _check_date(col: Col, value: str) -> str | None:
    formats = ("%Y-%m-%d", "%Y-%m") if col.kind == "month_or_date" else ("%Y-%m-%d",)
    for fmt in formats:
        try:
            parsed = datetime.strptime(value, fmt).date()
        except ValueError:
            continue
        return f"{col.name} {value} is in the future" if parsed > date.today() else None
    month_hint = ", or YYYY-MM for a month" if col.kind == "month_or_date" else ""
    return f"{col.name} is '{value}'; {_DATE_HINT}{month_hint}"


def _check_datetime(col: Col, value: str) -> str | None:
    try:
        moment = datetime.strptime(value, "%Y-%m-%d %H:%M")
    except ValueError:
        return f"{col.name} is '{value}'; write it as YYYY-MM-DD HH:MM in IST, for example 2026-11-14 18:45"
    return f"{col.name} {value} is in the future" if moment.date() > date.today() else None


def check_value(col: Col, value: str) -> str | None:
    """Return a friendly error message for a non-empty value, or None if it is acceptable."""
    if col.kind == "enum" and value not in col.allowed:
        return f"{col.name} is '{value}'; allowed values are {' | '.join(col.allowed)} (lower case)"
    if col.kind in ("number", "int"):
        return _check_number(col, value)
    if col.kind == "url" and not re.fullmatch(r"https?://\S+", value):
        return f"{col.name} must be a full link starting with http:// or https://"
    if col.kind in ("date", "month_or_date"):
        return _check_date(col, value)
    if col.kind == "time" and not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", value):
        return f"{col.name} is '{value}'; write 24-hour time as HH:MM, for example 18:45"
    if col.kind == "datetime":
        return _check_datetime(col, value)
    return None


# ---------------------------------------------------------------- row and table validation
def check_header(spec: FileSpec, header: Sequence[str]) -> list[Issue]:
    got = [h.strip() for h in header]
    if got == spec.header:
        return []
    missing = [c for c in spec.header if c not in got]
    extra = [c for c in got if c not in spec.header]
    parts = []
    if missing:
        parts.append(f"missing columns: {', '.join(missing)}")
    if extra:
        parts.append(f"unexpected columns: {', '.join(extra)}")
    if not parts:
        parts.append("columns are in the wrong order")
    message = "; ".join(parts) + ". Copy the header row exactly from the template in data/corpus/"
    return [Issue(spec.filename, 0, "", ERROR, message)]


def _clean(raw: Row) -> Row:
    return {k: (v or "").strip() for k, v in raw.items() if isinstance(k, str)}


def _completeness(spec: FileSpec, row: Row) -> list[Finding]:
    out: list[Finding] = []
    gaps = [name for name in spec.needed_when_verified(row) if not row.get(name)]
    if gaps and spec.has_verified:
        if row.get("verified") == "yes":
            out.append((ERROR, f"marked verified=yes but missing: {', '.join(gaps)}"))
        else:
            out.append((WARN, f"draft: fill {', '.join(gaps)} before marking verified=yes"))
    soft = [name for name in spec.recommended(row) if not row.get(name)]
    if soft:
        out.append((WARN, f"recommended but empty: {', '.join(soft)}"))
    return out


def _paraphrase_rules(spec: FileSpec, row: Row) -> list[Finding]:
    out: list[Finding] = []
    for name in spec.paraphrase_columns:
        text = row.get(name, "")
        words = len(text.split())
        if words > MAX_PARAPHRASE_WORDS:
            out.append((ERROR, f"{name} has {words} words; rewrite it in at most {MAX_PARAPHRASE_WORDS} of your own words"))
        if "@" in text:
            out.append((ERROR, f"{name} contains '@'; remove handles and names of people"))
    return out


def validate_row(spec: FileSpec, raw: Row) -> list[Finding]:
    """Check one row. Returns (level, message) pairs."""
    row = _clean(raw)
    out: list[Finding] = []
    for col in spec.columns:
        value = row.get(col.name, "")
        if not value:
            if col.always:
                out.append((ERROR, f"{col.name} is empty but required on every row"))
            continue
        problem = check_value(col, value)
        if problem:
            out.append((ERROR, problem))
    out.extend(_completeness(spec, row))
    out.extend(_paraphrase_rules(spec, row))
    for check in spec.extra_checks:
        out.extend(check(row))
    return out


def validate_table(spec: FileSpec, header: Sequence[str], rows: Sequence[Row]) -> list[Issue]:
    issues = check_header(spec, header)
    if issues:
        return issues
    seen: dict[str, int] = {}
    for index, raw in enumerate(rows):
        sheet_row = index + 2
        row_id = _clean(raw).get(spec.id_column, "")
        issues.extend(Issue(spec.filename, sheet_row, row_id, level, message)
                      for level, message in validate_row(spec, raw))
        if not row_id:
            continue
        if row_id in seen:
            issues.append(Issue(spec.filename, sheet_row, row_id, ERROR,
                                f"{spec.id_column} '{row_id}' is already used on row {seen[row_id]}"))
        else:
            seen[row_id] = sheet_row
    return issues


def usable_rows(spec: FileSpec, rows: Sequence[Row]) -> list[Row]:
    """The only rows a result may use: verified=yes (where the file has that column) and no ERROR."""
    out = []
    for raw in rows:
        row = _clean(raw)
        if spec.has_verified and row.get("verified") != "yes":
            continue
        if any(level == ERROR for level, _ in validate_row(spec, row)):
            continue
        out.append(row)
    return out


def read_csv(path: Path) -> tuple[list[str], list[Row]]:
    """Read a CSV exported from Google Sheets or Excel (UTF-8, optional BOM). Fully empty rows are dropped."""
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        header = list(reader.fieldnames or [])
        rows = [row for row in reader
                if any(isinstance(v, str) and v.strip() for v in row.values())]
    return header, rows
