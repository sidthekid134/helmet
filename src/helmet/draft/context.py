"""Static draft-format parameters shared by every tree-building component."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from helmet.analytics import ScoringSettings


@dataclass(frozen=True, slots=True)
class DraftContext:
    """The shape of one draft: format plus your own targets and league scoring.

    ``roster_targets`` drives the need bonus when ranking candidates (how many
    of each position you want on your roster). ``starters_per_team`` is the
    league-wide starter count per position used for VORP replacement level —
    the same shape `helmet.analytics.replacement_levels` already expects.
    """

    num_teams: int
    my_slot: int
    rounds: int
    roster_targets: Mapping[str, int]
    starters_per_team: Mapping[str, int]
    scoring: ScoringSettings

    def __post_init__(self) -> None:
        if self.num_teams < 2:
            raise ValueError("num_teams must be at least two")
        if not 1 <= self.my_slot <= self.num_teams:
            raise ValueError("my_slot must be between 1 and num_teams")
        if self.rounds < 1:
            raise ValueError("rounds must be positive")
        if not self.roster_targets:
            raise ValueError("roster_targets cannot be empty")
        if not self.starters_per_team:
            raise ValueError("starters_per_team cannot be empty")

    def my_picks(self) -> tuple[int, ...]:
        """Overall (1-indexed) pick numbers belonging to this slot in a snake draft."""
        picks = []
        for round_no in range(1, self.rounds + 1):
            position_in_round = (
                self.my_slot if round_no % 2 == 1 else self.num_teams - self.my_slot + 1
            )
            picks.append((round_no - 1) * self.num_teams + position_in_round)
        return tuple(picks)

    def round_of(self, overall_pick: int) -> int:
        if overall_pick < 1:
            raise ValueError("overall_pick must be positive")
        return (overall_pick - 1) // self.num_teams + 1

    def team_slot_for_pick(self, overall_pick: int) -> int:
        """Return which 1-indexed roster slot owns a given overall pick."""
        round_no = self.round_of(overall_pick)
        position_in_round = overall_pick - (round_no - 1) * self.num_teams
        if round_no % 2 == 1:
            return position_in_round
        return self.num_teams - position_in_round + 1
