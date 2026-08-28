from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from helmet.draft import precompute_all_draft_plans
from helmet.persistence import PersistenceContext
from helmet.repositories import LeagueRepository, LocalClient, canonical_content_hash

OWNER = "11111111-1111-4111-8111-111111111111"
ROSTER_POSITIONS = ("QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "BN", "BN", "BN")


@pytest.fixture
def db(tmp_path: Path) -> PersistenceContext:
    return PersistenceContext(
        client=LocalClient(tmp_path / "helmet.db"), owner_user_id=OWNER, backend="local"
    )


def _seed_league(db: PersistenceContext, *, external_id: str, total_rosters: int) -> dict:
    settings = {
        "total_rosters": total_rosters,
        "roster_positions": list(ROSTER_POSITIONS),
        "scoring_settings": {"rec": 1.0, "rush_yd": 0.1, "rec_yd": 0.1},
    }
    now = datetime.now(UTC).isoformat()
    return LeagueRepository(db.client, db.owner_user_id).create(
        {
            "source_system": "sleeper",
            "external_league_id": external_id,
            "name": f"League {external_id}",
            "season": 2026,
            "settings": settings,
            "observed_at": now,
            "effective_at": now,
            "content_hash": canonical_content_hash(settings),
        }
    )


def test_precompute_calls_generate_draft_plan_once_per_slot(db: PersistenceContext) -> None:
    league = _seed_league(db, external_id="league-a", total_rosters=4)

    def fake_generate(**kwargs):
        return {"plan": {"id": f"plan-{kwargs['my_slot']}"}, "created": True}

    with patch("helmet.draft.service.generate_draft_plan", side_effect=fake_generate) as mocked:
        results = precompute_all_draft_plans(db)

    assert mocked.call_count == 4
    assert [row["slot"] for row in results] == [1, 2, 3, 4]
    assert all(row["league_id"] == league["external_league_id"] for row in results)
    assert all(row["num_teams"] == 4 for row in results)

    first_call_kwargs = mocked.call_args_list[0].kwargs
    assert first_call_kwargs["num_teams"] == 4
    assert first_call_kwargs["rounds"] == len(ROSTER_POSITIONS)
    assert first_call_kwargs["starters_per_team"] == {"QB": 1, "RB": 3, "WR": 2, "TE": 1}
    assert first_call_kwargs["league_id"] == league["id"]


def test_precompute_covers_every_connected_league(db: PersistenceContext) -> None:
    _seed_league(db, external_id="league-a", total_rosters=2)
    _seed_league(db, external_id="league-b", total_rosters=3)

    def fake_generate(**kwargs):
        return {"plan": {"id": "plan"}, "created": False}

    with patch("helmet.draft.service.generate_draft_plan", side_effect=fake_generate) as mocked:
        results = precompute_all_draft_plans(db)

    assert mocked.call_count == 5  # 2 slots + 3 slots
    assert {row["league_id"] for row in results} == {"league-a", "league-b"}


def test_precompute_raises_for_a_league_missing_total_rosters(db: PersistenceContext) -> None:
    now = datetime.now(UTC).isoformat()
    settings = {"scoring_settings": {"rec": 1.0}}
    LeagueRepository(db.client, db.owner_user_id).create(
        {
            "source_system": "sleeper",
            "external_league_id": "legacy-league",
            "name": "Legacy League",
            "season": 2026,
            "settings": settings,
            "observed_at": now,
            "effective_at": now,
            "content_hash": canonical_content_hash(settings),
        }
    )

    with pytest.raises(KeyError):
        precompute_all_draft_plans(db)
