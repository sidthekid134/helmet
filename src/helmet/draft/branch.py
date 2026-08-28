"""Rules for turning an available player pool into a small set of branches.

Iteration 1 branches on individual players for the first ``individual_rounds``
of the draft, then switches to named archetype buckets so the tree stays a
manageable size deep into a 15+ round draft. Archetype predicates and top-K
counts are data on `BranchPolicy`, not code, so tuning them — or adding a new
archetype — never requires touching `helmet.draft.tree`.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

from helmet.analytics import PlayerProjection, ScoredProjection

ArchetypePredicate = Callable[[PlayerProjection], bool]

DEFAULT_ARCHETYPES: Mapping[str, ArchetypePredicate] = {
    "elite_rb": lambda p: p.position == "RB",
    "elite_wr": lambda p: p.position == "WR",
    "te_premium": lambda p: p.position == "TE",
    "qb": lambda p: p.position == "QB",
    "best_available": lambda p: True,
}


@dataclass(frozen=True, slots=True)
class BranchCandidate:
    player: ScoredProjection
    archetype: str | None
    marginal_value: float
    survival_probability: float
    expand: bool


@dataclass(frozen=True, slots=True)
class BranchPolicy:
    """How wide the tree gets at each round, and where it stops branching on players."""

    individual_rounds: int
    top_k_by_round: Mapping[int, int]
    default_top_k: int
    archetypes: Mapping[str, ArchetypePredicate] = field(
        default_factory=lambda: dict(DEFAULT_ARCHETYPES)
    )
    beam_width: int = 16
    menu_size: int = 10
    need_weight: float = 2.0

    def __post_init__(self) -> None:
        if self.individual_rounds < 0:
            raise ValueError("individual_rounds cannot be negative")
        if self.default_top_k < 1:
            raise ValueError("default_top_k must be positive")
        if any(value < 1 for value in self.top_k_by_round.values()):
            raise ValueError("top_k_by_round values must be positive")
        if not self.archetypes:
            raise ValueError("at least one archetype is required")
        if self.beam_width < 1:
            raise ValueError("beam_width must be positive")
        if self.menu_size < 1:
            raise ValueError("menu_size must be positive")
        if self.need_weight < 0:
            raise ValueError("need_weight cannot be negative")

    def top_k(self, round_no: int) -> int:
        return self.top_k_by_round.get(round_no, self.default_top_k)

    def is_individual_round(self, round_no: int) -> bool:
        return round_no <= self.individual_rounds


def rank_available(
    available: Sequence[ScoredProjection],
    *,
    roster: Sequence[PlayerProjection],
    roster_targets: Mapping[str, int],
    need_weight: float,
) -> list[ScoredProjection]:
    """Rank by VORP plus a positional-need bonus, mirroring `live_draft_recommendations`."""
    counts = Counter(player.position for player in roster)

    def score(item: ScoredProjection) -> float:
        need = max(roster_targets.get(item.player.position, 0) - counts[item.player.position], 0)
        return item.vorp + need * need_weight

    return sorted(available, key=lambda item: (-score(item), item.player.player_id))


def select_branch_candidates(
    available: Sequence[ScoredProjection],
    *,
    round_no: int,
    roster: Sequence[PlayerProjection],
    roster_targets: Mapping[str, int],
    survival: Mapping[str, float],
    policy: BranchPolicy,
) -> list[BranchCandidate]:
    """Return the ranked menu for one decision, flagging which entries expand.

    Entries with ``expand=True`` become tree nodes and continue the plan.
    The rest are informational context — real ranked alternatives the model
    considered but did not branch on, so the menu never hides that a decision
    had more options than the tree explores.
    """
    if not available:
        return []
    ranked = rank_available(
        available, roster=roster, roster_targets=roster_targets, need_weight=policy.need_weight
    )
    by_id = {item.player.player_id: item for item in ranked}

    archetype_for: dict[str, str | None] = {}
    if policy.is_individual_round(round_no):
        for item in ranked[: policy.top_k(round_no)]:
            archetype_for[item.player.player_id] = None
    else:
        chosen: set[str] = set()
        for name, predicate in policy.archetypes.items():
            match = next(
                (
                    item
                    for item in ranked
                    if item.player.player_id not in chosen and predicate(item.player)
                ),
                None,
            )
            if match is None:
                continue
            chosen.add(match.player.player_id)
            archetype_for[match.player.player_id] = name

    expand_ids = set(archetype_for)
    ordered_ids: list[str] = list(archetype_for)
    for item in ranked:
        if len(ordered_ids) >= policy.menu_size:
            break
        if item.player.player_id not in expand_ids and item.player.player_id not in ordered_ids:
            ordered_ids.append(item.player.player_id)

    return [
        BranchCandidate(
            player=by_id[player_id],
            archetype=archetype_for.get(player_id),
            marginal_value=by_id[player_id].vorp,
            survival_probability=survival.get(player_id, 1.0),
            expand=player_id in expand_ids,
        )
        for player_id in ordered_ids
    ]
