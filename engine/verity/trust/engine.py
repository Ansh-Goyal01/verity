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
from .priors import BetaPrior, operator_key, pooled_prior, prior_for, region_of
from .rings import detect_rings
from .source_trust import LABEL_OF, USAGE_SENS, USAGE_SOURCE_ID, USAGE_SPEC, ReportSet, fit_trust

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
    basis: str = "prior only"  # own reports | operator pool | prior only (WP3)
    pool_n: int = 0  # real reports on the operator's other chargers behind an "operator pool" prior


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


def operator_pool(chargers: Sequence[ChargerRecord], reports: Sequence[Report], now: datetime,
                  recent_days: float = RECENT_DAYS) -> dict[str, tuple[int, int]]:
    """{operator key: (reports saying it works, reports)} over RECENT, REAL reports only (WP3). Simulated reports
    never feed a pool, so a real-route plan's priors never borrow from the simulated demo."""
    op_of = {c.id: operator_key(c.operator) for c in chargers}
    pool: dict[str, list[int]] = {}
    for r in reports:
        key = op_of.get(r.charger_id, "")
        if not key or r.is_simulated or r.status not in LABEL_OF:
            continue
        if (now - r.t).total_seconds() / 86400.0 > recent_days:
            continue
        k_n = pool.setdefault(key, [0, 0])
        k_n[0] += LABEL_OF[r.status]
        k_n[1] += 1
    return {k: (v[0], v[1]) for k, v in pool.items()}


def with_operator_pool(chargers: Sequence[ChargerRecord], priors: Sequence[BetaPrior], reports: Sequence[Report],
                       now: datetime) -> tuple[list[BetaPrior], list[str], list[int]]:
    """(priors, basis, pool_n) per charger. A charger with ANY report of its own keeps its prior untouched; one with
    none borrows from its operator's real record when there is one; otherwise it stays on the prior alone."""
    own = {r.charger_id for r in reports}
    pool = operator_pool(chargers, reports, now)
    rows: list[tuple[BetaPrior, str, int]] = []
    for c, prior in zip(chargers, priors):
        k_n = pool.get(operator_key(c.operator))
        if c.id in own:
            rows.append((prior, "own reports", 0))
        elif k_n is not None:
            rows.append((pooled_prior(prior, *k_n), "operator pool", k_n[1]))
        else:
            rows.append((prior, "prior only", 0))
    return [r[0] for r in rows], [r[1] for r in rows], [r[2] for r in rows]


class TrustEngine:
    """Estimate charger reliability from reports. Pure computation: no I/O, no randomness."""

    def __init__(self, chargers: Sequence[ChargerRecord], *, use_rings: bool = True, use_usage: bool = True,
                 pool_operators: bool = False):
        self.chargers = list(chargers)
        self.use_rings = use_rings
        self.use_usage = use_usage
        self.pool_operators = pool_operators
        self._priors = priors_for(self.chargers)

    def estimate(self, reports: Sequence[Report], now: datetime,
                 usage: dict[str, tuple[int, int]] | None = None) -> TrustReport:
        ids = [c.id for c in self.chargers]
        rs = ReportSet.from_reports(reports, ids, now, usage if self.use_usage else None)
        if self.pool_operators:
            priors, basis, pool_n = with_operator_pool(self.chargers, self._priors, reports, now)
        else:
            own = {r.charger_id for r in reports}
            priors = self._priors
            basis = ["own reports" if c.id in own else "prior only" for c in self.chargers]
            pool_n = [0] * len(self.chargers)
        prior_a = np.array([p.a for p in priors])
        prior_b = np.array([p.b for p in priors])
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
                                     trust_level(n, days), priors[i].mean, basis[i], pool_n[i])
        return TrustReport(out, dict(zip(rs.source_ids, map(float, fit.sens))),
                           dict(zip(rs.source_ids, map(float, fit.spec))),
                           tuple(rs.source_ids[i] for i in flagged), fit.n_iter)
