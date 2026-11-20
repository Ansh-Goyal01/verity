"""Trip options (WP4, docs/FINALE_BUILD.md): "How do you want to go?" as up to three full releases.

The candidates are proposed and scored ONCE by the same machinery as ``dispatch`` (``_choose``), then one plan is
picked per objective:

* ``safest``: the highest confidence, compared at the Monte Carlo's own resolution (2 points) and then by expected
  lost minutes, so noise alone never makes a 3-stop plan "safer" than a 1-stop plan;
* ``fastest``: the lowest mean door-to-door minutes among plans that meet the target;
* ``fewest_stops``: the fewest stops among plans that meet the target, ties broken by minutes.

When no plan meets the target, ``fastest`` and ``fewest_stops`` fall back to the safest plan and say so
(``meets_target: false``). Objectives that pick the same plan (same stops AND same charge targets) share one option.
Every option then goes through ``dispatch._release``: the same evidence check, decision rules, Bingo Line and reason as
``POST /api/dispatch``. Only the levers that re-plan from scratch (cruise at 80, another vehicle) are left out of the
options: they would re-plan the whole trip, not this option (docs/DECISIONS.md WP4).
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Sequence

import numpy as np

from .chargers import RouteCharger
from .dispatch import (
    DispatchResult,
    Node,
    PlanEval,
    Stop,
    VehicleContext,
    _build_ctx,
    _choose,
    _release,
    graph_operators,
    make_nodes,
    meets_target,
)
from .forecast import RESERVE_PCT, ForecastConfig, PlannedRoute
from .trust.engine import ChargerTrust
from .trust.graph import ChargerGraph

OBJECTIVES = ("fastest", "fewest_stops", "safest")
MC_RESOLUTION = 0.02  # confidences closer than this are a tie (the Monte Carlo cannot tell them apart)
COUNTRY_TAG = re.compile(r"\s*\((in|india)\)$")  # Open Charge Map suffixes the operator's country
OPTION_SEED_STEP = 101  # each option's Bingo Line and evidence draws get their own seed (seed + step * index)

Scored = tuple[list[Stop], PlanEval]


@dataclass(frozen=True)
class Preferences:
    objectives: tuple[str, ...] = OBJECTIVES
    preferred_operators: tuple[str, ...] = ()
    avoid_operators: tuple[str, ...] = ()
    arrival_buffer_pct: float = RESERVE_PCT
    max_stops: int | None = None


@dataclass
class PlanOption:
    labels: tuple[str, ...]
    meets_target: bool  # the plan's displayed confidence meets the target (the decision may still refuse)
    result: DispatchResult


@dataclass
class OptionsResult:
    options: list[PlanOption]
    unsupported: list[dict] = field(default_factory=list)  # {"preference", "reason"}: what could not be honoured
    n_candidates: int = 0
    avoided_chargers: int = 0
    compute_ms: int = 0


def _norm(name: str) -> str:
    """Case- and space-insensitive, without the directory's country tag: "Statiq (IN)" and "statiq" are one operator."""
    return COUNTRY_TAG.sub("", " ".join(name.casefold().split()))


def operator_of(node: Node) -> str:
    """The directory operator of a node's charger, normalised ("" when the directory lists none)."""
    return _norm(node.charger.operator or "") if node.charger is not None else ""


def plan_signature(stops: Sequence[Stop]) -> tuple[tuple[str, float], ...]:
    """Two plans are the same option when they stop at the same chargers with the same charge targets."""
    return tuple((s.node.id, round(s.charge_to, 1)) for s in stops)


def filter_nodes(nodes: Sequence[Node], prefs: Preferences) -> tuple[list[Node], list[Node] | None, list[dict], int]:
    """(nodes left after ``avoid_operators``, the PLANNED-stop candidates for ``preferred_operators`` or None,
    unsupported preferences, chargers removed). Avoided operators are gone everywhere (stops and backups);
    preferred operators only restrict planned stops: a backup of any other operator is still better than none."""
    unsupported: list[dict] = []
    avoid = {_norm(o) for o in prefs.avoid_operators if o.strip()}
    prefer = {_norm(o) for o in prefs.preferred_operators if o.strip()}
    both = avoid & prefer
    if both:
        unsupported.append({"preference": "preferred_operators",
                            "reason": f"{', '.join(sorted(both))} is both preferred and avoided; it is avoided."})
        prefer -= both
    kept = [n for n in nodes if operator_of(n) not in avoid] if avoid else list(nodes)
    present = {operator_of(n) for n in nodes}
    for op in sorted(avoid - present):
        unsupported.append({"preference": "avoid_operators",
                            "reason": f"No charger run by {op!r} on this route, so there was nothing to avoid."})
    stop_nodes = None
    if prefer:
        stop_nodes = [n for n in kept if operator_of(n) in prefer]
        missing = sorted(prefer - {operator_of(n) for n in stop_nodes})
        if missing:
            unsupported.append({"preference": "preferred_operators",
                                "reason": f"No charger run by {', '.join(missing)} on this route."})
        if not stop_nodes:
            stop_nodes = None  # nothing to prefer: plan with every remaining operator, and say so (above)
    return kept, stop_nodes, unsupported, len(nodes) - len(kept)


