"""Application of research-promoted modifiers onto a projection.

The warm-start research layer promotes named modifiers (see
``helmet.research.warm_start``). This module is the single place where a promoted
name turns into an actual change to a projection. Registering an applier here is
the only step needed to make a new research finding affect the draft.

Unknown modifier names raise. A promoted modifier that silently does nothing
would make the research layer look connected when it is not.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace

from helmet.analytics import PlayerProjection


@dataclass(frozen=True, slots=True)
class ModifierContext:
    """Evidence an applier may use beyond the projection itself."""

    player: PlayerProjection
    prior_season_totals: Mapping[str, float]
    prior_season_games: int


ModifierApplier = Callable[[ModifierContext, float], PlayerProjection]


def _scale_stats(player: PlayerProjection, factor: float) -> PlayerProjection:
    return replace(
        player,
        stats={key: value * factor for key, value in player.stats.items()},
        floor=player.floor * factor,
        ceiling=player.ceiling * factor,
    )


def _rb_high_touch_hangover(context: ModifierContext, effect: float) -> PlayerProjection:
    """Scale a back's projection when last season exceeded the touch threshold.

    ``effect`` is the measured change in next-season points per touch. It is
    applied as a proportional adjustment to the current points-per-touch rate.
    """
    player = context.player
    if player.position != "RB":
        return player
    touches = context.prior_season_totals.get("carries", 0.0) + context.prior_season_totals.get(
        "receptions", 0.0
    )
    if touches < 350:
        return player
    projected_touches = player.stats.get("carries", 0.0) + player.stats.get("receptions", 0.0)
    if projected_touches <= 0:
        raise ValueError(f"{player.player_id} has prior touches but no projected touches")
    baseline_points = sum(
        value for key, value in player.stats.items() if key in {"rushing_yards", "receiving_yards"}
    )
    if baseline_points <= 0:
        return player
    points_per_touch = baseline_points / projected_touches
    factor = 1.0 + effect / points_per_touch
    if factor <= 0:
        raise ValueError(f"modifier effect {effect} would zero out {player.player_id}")
    return _scale_stats(player, factor)


MODIFIER_APPLIERS: Mapping[str, ModifierApplier] = {
    "rb_350_touch_next_year_points_per_touch": _rb_high_touch_hangover,
}


def apply_modifiers(
    context: ModifierContext, modifiers: Mapping[str, float]
) -> PlayerProjection:
    """Apply every promoted modifier in a stable order."""
    unknown = sorted(modifiers.keys() - MODIFIER_APPLIERS.keys())
    if unknown:
        raise ValueError(
            f"no applier is registered for promoted modifiers: {', '.join(unknown)}; "
            "register one in helmet.projections.modifiers"
        )
    player = context.player
    for name in sorted(modifiers):
        player = MODIFIER_APPLIERS[name](replace(context, player=player), float(modifiers[name]))
    return player
