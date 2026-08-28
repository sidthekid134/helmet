"""Expectimax draft planning: your choices are decisions, opponents are chance.

`build_draft_tree` alternates two steps for every one of your picks:

1. Simulate the opponent picks in the gap since your last pick (or the draft
   start) with an `OpponentModel`, producing a survival probability for every
   player still on the board. A canonical post-gap pool keeps players whose
   survival probability is at least 0.5 — majority rule, not a fabricated
   single "most likely" board.
2. Branch on that pool per `BranchPolicy`: individual players early, archetype
   buckets once the draft is deep, always pruned to a beam width so the tree
   stays small enough to store and render.

The result is two flat, denormalized sequences — `DraftTreeNode` and
`DraftTreeCandidate` — built to map directly onto the `draft_plans` /
`draft_plan_nodes` / `draft_plan_candidates` persistence tables. Nothing here
talks to a database; `helmet.repositories` owns that boundary.

Branches pruned by the beam width are not deleted: their node is still created
(so the plan shows what was considered), it simply is not expanded further.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from random import Random

from helmet.analytics import PlayerProjection, replacement_levels, value_over_replacement

from .branch import BranchPolicy, select_branch_candidates
from .context import DraftContext
from .opponent import AdpOpponentModel, OpponentModel, simulate_gap_survival
from .value import ValueModel, VorpValueModel

ROOT_NODE_ID = "root"


def _board_state_hash(taken_player_ids: frozenset[str]) -> str:
    """Fingerprint of a roster as an unordered set, for matching against a live board.

    Two different draft paths can legitimately end up with the same set of
    players (e.g. picking A then B vs. B then A). This hash is deliberately
    order-independent so it can be compared against a real board later, but it
    must never be used as node identity -- see `_node_id`.
    """
    canonical = ",".join(sorted(taken_player_ids))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _node_id(parent_node_id: str, player_id: str) -> str:
    """A unique identity for one path through the tree.

    Unlike `_board_state_hash`, this depends on the specific parent, so two
    lineages that reach the same set of players via a different order never
    collide into a single node.
    """
    return hashlib.sha256(f"{parent_node_id}:{player_id}".encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class DraftTreeCandidate:
    """One ranked option considered at a decision node.

    ``child_node_id`` is set only when ``expanded`` is True; unexpanded rows
    are informational context, not dropped alternatives. Player name/team/
    position are denormalized here (not just ``player_id``) because nothing
    in Helmet yet maps nflverse identities into ``player_identities`` for a
    later join — see `helmet.draft.persistence` for the same reasoning on
    `DraftTreeNode`.
    """

    parent_node_id: str
    player_id: str
    player_name: str
    player_team: str
    player_position: str
    archetype: str | None
    survival_probability: float
    marginal_value: float
    rank: int
    expanded: bool
    child_node_id: str | None


@dataclass(frozen=True, slots=True)
class DraftTreeNode:
    node_id: str
    parent_id: str | None
    depth: int
    overall_pick: int | None
    round: int | None
    chosen_player_id: str | None
    chosen_player_name: str | None
    chosen_player_team: str | None
    chosen_player_position: str | None
    chosen_archetype: str | None
    board_state_hash: str
    reach_probability: float
    roster_player_ids: tuple[str, ...]
    ev: float
    ev_floor: float
    ev_ceiling: float
    rationale: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DraftTree:
    seed: int
    context: DraftContext
    nodes: tuple[DraftTreeNode, ...]
    candidates: tuple[DraftTreeCandidate, ...]

    def by_id(self) -> dict[str, DraftTreeNode]:
        return {node.node_id: node for node in self.nodes}

    def children(self, node_id: str) -> tuple[DraftTreeNode, ...]:
        return tuple(node for node in self.nodes if node.parent_id == node_id)

    def candidates_for(self, node_id: str) -> tuple[DraftTreeCandidate, ...]:
        return tuple(
            candidate for candidate in self.candidates if candidate.parent_node_id == node_id
        )


@dataclass(frozen=True, slots=True)
class _FrontierState:
    node_id: str
    roster: tuple[PlayerProjection, ...]
    taken: frozenset[str]
    reach_probability: float


def build_draft_tree(
    *,
    context: DraftContext,
    pool: Sequence[PlayerProjection],
    branch_policy: BranchPolicy,
    opponent_model: OpponentModel | None = None,
    value_model: ValueModel | None = None,
    simulation_iterations: int = 150,
    seed: int = 20260827,
) -> DraftTree:
    if not pool:
        raise ValueError("pool cannot be empty")
    player_ids = [player.player_id for player in pool]
    if len(set(player_ids)) != len(player_ids):
        raise ValueError("pool contains duplicate player_id")
    if simulation_iterations < 1:
        raise ValueError("simulation_iterations must be positive")

    opponent = opponent_model or AdpOpponentModel()
    replacement_points = replacement_levels(
        pool, context.scoring, context.num_teams, context.starters_per_team
    )
    values = value_model or VorpValueModel(
        scoring=context.scoring, replacement_points=replacement_points
    )

    my_picks = context.my_picks()
    if len(my_picks) < context.rounds:
        raise ValueError("draft context produced fewer picks than rounds")

    root = DraftTreeNode(
        node_id=ROOT_NODE_ID,
        parent_id=None,
        depth=0,
        overall_pick=None,
        round=None,
        chosen_player_id=None,
        chosen_player_name=None,
        chosen_player_team=None,
        chosen_player_position=None,
        chosen_archetype=None,
        board_state_hash=_board_state_hash(frozenset()),
        reach_probability=1.0,
        roster_player_ids=(),
        ev=0.0,
        ev_floor=0.0,
        ev_ceiling=0.0,
        rationale=(),
    )
    nodes: list[DraftTreeNode] = [root]
    candidates: list[DraftTreeCandidate] = []
    frontier = [
        _FrontierState(node_id=root.node_id, roster=(), taken=frozenset(), reach_probability=1.0)
    ]
    rng = Random(seed)
    previous_pick = 0

    for depth, overall_pick in enumerate(my_picks, start=1):
        round_no = context.round_of(overall_pick)
        gap_size = overall_pick - previous_pick - 1
        gap_slots = [
            context.team_slot_for_pick(pick) for pick in range(previous_pick + 1, overall_pick)
        ]
        scored_next_frontier: list[tuple[float, _FrontierState]] = []

        for state in frontier:
            available = [player for player in pool if player.player_id not in state.taken]
            if not available:
                raise ValueError(f"no players remain available for pick {overall_pick}")
            survival = simulate_gap_survival(
                opponent_model=opponent,
                available=available,
                start_pick=previous_pick + 1,
                gap_size=gap_size,
                slots=gap_slots,
                iterations=simulation_iterations,
                rng=rng,
            )
            canonical_ids = {player_id for player_id, prob in survival.items() if prob >= 0.5}
            canonical_pool = [player for player in available if player.player_id in canonical_ids]
            if not canonical_pool:
                # Every remaining player was more likely gone than not; still
                # must offer *something*, so fall back to the full available set.
                canonical_pool = available

            scored_pool = value_over_replacement(
                canonical_pool, context.scoring, replacement_points
            )
            branch_candidates = select_branch_candidates(
                scored_pool,
                round_no=round_no,
                roster=state.roster,
                roster_targets=context.roster_targets,
                survival=survival,
                policy=branch_policy,
            )
            if not any(candidate.expand for candidate in branch_candidates):
                raise ValueError(f"no branch candidates were expandable at pick {overall_pick}")

            for rank, candidate in enumerate(branch_candidates, start=1):
                player = candidate.player.player
                node_id: str | None = None
                if candidate.expand:
                    new_taken = state.taken | {player.player_id}
                    node_id = _node_id(state.node_id, player.player_id)
                    new_roster = (*state.roster, player)
                    reach = state.reach_probability * candidate.survival_probability
                    outcome = values.evaluate(new_roster)
                    rationale = (
                        f"VORP {candidate.marginal_value:.2f}",
                        f"survival {candidate.survival_probability:.2f}",
                        *((f"archetype {candidate.archetype}",) if candidate.archetype else ()),
                    )
                    nodes.append(
                        DraftTreeNode(
                            node_id=node_id,
                            parent_id=state.node_id,
                            depth=depth,
                            overall_pick=overall_pick,
                            round=round_no,
                            chosen_player_id=player.player_id,
                            chosen_player_name=player.name,
                            chosen_player_team=player.team,
                            chosen_player_position=player.position,
                            chosen_archetype=candidate.archetype,
                            board_state_hash=_board_state_hash(new_taken),
                            reach_probability=reach,
                            roster_player_ids=tuple(p.player_id for p in new_roster),
                            ev=outcome.ev,
                            ev_floor=outcome.ev_floor,
                            ev_ceiling=outcome.ev_ceiling,
                            rationale=rationale,
                        )
                    )
                    scored_next_frontier.append(
                        (
                            outcome.ev,
                            _FrontierState(
                                node_id=node_id,
                                roster=new_roster,
                                taken=new_taken,
                                reach_probability=reach,
                            ),
                        )
                    )
                candidates.append(
                    DraftTreeCandidate(
                        parent_node_id=state.node_id,
                        player_id=player.player_id,
                        player_name=player.name,
                        player_team=player.team,
                        player_position=player.position,
                        archetype=candidate.archetype,
                        survival_probability=candidate.survival_probability,
                        marginal_value=candidate.marginal_value,
                        rank=rank,
                        expanded=candidate.expand,
                        child_node_id=node_id,
                    )
                )

        scored_next_frontier.sort(key=lambda item: -item[0])
        frontier = [state for _, state in scored_next_frontier[: branch_policy.beam_width]]
        if not frontier:
            raise ValueError(f"beam pruning eliminated every branch at pick {overall_pick}")
        previous_pick = overall_pick

    return DraftTree(seed=seed, context=context, nodes=tuple(nodes), candidates=tuple(candidates))
