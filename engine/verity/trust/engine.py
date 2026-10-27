"""Trust engine: per-charger P(works), evidence summary and trust level for the planner and the API.

Pipeline: priors by operator/site/region -> reports (+ optional usage traces) -> ring detection
(detected rings get a tiny source weight) -> Dawid-Skene EM -> Beta posterior per charger.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Sequence

import numpy as np
from scipy.stats import beta as beta_dist

from ..schemas import ChargerRecord, Report
from .priors import BetaPrior, prior_for, region_of
from .rings import detect_rings
from .source_trust import USAGE_SENS, USAGE_SOURCE_ID, USAGE_SPEC, ReportSet, fit_trust

RECENT_DAYS = 60  # "based on N reports in 60 days"
RING_WEIGHT = 0.05  # weight kept by sources inside a detected ring


@dataclass(frozen=True)
class ChargerTrust:
    charger_id: str
    p_works: float  # posterior mean (rounded to 5 % only at the UI/API boundary)
    p_lo: float  # 10th percentile of the Beta posterior
    p_hi: float  # 90th percentile
    n_reports: int  # reports from non-flagged sources in the last RECENT_DAYS
    days_since_last: float | None
    trust_level: str  # high | medium | low | unknown
    prior_p: float


@dataclass(frozen=True)
class TrustReport:
    chargers: dict[str, ChargerTrust]
    source_sens: dict[str, float]
    source_spec: dict[str, float]
    flagged_sources: tuple[str, ...]
    n_iter: int


def trust_level(n_reports: int, days_since_last: float | None) -> str:
    if n_reports == 0 or days_since_last is None:
        return "unknown"
    if n_reports >= 5 and days_since_last <= 14:
        return "high"
    if n_reports >= 2 and days_since_last <= 45:
        return "medium"
    return "low"


def priors_for(chargers: Sequence[ChargerRecord]) -> list[BetaPrior]:
    return [prior_for(c.operator, c.site_type, region_of(c.lat, c.lon)) for c in chargers]


class TrustEngine:
    """Estimate charger reliability from reports. Pure computation: no I/O, no randomness."""

    def __init__(self, chargers: Sequence[ChargerRecord], *, use_rings: bool = True, use_usage: bool = True):
        self.chargers = list(chargers)
        self.use_rings = use_rings
        self.use_usage = use_usage
        self._priors = priors_for(self.chargers)

    def estimate(self, reports: Sequence[Report], now: datetime,
                 usage: dict[str, tuple[int, int]] | None = None) -> TrustReport:
        ids = [c.id for c in self.chargers]
        rs = ReportSet.from_reports(reports, ids, now, usage if self.use_usage else None)
        prior_a = np.array([p.a for p in self._priors])
        prior_b = np.array([p.b for p in self._priors])
        usage_idx = rs.source_ids.index(USAGE_SOURCE_ID) if USAGE_SOURCE_ID in rs.source_ids else None
        weights = np.ones(len(rs.source_ids))
        flagged: tuple[int, ...] = ()
        if self.use_rings and len(rs.source_ids):
            flagged = detect_rings(rs, ignore=() if usage_idx is None else (usage_idx,)).flagged
            weights[list(flagged)] = RING_WEIGHT
        fixed = {} if usage_idx is None else {usage_idx: (USAGE_SENS, USAGE_SPEC)}
        fit = fit_trust(rs, prior_a, prior_b, source_weight=weights, fixed=fixed)

        recent = (rs.age_days <= RECENT_DAYS) & (weights[rs.source] == 1.0)
        if usage_idx is not None:
            recent &= rs.source != usage_idx
        lo = beta_dist.ppf(0.10, fit.a_post, fit.b_post)
        hi = beta_dist.ppf(0.90, fit.a_post, fit.b_post)
        out: dict[str, ChargerTrust] = {}
        for i, c in enumerate(self.chargers):
            mask = recent & (rs.charger == i)
            n = int(mask.sum())
            days = float(rs.age_days[mask].min()) if n else None
            out[c.id] = ChargerTrust(c.id, float(fit.r_mean[i]), float(lo[i]), float(hi[i]), n, days,
                                     trust_level(n, days), self._priors[i].mean)
        return TrustReport(out, dict(zip(rs.source_ids, map(float, fit.sens))),
                           dict(zip(rs.source_ids, map(float, fit.spec))),
                           tuple(rs.source_ids[i] for i in flagged), fit.n_iter)
