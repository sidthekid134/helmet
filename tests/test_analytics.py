from __future__ import annotations

import unittest

from helmet.analytics import (
    PlayerProjection,
    Recommendation,
    RosterSlot,
    ScoredProjection,
    ScoringRule,
    ScoringSettings,
    assign_tiers,
    bye_week_exposure,
    chaos_response,
    evaluate_trade,
    live_draft_recommendations,
    optimize_lineup,
    replacement_levels,
    score_outcome,
    score_projection,
    simulate_rest_of_season,
    value_over_replacement,
    waiver_rankings,
)

SCORING = ScoringSettings(
    (
        ScoringRule("yards", 0.1, threshold=100, bonus=3),
        ScoringRule("td", 6),
        ScoringRule("turnovers", -2),
    )
)


def player(
    player_id: str,
    position: str,
    yards: float,
    *,
    td: float = 1,
    turnovers: float = 0,
    bye: int = 7,
    floor: float = 5,
    ceiling: float = 25,
    adp: float | None = 10,
    team: str = "A",
) -> PlayerProjection:
    return PlayerProjection(
        player_id,
        player_id,
        position,
        team,
        bye,
        {"yards": yards, "td": td, "turnovers": turnovers},
        floor,
        ceiling,
        adp,
    )


class ScoringTests(unittest.TestCase):
    def test_projection_and_outcome_use_identical_deterministic_scoring(self) -> None:
        projection = player("p1", "RB", 100, turnovers=1)
        self.assertEqual(score_projection(projection, SCORING), 17)
        self.assertEqual(score_outcome(projection.stats, SCORING), 17)

    def test_missing_scoring_stat_fails(self) -> None:
        with self.assertRaisesRegex(ValueError, "missing required scoring stats"):
            score_outcome({"yards": 10}, SCORING)

    def test_projection_contract_rejects_invalid_bounds(self) -> None:
        with self.assertRaisesRegex(ValueError, "floor cannot exceed ceiling"):
            player("bad", "RB", 10, floor=20, ceiling=10)


class ValuationTests(unittest.TestCase):
    def test_replacement_vorp_and_tiers(self) -> None:
        players = [player(f"r{i}", "RB", yards) for i, yards in enumerate((100, 90, 80))]
        levels = replacement_levels(players, SCORING, 1, {"RB": 1})
        self.assertEqual(levels["RB"], 15)
        values = value_over_replacement(players, SCORING, levels)
        self.assertEqual([item.vorp for item in values], [4, 0, -1])
        tiers = assign_tiers(values, gap=3)
        self.assertEqual([item.tier for item in tiers], [1, 2, 2])

    def test_replacement_requires_sufficient_pool(self) -> None:
        with self.assertRaisesRegex(ValueError, "needs at least"):
            replacement_levels([player("r1", "RB", 10)], SCORING, 1, {"RB": 1})

    def test_bye_exposure_is_sorted_and_weighted(self) -> None:
        roster = [player("a", "RB", 10, bye=9), player("b", "WR", 20, bye=7)]
        self.assertEqual(bye_week_exposure(roster, weights={"a": 2, "b": 1}), {7: 1, 9: 2})


class DecisionTests(unittest.TestCase):
    def test_lineup_optimizer_honors_slots_and_correlations(self) -> None:
        rb = player("rb", "RB", 100, floor=8, ceiling=30)
        wr_a = player("wr-a", "WR", 100, floor=9, ceiling=25)
        wr_b = player("wr-b", "WR", 99, floor=10, ceiling=22)
        slots = [
            RosterSlot("RB", frozenset({"RB"})),
            RosterSlot("FLEX", frozenset({"RB", "WR"})),
        ]
        result = optimize_lineup(
            [rb, wr_a, wr_b],
            slots,
            SCORING,
            correlations={("rb", "wr-b"): 1},
            correlation_weight=4,
        )
        self.assertEqual(result.assignments["FLEX"].player_id, "wr-b")
        self.assertEqual(result.correlation_adjustment, 4)

    def test_floor_and_ceiling_objectives_change_lineup(self) -> None:
        safe = player("safe", "RB", 10, floor=10, ceiling=11)
        volatile = player("volatile", "RB", 100, floor=1, ceiling=30)
        slot = [RosterSlot("RB", frozenset({"RB"}))]
        self.assertEqual(
            optimize_lineup([safe, volatile], slot, SCORING, objective="floor")
            .assignments["RB"]
            .player_id,
            "safe",
        )
        self.assertEqual(
            optimize_lineup([safe, volatile], slot, SCORING, objective="ceiling")
            .assignments["RB"]
            .player_id,
            "volatile",
        )

    def test_draft_waiver_trade_and_chaos(self) -> None:
        rostered = player("old", "RB", 20, bye=7)
        free = player("new", "RB", 100, bye=7)
        scored = ScoredProjection(free, 19, replacement_points=10, vorp=9)
        draft = live_draft_recommendations([scored], [rostered], {"RB": 2}, current_pick=12)
        self.assertEqual(draft[0].player_id, "new")
        waiver = waiver_rankings([scored], [rostered], SCORING)
        self.assertEqual(waiver[0].reasons[0], "drop old")
        trade = evaluate_trade(
            [ScoredProjection(rostered, 8, vorp=1)],
            [scored],
        )
        self.assertGreater(trade.score, 0)
        self.assertEqual(
            chaos_response(
                [Recommendation("gone", 2, ()), Recommendation("new", 1, ())],
                ["gone"],
            )[0].player_id,
            "new",
        )

    def test_chaos_refuses_to_hide_total_failure(self) -> None:
        with self.assertRaisesRegex(ValueError, "every recommendation"):
            chaos_response([Recommendation("gone", 1, ())], ["gone"])


class SimulationTests(unittest.TestCase):
    def test_seeded_monte_carlo_is_reproducible(self) -> None:
        kwargs = {
            "weekly_means": {"a": [10, 12]},
            "weekly_stdevs": {"a": [2, 3]},
            "iterations": 100,
            "seed": 42,
        }
        first = simulate_rest_of_season(**kwargs)
        second = simulate_rest_of_season(**kwargs)
        self.assertEqual(first["a"].samples, second["a"].samples)
        self.assertLessEqual(first["a"].floor, first["a"].median)
        self.assertLessEqual(first["a"].median, first["a"].ceiling)

    def test_simulation_rejects_misaligned_inputs(self) -> None:
        with self.assertRaisesRegex(ValueError, "identical"):
            simulate_rest_of_season({"a": [1]}, {"b": [1]}, iterations=10, seed=1)


if __name__ == "__main__":
    unittest.main()
