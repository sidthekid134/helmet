from datetime import UTC, datetime

import httpx
import pytest

from helmet.domain import Player, Roster, SourceObservation
from helmet.integrations import PlayerIdentityResolver, SleeperClient, SleeperError


def test_source_observation_hash_is_stable() -> None:
    observed = datetime(2026, 8, 26, tzinfo=UTC)
    first = SourceObservation(
        source_system="test",
        entity_type="player",
        entity_id="1",
        observed_at=observed,
        effective_at=observed,
        payload={"b": 2, "a": 1},
    )
    second = first.model_copy(update={"payload": {"a": 1, "b": 2}})
    assert first.content_hash == second.content_hash


def test_roster_rejects_unrostered_starter() -> None:
    with pytest.raises(ValueError, match="starters are not rostered"):
        Roster(roster_id=1, owner_id="u", player_ids=("1",), starter_ids=("2",))


def test_identity_resolver_requires_review_for_ambiguous_name() -> None:
    resolver = PlayerIdentityResolver(
        [
            Player(source_id="a", full_name="John Smith", position="WR", team="A"),
            Player(source_id="b", full_name="John Smith", position="WR", team="B"),
        ]
    )
    result = resolver.resolve(external_id="x", full_name="John Smith", position="WR", team=None)
    assert result.requires_review
    assert result.canonical_id is None


@pytest.mark.asyncio
async def test_sleeper_client_parses_league() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/league/123"
        return httpx.Response(
            200,
            json={
                "league_id": "123",
                "name": "Helmet League",
                "season": "2026",
                "status": "pre_draft",
                "total_rosters": 12,
                "roster_positions": ["QB", "RB"],
                "scoring_settings": {"rec": 1},
                "settings": {},
            },
        )

    client = SleeperClient(
        base_url="https://api.sleeper.app/v1",
        transport=httpx.MockTransport(handler),
    )
    try:
        league = await client.league("123")
    finally:
        await client.close()
    assert league.name == "Helmet League"
    assert league.scoring_settings == {"rec": 1.0}
    assert league.total_rosters == 12


@pytest.mark.asyncio
async def test_sleeper_client_rejects_invalid_payload() -> None:
    client = SleeperClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=[])))
    try:
        with pytest.raises(SleeperError, match="response must be an object"):
            await client.league("123")
    finally:
        await client.close()
