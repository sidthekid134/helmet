from __future__ import annotations

from random import Random

import pytest

from helmet.analytics import PlayerProjection, ScoringRule, ScoringSettings, value_over_replacement
from helmet.draft import (
    AdpOpponentModel,
    BranchPolicy,
    DraftContext,
    VorpValueModel,
    build_draft_tree,
    rank_available,
    select_branch_candidates,
    simulate_gap_survival,
)

SCORING = ScoringSettings((ScoringRule("points", 1.0),))


def make_player(
    player_id: str, position: str, points: float, adp: float, adp_stdev: float = 3.0
) -> PlayerProjection:
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
        adp_stdev=adp_stdev,
    )


# ---------------------------------------------------------------------------
# DraftContext: snake draft geometry
# ---------------------------------------------------------------------------


def test_my_picks_snake_pattern_for_middle_slot() -> None:
    context = DraftContext(
        num_teams=4,
        my_slot=2,
        rounds=4,
        roster_targets={"RB": 1},
        starters_per_team={"RB": 1},
        scoring=SCORING,
    )

    assert context.my_picks() == (2, 7, 10, 15)


def test_team_slot_for_pick_matches_my_picks() -> None:
    context = DraftContext(
        num_teams=4,
        my_slot=3,
        rounds=3,
        roster_targets={"RB": 1},
        starters_per_team={"RB": 1},
        scoring=SCORING,
    )

    for pick in context.my_picks():
        assert context.team_slot_for_pick(pick) == 3


def test_draft_context_rejects_slot_outside_num_teams() -> None:
    with pytest.raises(ValueError, match="my_slot"):
        DraftContext(
            num_teams=4,
            my_slot=5,
            rounds=1,
            roster_targets={"RB": 1},
            starters_per_team={"RB": 1},
            scoring=SCORING,
        )


# ---------------------------------------------------------------------------
# Opponent model
# ---------------------------------------------------------------------------


def test_adp_opponent_model_prefers_players_near_their_adp() -> None:
    model = AdpOpponentModel(default_stdev=2.0)
    near = make_player("near", "RB", 100, adp=10.0)
    far = make_player("far", "WR", 100, adp=80.0)
    rng = Random(7)

    picks = [model.sample_pick(rng=rng, available=[near, far], overall_pick=10) for _ in range(200)]

    assert picks.count("near") > picks.count("far")


def test_adp_opponent_model_requires_adp() -> None:
    model = AdpOpponentModel()
    missing_adp = PlayerProjection(
        player_id="x", name="x", position="RB", team="AAA", bye_week=1, stats={"points": 1.0},
        floor=1.0, ceiling=2.0,
    )
    with pytest.raises(ValueError, match="has no ADP"):
        model.sample_pick(rng=Random(1), available=[missing_adp], overall_pick=1)


def test_simulate_gap_survival_zero_gap_is_certain() -> None:
    players = [make_player("a", "RB", 100, adp=1.0)]
    survival = simulate_gap_survival(
        opponent_model=AdpOpponentModel(),
        available=players,
        start_pick=1,
        gap_size=0,
        slots=[],
        iterations=10,
        rng=Random(1),
    )
    assert survival == {"a": 1.0}


def test_simulate_gap_survival_favors_the_deep_sleeper() -> None:
    hot = make_player("hot", "RB", 100, adp=1.0, adp_stdev=1.0)
    sleeper = make_player("sleeper", "WR", 100, adp=50.0, adp_stdev=1.0)
    survival = simulate_gap_survival(
        opponent_model=AdpOpponentModel(),
        available=[hot, sleeper],
        start_pick=1,
        gap_size=1,
        slots=[2],
        iterations=200,
        rng=Random(3),
    )
    assert survival["sleeper"] > survival["hot"]


# ---------------------------------------------------------------------------
# Branch policy
# ---------------------------------------------------------------------------


def test_rank_available_applies_positional_need_bonus() -> None:
    scored = value_over_replacement(
        [make_player("rb1", "RB", 50, adp=1.0), make_player("wr1", "WR", 55, adp=2.0)],
        SCORING,
        {"RB": 10, "WR": 10},
    )

    ranked = rank_available(scored, roster=(), roster_targets={"RB": 2, "WR": 0}, need_weight=100.0)

    assert ranked[0].player.player_id == "rb1"


def test_select_branch_candidates_individual_round_returns_top_k() -> None:
    players = [make_player(f"p{i}", "RB", 100 - i, adp=float(i + 1)) for i in range(5)]
    scored = value_over_replacement(players, SCORING, {"RB": 10})
    policy = BranchPolicy(individual_rounds=3, top_k_by_round={1: 2}, default_top_k=2, menu_size=4)

    candidates = select_branch_candidates(
        scored, round_no=1, roster=(), roster_targets={}, survival={}, policy=policy
    )

    expanded = [c for c in candidates if c.expand]
    assert [c.player.player.player_id for c in expanded] == ["p0", "p1"]
    assert len(candidates) == 4  # menu_size fills in extra context-only options


