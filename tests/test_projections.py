from __future__ import annotations

import polars as pl
import pytest

from helmet.analytics import PlayerProjection
from helmet.integrations.nflverse import NflverseDataset
from helmet.projections import (
    ModifierContext,
    ProjectionSettings,
    apply_modifiers,
    build_projection_pool,
    translate_sleeper_scoring,
)

# ---------------------------------------------------------------------------
# Sleeper -> nflverse scoring translation
# ---------------------------------------------------------------------------


def test_translate_sleeper_scoring_maps_known_keys_and_skips_zero_weights() -> None:
    translation = translate_sleeper_scoring({"rec": 1.0, "rec_yd": 0.1, "pass_2pt": 0.0})

    assert translation.stat_columns == ("receiving_yards", "receptions")
    assert translation.unsupported_keys == ()
    rules = {rule.stat: rule.points_per_unit for rule in translation.settings.rules}
    assert rules == {"receptions": 1.0, "receiving_yards": 0.1}


def test_translate_sleeper_scoring_reports_unsupported_keys() -> None:
    translation = translate_sleeper_scoring({"rec": 1.0, "made_up_stat": 3.0})

    assert translation.unsupported_keys == ("made_up_stat",)
    assert translation.stat_columns == ("receptions",)


def test_translate_sleeper_scoring_rejects_empty_settings() -> None:
    with pytest.raises(ValueError, match="cannot be empty"):
        translate_sleeper_scoring({})


def test_translate_sleeper_scoring_rejects_all_unsupported() -> None:
    with pytest.raises(ValueError, match="no supported scoring rules"):
        translate_sleeper_scoring({"made_up_stat": 1.0})


# ---------------------------------------------------------------------------
# Research-promoted modifiers
# ---------------------------------------------------------------------------


def _rb(**overrides: object) -> PlayerProjection:
    base = dict(
        player_id="rb1",
        name="Test Back",
        position="RB",
        team="AAA",
        bye_week=5,
        stats={
            "carries": 250.0,
            "receptions": 30.0,
            "rushing_yards": 1000.0,
            "receiving_yards": 200.0,
        },
        floor=150.0,
        ceiling=300.0,
    )
    base.update(overrides)
    return PlayerProjection(**base)  # type: ignore[arg-type]


def test_apply_modifiers_rejects_unknown_names() -> None:
    context = ModifierContext(player=_rb(), prior_season_totals={}, prior_season_games=17)
    with pytest.raises(ValueError, match="no applier is registered"):
        apply_modifiers(context, {"not_a_real_modifier": 1.0})


def test_rb_hangover_modifier_scales_down_high_touch_backs() -> None:
    player = _rb()
    context = ModifierContext(
        player=player,
        prior_season_totals={"carries": 300.0, "receptions": 60.0},  # 360 touches, over threshold
        prior_season_games=17,
    )

    adjusted = apply_modifiers(context, {"rb_350_touch_next_year_points_per_touch": -0.5})

    assert adjusted.stats["rushing_yards"] < player.stats["rushing_yards"]
    assert adjusted.floor < player.floor
    assert adjusted.ceiling < player.ceiling


def test_rb_hangover_modifier_ignores_backs_under_threshold() -> None:
    player = _rb()
    context = ModifierContext(
        player=player,
        prior_season_totals={"carries": 100.0, "receptions": 20.0},  # 120 touches, under threshold
        prior_season_games=17,
    )

    unchanged = apply_modifiers(context, {"rb_350_touch_next_year_points_per_touch": -0.5})

    assert unchanged == player


def test_rb_hangover_modifier_ignores_non_rb_positions() -> None:
    wr = _rb(position="WR", stats={"receptions": 100.0, "receiving_yards": 1200.0})
    context = ModifierContext(
        player=wr, prior_season_totals={"carries": 400.0, "receptions": 0.0}, prior_season_games=17
    )

    unchanged = apply_modifiers(context, {"rb_350_touch_next_year_points_per_touch": -0.5})

    assert unchanged == wr


# ---------------------------------------------------------------------------
# Projection pool builder (fake nflverse client, no network)
# ---------------------------------------------------------------------------


