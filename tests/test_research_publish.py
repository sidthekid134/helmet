from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from helmet.persistence import PersistenceContext
from helmet.repositories import LocalClient, PolicyVersionRepository, ResearchFindingRepository
from helmet.research.publish import (
    PROJECTION_MODIFIERS_POLICY_KEY,
    active_projection_modifiers,
    publish_findings,
    publish_promoted_modifiers,
    publish_warm_start,
)
from helmet.research.warm_start import FindingStatus, ResearchFinding, WarmStartReport

OWNER = "11111111-1111-4111-8111-111111111111"


def _report(**overrides: object) -> WarmStartReport:
    base = dict(
        model_version="2026.0.0",
        generated_at=datetime.now(UTC).isoformat(),
        training_seasons=(2024, 2025),
        target_season=2026,
        findings=(
            ResearchFinding(
                hypothesis="rb_350_touch_hangover",
                status=FindingStatus.SUPPORTED,
                effect_size=-0.4,
                sample_size=12,
                confidence=0.8,
                evidence="high-touch backs regressed the following season",
                required_sources=("nflverse.player_stats",),
            ),
        ),
        promoted_modifiers={"rb_350_touch_next_year_points_per_touch": -0.4},
    )
    base.update(overrides)
    return WarmStartReport(**base)  # type: ignore[arg-type]


@pytest.fixture
def db(tmp_path: Path) -> PersistenceContext:
    return PersistenceContext(
        client=LocalClient(tmp_path / "helmet.db"), owner_user_id=OWNER, backend="local"
    )


def test_publish_findings_writes_one_row_per_hypothesis(db: PersistenceContext) -> None:
    written = publish_findings(_report(), db)

    assert len(written) == 1
    assert written[0]["topic"] == "rb_350_touch_hangover"
    stored = ResearchFindingRepository(db.client, db.owner_user_id).list()
    assert len(stored) == 1


def test_publish_findings_is_idempotent_for_identical_findings(db: PersistenceContext) -> None:
    publish_findings(_report(), db)
    publish_findings(_report(), db)

    assert len(ResearchFindingRepository(db.client, db.owner_user_id).list()) == 1


def test_publish_promoted_modifiers_creates_an_active_version(db: PersistenceContext) -> None:
    activated = publish_promoted_modifiers(_report(), db)

    assert activated["status"] == "active"
    assert activated["definition"] == {"rb_350_touch_next_year_points_per_touch": -0.4}
    assert activated["version"] == 1


def test_publish_promoted_modifiers_retires_the_prior_active_version(
    db: PersistenceContext,
) -> None:
    publish_promoted_modifiers(_report(), db)

    second = publish_promoted_modifiers(
        _report(promoted_modifiers={"rb_350_touch_next_year_points_per_touch": -0.6}), db
    )

    versions = PolicyVersionRepository(db.client, db.owner_user_id).list(
        filters={"policy_key": PROJECTION_MODIFIERS_POLICY_KEY}
    )
    statuses = {row["id"]: row["status"] for row in versions}
    assert statuses[second["id"]] == "active"
    assert sum(1 for status in statuses.values() if status == "active") == 1
    assert second["version"] == 2


def test_publish_promoted_modifiers_reactivates_a_retired_version(db: PersistenceContext) -> None:
    first = publish_promoted_modifiers(_report(), db)
    publish_promoted_modifiers(
        _report(promoted_modifiers={"rb_350_touch_next_year_points_per_touch": -0.6}), db
    )

    reactivated = publish_promoted_modifiers(_report(), db)  # matches the original definition again

    assert reactivated["id"] == first["id"]
    assert reactivated["status"] == "active"
    versions = PolicyVersionRepository(db.client, db.owner_user_id).list(
        filters={"policy_key": PROJECTION_MODIFIERS_POLICY_KEY}
    )
    assert sum(1 for row in versions if row["status"] == "active") == 1


def test_active_projection_modifiers_returns_empty_when_nothing_promoted(
    db: PersistenceContext,
) -> None:
    assert active_projection_modifiers(db) == {}


def test_active_projection_modifiers_reflects_the_latest_publish(db: PersistenceContext) -> None:
    publish_warm_start(_report(), db)

    assert active_projection_modifiers(db) == {"rb_350_touch_next_year_points_per_touch": -0.4}
