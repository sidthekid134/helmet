"""Grounded learning contracts and deterministic policy governance."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from math import isfinite
from types import MappingProxyType


def _text(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _metrics(values: Mapping[str, float], name: str = "metrics") -> Mapping[str, float]:
    if not values:
        raise ValueError(f"{name} cannot be empty")
    output = {}
    for key, value in values.items():
        key = _text(key, f"{name} key")
        value = float(value)
        if not isfinite(value):
            raise ValueError(f"{name}[{key!r}] must be finite")
        output[key] = value
    return MappingProxyType(output)


class AttributionCategory(StrEnum):
    PROJECTION_ERROR = "projection_error"
    ROLE_CHANGE = "role_change"
    AVAILABILITY = "availability"
    MATCHUP = "matchup"
    MARKET_VALUE = "market_value"
    LINEUP_DECISION = "lineup_decision"
    DATA_QUALITY = "data_quality"


@dataclass(frozen=True, slots=True)
class Evidence:
    evidence_id: str
    source: str
    claim: str
    observed_at: datetime

    def __post_init__(self) -> None:
        for name in ("evidence_id", "source", "claim"):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")


@dataclass(frozen=True, slots=True)
class GroundedAttribution:
    category: AttributionCategory
    explanation: str
    evidence_ids: tuple[str, ...]
    confidence: float

    def __post_init__(self) -> None:
        if not isinstance(self.category, AttributionCategory):
            raise TypeError("category must be an AttributionCategory")
        object.__setattr__(self, "explanation", _text(self.explanation, "explanation"))
        evidence_ids = tuple(_text(value, "evidence_id") for value in self.evidence_ids)
        if not evidence_ids:
            raise ValueError("attribution requires at least one evidence ID")
        if len(set(evidence_ids)) != len(evidence_ids):
            raise ValueError("attribution evidence IDs must be unique")
        object.__setattr__(self, "evidence_ids", evidence_ids)
        confidence = float(self.confidence)
        if not isfinite(confidence) or not 0 <= confidence <= 1:
            raise ValueError("confidence must be between zero and one")
        object.__setattr__(self, "confidence", confidence)

    def validate_grounding(self, evidence: Iterable[Evidence]) -> None:
        available = {item.evidence_id for item in evidence}
        missing = sorted(set(self.evidence_ids) - available)
        if missing:
            raise ValueError(f"attribution references missing evidence: {', '.join(missing)}")


@dataclass(frozen=True, slots=True)
class LearningEvent:
    event_id: str
    recurrence_key: str
    attribution: GroundedAttribution
    evidence: tuple[Evidence, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _text(self.event_id, "event_id"))
        object.__setattr__(self, "recurrence_key", _text(self.recurrence_key, "recurrence_key"))
        evidence = tuple(self.evidence)
        if len({item.evidence_id for item in evidence}) != len(evidence):
            raise ValueError("learning event evidence IDs must be unique")
        self.attribution.validate_grounding(evidence)
        object.__setattr__(self, "evidence", evidence)


@dataclass(frozen=True, slots=True)
class PolicyProposal:
    recurrence_key: str
    category: AttributionCategory
    event_ids: tuple[str, ...]
    proposed_change: str
    recurrence_count: int


def propose_policy(
    events: Iterable[LearningEvent],
    proposed_change: str,
    *,
    minimum_recurrences: int = 3,
) -> PolicyProposal:
    """Create a proposal only from repeated, consistently attributed events."""
    if isinstance(minimum_recurrences, bool) or minimum_recurrences < 2:
        raise ValueError("minimum_recurrences must be an integer of at least two")
    events = tuple(events)
    if not events:
        raise ValueError("events cannot be empty")
    if len({event.event_id for event in events}) != len(events):
        raise ValueError("event IDs must be unique")
    keys = {event.recurrence_key for event in events}
    categories = {event.attribution.category for event in events}
    if len(keys) != 1:
        raise ValueError("all events must share one recurrence_key")
    if len(categories) != 1:
        raise ValueError("all events must share one attribution category")
    if len(events) < minimum_recurrences:
        raise ValueError(
            f"policy proposal requires {minimum_recurrences} recurrences; got {len(events)}"
        )
    return PolicyProposal(
        recurrence_key=next(iter(keys)),
        category=next(iter(categories)),
        event_ids=tuple(sorted(event.event_id for event in events)),
        proposed_change=_text(proposed_change, "proposed_change"),
        recurrence_count=len(events),
    )


@dataclass(frozen=True, slots=True)
class PolicyEvaluation:
    policy_id: str
    sample_size: int
    metrics: Mapping[str, float]

    def __post_init__(self) -> None:
        object.__setattr__(self, "policy_id", _text(self.policy_id, "policy_id"))
        if isinstance(self.sample_size, bool) or self.sample_size < 1:
            raise ValueError("sample_size must be a positive integer")
        object.__setattr__(self, "metrics", _metrics(self.metrics))


@dataclass(frozen=True, slots=True)
class PromotionCriteria:
    minimum_sample_size: int
    minimum_improvements: Mapping[str, float]
    maximum_regressions: Mapping[str, float]

    def __post_init__(self) -> None:
        if isinstance(self.minimum_sample_size, bool) or self.minimum_sample_size < 1:
            raise ValueError("minimum_sample_size must be positive")
        improvements = _metrics(self.minimum_improvements, "minimum_improvements")
        regressions = _metrics(self.maximum_regressions, "maximum_regressions")
        if set(improvements) != set(regressions):
            raise ValueError("promotion criteria metric sets must match")
        if any(value < 0 for value in regressions.values()):
            raise ValueError("maximum regressions must be non-negative")
        object.__setattr__(self, "minimum_improvements", improvements)
        object.__setattr__(self, "maximum_regressions", regressions)


@dataclass(frozen=True, slots=True)
class PromotionDecision:
    promote: bool
    deltas: Mapping[str, float]
    reasons: tuple[str, ...]


def compare_for_promotion(
    incumbent: PolicyEvaluation,
    candidate: PolicyEvaluation,
    criteria: PromotionCriteria,
) -> PromotionDecision:
    """Compare higher-is-better metrics against explicit per-metric gates."""
    required = set(criteria.minimum_improvements)
    if set(incumbent.metrics) != required or set(candidate.metrics) != required:
        raise ValueError("evaluations must contain exactly the criteria metrics")
    reasons = []
    if incumbent.sample_size < criteria.minimum_sample_size:
        reasons.append("incumbent sample size below minimum")
    if candidate.sample_size < criteria.minimum_sample_size:
        reasons.append("candidate sample size below minimum")
    deltas = {
        metric: candidate.metrics[metric] - incumbent.metrics[metric] for metric in sorted(required)
    }
    for metric, delta in deltas.items():
        if delta < -criteria.maximum_regressions[metric]:
            reasons.append(f"{metric} regression exceeds limit")
        if delta < criteria.minimum_improvements[metric]:
            reasons.append(f"{metric} improvement below minimum")
    return PromotionDecision(
        promote=not reasons,
        deltas=MappingProxyType(deltas),
        reasons=tuple(reasons) if reasons else ("all promotion gates passed",),
    )


@dataclass(frozen=True, slots=True)
class RollbackDecision:
    rollback: bool
    regressions: Mapping[str, float]
    reasons: tuple[str, ...]


def evaluate_rollback(
    promoted_baseline: PolicyEvaluation,
    live_evaluation: PolicyEvaluation,
    maximum_regressions: Mapping[str, float],
) -> RollbackDecision:
    """Rollback when any live higher-is-better metric breaches its fixed guardrail."""
    limits = _metrics(maximum_regressions, "maximum_regressions")
    if any(value < 0 for value in limits.values()):
        raise ValueError("maximum regressions must be non-negative")
    if set(promoted_baseline.metrics) != set(limits) or set(live_evaluation.metrics) != set(limits):
        raise ValueError("rollback evaluations must contain exactly the guardrail metrics")
    regressions = {
        metric: promoted_baseline.metrics[metric] - live_evaluation.metrics[metric]
        for metric in sorted(limits)
    }
    reasons = tuple(
        f"{metric} regression exceeds limit"
        for metric, regression in regressions.items()
        if regression > limits[metric]
    )
    return RollbackDecision(
        rollback=bool(reasons),
        regressions=MappingProxyType(regressions),
        reasons=reasons if reasons else ("all rollback guardrails passed",),
    )
