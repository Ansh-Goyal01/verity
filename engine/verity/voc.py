"""Voice of customer (F6): classify charging-app reviews into the 8 Asensio et al. (Nature Sustainability 2020)
categories plus a claim type, and measure agreement with human labels. Pure functions; the Sarvam call and the
private review text live in experiments/voc_app_reviews.py. No review text is ever written to a committed file.

Category definitions below are our one-line paraphrases of the paper's categories (approximate - verify against the
paper before quoting). A transparent keyword classifier is kept as a baseline the model must beat.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Sequence

from .corpus import APP_REVIEWS, ASENSIO_CATEGORIES

CLAIMS: tuple[str, ...] = next(c.allowed for c in APP_REVIEWS.columns if c.name == "human_claim")
PROMPT_VERSION = "voc-v1"
CATEGORY_DEFS = {
    "functionality": "the charger or station did not work, stopped mid-session, or worked fine",
    "availability": "chargers occupied, queues, blocked by parked cars, or not available when needed",
    "cost": "price, tariffs, billing amounts, refunds, fees",
    "location": "finding the charger, wrong map location, access, placement, surroundings",
    "dealership": "charging at a car dealership or service by a dealer",
    "user_interaction": "the app, payment flow, login, wallet, customer support, staff",
    "service_time": "charging speed, time taken, slower than rated power",
    "range_anxiety": "worry about running out of battery or reaching a destination",
    "other": "none of the above",
}
CLAIM_DEFS = {
    "charger_not_working": "says a charger was broken / not working",
    "wrong_status": "the app showed a status that was wrong (e.g. available but dead)",
    "payment": "payment failed, money deducted, refund problems",
    "app_bug": "the app crashed, would not load, login or other software problems",
    "queue": "had to wait / queue for a charger",
    "other": "none of the above",
}
SCHEMA = {
    "type": "object",
    "properties": {"labels": {"type": "array", "items": {
        "type": "object",
        "properties": {"key": {"type": "string"}, "category": {"type": "string", "enum": list(ASENSIO_CATEGORIES)},
                       "claim": {"type": "string", "enum": list(CLAIMS)}},
        "required": ["key", "category", "claim"]}}},
    "required": ["labels"],
}


def build_prompt(batch: Sequence[tuple[str, str]]) -> tuple[str, str]:
    """(system, user) messages for a batch of (key, review text). Text may be English, Hindi or Hinglish."""
    cats = "\n".join(f"- {k}: {v}" for k, v in CATEGORY_DEFS.items())
    claims = "\n".join(f"- {k}: {v}" for k, v in CLAIM_DEFS.items())
    system = ("You label reviews of Indian EV charging apps. Reviews may be in English, Hindi or Hinglish "
              "(Hindi in Latin script). For each review give the ONE main category and the ONE main claim.\n"
              f"Categories:\n{cats}\nClaims:\n{claims}\n"
              'Answer only with JSON: {"labels": [{"key": ..., "category": ..., "claim": ...}]}, one entry per review.')
    user = "\n".join(f"[{k}] {' '.join(text.split())}" for k, text in batch)
    return system, user


def parse_labels(obj: object, keys: Sequence[str]) -> dict[str, tuple[str, str]]:
    """Valid (category, claim) per requested key; anything malformed or unknown is dropped, never guessed."""
    out: dict[str, tuple[str, str]] = {}
    items = obj.get("labels", []) if isinstance(obj, dict) else []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        key, cat, claim = str(item.get("key", "")).strip("[] "), item.get("category"), item.get("claim")
        if key in keys and cat in ASENSIO_CATEGORIES and claim in CLAIMS:
            out[key] = (cat, claim)
    return out


# ---------------------------------------------------------------- keyword baseline (English / Hinglish)
_CAT_RULES = (  # first match wins; patterns over lower-cased text
    ("user_interaction", r"\bapp\b|login|otp|wallet|crash|payment|paytm|upi|support|customer care|refund"),
    ("functionality", r"not working|doesn.?t work|broken|dead|faulty|kharab|band hai|nahi chal|error|stopped"),
    ("availability", r"occupied|queue|waiting|\bwait|busy|parked|line me|blocked"),
    ("service_time", r"slow|speed|hours|ghante"),
    ("cost", r"price|expensive|costly|tariff|rupee|₹|mehenga"),
    ("location", r"location|\bmap\b|address|nahi mila|direction"),
    ("range_anxiety", r"\brange\b|stranded|battery khatam"),
    ("dealership", r"dealer|showroom"),
)
_CLAIM_RULES = (
    ("wrong_status", r"shows? available|showing available|available dikha|wrong status"),
    ("payment", r"payment|money|deducted|refund|paise|\bupi\b|wallet"),
    ("app_bug", r"crash|login|otp|not loading|\bbug|hang"),
    ("charger_not_working", r"not working|doesn.?t work|broken|dead|faulty|kharab|band hai|nahi chal"),
    ("queue", r"queue|waiting|\bwait|line me|occupied"),
)


def keyword_classify(text: str) -> tuple[str, str]:
    low = (text or "").lower()
    cat = next((c for c, p in _CAT_RULES if re.search(p, low)), "other")
    claim = next((c for c, p in _CLAIM_RULES if re.search(p, low)), "other")
    return cat, claim


# ---------------------------------------------------------------- statistics
def wilson(k: int, n: int) -> list[float] | None:
    if n <= 0:
        return None
    z2, p = 1.96**2, k / n
    centre = (p + z2 / (2 * n)) / (1 + z2 / n)
    half = 1.96 * math.sqrt(p * (1 - p) / n + z2 / (4 * n**2)) / (1 + z2 / n)
    return [round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4)]


def cohen_kappa(a: Sequence[str], b: Sequence[str]) -> float | None:
    """Cohen's kappa of two label sequences; None when undefined (no items, or chance agreement is 1)."""
    n = len(a)
    if n == 0 or n != len(b):
        return None
    po = sum(x == y for x, y in zip(a, b)) / n
    ca, cb = Counter(a), Counter(b)
    pe = sum(ca[k] * cb[k] for k in set(ca) | set(cb)) / n**2
    return None if pe >= 1.0 else round((po - pe) / (1.0 - pe), 4)


def agreement(predicted: Sequence[str], human: Sequence[str]) -> dict:
    n = len(human)
    k = sum(p == h for p, h in zip(predicted, human))
    return {"n": n, "accuracy": round(k / n, 4) if n else None, "accuracy_ci95": wilson(k, n),
            "cohen_kappa": cohen_kappa(list(predicted), list(human))}


def shares(labels: Sequence[str], universe: Sequence[str] = ASENSIO_CATEGORIES) -> dict:
    """Share of each label with a Wilson 95 % CI (each review has one main label here)."""
    n, counts = len(labels), Counter(labels)
    return {"n": n, "shares": [{"label": u, "k": counts[u], "share": round(counts[u] / n, 4) if n else None,
                                "ci95": wilson(counts[u], n)} for u in universe]}