class FakeNflverseClient:
    """A stand-in for NflverseClient returning fixed, hand-built frames."""

    def __init__(self) -> None:
        self.rankings = pl.DataFrame(
            {
                "id": [1, 2, 3, 4],
                "player": ["Runner A", "Wideout B", "Ghost C", "Runner D"],
                "pos": ["RB", "WR", "WR", "RB"],
                "team": ["AAA", "AAA", "AAA", "ZZZ"],
                "ecr": [5.0, 10.0, 15.0, 20.0],
                "sd": [2.0, 3.0, 1.5, 4.0],
                "page_type": ["redraft-overall"] * 4,
            }
        )
        self.ff_ids = pl.DataFrame(
            {
                # Ghost C (fantasypros_id=3) is deliberately absent: no identity mapping.
                "fantasypros_id": [1, 2, 4],
                "gsis_id": ["00-AAA", "00-BBB", "00-DDD"],
            }
        )
        self.schedules = pl.DataFrame(
            {
                "season": [2026, 2026, 2026],
                "week": [1, 2, 3],
                "home_team": ["AAA", "AAA", "OPP1"],
                "away_team": ["OPP1", "OPP2", "OPP2"],
            }
        )
        stat_columns = [
            "rushing_yards",
            "rushing_tds",
            "receptions",
            "receiving_yards",
            "receiving_tds",
        ]
        rows = []
        for week, (ry, rt, rec, recy, rectd) in enumerate(
            [(80, 1, 2, 15, 0), (90, 0, 3, 20, 1), (70, 1, 1, 10, 0)], start=1
        ):
            rows.append(
                {
                    "player_id": "00-AAA",
                    "season": 2025,
                    "season_type": "REG",
                    "week": week,
                    "rushing_yards": ry,
                    "rushing_tds": rt,
                    "receptions": rec,
                    "receiving_yards": recy,
                    "receiving_tds": rectd,
                }
            )
            rows.append(
                {
                    "player_id": "00-DDD",
                    "season": 2025,
                    "season_type": "REG",
                    "week": week,
                    "rushing_yards": ry - 10,
                    "rushing_tds": rt,
                    "receptions": max(rec - 1, 0),
                    "receiving_yards": recy - 5,
                    "receiving_tds": 0,
                }
            )
        # 00-BBB has no rows at all: no prior regular-season stats.
        self.player_stats = pl.DataFrame(rows).select(
            "player_id", "season", "season_type", "week", *stat_columns
        )

    def load(
        self, dataset: NflverseDataset, *, seasons: object = None, **_: object
    ) -> pl.DataFrame:
        if dataset is NflverseDataset.FF_RANKINGS:
            return self.rankings
        if dataset is NflverseDataset.FF_IDS:
            return self.ff_ids
        if dataset is NflverseDataset.SCHEDULES:
            return self.schedules
        if dataset is NflverseDataset.PLAYER_STATS:
            return self.player_stats
        raise AssertionError(f"unexpected dataset requested: {dataset}")


def _settings(**overrides: object) -> ProjectionSettings:
    base = dict(
        target_season=2026,
        lookback_seasons=(2025,),
        positions=("RB", "WR"),
        min_prior_games=2,
        max_players=10,
        simulation_iterations=20,
        seed=1,
    )
    base.update(overrides)
    return ProjectionSettings(**base)  # type: ignore[arg-type]


def test_build_projection_pool_ranks_and_excludes_correctly() -> None:
    scoring = translate_sleeper_scoring(
        {"rush_yd": 0.1, "rush_td": 6.0, "rec": 1.0, "rec_yd": 0.1, "rec_td": 6.0}
    )

    pool = build_projection_pool(scoring=scoring, settings=_settings(), client=FakeNflverseClient())

    assert [p.player_id for p in pool.players] == ["00-AAA"]
    runner_a = pool.players[0]
    assert runner_a.name == "Runner A"
    assert runner_a.bye_week == 3
    assert runner_a.floor <= runner_a.ceiling
    assert runner_a.adp == 5.0
    assert runner_a.adp_stdev == 2.0

    reasons = {item.name: item.reason for item in pool.excluded}
    assert reasons["Wideout B"] == "no prior regular-season stats"
    assert reasons["Ghost C"] == "no nflverse identity mapping"
    assert reasons["Runner D"] == "no ZZZ bye week in schedule"


def test_build_projection_pool_reports_unsupported_scoring_keys() -> None:
    scoring = translate_sleeper_scoring(
        {
            "rush_yd": 0.1,
            "rush_td": 6.0,
            "rec": 1.0,
            "rec_yd": 0.1,
            "rec_td": 6.0,
            "made_up_stat": 3.0,
        }
    )

    pool = build_projection_pool(scoring=scoring, settings=_settings(), client=FakeNflverseClient())

    assert pool.unsupported_scoring_keys == ("made_up_stat",)


def test_build_projection_pool_rejects_lookahead_seasons() -> None:
    with pytest.raises(ValueError, match="must precede target_season"):
        _settings(lookback_seasons=(2026,))
