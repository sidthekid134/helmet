"""Validated data contracts for deterministic fantasy analytics."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from math import isfinite
from types import MappingProxyType


def _finite(value: float, field_name: str) -> float:
    value = float(value)
    if not isfinite(value):
        raise ValueError(f"{field_name} must be finite")
    return value


def _required_text(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _numeric_mapping(values: Mapping[str, float], field_name: str) -> Mapping[str, float]:
    if not isinstance(values, Mapping):
        raise TypeError(f"{field_name} must be a mapping")
    result: dict[str, float] = {}
    for key, value in values.items():
        result[_required_text(key, f"{field_name} key")] = _finite(value, f"{field_name}[{key!r}]")
    return MappingProxyType(result)


@dataclass(frozen=True, slots=True)
class ScoringRule:
    stat: str
    points_per_unit: float
    threshold: float | None = None
    bonus: float = 0.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "stat", _required_text(self.stat, "stat"))
        object.__setattr__(
            self, "points_per_unit", _finite(self.points_per_unit, "points_per_unit")
        )
        object.__setattr__(self, "bonus", _finite(self.bonus, "bonus"))
        if self.threshold is not None:
            threshold = _finite(self.threshold, "threshold")
            if threshold < 0:
                raise ValueError("threshold must be non-negative")
            object.__setattr__(self, "threshold", threshold)
        elif self.bonus:
            raise ValueError("bonus requires a threshold")


@dataclass(frozen=True, slots=True)
class ScoringSettings:
    rules: tuple[ScoringRule, ...]

    def __post_init__(self) -> None:
        rules = tuple(self.rules)
        if not rules:
            raise ValueError("at least one scoring rule is required")
        if len({(rule.stat, rule.threshold) for rule in rules}) != len(rules):
            raise ValueError("duplicate scoring rule for stat and threshold")
        object.__setattr__(self, "rules", rules)


@dataclass(frozen=True, slots=True)
class PlayerProjection:
    player_id: str
    name: str
    position: str
    team: str
    bye_week: int
    stats: Mapping[str, float]
    floor: float
    ceiling: float
    adp: float | None = None
    adp_stdev: float | None = None
    availability: float = 1.0

    def __post_init__(self) -> None:
        for field_name in ("player_id", "name", "position", "team"):
            object.__setattr__(
                self, field_name, _required_text(getattr(self, field_name), field_name)
            )
        if (
            isinstance(self.bye_week, bool)
            or not isinstance(self.bye_week, int)
            or self.bye_week < 1
        ):
            raise ValueError("bye_week must be a positive integer")
        object.__setattr__(self, "stats", _numeric_mapping(self.stats, "stats"))
        floor = _finite(self.floor, "floor")
        ceiling = _finite(self.ceiling, "ceiling")
        if floor > ceiling:
            raise ValueError("floor cannot exceed ceiling")
        object.__setattr__(self, "floor", floor)
        object.__setattr__(self, "ceiling", ceiling)
        if self.adp is not None:
            adp = _finite(self.adp, "adp")
            if adp <= 0:
                raise ValueError("adp must be positive")
            object.__setattr__(self, "adp", adp)
        if self.adp_stdev is not None:
            if self.adp is None:
                raise ValueError("adp_stdev requires adp")
            adp_stdev = _finite(self.adp_stdev, "adp_stdev")
            if adp_stdev < 0:
                raise ValueError("adp_stdev cannot be negative")
            object.__setattr__(self, "adp_stdev", adp_stdev)
        availability = _finite(self.availability, "availability")
        if not 0 <= availability <= 1:
            raise ValueError("availability must be between zero and one")
        object.__setattr__(self, "availability", availability)


@dataclass(frozen=True, slots=True)
class ScoredProjection:
    player: PlayerProjection
    projected_points: float
    replacement_points: float = 0.0
    vorp: float = 0.0
    tier: int = 1
    adp_value: float | None = None


@dataclass(frozen=True, slots=True)
class RosterSlot:
    name: str
    eligible_positions: frozenset[str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _required_text(self.name, "name"))
        positions = frozenset(
            _required_text(value, "eligible position") for value in self.eligible_positions
        )
        if not positions:
            raise ValueError("eligible_positions cannot be empty")
        object.__setattr__(self, "eligible_positions", positions)


@dataclass(frozen=True, slots=True)
class LineupResult:
    assignments: Mapping[str, PlayerProjection]
    objective_score: float
    projected_points: float
    floor: float
    ceiling: float
    correlation_adjustment: float = 0.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "assignments", MappingProxyType(dict(self.assignments)))


@dataclass(frozen=True, slots=True)
class SimulationResult:
    player_id: str
    mean: float
    median: float
    floor: float
    ceiling: float
    samples: tuple[float, ...] = field(repr=False)


@dataclass(frozen=True, slots=True)
class Recommendation:
    player_id: str
    score: float
    reasons: tuple[str, ...]
