"""SIMULATED charger-report worlds with noisy honest users, a lying operator feed and a fake-report ring.

Everything here is simulation: the reliabilities, accuracies and attack shapes are parameters chosen to
span the range of published figures in docs/PLAN.md section 3, not measurements. Reports carry star
ratings too (kept in a parallel array) so the "average rating" baseline of E5 can be computed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import numpy as np

from ..schemas import ChargerRecord, Report

IST = timezone(timedelta(hours=5, minutes=30))
NOW = datetime(2026, 10, 27, 12, 0, tzinfo=IST)
OPERATORS = ("OpA", "OpB", "OpC", "OpD")
OPERATOR_RATE = {"OpA": 0.88, "OpB": 0.72, "OpC": 0.50, "OpD": 0.30}  # simulation parameters, not data
SITE_TYPES = ("highway", "mall", "public_parking", "office")
BOUNCE_IF_BROKEN, BOUNCE_IF_WORKING = 0.70, 0.05  # usage-trace generator (assumptions)


@dataclass(frozen=True)
class WorldConfig:
    n_chargers: int = 120
    n_honest: int = 40
    days: int = 60
    reports_per_honest: tuple[int, int] = (3, 14)
    ring_size: int = 0
    ring_targets: int = 30
    ring_window_h: float = 6.0
    operator_feed: bool = False
    usage_visits_mean: float = 5.0
    seed: int = 0


@dataclass
class World:
    chargers: list[ChargerRecord]
    true_r: np.ndarray  # (C,) true probability each charger works when a driver arrives
    reports: list[Report]
    stars: np.ndarray  # (R,) 1-5 star rating of each report
    usage: dict[str, tuple[int, int]]  # charger id -> (visits, bounces)
    ring_sources: set[str] = field(default_factory=set)
    source_kind: dict[str, str] = field(default_factory=dict)
    ring_targets: set[str] = field(default_factory=set)  # charger ids the ring attacks
    is_simulated: bool = True


def _report(cid: str, sid: str, label: int, age_days: float, rng: np.random.Generator) -> tuple[Report, int]:
    stars = int(rng.choice([4, 5]) if label == 1 else rng.choice([1, 2]))
    status = "working" if label == 1 else str(rng.choice(["broken", "no_power", "blocked"]))
    return Report(charger_id=cid, source_id=sid, status=status, t=NOW - timedelta(days=age_days),
                  evidence="SIMULATED"), stars


def simulate_world(cfg: WorldConfig) -> World:
    rng = np.random.default_rng(cfg.seed)
    n = cfg.n_chargers
    ops = [OPERATORS[i % len(OPERATORS)] for i in range(n)]
    base = np.array([OPERATOR_RATE[o] for o in ops])
    true_r = np.clip(rng.beta(base * 6.0 + 0.5, (1.0 - base) * 6.0 + 0.5), 0.02, 0.98)
    chargers = [
        ChargerRecord(id=f"SIM-CH-{i:04d}", name=f"Simulated charger {i}", lat=float(rng.uniform(28.3, 28.8)),
                      lon=float(rng.uniform(77.0, 77.6)), operator=ops[i], site_type=str(rng.choice(SITE_TYPES)),
                      source_ids=["simulated"])
        for i in range(n)
    ]
    popularity = (np.arange(1, n + 1, dtype=float)) ** -0.8
    popularity = popularity[rng.permutation(n)]
    popularity /= popularity.sum()

    reports: list[Report] = []
    stars: list[int] = []
    kinds: dict[str, str] = {}

    for h in range(cfg.n_honest):
        sid = f"user-{h:03d}"
        kinds[sid] = "honest"
        sens, spec = rng.uniform(0.80, 0.97), rng.uniform(0.70, 0.95)
        for _ in range(int(rng.integers(cfg.reports_per_honest[0], cfg.reports_per_honest[1] + 1))):
            c = int(rng.choice(n, p=popularity))
            working = rng.random() < true_r[c]
            label = int(rng.random() < sens) if working else int(rng.random() >= spec)
            rep, st = _report(chargers[c].id, sid, label, float(rng.uniform(0, cfg.days)), rng)
            reports.append(rep); stars.append(st)

    if cfg.operator_feed:  # the weakest operator's own feed claims "working" almost everywhere
        sid = "feed:OpD"
        kinds[sid] = "operator_feed"
        for c in range(n):
            if ops[c] == "OpD" and rng.random() < 0.95:
                rep, _ = _report(chargers[c].id, sid, 1, float(rng.uniform(0, 2)), rng)
                reports.append(rep); stars.append(5)

    ring: set[str] = set()
    ring_targets: set[str] = set()
    if cfg.ring_size > 0:  # accounts attacking the best operator: "broken", 1 star, inside one short burst
        targets = rng.choice([c for c in range(n) if ops[c] == "OpA"], size=min(cfg.ring_targets, n // 4), replace=False)
        ring_targets = {chargers[int(c)].id for c in targets}
        centre = float(rng.uniform(2, 20))
        for m in range(cfg.ring_size):
            sid = f"ring-{m:02d}"
            ring.add(sid)
            kinds[sid] = "ring"
            for c in targets:
                if rng.random() < 0.85:
                    age = centre + float(rng.uniform(-cfg.ring_window_h, cfg.ring_window_h)) / 24.0
                    rep, _ = _report(chargers[int(c)].id, sid, 0, max(age, 0.0), rng)
                    reports.append(rep); stars.append(1)

    usage: dict[str, tuple[int, int]] = {}
    for c in range(n):
        visits = int(rng.poisson(cfg.usage_visits_mean * n * popularity[c] / 2.0))
        if visits == 0:
            continue
        works = rng.random(visits) < true_r[c]
        bounces = int(np.sum(rng.random(visits) < np.where(works, BOUNCE_IF_WORKING, BOUNCE_IF_BROKEN)))
        usage[chargers[c].id] = (visits, bounces)

    return World(chargers, true_r, reports, np.array(stars, dtype=float), usage, ring, kinds, ring_targets)
