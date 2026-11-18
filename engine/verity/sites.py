"""Site correlation: chargers close together share failure causes, so a backup at the same Site is not independent.

Chargers within ``SITE_RADIUS_M`` of each other (single-linkage) form one Site: the same parking lot, grid
connection, access gate or operator outage. Failures are modelled with the beta-factor common-cause model used
in reliability engineering (IEC 61508-6 Annex D): a fraction ``BETA_SITE`` of each charger's failure probability
is a Site-wide cause shared by every charger there, the rest is independent. Each charger keeps its own marginal
P(works) exactly; only the joint behaviour changes.

Pure functions; no network.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from .trips import haversine_m

Array = np.ndarray

SITE_RADIUS_M = 150.0  # chargers this close form one Site (finale task F8c "~150 m"; approximate - verify)
BETA_SITE = 0.5  # share of a charger's failure probability that is Site-wide (assumption, approximate - verify)
# Share that is OPERATOR-wide (back-end, payment gateway, firmware, maintenance contract). An assumption, deliberately
# below the Site share: two chargers of one operator in different places fail together less than two on one plot.
# No outage data exists to estimate it (W2.2: estimate it from same-operator same-day failures once R2/R3 give rows).
BETA_OPERATOR = 0.2  # (assumption, approximate - verify)


def operators_from_pairs(pairs: Sequence[tuple[str, str]]) -> dict[str, str]:
    """Charger id -> operator id, from the (charger id, shared operator id) rows the graph's shared-cause query
    returns. A charger that shares its operator with no other charger in the set is absent: alone, its operator
    adds no common cause (and an unknown operator is never a shared one)."""
    return {cid: op for cid, op in pairs if op}


def assign_sites(points: Sequence[tuple[str, float, float]], radius_m: float = SITE_RADIUS_M) -> dict[str, str]:
    """``points`` = (charger id, lat, lon). Returns charger id -> site id (the smallest member id of its Site)."""
    ids = [p[0] for p in points]
    parent = list(range(len(points)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    if points:
        lat = np.array([p[1] for p in points], dtype=float)
        lon = np.array([p[2] for p in points], dtype=float)
        for i in range(len(points)):
            d = haversine_m(np.full(len(points), lat[i]), np.full(len(points), lon[i]), lat, lon)
            for j in np.nonzero(d <= radius_m)[0]:
                if j > i:
                    parent[find(int(j))] = find(i)
    groups: dict[int, list[str]] = {}
    for i, cid in enumerate(ids):
        groups.setdefault(find(i), []).append(cid)
    return {cid: min(members) for members in groups.values() for cid in members}


class WorksSampler:
    """Draws "does this charger work right now?" for every Monte Carlo run, with Site-wide common causes.

    One shared uniform per Site and run is drawn on first use and reused for every charger of that Site, so two
    chargers at one Site fail together far more often than two independent ones. Marginals are exact:
    P(fail) = beta*f + (1 - beta*f) * (1 - beta)*f / (1 - beta*f) = f.

    A charger may also name an operator (W2.2): one more shared uniform per operator and run, with the share
    ``beta_operator``. Three independent causes (site, operator, own) then give
    1 - P(fail) = (1 - cs)(1 - co)(1 - indep) = 1 - f, so every marginal stays exact here too. A charger with no
    operator draws exactly what it drew before operators existed (same random stream).
    """

    def __init__(self, rng: np.random.Generator, n_runs: int, beta: float = BETA_SITE,
                 beta_operator: float = BETA_OPERATOR) -> None:
        if not 0.0 <= beta <= 1.0 or not 0.0 <= beta_operator <= 1.0 or beta + beta_operator > 1.0:
            raise ValueError("beta + beta_operator must lie in [0, 1], or a marginal would need a negative probability")
        self._rng, self._n, self._beta, self._beta_op = rng, n_runs, float(beta), float(beta_operator)
        self._site_u: dict[str, Array] = {}
        self._op_u: dict[str, Array] = {}

    def _shared(self, cache: dict[str, Array], key: str) -> Array:
        u = cache.get(key)
        if u is None:
            u = self._rng.random(self._n)
            cache[key] = u
        return u

    def works(self, site: str, p_works: float, operator: str = "") -> Array:
        f = float(np.clip(1.0 - p_works, 0.0, 1.0))
        common = self._beta * f
        u = self._shared(self._site_u, site)
        if not operator or self._beta_op == 0.0:
            indep = 0.0 if common >= 1.0 else (f - common) / (1.0 - common)
            return ~((u < common) | (self._rng.random(self._n) < indep))
        common_op = self._beta_op * f
        u_op = self._shared(self._op_u, operator)
        keep = (1.0 - common) * (1.0 - common_op)
        indep = 0.0 if keep <= 0.0 else max(1.0 - (1.0 - f) / keep, 0.0)
        return ~((u < common) | (u_op < common_op) | (self._rng.random(self._n) < indep))


def p_all_fail(p_works: Sequence[float], sites: Sequence[str], reach: Array, beta: float = BETA_SITE,
               operators: Sequence[str] | None = None, beta_operator: float = BETA_OPERATOR) -> Array:
    """P(every REACHABLE candidate fails), for a boolean ``reach`` of shape (C, ...) over the C candidates.

    Sites are independent of each other. Inside a Site the most reliable reachable charger fails with its own f;
    every further reachable charger there then fails with beta + (1 - beta) * f (given the Site already shows a
    fault, a common cause is likely). With beta = 0 this is the plain product of independent failures.

    ``operators`` (W2.2, "" = none shared) adds the same rule one level up: a reachable charger that has no earlier
    reachable charger on its Site but one of the same operator fails with beta_operator + (1 - beta_operator) * f.
    The Site link wins when both apply. Without operators this is exactly the Site-only computation.
    """
    reach = np.asarray(reach, dtype=bool)
    fails = np.clip(1.0 - np.asarray(p_works, dtype=float), 0.0, 1.0)
    if operators is not None and any(operators):
        return _p_all_fail_linked(fails, sites, operators, reach, beta, beta_operator)
    out = np.ones(reach.shape[1:])
    for site in dict.fromkeys(sites):
        idx = sorted((i for i, s in enumerate(sites) if s == site), key=lambda i: fails[i])
        found = np.zeros(reach.shape[1:], dtype=bool)
        for i in idx:
            f_i = np.where(found, beta + (1.0 - beta) * fails[i], fails[i])
            out = out * np.where(reach[i], f_i, 1.0)
            found |= reach[i]
    return out


def _p_all_fail_linked(fails: Array, sites: Sequence[str], operators: Sequence[str], reach: Array, beta: float,
                       beta_operator: float) -> Array:
    """``p_all_fail`` with an operator level: candidates in global reliability order (most reliable first), each
    conditioned on the earlier reachable ones through its Site, else through its operator, else not at all."""
    out = np.ones(reach.shape[1:])
    found_site: dict[str, Array] = {}
    found_op: dict[str, Array] = {}
    for i in sorted(range(len(sites)), key=lambda k: (fails[k], k)):
        f_i = np.full(reach.shape[1:], fails[i])
        op = operators[i]
        if op in found_op:
            f_i = np.where(found_op[op], beta_operator + (1.0 - beta_operator) * fails[i], f_i)
        if sites[i] in found_site:
            f_i = np.where(found_site[sites[i]], beta + (1.0 - beta) * fails[i], f_i)
        out = out * np.where(reach[i], f_i, 1.0)
        found_site[sites[i]] = found_site.get(sites[i], np.zeros(reach.shape[1:], dtype=bool)) | reach[i]
        if op:
            found_op[op] = found_op.get(op, np.zeros(reach.shape[1:], dtype=bool)) | reach[i]
    return out
