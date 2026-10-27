"""Source trust: Dawid-Skene inference of charger reliability and per-source honesty, in numpy.

Classic Dawid-Skene assumes one latent truth per item. A charger is intermittent, so here every REPORT
has its own latent state z (the charger really was working / not when that source looked):

    z_r ~ Bernoulli(r_c)                                  r_c = charger reliability (what we want)
    P(label = working | z = 1) = sens_s                   P(label = broken | z = 0) = spec_s

EM alternates (E) the posterior of each report's z given its label, the source's sens/spec and the
charger's current r_c, and (M) re-estimates sens/spec per source and r_c per charger, with Beta priors:
sources start honest-leaning (sens, spec ~ Beta(4, 1)) and chargers start at their operator/site prior.
This learns which sources lie (an operator feed that always says "working" ends with sens ~ 1 and
spec ~ 0, i.e. no information) without anyone labelling them. Time decay (half-life 30 days) and a
per-source weight (used to down-weight detected fake-report rings) scale each report's influence.
Known weakness: like all Dawid-Skene variants it needs honest sources to anchor each charger; a ring
that out-reports the honest users on a charger can still win, which is why rings are detected first.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Sequence

import numpy as np

from ..schemas import Report

Array = np.ndarray

LABEL_OF = {"working": 1, "occupied": 1, "broken": 0, "blocked": 0, "no_power": 0}

USAGE_SOURCE_ID = "usage"
# Usage traces are indirect evidence: a driver arrives and leaves within minutes without charging
# ("bounce"). ASSUMPTIONS, approximate - verify: P(session starts | works) and P(bounce | broken).
USAGE_SENS = 0.95
USAGE_SPEC = 0.70

# Beta prior of sens and spec: honest-leaning (mean 0.8), ~5 reports' worth. Beta(8, 2) held a lying feed's
# learned spec too high and weakened DS under a fake-report ring; chosen on simulator seeds 0-7, E5 uses others.
SOURCE_PRIOR = (4.0, 1.0)
HALF_LIFE_DAYS = 30.0


@dataclass(frozen=True, eq=False)
class ReportSet:
    """Reports as parallel arrays (one row per report, or per aggregated usage count)."""

    charger: Array
    source: Array
    label: Array
    age_days: Array
    weight: Array
    charger_ids: tuple[str, ...]
    source_ids: tuple[str, ...]

    @classmethod
    def from_reports(cls, reports: Sequence[Report], charger_ids: Sequence[str], now: datetime,
                     usage: dict[str, tuple[int, int]] | None = None) -> ReportSet:
        c_index = {c: i for i, c in enumerate(charger_ids)}
        sources = sorted({r.source_id for r in reports if r.charger_id in c_index})
        if usage:
            sources.append(USAGE_SOURCE_ID)
        s_index = {s: i for i, s in enumerate(sources)}
        ch, so, lab, age, wt = [], [], [], [], []
        for r in reports:
            if r.charger_id not in c_index:
                continue
            ch.append(c_index[r.charger_id]); so.append(s_index[r.source_id]); lab.append(LABEL_OF[r.status])
            age.append(max((now - r.t).total_seconds() / 86400.0, 0.0)); wt.append(1.0)
        for cid, (visits, bounces) in (usage or {}).items():
            if cid not in c_index or visits <= 0:
                continue
            for label, count in ((1, visits - bounces), (0, bounces)):
                if count > 0:
                    ch.append(c_index[cid]); so.append(s_index[USAGE_SOURCE_ID]); lab.append(label)
                    age.append(0.0); wt.append(float(count))
        return cls(np.array(ch, dtype=int), np.array(so, dtype=int), np.array(lab, dtype=int),
                   np.array(age, dtype=float), np.array(wt, dtype=float), tuple(charger_ids), tuple(sources))


@dataclass(frozen=True, eq=False)
class TrustFit:
    r_mean: Array  # (C,) posterior mean reliability
    a_post: Array
    b_post: Array
    sens: Array  # (S,)
    spec: Array
    n_eff: Array  # (C,) decayed, weighted evidence per charger
    n_iter: int


def fit_trust(
    rs: ReportSet,
    prior_a: Array,
    prior_b: Array,
    *,
    source_weight: Array | None = None,
    fixed: dict[int, tuple[float, float]] | None = None,
    half_life_days: float = HALF_LIFE_DAYS,
    n_iter: int = 300,
    tol: float = 1e-8,
) -> TrustFit:
    """EM for charger reliabilities and source confusion rates. ``fixed`` maps a source index to a
    (sens, spec) that is not learned (used for the usage-trace pseudo-source)."""
    n_c, n_s = len(rs.charger_ids), len(rs.source_ids)
    sw = np.ones(n_s) if source_weight is None else np.asarray(source_weight, dtype=float)
    w = rs.weight * 0.5 ** (rs.age_days / half_life_days) * sw[rs.source]
    sens, spec = np.full(n_s, 0.8), np.full(n_s, 0.8)
    for s, (se, sp) in (fixed or {}).items():
        sens[s], spec[s] = se, sp
    learn = np.array([s not in (fixed or {}) for s in range(n_s)], dtype=bool)
    r = prior_a / (prior_a + prior_b)
    y = rs.label.astype(float)
    alpha, beta = SOURCE_PRIOR
    it = 0
    for it in range(1, n_iter + 1):
        rc, se, sp = r[rs.charger], sens[rs.source], spec[rs.source]
        like1 = np.where(rs.label == 1, se, 1.0 - se)
        like0 = np.where(rs.label == 1, 1.0 - sp, sp)
        q = rc * like1 / np.maximum(rc * like1 + (1.0 - rc) * like0, 1e-12)
        wq, wnq = w * q, w * (1.0 - q)
        sens_new = (alpha + np.bincount(rs.source, wq * y, n_s)) / (alpha + beta + np.bincount(rs.source, wq, n_s))
        spec_new = (alpha + np.bincount(rs.source, wnq * (1.0 - y), n_s)) / (
            alpha + beta + np.bincount(rs.source, wnq, n_s))
        r_new = (prior_a + np.bincount(rs.charger, wq, n_c)) / (prior_a + prior_b + np.bincount(rs.charger, w, n_c))
        delta = max(np.abs(r_new - r).max(), np.abs(sens_new[learn] - sens[learn]).max(initial=0.0),
                    np.abs(spec_new[learn] - spec[learn]).max(initial=0.0))
        r, sens, spec = r_new, np.where(learn, sens_new, sens), np.where(learn, spec_new, spec)
        if delta < tol:
            break
    rc, se, sp = r[rs.charger], sens[rs.source], spec[rs.source]
    like1 = np.where(rs.label == 1, se, 1.0 - se)
    like0 = np.where(rs.label == 1, 1.0 - sp, sp)
    q = rc * like1 / np.maximum(rc * like1 + (1.0 - rc) * like0, 1e-12)
    a_post = prior_a + np.bincount(rs.charger, w * q, n_c)
    b_post = prior_b + np.bincount(rs.charger, w * (1.0 - q), n_c)
    return TrustFit(a_post / (a_post + b_post), a_post, b_post, sens, spec, np.bincount(rs.charger, w, n_c), it)


def average_rating_baseline(rs: ReportSet, stars: Array, fallback: float) -> Array:
    """The baseline of experiment E5: mean star rating per charger mapped to a reliability in [0, 1]."""
    n_c = len(rs.charger_ids)
    count = np.bincount(rs.charger, minlength=n_c)
    total = np.bincount(rs.charger, np.asarray(stars, dtype=float), n_c)
    mean = np.where(count > 0, total / np.maximum(count, 1), np.nan)
    return np.where(np.isnan(mean), fallback, (mean - 1.0) / 4.0)
