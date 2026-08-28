"""Validated cross-layer fantasy football models."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class IngestionStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"


class RecommendationKind(StrEnum):
    DRAFT = "draft"
    LINEUP = "lineup"
    WAIVER = "waiver"
    TRADE = "trade"
    CHAOS = "chaos"


class Player(StrictModel):
    source_id: str = Field(min_length=1)
    full_name: str = Field(min_length=1)
    position: str | None = None
    team: str | None = None
    status: str | None = None
    age: int | None = Field(default=None, ge=18, le=60)
    metadata: dict[str, Any] = Field(default_factory=dict)


class PlayerIdentity(StrictModel):
    canonical_id: str = Field(min_length=1)
    source: str = Field(min_length=1)
    external_id: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    requires_review: bool = False


class League(StrictModel):
    league_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    season: int = Field(ge=2000, le=2100)
    status: str
    total_rosters: int = Field(ge=2)
    roster_positions: tuple[str, ...]
    scoring_settings: dict[str, float]
    settings: dict[str, Any] = Field(default_factory=dict)


class LeagueConnection(StrictModel):
    id: str
    provider: str = "sleeper"
    name: str
    season: int
    status: str


class Roster(StrictModel):
    roster_id: int = Field(ge=1)
    owner_id: str | None
    player_ids: tuple[str, ...]
    starter_ids: tuple[str, ...]
    settings: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def starters_must_be_rostered(self) -> Roster:
        missing = set(self.starter_ids) - set(self.player_ids)
        if missing:
            raise ValueError(f"starters are not rostered: {sorted(missing)}")
        return self


class DraftPick(StrictModel):
    draft_id: str
    pick_number: int = Field(ge=1)
    round: int = Field(ge=1)
    roster_id: int = Field(ge=1)
    player_id: str
    picked_at: datetime | None = None


class SourceObservation(StrictModel):
    source_system: str
    entity_type: str
    entity_id: str
    observed_at: datetime
    effective_at: datetime
    payload: dict[str, Any]
    source_url: str | None = None

    @field_validator("observed_at", "effective_at")
    @classmethod
    def timezone_required(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware")
        return value

    @property
    def content_hash(self) -> str:
        encoded = json.dumps(
            self.payload, sort_keys=True, separators=(",", ":"), default=str
        ).encode()
        return sha256(encoded).hexdigest()

    @classmethod
    def now(
        cls,
        *,
        source_system: str,
        entity_type: str,
        entity_id: str,
        payload: dict[str, Any],
        effective_at: datetime | None = None,
        source_url: str | None = None,
    ) -> SourceObservation:
        observed_at = datetime.now(UTC)
        return cls(
            source_system=source_system,
            entity_type=entity_type,
            entity_id=entity_id,
            observed_at=observed_at,
            effective_at=effective_at or observed_at,
            payload=payload,
            source_url=source_url,
        )
