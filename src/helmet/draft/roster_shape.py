"""Derive draft-planning roster shape from a Sleeper league's `roster_positions`.

Sleeper's `roster_positions` is a flat list of slot names, e.g.::

    ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "BN", ..., "K", "DEF"]

`helmet.draft.tree` needs one starter count per *exact* position (it only
models QB/RB/WR/TE -- see `ProjectionSettings.positions`), but flex slots
don't name a single position; they're eligible for several. This module
resolves that ambiguity with a documented, deterministic rule instead of
silently guessing: each flex slot is assigned round-robin to the eligible
positions, in the priority order given below. K/DEF/IDP/bench/IR/taxi slots
are dropped entirely -- the planner can never "draft" a kicker, so counting
that slot would misrepresent positional need.

This lets both the on-demand API/CLI path and the warm-start precompute path
derive an identical roster shape from the same league data, which is what
makes precomputed plans a cache hit instead of a rebuild.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

CORE_POSITIONS = ("QB", "RB", "WR", "TE")

# Order matters: the first eligible position in this tuple absorbs a flex
# slot. RB/WR come first because bench depth there swings VORP-based need
# scoring the most; a second slot of the same flex type cycles to the next
# eligible position instead of stacking onto the first.
_FLEX_ELIGIBILITY: Mapping[str, tuple[str, ...]] = {
    "FLEX": ("RB", "WR", "TE"),
    "WRRB_FLEX": ("RB", "WR"),
    "RB_WR_FLEX": ("RB", "WR"),
    "REC_FLEX": ("WR", "TE"),
    "WRTE_FLEX": ("WR", "TE"),
    "SUPER_FLEX": ("QB", "RB", "WR", "TE"),
}

# Target *total* copies of a position to roster, expressed as a multiple of
# its starter count. This is a strategy default (real managers reasonably
# disagree on bench construction), used only to seed `roster_targets` when a
# caller hasn't specified its own. Matches the ratios the draft-plan UI
# previously hardcoded for a standard 1QB/2RB/2WR/1TE/1FLEX league.
_BENCH_MULTIPLIER: Mapping[str, float] = {
    "QB": 2.0,
    "RB": 2.5,
    "WR": 3.0,
    "TE": 2.0,
}


@dataclass(frozen=True, slots=True)
class DraftShape:
    rounds: int
    starters_per_team: dict[str, int]
    roster_targets: dict[str, int]


def derive_starters_per_team(roster_positions: Sequence[str]) -> dict[str, int]:
    """Count exact-position starters, apportioning flex slots round-robin."""
    starters: Counter[str] = Counter()
    flex_cursors: Counter[str] = Counter()
    for raw_slot in roster_positions:
        slot = raw_slot.upper()
        if slot in CORE_POSITIONS:
            starters[slot] += 1
            continue
        eligible = _FLEX_ELIGIBILITY.get(slot)
        if not eligible:
            continue  # bench, IR, taxi, K, DEF, IDP, or an unrecognized slot
        target = eligible[flex_cursors[slot] % len(eligible)]
        starters[target] += 1
        flex_cursors[slot] += 1
    if not starters:
        raise ValueError(
            "roster_positions contained no QB/RB/WR/TE starters or flex slots eligible for them"
        )
    return dict(starters)


def derive_roster_targets(starters_per_team: Mapping[str, int]) -> dict[str, int]:
    """A reasonable default bench-depth target, seeded from starter counts."""
    return {
        position: max(count, round(count * _BENCH_MULTIPLIER.get(position, 2.0)))
        for position, count in starters_per_team.items()
    }


def derive_draft_shape(roster_positions: Sequence[str]) -> DraftShape:
    """Full derived shape: starters, bench targets, and total draft rounds.

    `rounds` is the total slot count in `roster_positions`, matching how a
    real snake draft fills every roster spot -- including bench -- exactly
    once.
    """
    starters = derive_starters_per_team(roster_positions)
    return DraftShape(
        rounds=len(roster_positions),
        starters_per_team=starters,
        roster_targets=derive_roster_targets(starters),
    )
