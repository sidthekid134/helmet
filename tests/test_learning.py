from __future__ import annotations

import unittest
from datetime import UTC, datetime

from helmet.agents import (
    AttributionCategory,
    Evidence,
    GroundedAttribution,
    LearningEvent,
    PolicyEvaluation,
    PromotionCriteria,
    compare_for_promotion,
    evaluate_rollback,
    propose_policy,
)


def event(index: int, *, key: str = "rb-volume") -> LearningEvent:
    evidence = Evidence(
        f"evidence-{index}",
        "official-box-score",
        "snap share exceeded projection",
        datetime(2026, 1, index + 1, tzinfo=UTC),
    )
    attribution = GroundedAttribution(
        AttributionCategory.ROLE_CHANGE,
        "Unexpected usage drove the miss.",
        (evidence.evidence_id,),
        0.9,
    )
    return LearningEvent(f"event-{index}", key, attribution, (evidence,))


class AttributionTests(unittest.TestCase):
    def test_attribution_requires_grounded_evidence(self) -> None:
        evidence = Evidence("real", "league", "player was inactive", datetime.now(UTC))
        attribution = GroundedAttribution(
            AttributionCategory.AVAILABILITY,
            "Inactive player could not score.",
            ("missing",),
            1.0,
        )
        with self.assertRaisesRegex(ValueError, "missing evidence"):
            attribution.validate_grounding([evidence])

    def test_categories_are_fixed(self) -> None:
        with self.assertRaises(ValueError):
            AttributionCategory("invented_category")


class RecurrenceTests(unittest.TestCase):
    def test_policy_proposal_requires_recurrence_gate(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires 3 recurrences"):
            propose_policy([event(1), event(2)], "Increase role-change weight.")
        proposal = propose_policy([event(3), event(1), event(2)], "Increase role-change weight.")
        self.assertEqual(proposal.event_ids, ("event-1", "event-2", "event-3"))
        self.assertEqual(proposal.recurrence_count, 3)

    def test_recurrence_cannot_mix_patterns(self) -> None:
        with self.assertRaisesRegex(ValueError, "one recurrence_key"):
            propose_policy(
                [event(1), event(2), event(3, key="wr-volume")],
                "Change policy.",
            )


class PolicyLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.incumbent = PolicyEvaluation("v1", 100, {"accuracy": 0.80, "calibration": 0.70})
        self.criteria = PromotionCriteria(
            50,
            {"accuracy": 0.01, "calibration": 0.0},
            {"accuracy": 0.0, "calibration": 0.01},
        )

    def test_candidate_promotes_only_when_every_gate_passes(self) -> None:
        candidate = PolicyEvaluation("v2", 100, {"accuracy": 0.82, "calibration": 0.71})
        decision = compare_for_promotion(self.incumbent, candidate, self.criteria)
        self.assertTrue(decision.promote)

    def test_sample_gate_blocks_promotion(self) -> None:
        candidate = PolicyEvaluation("v2", 10, {"accuracy": 0.90, "calibration": 0.90})
        decision = compare_for_promotion(self.incumbent, candidate, self.criteria)
        self.assertFalse(decision.promote)
        self.assertIn("candidate sample size below minimum", decision.reasons)

    def test_live_guardrail_has_explicit_rollback_semantics(self) -> None:
        promoted = PolicyEvaluation("v2", 100, {"accuracy": 0.82, "calibration": 0.71})
        live = PolicyEvaluation("v2-live", 100, {"accuracy": 0.79, "calibration": 0.705})
        decision = evaluate_rollback(promoted, live, {"accuracy": 0.02, "calibration": 0.01})
        self.assertTrue(decision.rollback)
        self.assertEqual(decision.reasons, ("accuracy regression exceeds limit",))


if __name__ == "__main__":
    unittest.main()