def _safest_key(s: Scored) -> tuple:
    return (round(s[1].confidence / MC_RESOLUTION), -s[1].expected_lost_minutes, -len(s[0]))


def pick(scored: Sequence[Scored], objective: str, target: float) -> tuple[Scored, bool]:
    """(the plan for one objective, whether it meets the target). Falls back to the safest plan when nothing meets it."""
    meeting = [s for s in scored if meets_target(s[1].confidence, target)]
    if objective == "fastest" and meeting:
        return min(meeting, key=lambda s: (s[1].mean_minutes, len(s[0]))), True
    if objective == "fewest_stops" and meeting:
        return min(meeting, key=lambda s: (len(s[0]), s[1].mean_minutes)), True
    best = max(scored, key=_safest_key)
    return best, bool(meets_target(best[1].confidence, target))


def plan_options(vehicle: VehicleContext, route: PlannedRoute, route_chargers: Sequence[RouteCharger],
                 trust: dict[str, ChargerTrust], *, start_soc: float, target_confidence: float = 0.9,
                 prefs: Preferences = Preferences(), polyline: list | None = None, seed: int = 0,
                 cfg: ForecastConfig = ForecastConfig(), graph: ChargerGraph | None = None,
                 queue_wait_min: dict[str, float] | None = None) -> OptionsResult:
    t0 = time.perf_counter()
    rng = np.random.default_rng(seed)
    ctx = _build_ctx(vehicle, route, start_soc, target_confidence, rng, cfg, reserve_pct=prefs.arrival_buffer_pct)
    nodes = make_nodes(route_chargers, trust, trust_aware=True, queue_wait_min=queue_wait_min,
                       operators=graph_operators(graph, route_chargers) if graph is not None else None)
    nodes, stop_nodes, unsupported, n_avoided = filter_nodes(nodes, prefs)
    _, _, scored = _choose(ctx, nodes, rng, trust_aware=True, graph=graph, stop_nodes=stop_nodes)
    pool = scored
    if prefs.max_stops is not None:
        pool = [s for s in scored if len(s[0]) <= prefs.max_stops]  # never empty: the no-stop plan is always scored
        if not any(meets_target(s[1].confidence, target_confidence) for s in pool) and any(
                meets_target(s[1].confidence, target_confidence) for s in scored):
            unsupported.append({"preference": "max_stops",
                                "reason": f"No plan with at most {prefs.max_stops} stops meets the target; the options "
                                          "shown keep the cap and say they miss the target."})
    objectives = tuple(dict.fromkeys(prefs.objectives)) or OBJECTIVES  # unique, in the order asked
    chosen: dict[tuple, tuple[list[str], Scored, bool]] = {}
    for obj in objectives:
        plan, ok = pick(pool, obj, target_confidence)
        chosen.setdefault(plan_signature(plan[0]), ([], plan, ok))[0].append(obj)
    options = []
    for i, (labels, (stops, ev), ok) in enumerate(chosen.values()):
        opt_seed = seed + OPTION_SEED_STEP * (i + 1)
        res = _release(vehicle, route, route_chargers, trust, ctx, nodes, stops, ev, np.random.default_rng(opt_seed),
                       start_soc=start_soc, target_confidence=target_confidence, polyline=polyline, seed=opt_seed,
                       cfg=cfg, trust_aware=True, other_vehicles=(), compute_levers=True, graph=graph,
                       replan_levers=False)
        options.append(PlanOption(tuple(labels), ok, res))
    ms = int(round(1000 * (time.perf_counter() - t0)))
    for o in options:
        o.result.compute_ms = ms
    return OptionsResult(options, unsupported, len(scored), n_avoided, ms)
