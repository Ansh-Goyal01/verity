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

SITE_RADIUS_M = 150.0  # chargers this close form one Site (TASKS_FINALE F8c "~150 m"; approximate - verify)
BETA_SITE = 0.5  # share of a charger's failure probability that is Site-wide (assumption, approximate - verify)


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
    """

    def __init__(self, rng: np.random.Generator, n_runs: int, beta: float = BETA_SITE) -> None:
        self._rng, self._n, self._beta = rng, n_runs, float(beta)
        self._site_u: dict[str, Array] = {}

    def works(self, site: str, p_works: float) -> Array:
        f = float(np.clip(1.0 - p_works, 0.0, 1.0))
        common = self._beta * f
        u = self._site_u.get(site)
        if u is None:
            u = self._rng.random(self._n)
            self._site_u[site] = u
        indep = 0.0 if common >= 1.0 else (f - common) / (1.0 - common)
        return ~((u < common) | (self._rng.random(self._n) < indep))


def p_all_fail(p_works: Sequence[float], sites: Sequence[str], reach: Array, beta: float = BETA_SITE) -> Array:
    """P(every REACHABLE candidate fails), for a boolean ``reach`` of shape (C, ...) over the C candidates.

    Sites are independent of each other. Inside a Site the most reliable reachable charger fails with its own f;
    every further reachable charger there then fails with beta + (1 - beta) * f (given the Site already shows a
    fault, a common cause is likely). With beta = 0 this is the plain product of independent failures.
    """
    reach = np.asarray(reach, dtype=bool)
    fails = np.clip(1.0 - np.asarray(p_works, dtype=float), 0.0, 1.0)
    out = np.ones(reach.shape[1:])
    for site in dict.fromkeys(sites):
        idx = sorted((i for i, s in enumerate(sites) if s == site), key=lambda i: fails[i])
        found = np.zeros(reach.shape[1:], dtype=bool)
        for i in idx:
            f_i = np.where(found, beta + (1.0 - beta) * fails[i], fails[i])
            out = out * np.where(reach[i], f_i, 1.0)
            found |= reach[i]
    return out
