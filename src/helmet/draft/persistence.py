"""Persist a `DraftTree` into the `draft_plans` / `draft_plan_nodes` /
`draft_plan_candidates` tables.

Plans are immutable once written: regenerating a plan with the same
`config` returns the existing row instead of duplicating it (idempotent by
content hash, the same pattern used by `helmet.research.publish`); a
genuinely different config always produces a new plan rather than mutating
an old one in place, so a stale precomputed plan can never be silently
edited out from under a live draft.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from helmet.persistence import PersistenceContext
from helmet.repositories import (
    DraftPlanCandidateRepository,
    DraftPlanNodeRepository,
    DraftPlanRepository,
    canonical_content_hash,
)

from .tree import DraftTree


def find_existing_plan(db: PersistenceContext, content_hash: str) -> dict[str, Any] | None:
    """Look up an already-persisted plan by content hash, if one exists.

    Split out from `persist_draft_tree` so `generate_draft_plan` can check
    for a precomputed plan *before* paying for a projection pool build and a
    tree search -- the whole point of precomputing during
    `helmet research warm-start` is that this lookup, not a full rebuild, is
    what a cache hit costs.
    """
    plans = DraftPlanRepository(db.client, db.owner_user_id)
    existing = plans.list(filters={"content_hash": content_hash}, limit=1)
    return existing[0] if existing else None


def persist_draft_tree(
    tree: DraftTree,
    *,
    db: PersistenceContext,
    config: Mapping[str, Any],
    league_id: str | None = None,
    draft_id: str | None = None,
    research_policy_version_id: str | None = None,
) -> dict[str, Any]:
    plans = DraftPlanRepository(db.client, db.owner_user_id)
    nodes_repo = DraftPlanNodeRepository(db.client, db.owner_user_id)
    candidates_repo = DraftPlanCandidateRepository(db.client, db.owner_user_id)

    content_hash = canonical_content_hash(dict(config))
    existing = find_existing_plan(db, content_hash)
    if existing:
        return {"plan": existing, "created": False, "nodes": None, "candidates": None}

    now = datetime.now(UTC).isoformat()
    plan = plans.create(
        {
            "league_id": league_id,
            "draft_id": draft_id,
            "research_policy_version_id": research_policy_version_id,
            "num_teams": tree.context.num_teams,
            "my_slot": tree.context.my_slot,
            "rounds": tree.context.rounds,
            "seed": tree.seed,
            "simulation_iterations": int(config["simulation_iterations"]),
            "node_count": len(tree.nodes),
            "status": "active",
            "config": dict(config),
            "observed_at": now,
            "effective_at": now,
            "content_hash": content_hash,
        }
    )

    # `tree.nodes` is produced breadth-first, depth by depth, so every node's
    # parent has already been inserted (and is in `node_id_map`) by the time
    # the node itself is reached.
    node_id_map: dict[str, str] = {}
    for node in tree.nodes:
        parent_db_id = node_id_map.get(node.parent_id) if node.parent_id is not None else None
        row = nodes_repo.create(
            {
                "plan_id": plan["id"],
                "parent_node_id": parent_db_id,
                "node_key": node.node_id,
                "depth": node.depth,
                "overall_pick": node.overall_pick,
                "round": node.round,
                "chosen_player_id": node.chosen_player_id,
                "chosen_player_name": node.chosen_player_name,
                "chosen_player_team": node.chosen_player_team,
                "chosen_player_position": node.chosen_player_position,
                "chosen_archetype": node.chosen_archetype,
                "board_state_hash": node.board_state_hash,
                "reach_probability": node.reach_probability,
                "roster_player_ids": list(node.roster_player_ids),
                "ev": node.ev,
                "ev_floor": node.ev_floor,
                "ev_ceiling": node.ev_ceiling,
                "rationale": list(node.rationale),
            }
        )
        node_id_map[node.node_id] = row["id"]

    for candidate in tree.candidates:
        candidates_repo.create(
            {
                "plan_id": plan["id"],
                "parent_node_id": node_id_map[candidate.parent_node_id],
                "player_id": candidate.player_id,
                "player_name": candidate.player_name,
                "player_team": candidate.player_team,
                "player_position": candidate.player_position,
                "archetype": candidate.archetype,
                "survival_probability": candidate.survival_probability,
                "marginal_value": candidate.marginal_value,
                "rank": candidate.rank,
                "expanded": candidate.expanded,
                "child_node_id": (
                    node_id_map[candidate.child_node_id]
                    if candidate.child_node_id is not None
                    else None
                ),
            }
        )

    return {
        "plan": plan,
        "created": True,
        "nodes": len(tree.nodes),
        "candidates": len(tree.candidates),
    }
