"""Pluggable models of what other managers do between your picks.

`AdpOpponentModel` is intentionally the crudest possible model: a Gaussian
kernel over ADP distance, with no notion of a specific opponent's tendencies.
Once `manager_profiles` (see `helmet.repositories.tables.ManagerProfileRepository`)
carries real signal, implement `OpponentModel` against per-manager tendencies
and pass it into `helmet.draft.tree.build_draft_tree` — the tree engine only
depends on the protocol below, not on this implementation.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import exp
from random import Random
from typing import Protocol

from helmet.analytics import PlayerProjection

DEFAULT_ADP_STDEV = 6.0
DEFAULT_CONSIDERATION_WINDOW = 60


class OpponentModel(Protocol):
    """Samples what one rival drafts at a given overall pick."""

    def sample_pick(
        self,
        *,
        rng: Random,
        available: Sequence[PlayerProjection],
        overall_pick: int,
        slot: int | None = None,
    ) -> str:
        """Return the player_id a simulated opponent selects."""
        ...


@dataclass(frozen=True, slots=True)
class AdpOpponentModel:
    """Samples opponent picks from a Gaussian kernel centered on ADP.

    A player exactly at their ADP for the current overall pick is most likely
    to go; likelihood falls off with distance, scaled by that player's own ADP
    standard deviation (or `default_stdev` when a player lacks one).
    """

    default_stdev: float = DEFAULT_ADP_STDEV
    consideration_window: int = DEFAULT_CONSIDERATION_WINDOW
    positional_bias: Mapping[int, Mapping[str, float]] | None = None
    """Optional per-team-slot positional multipliers (e.g. a team that reaches
    for QBs). Keyed by 1-indexed roster slot. Absent means neutral (1.0) for
    everyone — the extension seam for a future `manager_profiles`-driven model."""

    def __post_init__(self) -> None:
        if self.default_stdev <= 0:
            raise ValueError("default_stdev must be positive")
        if self.consideration_window < 1:
            raise ValueError("consideration_window must be positive")

    def sample_pick(
        self,
        *,
        rng: Random,
        available: Sequence[PlayerProjection],
        overall_pick: int,
        slot: int | None = None,
    ) -> str:
        if not available:
            raise ValueError("cannot sample a pick from an empty pool")
        bias = (self.positional_bias or {}).get(slot, {}) if slot is not None else {}
        scored: list[tuple[float, str]] = []
        for player in available:
            if player.adp is None:
                raise ValueError(
                    f"{player.player_id} has no ADP; opponent modeling requires it"
                )
            stdev = (
                player.adp_stdev
                if player.adp_stdev is not None and player.adp_stdev > 0
                else self.default_stdev
            )
            z = (overall_pick - player.adp) / stdev
            weight = exp(-0.5 * z * z) * bias.get(player.position, 1.0)
            scored.append((weight, player.player_id))
        scored.sort(key=lambda item: -item[0])
        window = scored[: self.consideration_window]
        total = sum(weight for weight, _ in window)
        if total <= 0:
            raise ValueError("all candidate weights are non-positive; check ADP inputs")
        threshold = rng.random() * total
        cumulative = 0.0
        for weight, player_id in window:
            cumulative += weight
            if cumulative >= threshold:
                return player_id
        return window[-1][1]


def simulate_gap_survival(
    *,
    opponent_model: OpponentModel,
    available: Sequence[PlayerProjection],
    start_pick: int,
    gap_size: int,
    slots: Sequence[int] | None,
    iterations: int,
    rng: Random,
) -> dict[str, float]:
    """Return, per available player, the fraction of runs they survive the gap.

    A gap is the run of opponent picks between two of your own picks (or before
    your first pick). Each of ``iterations`` runs samples ``gap_size``
    sequential opponent picks and removes whoever was taken; the result is the
    empirical probability each player is still there afterward.
    """
    if gap_size < 0:
        raise ValueError("gap_size cannot be negative")
    if slots is not None and len(slots) != gap_size:
        raise ValueError("slots must have exactly one entry per gap pick")
    if iterations < 1:
        raise ValueError("iterations must be positive")
    ids = [player.player_id for player in available]
    if gap_size == 0:
        return dict.fromkeys(ids, 1.0)
    survived = dict.fromkeys(ids, 0)
    for _ in range(iterations):
        pool = list(available)
        for offset in range(gap_size):
            if not pool:
                break
            slot = slots[offset] if slots is not None else None
            picked_id = opponent_model.sample_pick(
                rng=rng, available=pool, overall_pick=start_pick + offset, slot=slot
            )
            pool = [player for player in pool if player.player_id != picked_id]
        for player in pool:
            survived[player.player_id] += 1
    return {player_id: count / iterations for player_id, count in survived.items()}
