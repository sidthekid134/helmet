"""Deterministic fantasy-football analytics primitives."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from itertools import combinations
from math import isfinite
from random import Random
from statistics import fmean, median

from .models import (
    LineupResult,
    PlayerProjection,
    Recommendation,
    RosterSlot,
    ScoredProjection,
    ScoringSettings,
    SimulationResult,
)


def _players_by_id(players: Iterable[PlayerProjection]) -> dict[str, PlayerProjection]:
    result: dict[str, PlayerProjection] = {}
    for player in players:
        if player.player_id in result:
            raise ValueError(f"duplicate player_id: {player.player_id}")
        result[player.player_id] = player
    if not result:
        raise ValueError("at least one player is required")
    return result


def score_outcome(stats: Mapping[str, float], scoring: ScoringSettings) -> float:
    """Score stats in rule order, including threshold bonuses."""
    missing = sorted({rule.stat for rule in scoring.rules} - stats.keys())
    if missing:
        raise ValueError(f"missing required scoring stats: {', '.join(missing)}")
    total = 0.0
    for rule in scoring.rules:
        value = float(stats[rule.stat])
        if not isfinite(value):
            raise ValueError(f"stat {rule.stat!r} must be finite")
        total += value * rule.points_per_unit
        if rule.threshold is not None and value >= rule.threshold:
            total += rule.bonus
    return total


def score_projection(player: PlayerProjection, scoring: ScoringSettings) -> float:
    return score_outcome(player.stats, scoring)


def score_projections(
    players: Iterable[PlayerProjection], scoring: ScoringSettings
) -> dict[str, float]:
    return {
        player_id: score_projection(player, scoring)
        for player_id, player in _players_by_id(players).items()
    }


def replacement_levels(
    players: Iterable[PlayerProjection],
    scoring: ScoringSettings,
    league_size: int,
    starters_per_team: Mapping[str, int],
) -> dict[str, float]:
    """Return points at the first non-starting rank for each position."""
    if isinstance(league_size, bool) or league_size < 1:
        raise ValueError("league_size must be a positive integer")
    by_position: dict[str, list[float]] = defaultdict(list)
    for player in _players_by_id(players).values():
        by_position[player.position].append(score_projection(player, scoring))
    if not starters_per_team:
        raise ValueError("starters_per_team cannot be empty")
    levels: dict[str, float] = {}
    for position, count in starters_per_team.items():
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            raise ValueError(f"starter count for {position} must be a positive integer")
        values = sorted(by_position.get(position, ()), reverse=True)
        replacement_index = league_size * count
        if len(values) <= replacement_index:
            raise ValueError(
                f"position {position} needs at least {replacement_index + 1} projections"
            )
        levels[position] = values[replacement_index]
    return levels


def value_over_replacement(
    players: Iterable[PlayerProjection],
    scoring: ScoringSettings,
    levels: Mapping[str, float],
) -> list[ScoredProjection]:
    player_map = _players_by_id(players)
    missing = sorted({player.position for player in player_map.values()} - levels.keys())
    if missing:
        raise ValueError(f"missing replacement levels for: {', '.join(missing)}")
    result = []
    for player in player_map.values():
        points = score_projection(player, scoring)
        replacement = float(levels[player.position])
        if not isfinite(replacement):
            raise ValueError(f"replacement level for {player.position} must be finite")
        result.append(
            ScoredProjection(
                player=player,
                projected_points=points,
                replacement_points=replacement,
                vorp=points - replacement,
            )
        )
    return sorted(result, key=lambda item: (-item.vorp, item.player.player_id))


def assign_tiers(projections: Sequence[ScoredProjection], gap: float) -> list[ScoredProjection]:
    if not isfinite(gap) or gap <= 0:
        raise ValueError("gap must be positive and finite")
    ordered = sorted(projections, key=lambda item: (-item.vorp, item.player.player_id))
    if not ordered:
        raise ValueError("at least one projection is required")
    output: list[ScoredProjection] = []
    tier = 1
    previous = ordered[0].vorp
    for item in ordered:
        if previous - item.vorp >= gap:
            tier += 1
        output.append(
            ScoredProjection(
                player=item.player,
                projected_points=item.projected_points,
                replacement_points=item.replacement_points,
                vorp=item.vorp,
                tier=tier,
                adp_value=item.adp_value,
            )
        )
        previous = item.vorp
    return output


def calculate_adp_value(
    projections: Sequence[ScoredProjection],
) -> list[ScoredProjection]:
    if not projections:
        raise ValueError("at least one projection is required")
    ordered = sorted(projections, key=lambda item: (-item.vorp, item.player.player_id))
    output = []
    for rank, item in enumerate(ordered, 1):
        if item.player.adp is None:
            raise ValueError(f"ADP is required for player {item.player.player_id}")
        output.append(
            ScoredProjection(
                player=item.player,
                projected_points=item.projected_points,
                replacement_points=item.replacement_points,
                vorp=item.vorp,
                tier=item.tier,
                adp_value=item.player.adp - rank,
            )
        )
    return output


def bye_week_exposure(
    roster: Iterable[PlayerProjection],
    *,
    weights: Mapping[str, float] | None = None,
) -> dict[int, float]:
    players = _players_by_id(roster)
    exposure: dict[int, float] = defaultdict(float)
    for player in players.values():
        weight = 1.0 if weights is None else float(weights.get(player.player_id, 0.0))
        if weights is not None and player.player_id not in weights:
            raise ValueError(f"missing bye exposure weight for {player.player_id}")
        if not isfinite(weight) or weight < 0:
            raise ValueError("bye exposure weights must be finite and non-negative")
        exposure[player.bye_week] += weight
    return dict(sorted(exposure.items()))


def live_draft_recommendations(
    available: Sequence[ScoredProjection],
    roster: Iterable[PlayerProjection],
    roster_targets: Mapping[str, int],
    *,
    current_pick: int,
    bye_penalty: float = 1.0,
) -> list[Recommendation]:
    if current_pick < 1:
        raise ValueError("current_pick must be positive")
    if bye_penalty < 0 or not isfinite(bye_penalty):
        raise ValueError("bye_penalty must be finite and non-negative")
    roster_list = list(roster)
    roster_ids = {player.player_id for player in roster_list}
    if len(roster_ids) != len(roster_list):
        raise ValueError("roster contains duplicate player_id")
    counts = Counter(player.position for player in roster_list)
    byes = Counter(player.bye_week for player in roster_list)
    output = []
    for item in available:
        player = item.player
        if player.player_id in roster_ids:
            raise ValueError(f"available player {player.player_id} is already rostered")
        if player.position not in roster_targets:
            raise ValueError(f"missing roster target for position {player.position}")
        need = max(roster_targets[player.position] - counts[player.position], 0)
        need_bonus = need * 2.0
        bye_cost = byes[player.bye_week] * bye_penalty
        urgency = 0.0
        if player.adp is not None:
            urgency = max(current_pick - player.adp, 0.0) * 0.1
        score = item.vorp + need_bonus + urgency - bye_cost
        reasons = (
            f"VORP {item.vorp:.2f}",
            f"position need {need}",
            f"bye overlap {byes[player.bye_week]}",
        )
        output.append(Recommendation(player.player_id, score, reasons))
    return sorted(output, key=lambda item: (-item.score, item.player_id))


def optimize_lineup(
    players: Iterable[PlayerProjection],
    slots: Sequence[RosterSlot],
    scoring: ScoringSettings,
    *,
    objective: str = "mean",
    correlations: Mapping[tuple[str, str], float] | None = None,
    correlation_weight: float = 1.0,
) -> LineupResult:
    """Find the globally optimal legal lineup via deterministic backtracking."""
    player_map = _players_by_id(players)
    if not slots:
        raise ValueError("at least one roster slot is required")
    if len({slot.name for slot in slots}) != len(slots):
        raise ValueError("roster slot names must be unique")
    if objective not in {"floor", "mean", "ceiling"}:
        raise ValueError("objective must be 'floor', 'mean', or 'ceiling'")
    if not isfinite(correlation_weight):
        raise ValueError("correlation_weight must be finite")
    points = {key: score_projection(value, scoring) for key, value in player_map.items()}
    correlation_map: dict[frozenset[str], float] = {}
    for pair, value in (correlations or {}).items():
        if len(pair) != 2 or pair[0] == pair[1]:
            raise ValueError("correlation keys must contain two distinct player IDs")
        if pair[0] not in player_map or pair[1] not in player_map:
            raise ValueError(f"unknown player in correlation pair: {pair}")
        coefficient = float(value)
        if not isfinite(coefficient) or not -1 <= coefficient <= 1:
            raise ValueError("correlations must be finite and between -1 and 1")
        correlation_map[frozenset(pair)] = coefficient

    candidates = {
        slot.name: sorted(
            (
                player
                for player in player_map.values()
                if player.position in slot.eligible_positions
            ),
            key=lambda player: player.player_id,
        )
        for slot in slots
    }
    impossible = [name for name, values in candidates.items() if not values]
    if impossible:
        raise ValueError(f"no eligible players for slots: {', '.join(impossible)}")

    best: tuple[float, tuple[str, ...], dict[str, PlayerProjection], float] | None = None

    def visit(index: int, selected: dict[str, PlayerProjection], used: set[str]) -> None:
        nonlocal best
        if index < len(slots):
            slot = slots[index]
            for player in candidates[slot.name]:
                if player.player_id not in used:
                    selected[slot.name] = player
                    used.add(player.player_id)
                    visit(index + 1, selected, used)
                    used.remove(player.player_id)
                    del selected[slot.name]
            return
        chosen = list(selected.values())
        base = sum(
            player.floor
            if objective == "floor"
            else player.ceiling
            if objective == "ceiling"
            else points[player.player_id]
            for player in chosen
        )
        adjustment = sum(
            correlation_map.get(frozenset((left.player_id, right.player_id)), 0.0)
            * correlation_weight
            for left, right in combinations(chosen, 2)
        )
        total = base + adjustment
        identity = tuple(player.player_id for player in chosen)
        candidate = (total, identity, dict(selected), adjustment)
        if best is None or total > best[0] or (total == best[0] and identity < best[1]):
            best = candidate

    visit(0, {}, set())
    if best is None:
        raise ValueError("no legal lineup can fill all roster slots")
    chosen = list(best[2].values())
    return LineupResult(
        assignments=best[2],
        objective_score=best[0],
        projected_points=sum(points[player.player_id] for player in chosen),
        floor=sum(player.floor for player in chosen),
        ceiling=sum(player.ceiling for player in chosen),
        correlation_adjustment=best[3],
    )


def waiver_rankings(
    free_agents: Sequence[ScoredProjection],
    roster: Iterable[PlayerProjection],
    scoring: ScoringSettings,
) -> list[Recommendation]:
    roster_by_position: dict[str, list[tuple[float, PlayerProjection]]] = defaultdict(list)
    for player in _players_by_id(roster).values():
        roster_by_position[player.position].append((score_projection(player, scoring), player))
    output = []
    for free_agent in free_agents:
        candidates = roster_by_position.get(free_agent.player.position)
        if not candidates:
            raise ValueError(f"no rostered drop candidate at position {free_agent.player.position}")
        drop_points, drop = min(candidates, key=lambda pair: (pair[0], pair[1].player_id))
        gain = free_agent.projected_points - drop_points
        output.append(
            Recommendation(
                free_agent.player.player_id,
                gain,
                (f"drop {drop.player_id}", f"projected gain {gain:.2f}"),
            )
        )
    return sorted(output, key=lambda item: (-item.score, item.player_id))


def simulate_rest_of_season(
    weekly_means: Mapping[str, Sequence[float]],
    weekly_stdevs: Mapping[str, Sequence[float]],
    *,
    iterations: int,
    seed: int,
) -> dict[str, SimulationResult]:
    """Run reproducible independent normal Monte Carlo outcomes."""
    if iterations < 1:
        raise ValueError("iterations must be positive")
    if set(weekly_means) != set(weekly_stdevs) or not weekly_means:
        raise ValueError("means and standard deviations require identical non-empty player IDs")
    random = Random(seed)
    results: dict[str, SimulationResult] = {}
    for player_id in sorted(weekly_means):
        means = tuple(float(value) for value in weekly_means[player_id])
        stdevs = tuple(float(value) for value in weekly_stdevs[player_id])
        if not means or len(means) != len(stdevs):
            raise ValueError(f"weekly inputs for {player_id} must have equal non-zero length")
        if any(not isfinite(value) for value in means + stdevs):
            raise ValueError(f"weekly inputs for {player_id} must be finite")
        if any(value < 0 for value in stdevs):
            raise ValueError(f"standard deviations for {player_id} cannot be negative")
        samples = tuple(
            sum(random.gauss(mean, stdev) for mean, stdev in zip(means, stdevs, strict=True))
            for _ in range(iterations)
        )
        ordered = sorted(samples)
        low_index = int((iterations - 1) * 0.1)
        high_index = int((iterations - 1) * 0.9)
        results[player_id] = SimulationResult(
            player_id=player_id,
            mean=fmean(samples),
            median=median(samples),
            floor=ordered[low_index],
            ceiling=ordered[high_index],
            samples=samples,
        )
    return results


def evaluate_trade(
    give: Iterable[ScoredProjection],
    receive: Iterable[ScoredProjection],
    *,
    risk_weight: float = 0.0,
) -> Recommendation:
    if risk_weight < 0 or not isfinite(risk_weight):
        raise ValueError("risk_weight must be finite and non-negative")
    outgoing = list(give)
    incoming = list(receive)
    if not outgoing or not incoming:
        raise ValueError("both sides of a trade must contain at least one player")
    all_ids = [item.player.player_id for item in outgoing + incoming]
    if len(all_ids) != len(set(all_ids)):
        raise ValueError("a player cannot appear more than once in a trade")

    def utility(items: Sequence[ScoredProjection]) -> float:
        return sum(
            item.vorp - risk_weight * max(item.player.ceiling - item.player.floor, 0.0)
            for item in items
        )

    delta = utility(incoming) - utility(outgoing)
    return Recommendation(
        player_id="trade",
        score=delta,
        reasons=(
            f"receive utility {utility(incoming):.2f}",
            f"give utility {utility(outgoing):.2f}",
        ),
    )


def chaos_response(
    recommendations: Sequence[Recommendation],
    unavailable_player_ids: Iterable[str],
) -> list[Recommendation]:
    """Remove invalidated options without changing surviving scores or order."""
    unavailable = set(unavailable_player_ids)
    if any(not player_id for player_id in unavailable):
        raise ValueError("unavailable player IDs must be non-empty")
    if len({item.player_id for item in recommendations}) != len(recommendations):
        raise ValueError("recommendations contain duplicate player IDs")
    remaining = [item for item in recommendations if item.player_id not in unavailable]
    if not remaining:
        raise ValueError("chaos event invalidated every recommendation")
    return remaining
