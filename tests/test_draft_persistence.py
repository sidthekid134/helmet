from __future__ import annotations

from pathlib import Path

import pytest

from helmet.analytics import PlayerProjection, ScoringRule, ScoringSettings
from helmet.draft import BranchPolicy, DraftContext, build_draft_tree, persist_draft_tree
from helmet.persistence import PersistenceContext
from helmet.repositories import (
    DraftPlanCandidateRepository,
    DraftPlanNodeRepository,
    DraftPlanRepository,
    LocalClient,
)

OWNER = "11111111-1111-4111-8111-111111111111"
SCORING = ScoringSettings((ScoringRule("points", 1.0),))


def make_player(player_id: str, position: str, points: float, adp: float) -> PlayerProjection:
    return PlayerProjection(
        player_id=player_id,
        name=player_id,
        position=position,
        team="AAA",
        bye_week=5,
        stats={"points": points},
        floor=points * 0.7,
        ceiling=points * 1.3,
        adp=adp,
        adp_stdev=3.0,
    )


def _tree(seed: int = 1):
    positions = ["QB", "RB", "WR", "TE"]
    pool = [make_player(f"p{i}", positions[i % 4], 100 - i, float(i + 1)) for i in range(24)]
    context = DraftContext(
        num_teams=4,
        my_slot=2,
        rounds=2,
        roster_targets={"QB": 1, "RB": 1, "WR": 1, "TE": 1},
        starters_per_team={"QB": 1, "RB": 1, "WR": 1, "TE": 1},
        scoring=SCORING,
    )
    policy = BranchPolicy(
        individual_rounds=2, top_k_by_round={1: 2, 2: 2}, default_top_k=2, beam_width=4
    )
    return build_draft_tree(
        context=context, pool=pool, branch_policy=policy, simulation_iterations=10, seed=seed
    )


@pytest.fixture
def db(tmp_path: Path) -> PersistenceContext:
    return PersistenceContext(
        client=LocalClient(tmp_path / "helmet.db"), owner_user_id=OWNER, backend="local"
    )


def test_persist_draft_tree_writes_all_nodes_and_candidates(db: PersistenceContext) -> None:
    tree = _tree()

    result = persist_draft_tree(tree, db=db, config={"seed": 1, "simulation_iterations": 10})

    assert result["created"] is True
    assert result["nodes"] == len(tree.nodes)
    assert result["candidates"] == len(tree.candidates)
    assert result["plan"]["status"] == "active"
    stored_nodes = DraftPlanNodeRepository(db.client, db.owner_user_id).list(limit=1000)
    stored_candidates = DraftPlanCandidateRepository(db.client, db.owner_user_id).list(limit=1000)
    assert len(stored_nodes) == len(tree.nodes)
    assert len(stored_candidates) == len(tree.candidates)


def test_persist_draft_tree_links_parent_child_node_ids(db: PersistenceContext) -> None:
    tree = _tree()
    persist_draft_tree(tree, db=db, config={"seed": 1, "simulation_iterations": 10})

    stored_nodes = DraftPlanNodeRepository(db.client, db.owner_user_id).list(limit=1000)
    by_key = {row["node_key"]: row for row in stored_nodes}
    for node in tree.nodes:
        if node.parent_id is None:
            continue
        row = by_key[node.node_id]
        assert row["parent_node_id"] == by_key[node.parent_id]["id"]


def test_persist_draft_tree_is_idempotent_by_config_content_hash(db: PersistenceContext) -> None:
    tree = _tree()
    config = {"seed": 1, "simulation_iterations": 10}

    first = persist_draft_tree(tree, db=db, config=config)
    second = persist_draft_tree(tree, db=db, config=config)

    assert first["created"] is True
    assert second["created"] is False
    assert second["plan"]["id"] == first["plan"]["id"]
    assert len(DraftPlanRepository(db.client, db.owner_user_id).list()) == 1


def test_persist_draft_tree_creates_a_new_plan_for_a_different_config(
    db: PersistenceContext,
) -> None:
    tree = _tree()

    first = persist_draft_tree(tree, db=db, config={"seed": 1, "simulation_iterations": 10})
    second = persist_draft_tree(tree, db=db, config={"seed": 2, "simulation_iterations": 10})

    assert first["plan"]["id"] != second["plan"]["id"]
    assert len(DraftPlanRepository(db.client, db.owner_user_id).list()) == 2
