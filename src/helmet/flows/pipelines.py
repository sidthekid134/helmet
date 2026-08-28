"""Idempotent Prefect workflows for the fantasy lifecycle."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

import httpx
from prefect import flow, task
from prefect.tasks import task_input_hash

from helmet.analytics import (
    PlayerProjection,
    Recommendation,
    RosterSlot,
    ScoredProjection,
    ScoringSettings,
    chaos_response,
    optimize_lineup,
    waiver_rankings,
)
from helmet.config import get_settings
from helmet.integrations import SleeperClient
from helmet.persistence import open_persistence
from helmet.repositories import (
    IngestionRunRepository,
    LeagueRepository,
    SourceObservationRepository,
    canonical_content_hash,
)


def idempotency_key(source: str, league_id: str, season: int, period: str) -> str:
    if not all((source, league_id, period)):
        raise ValueError("idempotency key fields cannot be empty")
    raw = f"{source}:{league_id}:{season}:{period}".encode()
    return sha256(raw).hexdigest()


def _retry_transport(_task: Any, _run: Any, state: Any) -> bool:
    try:
        state.result()
    except Exception as exc:
        current: BaseException | None = exc
        while current is not None:
            if isinstance(current, httpx.TransportError):
                return True
            current = current.__cause__
    return False


@task(
    retries=2,
    retry_delay_seconds=[2, 10],
    retry_condition_fn=_retry_transport,
    cache_key_fn=task_input_hash,
)
async def fetch_league_snapshot(league_id: str, week: int | None = None) -> dict[str, Any]:
    settings = get_settings()
    observed = datetime.now(UTC)
    async with SleeperClient(
        base_url=str(settings.sleeper_base_url),
        requests_per_minute=settings.sleeper_requests_per_minute,
    ) as client:
        league = await client.league(league_id)
        rosters = await client.rosters(league_id)
        users = await client.league_users(league_id)
        payload: dict[str, Any] = {
            "league": league.model_dump(mode="json"),
            "rosters": [roster.model_dump(mode="json") for roster in rosters],
            "users": users,
        }
        if week is not None:
            payload["matchups"] = await client.matchups(league_id, week)
            payload["transactions"] = await client.transactions(league_id, week)
        period = (
            f"week-{week}-{observed:%Y-%m-%dT%H}"
            if week is not None
            else f"preseason-{observed:%Y-%m-%d}"
        )
        payload["idempotency_key"] = idempotency_key("sleeper", league_id, league.season, period)
        payload["observed_at"] = observed.isoformat()
        return payload


@task(retries=2, retry_delay_seconds=[1, 3], retry_condition_fn=_retry_transport)
async def fetch_draft_state(league_id: str) -> dict[str, Any]:
    settings = get_settings()
    async with SleeperClient(
        base_url=str(settings.sleeper_base_url),
        requests_per_minute=settings.sleeper_requests_per_minute,
    ) as client:
        league = await client.league(league_id)
        drafts = await client.league_drafts(league_id)
        if not drafts:
            raise ValueError(f"league {league_id} has no draft")
        draft = drafts[0]
        draft_id = str(draft["draft_id"])
        picks = await client.draft_picks(draft_id)
        return {
            "draft": draft,
            "picks": [pick.model_dump(mode="json") for pick in picks],
            "idempotency_key": idempotency_key(
                "sleeper", league_id, league.season, f"draft-{len(picks)}"
            ),
            "observed_at": datetime.now(UTC).isoformat(),
        }


@task
def persist_snapshot(
    payload: dict[str, Any],
    *,
    source_system: str,
    run_type: str,
    entity_type: str,
    entity_id: str,
) -> dict[str, Any]:
    """Persist one fetched snapshot and make successful reruns no-ops."""
    key = payload.get("idempotency_key")
    observed_at = payload.get("observed_at")
    if not isinstance(key, str) or not isinstance(observed_at, str):
        raise ValueError("snapshot requires idempotency_key and observed_at")
    persistence = open_persistence()
    client = persistence.client
    owner = persistence.owner_user_id
    runs = IngestionRunRepository(client, owner)
    existing = runs.list(filters={"idempotency_key": key}, limit=1)
    if existing and existing[0]["status"] == "succeeded":
        return {
            "run_id": existing[0]["id"],
            "status": "succeeded",
            "idempotent_replay": True,
        }
    if existing:
        run = runs.mark_running(existing[0]["id"], started_at=observed_at)
    else:
        run = runs.create(
            {
                "source_system": source_system,
                "run_type": run_type,
                "idempotency_key": key,
                "status": "running",
                "started_at": observed_at,
                "metadata": {"entity_type": entity_type, "entity_id": entity_id},
            }
        )
    written = 0
    try:
        SourceObservationRepository(client, owner).create(
            {
                "ingestion_run_id": run["id"],
                "source_system": source_system,
                "source_entity_type": entity_type,
                "source_entity_id": entity_id,
                "payload": payload,
                "source_url": "https://api.sleeper.app/v1",
                "observed_at": observed_at,
                "effective_at": observed_at,
                "content_hash": canonical_content_hash(payload),
            }
        )
        written += 1
        league_payload = payload.get("league")
        if isinstance(league_payload, dict):
            LeagueRepository(client, owner).create(
                {
                    "source_system": "sleeper",
                    "external_league_id": league_payload["league_id"],
                    "name": league_payload["name"],
                    "season": league_payload["season"],
                    "settings": league_payload,
                    "observed_at": observed_at,
                    "effective_at": observed_at,
                    "content_hash": canonical_content_hash(league_payload),
                }
            )
            written += 1
        completed_at = datetime.now(UTC).isoformat()
        runs.mark_succeeded(
            run["id"],
            completed_at=completed_at,
            records_seen=written,
            records_written=written,
        )
        return {"run_id": run["id"], "status": "succeeded", "idempotent_replay": False}
    except Exception as exc:
        runs.mark_failed(
            run["id"],
            completed_at=datetime.now(UTC).isoformat(),
            error_code=type(exc).__name__,
            error_detail={"message": str(exc)},
        )
        raise


@flow(name="daily-preseason-sync", log_prints=False)
async def daily_preseason_flow(league_id: str) -> dict[str, Any]:
    payload = await fetch_league_snapshot(league_id)
    persisted = persist_snapshot(
        payload,
        source_system="sleeper",
        run_type="preseason",
        entity_type="league_snapshot",
        entity_id=league_id,
    )
    return {**payload, "persistence": persisted}


@flow(name="active-draft-poll", log_prints=False)
async def active_draft_flow(league_id: str) -> dict[str, Any]:
    payload = await fetch_draft_state(league_id)
    persisted = persist_snapshot(
        payload,
        source_system="sleeper",
        run_type="draft",
        entity_type="draft_state",
        entity_id=league_id,
    )
    return {**payload, "persistence": persisted}


@flow(name="weekly-league-sync", log_prints=False)
async def weekly_sync_flow(league_id: str, week: int | None = None) -> dict[str, Any]:
    if week is None:
        settings = get_settings()
        async with SleeperClient(
            base_url=str(settings.sleeper_base_url),
            requests_per_minute=settings.sleeper_requests_per_minute,
        ) as client:
            state = await client.nfl_state()
        try:
            week = int(state["week"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Sleeper NFL state does not contain a valid week") from exc
    if week < 1:
        raise ValueError("week must be positive")
    payload = await fetch_league_snapshot(league_id, week)
    persisted = persist_snapshot(
        payload,
        source_system="sleeper",
        run_type="weekly",
        entity_type="league_snapshot",
        entity_id=f"{league_id}:{week}",
    )
    return {**payload, "persistence": persisted}


@flow(name="pre-waiver-analysis", log_prints=False)
def pre_waiver_flow(
    free_agents: list[ScoredProjection],
    roster: list[PlayerProjection],
    scoring: ScoringSettings,
) -> list[dict[str, Any]]:
    recommendations = waiver_rankings(free_agents, roster, scoring)
    return [asdict(item) for item in recommendations]


@flow(name="pre-kickoff-lineup", log_prints=False)
def pre_kickoff_flow(
    players: list[PlayerProjection],
    slots: list[RosterSlot],
    scoring: ScoringSettings,
    mode: str = "mean",
) -> dict[str, Any]:
    return asdict(optimize_lineup(players, slots, scoring, objective=mode))


@flow(name="post-week-scoring", log_prints=False)
def post_week_scoring_flow(
    projected: dict[str, float], actual: dict[str, float]
) -> list[dict[str, Any]]:
    if set(projected) != set(actual):
        raise ValueError("projected and actual player IDs must match exactly")
    return [
        {
            "player_id": player_id,
            "projected": float(projected[player_id]),
            "actual": float(actual[player_id]),
            "signed_error": float(actual[player_id]) - float(projected[player_id]),
            "absolute_error": abs(float(actual[player_id]) - float(projected[player_id])),
        }
        for player_id in sorted(projected)
    ]


@flow(name="injury-event-analysis", log_prints=False)
def injury_event_flow(
    unavailable_player_ids: list[str],
    current_recommendations: list[Recommendation],
) -> list[dict[str, Any]]:
    result = chaos_response(current_recommendations, unavailable_player_ids)
    return [asdict(item) for item in result]


def serialize_flow_result(value: Any) -> str:
    """Strict serializer used by deployment adapters."""
    return json.dumps(value, sort_keys=True, default=str)
