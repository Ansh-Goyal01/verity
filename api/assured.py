"""WP9 Verity Assured: an ILLUSTRATIVE stranding-cover price on a GO release and a reward for verified reports.

Rupee inputs (tow cost with its source, margin, reward amount) come only from ``config/assured.json``, which Ansh
fills in. A missing, malformed or unsourced input means no rupee figure: the formula is shown without a number.
Nothing here is a real offer; every block says so.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from verity.paths import REPO_ROOT

CONFIG_PATH = REPO_ROOT / "config" / "assured.json"
DEFAULT_MIN_CONFIDENCE = 0.95  # suggested by the build guide
DEFAULT_DAILY_REPORT_CAP = 5  # assumption (docs/DECISIONS.md): rewarded reports per reporter per UTC day
PRICE_STEP_RS = 5
STRONG_EVIDENCE_P = 0.8  # assumption: a charger this likely working (or broken) is "strong" evidence...
STRONG_EVIDENCE_MIN_REPORTS = 3  # ...once at least this many reports back it
BROKEN_STATUSES = frozenset({"broken", "blocked", "no_power"})
NOTE = "Illustrative. A real cover would be sold through a licensed insurance or roadside partner."
BASIS = "upper bound: 1 - confidence, which also counts runs that arrived below the reserve"
FORMULA = "price = p_strand x tow cost x margin, rounded to the nearest Rs 5"


def _number(value: Any, low: float, high: float | None = None) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value) if value >= low and (high is None or value <= high) else None


def load_config(path: Path | None = None) -> dict:
    """The validated inputs; anything missing or invalid is None (or its documented default), never invented."""
    try:
        raw = json.loads((path or CONFIG_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    raw = raw if isinstance(raw, dict) else {}
    source = raw.get("tow_cost_source_url")
    sourced = isinstance(source, str) and source.startswith(("http://", "https://"))
    tow = _number(raw.get("tow_cost_rs"), 1)
    cap = _number(raw.get("report_daily_cap"), 1)
    return {"tow_cost_rs": tow if sourced else None, "tow_cost_source": source if sourced and tow else None,
            "margin": _number(raw.get("margin"), 1),
            "min_confidence": _number(raw.get("min_confidence"), 0.5, 0.999) or DEFAULT_MIN_CONFIDENCE,
            "report_reward_rs": _number(raw.get("report_reward_rs"), 0),
            "report_daily_cap": int(cap) if cap else DEFAULT_DAILY_REPORT_CAP}


def _nearest_5_percent(p: float) -> float:
    return round(min(max(p, 0.0), 1.0) * 20) / 20


def assured_block(*, decision: str, confidence: float, confidence_raw: float, band_method: str, cfg: dict) -> dict:
    """The cover block for one release. Eligible only for a GO at or above the threshold with calibrated bands."""
    p_strand = max(0.0, 1.0 - confidence_raw)
    if decision != "GO":
        reason = "Cover is offered on GO releases only."
    elif confidence < cfg["min_confidence"]:
        reason = f"Confidence is below the {round(cfg['min_confidence'] * 100)}% needed for cover."
    elif band_method == "model only":
        reason = "bands not yet calibrated on real data"
    else:
        reason = None
    eligible = reason is None
    priced = eligible and cfg["tow_cost_rs"] is not None and cfg["margin"] is not None
    price = round(p_strand * cfg["tow_cost_rs"] * cfg["margin"] / PRICE_STEP_RS) * PRICE_STEP_RS if priced else None
    return {"eligible": eligible, "reason": reason, "p_strand": _nearest_5_percent(p_strand), "p_strand_basis": BASIS,
            "tow_cost_rs": cfg["tow_cost_rs"], "tow_cost_source": cfg["tow_cost_source"], "margin": cfg["margin"],
            "price_rs": price, "formula": FORMULA, "is_illustrative": True, "note": NOTE}


def contradicts_strong_evidence(before: dict, status: str) -> bool:
    """True when a charger the trust engine already rates strongly one way gets a report the other way."""
    p, n = before.get("p_works"), before.get("n_reports", 0)
    if p is None or n < STRONG_EVIDENCE_MIN_REPORTS:
        return False
    return (p >= STRONG_EVIDENCE_P and status in BROKEN_STATUSES) or (p <= 1 - STRONG_EVIDENCE_P and status == "working")


def reward_block(*, is_simulated: bool, in_ring: bool, contradicts: bool, over_cap: bool, cfg: dict) -> dict:
    """Whether one report earns the illustrative reward, and if not, why (the first reason that applies)."""
    reasons = (
        (is_simulated, "simulated report: demo and ring-attack sources earn nothing"),
        (in_ring, "the source belongs to a flagged ring"),
        (contradicts, "contradicts strong recent evidence from trusted sources"),
        (over_cap, f"daily cap of {cfg['report_daily_cap']} rewarded reports reached"),
    )
    reason = next((text for hit, text in reasons if hit), None)
    eligible = reason is None
    return {"eligible": eligible, "amount_rs": cfg["report_reward_rs"] if eligible else None,
            "withheld_reason": reason, "is_illustrative": True}
