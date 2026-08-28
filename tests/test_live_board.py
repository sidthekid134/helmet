from __future__ import annotations

import pytest

from helmet.analytics import PlayerProjection, ScoredProjection, ScoringRule, ScoringSettings
from helmet.draft import (
    DraftContext,
    live_board_from_pool,
    rank_for_live_board,
)

SCORING = ScoringSettings((ScoringRule("points", 1.0),))


def make_player(
    player_id: str,
    position: str,
    points: float,
    adp: float,
    *,
    bye_week: int = 8,
) -> PlayerProjection:
    return PlayerProjection(
        player_id=player_id,
        name=player_id,
        position=position,
        team="AAA",
        bye_week=bye_week,
        stats={"points": points},
        floor=points * 0.7,
        ceiling=points * 1.3,
        adp=adp,
        adp_stdev=3.0,
    )


def scored(
    player_id: str,
    position: str,
    points: float,
    *,
    replacement: float = 40.0,
    adp: float = 20.0,
    bye_week: int = 8,
) -> ScoredProjection:
    player = make_player(player_id, position, points, adp, bye_week=bye_week)
    return ScoredProjection(
        player=player,
        projected_points=points,
        replacement_points=replacement,
        vorp=points - replacement,
    )


def _rank(
    available: list[ScoredProjection],
    *,
    roster: list[PlayerProjection] = (),
    survival: dict[str, float] | None = None,
    starters_per_team: dict[str, int] | None = None,
    roster_targets: dict[str, int] | None = None,
    overall_pick: int = 12,
    gap_size: int = 10,
) -> list:
    certain = {item.player.player_id: 1.0 for item in available}
    return rank_for_live_board(
        available,
        roster=list(roster),
        roster_targets=roster_targets or {"RB": 2, "WR": 2},
        starters_per_team=starters_per_team or {"RB": 1, "WR": 1},
        overall_pick=overall_pick,
        survival=survival or certain,
        gap_size=gap_size,
    )


def test_need_fills_the_empty_starter() -> None:
    wr_already = make_player("wr0", "WR", 90, adp=3.0)
    available = [
        scored("wr1", "WR", 65, replacement=40, adp=10.0),
        scored("wr2", "WR", 60, replacement=40, adp=18.0),
        scored("rb1", "RB", 55, replacement=40, adp=12.0),
        scored("rb2", "RB", 50, replacement=40, adp=22.0),
    ]

    ranked = _rank(available, roster=[wr_already])

    assert ranked[0].item.player.player_id == "rb1"
    assert any("Fills open RB1 starter" in reason for reason in ranked[0].reasons)


def test_vona_prefers_the_position_with_the_cliff() -> None:
    wr_already = make_player("wr0", "WR", 90, adp=3.0)
    rb_already = make_player("rb0", "RB", 90, adp=2.0)
    available = [
        scored("rb1", "RB", 100, replacement=60, adp=8.0),
        scored("rb2", "RB", 95, replacement=60, adp=14.0),
        scored("wr1", "WR", 90, replacement=60, adp=9.0),
        scored("wr2", "WR", 50, replacement=60, adp=30.0),
    ]
    survival = {item.player.player_id: 0.8 for item in available}

    ranked = _rank(
        available,
        roster=[wr_already, rb_already],
        survival=survival,
        starters_per_team={"RB": 1, "WR": 1},
        roster_targets={"RB": 1, "WR": 1},
    )

    assert ranked[0].item.player.player_id == "wr1"
    assert ranked[0].vona == 40.0
    assert any("VONA vs next WR" in reason for reason in ranked[0].reasons)


def test_low_survival_beats_wait_at_similar_vorp() -> None:
    available = [
        scored("hot", "WR", 90, replacement=40, adp=10.0),
        scored("wr_next", "WR", 80, replacement=40, adp=40.0),
        scored("cold", "RB", 89, replacement=40, adp=24.0),
        scored("rb_next", "RB", 79, replacement=40, adp=41.0),
    ]
    survival = {"hot": 0.1, "wr_next": 0.9, "cold": 0.9, "rb_next": 0.9}

    ranked = _rank(
        available,
        survival=survival,
        gap_size=11,
        starters_per_team={"RB": 1, "WR": 1},
        roster_targets={"RB": 1, "WR": 1},
        roster=[
            make_player("wr0", "WR", 100, adp=1.0),
            make_player("rb0", "RB", 100, adp=2.0),
        ],
    )

    assert ranked[0].item.player.player_id == "hot"
    assert ranked[0].urgency == "take_now"
    assert any("Gone by next pick" in reason for reason in ranked[0].reasons)
    cold = next(entry for entry in ranked if entry.item.player.player_id == "cold")
    assert cold.urgency == "wait"


def test_complete_draft_returns_empty_recommendations() -> None:
    context = DraftContext(
        num_teams=4,
        my_slot=1,
        rounds=1,
        roster_targets={"RB": 1},
        starters_per_team={"RB": 1},
        scoring=SCORING,
    )
    pool = [make_player("rb1", "RB", 50, adp=1.0), make_player("rb2", "RB", 40, adp=2.0)]

    result = live_board_from_pool(
        context=context,
        pool=pool,
        my_roster_player_ids=["rb1"],
        taken_by_others_player_ids=[],
    )

    assert result["complete"] is True
    assert result["recommendations"] == []
    assert result["overall_pick"] is None
    assert result["starters_per_team"] == {"RB": 1}


def test_live_board_from_pool_ranks_remaining_and_reports_survival() -> None:
    positions = ("RB", "WR")
    pool = [
        make_player(f"{position}{index}", position, 120 - index, adp=float(index + 1 + offset))
        for offset, position in enumerate(positions)
        for index in range(12)
    ]
    context = DraftContext(
        num_teams=4,
        my_slot=2,
        rounds=3,
        roster_targets={"RB": 2, "WR": 2},
        starters_per_team={"RB": 1, "WR": 1},
        scoring=SCORING,
    )

    result = live_board_from_pool(
        context=context,
        pool=pool,
        my_roster_player_ids=[],
        taken_by_others_player_ids=["RB0"],
        seed=7,
        simulation_iterations=20,
        limit=10,
    )

    assert result["complete"] is False
    assert result["overall_pick"] == 2
    assert result["picks_until_next"] > 0
    assert len(result["recommendations"]) == 10
    top = result["recommendations"][0]
    assert top["rank"] == 1
    assert 0.0 <= top["survival_to_next"] <= 1.0
    assert top["reasons"]
    assert top["player"]["id"] != "RB0"
    assert "urgency" in top
    assert "vona" in top
    assert "vorp" in top


def test_unknown_roster_player_is_a_hard_error() -> None:
    context = DraftContext(
        num_teams=4,
        my_slot=1,
        rounds=2,
        roster_targets={"RB": 1},
        starters_per_team={"RB": 1},
        scoring=SCORING,
    )
    with pytest.raises(ValueError, match="missing"):
        live_board_from_pool(
            context=context,
            pool=[make_player("rb1", "RB", 50, adp=1.0)],
            my_roster_player_ids=["missing"],
            taken_by_others_player_ids=[],
        )