def test_select_branch_candidates_archetype_round_picks_one_per_archetype() -> None:
    players = [
        make_player("rb1", "RB", 90, adp=1.0),
        make_player("wr1", "WR", 85, adp=2.0),
        make_player("te1", "TE", 40, adp=10.0),
        make_player("qb1", "QB", 60, adp=15.0),
    ]
    scored = value_over_replacement(players, SCORING, {"RB": 10, "WR": 10, "TE": 5, "QB": 10})
    policy = BranchPolicy(individual_rounds=0, top_k_by_round={}, default_top_k=2, menu_size=4)

    candidates = select_branch_candidates(
        scored, round_no=5, roster=(), roster_targets={}, survival={}, policy=policy
    )

    expanded = {c.player.player.player_id: c.archetype for c in candidates if c.expand}
    assert expanded == {"rb1": "elite_rb", "wr1": "elite_wr", "te1": "te_premium", "qb1": "qb"}


# ---------------------------------------------------------------------------
# Value model
# ---------------------------------------------------------------------------


def test_vorp_value_model_sums_over_replacement() -> None:
    players = [make_player("rb1", "RB", 50, adp=1.0), make_player("rb2", "RB", 30, adp=2.0)]
    model = VorpValueModel(scoring=SCORING, replacement_points={"RB": 20.0})

    result = model.evaluate(players)

    assert result.bye_penalty == pytest.approx(1.0)  # both share bye_week=5
    assert result.ev == pytest.approx((50 - 20) + (30 - 20) - result.bye_penalty)


def test_vorp_value_model_rejects_empty_roster() -> None:
    model = VorpValueModel(scoring=SCORING, replacement_points={"RB": 20.0})
    with pytest.raises(ValueError, match="cannot be empty"):
        model.evaluate([])


def test_vorp_value_model_rejects_missing_replacement_level() -> None:
    model = VorpValueModel(scoring=SCORING, replacement_points={"WR": 20.0})
    with pytest.raises(ValueError, match="missing replacement levels"):
        model.evaluate([make_player("rb1", "RB", 50, adp=1.0)])


# ---------------------------------------------------------------------------
# Full tree: shape, determinism, and no double-drafting a player
# ---------------------------------------------------------------------------


def _synthetic_pool() -> list[PlayerProjection]:
    players = []
    positions = ["QB", "RB", "WR", "TE"]
    for i in range(24):
        position = positions[i % len(positions)]
        players.append(make_player(f"p{i}", position, 100 - i, adp=float(i + 1)))
    return players


def _small_context() -> DraftContext:
    return DraftContext(
        num_teams=4,
        my_slot=2,
        rounds=3,
        roster_targets={"QB": 1, "RB": 2, "WR": 2, "TE": 1},
        starters_per_team={"QB": 1, "RB": 1, "WR": 1, "TE": 1},
        scoring=SCORING,
    )


def _small_policy() -> BranchPolicy:
    return BranchPolicy(
        individual_rounds=3,
        top_k_by_round={1: 3, 2: 2},
        default_top_k=2,
        beam_width=6,
        menu_size=4,
    )


def test_build_draft_tree_has_unique_node_ids_and_valid_parents() -> None:
    tree = build_draft_tree(
        context=_small_context(),
        pool=_synthetic_pool(),
        branch_policy=_small_policy(),
        simulation_iterations=15,
        seed=42,
    )

    ids = [node.node_id for node in tree.nodes]
    assert len(ids) == len(set(ids))

    by_id = tree.by_id()
    for node in tree.nodes:
        if node.parent_id is not None:
            assert node.parent_id in by_id


def test_build_draft_tree_never_drafts_the_same_player_twice() -> None:
    tree = build_draft_tree(
        context=_small_context(),
        pool=_synthetic_pool(),
        branch_policy=_small_policy(),
        simulation_iterations=15,
        seed=42,
    )

    leaves = [node for node in tree.nodes if node.depth == 3]
    assert leaves
    for leaf in leaves:
        assert len(set(leaf.roster_player_ids)) == len(leaf.roster_player_ids)


def test_build_draft_tree_is_deterministic_given_the_same_seed() -> None:
    kwargs = dict(
        context=_small_context(),
        pool=_synthetic_pool(),
        branch_policy=_small_policy(),
        simulation_iterations=15,
        seed=99,
    )

    first = build_draft_tree(**kwargs)
    second = build_draft_tree(**kwargs)

    assert [n.node_id for n in first.nodes] == [n.node_id for n in second.nodes]
    assert [n.ev for n in first.nodes] == [n.ev for n in second.nodes]


def test_build_draft_tree_root_has_no_pick_and_zero_ev() -> None:
    tree = build_draft_tree(
        context=_small_context(),
        pool=_synthetic_pool(),
        branch_policy=_small_policy(),
        simulation_iterations=15,
        seed=1,
    )

    root = tree.by_id()["root"]
    assert root.parent_id is None
    assert root.chosen_player_id is None
    assert root.ev == 0.0
    assert root.reach_probability == 1.0


def test_build_draft_tree_rejects_duplicate_player_ids_in_pool() -> None:
    pool = _synthetic_pool()
    pool.append(pool[0])
    with pytest.raises(ValueError, match="duplicate player_id"):
        build_draft_tree(context=_small_context(), pool=pool, branch_policy=_small_policy())
