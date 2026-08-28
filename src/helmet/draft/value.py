"""Pluggable evaluation of a partial or complete fantasy roster.

Iteration 1 scores a roster as the sum of VORP its players hold over league
replacement level, using `helmet.analytics.value_over_replacement` — the same
function every other part of Helmet uses. This intentionally ignores weekly
lineup construction (`helmet.analytics.optimize_lineup`) and the portfolio-theory
bye-clustering strategy from the research blueprint. Both are legitimate
`ValueModel` implementations to add later without touching `helmet.draft.tree`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from helmet.analytics import (
    PlayerProjection,
    ScoringSettings,
    bye_week_exposure,
    value_over_replacement,
)


@dataclass(frozen=True, slots=True)
class RosterValue:
    ev: float
    ev_floor: float
    ev_ceiling: float
    bye_penalty: float


class ValueModel(Protocol):
    def evaluate(self, roster: Sequence[PlayerProjection]) -> RosterValue: ...


@dataclass(frozen=True, slots=True)
class VorpValueModel:
    """Sums VORP (and floor/ceiling) across a roster, penalizing bye clustering.

    ``replacement_points`` must be precomputed once (via
    `helmet.analytics.replacement_levels`) over the full draft pool, since
    recomputing it per roster would be both wasteful and inconsistent — the
    replacement level is a property of the whole board, not one roster.
    """

    scoring: ScoringSettings
    replacement_points: Mapping[str, float]
    bye_penalty_per_overlap: float = 1.0

    def __post_init__(self) -> None:
        if self.bye_penalty_per_overlap < 0:
            raise ValueError("bye_penalty_per_overlap cannot be negative")

    def evaluate(self, roster: Sequence[PlayerProjection]) -> RosterValue:
        if not roster:
            raise ValueError("roster cannot be empty")
        missing = sorted({player.position for player in roster} - self.replacement_points.keys())
        if missing:
            raise ValueError(f"missing replacement levels for: {', '.join(missing)}")
        scored = value_over_replacement(roster, self.scoring, self.replacement_points)
        ev = sum(item.vorp for item in scored)
        ev_floor = sum(
            player.floor - self.replacement_points[player.position] for player in roster
        )
        ev_ceiling = sum(
            player.ceiling - self.replacement_points[player.position] for player in roster
        )
        exposure = bye_week_exposure(roster)
        overlap_penalty = (
            sum(max(count - 1, 0) for count in exposure.values()) * self.bye_penalty_per_overlap
        )
        return RosterValue(
            ev=ev - overlap_penalty,
            ev_floor=ev_floor - overlap_penalty,
            ev_ceiling=ev_ceiling - overlap_penalty,
            bye_penalty=overlap_penalty,
        )
