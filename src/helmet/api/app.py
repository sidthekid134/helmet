"""FastAPI surface consumed by Helmet's Next.js dashboard."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from helmet.agents import GroundedChatService
from helmet.config import Settings, get_settings
from helmet.draft import (
    BranchPolicy,
    derive_draft_shape,
    generate_draft_plan,
    live_pick_recommendations,
)
from helmet.integrations import SleeperClient, SleeperError
from helmet.integrations.nflverse import NflverseError
from helmet.persistence import PersistenceContext, open_persistence
from helmet.projections import ProjectionSettings
from helmet.repositories import (
    DraftPlanCandidateRepository,
    DraftPlanNodeRepository,
    DraftPlanRepository,
    ErrorAttributionRepository,
    IngestionRunRepository,
    LeagueRepository,
    PlayerIdentityRepository,
    RecommendationRepository,
    ResearchFindingRepository,
    canonical_content_hash,
)
from helmet.repositories.errors import (
    RepositoryError,
    RepositoryNotFoundError,
    RepositoryValidationError,
)


class ConnectLeagueInput(BaseModel):
    provider: str = Field(pattern="^sleeper$")
    league_id: str = Field(min_length=1)
    season: int = Field(ge=2000, le=2100)


class ChatInput(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


class DraftPlanInput(BaseModel):
    league_id: str = Field(min_length=1)
    my_slot: int = Field(ge=1)
    num_teams: int | None = Field(default=None, ge=2, le=32)
    rounds: int | None = Field(default=None, ge=1, le=40)
    roster_targets: dict[str, int] | None = None
    starters_per_team: dict[str, int] | None = None
    seed: int = 20260827
    simulation_iterations: int = Field(default=150, ge=10, le=2000)


class LiveDraftRecommendationsInput(BaseModel):
    my_roster_player_ids: list[str] = Field(default_factory=list)
    taken_by_others_player_ids: list[str] = Field(default_factory=list)
    limit: int = Field(default=80, ge=1, le=200)


def envelope(data: Any, *, league_id: str | None = None) -> dict[str, Any]:
    return {
        "data": data,
        "meta": {
            "generated_at": datetime.now(UTC).isoformat(),
            **({"league_id": league_id} if league_id else {}),
        },
    }


def database(settings: Annotated[Settings, Depends(get_settings)]) -> PersistenceContext:
    try:
        return open_persistence(settings)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


Db = Annotated[PersistenceContext, Depends(database)]


def _list(repository_type: type, db: PersistenceContext, **kwargs: Any) -> list[dict[str, Any]]:
    try:
        return repository_type(db.client, db.owner_user_id).list(**kwargs)
    except RepositoryError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


def _recommendations(db: PersistenceContext, kind: str) -> list[dict[str, Any]]:
    return _list(
        RecommendationRepository,
        db,
        filters={"recommendation_type": kind},
        limit=100,
    )


def _connection_payload(row: dict[str, Any]) -> dict[str, Any]:
    settings = row.get("settings") or {}
    payload: dict[str, Any] = {
        "id": row["external_league_id"],
        "provider": row["source_system"],
        "name": row["name"],
        "season": row["season"],
        "status": "connected",
        "total_rosters": settings.get("total_rosters"),
    }
    roster_positions = settings.get("roster_positions")
    if roster_positions:
        # Legacy rows connected before Helmet tracked total_rosters/roster
        # shape simply don't get a default draft shape -- reconnecting the
        # league (or seeding a fresh test league) picks it up.
        shape = derive_draft_shape(roster_positions)
        payload["default_rounds"] = shape.rounds
        payload["default_roster_targets"] = shape.roster_targets
        payload["default_starters_per_team"] = shape.starters_per_team
    return payload


def _plan_summary(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "league_id": row.get("league_id"),
        "num_teams": row["num_teams"],
        "my_slot": row["my_slot"],
        "rounds": row["rounds"],
        "node_count": row["node_count"],
        "status": row["status"],
        "seed": row["seed"],
        "generated_at": row["observed_at"],
    }


def _node_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "parent_id": row.get("parent_node_id"),
        "depth": row["depth"],
        "overall_pick": row.get("overall_pick"),
        "round": row.get("round"),
        "chosen_player": (
            {
                "id": row["chosen_player_id"],
                "name": row["chosen_player_name"],
                "team": row["chosen_player_team"],
                "position": row["chosen_player_position"],
            }
            if row.get("chosen_player_id")
            else None
        ),
        "chosen_archetype": row.get("chosen_archetype"),
        "reach_probability": row["reach_probability"],
        "roster_player_ids": row["roster_player_ids"],
        "ev": row["ev"],
        "ev_floor": row["ev_floor"],
        "ev_ceiling": row["ev_ceiling"],
        "rationale": row.get("rationale") or [],
    }


def _candidate_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "player": {
            "id": row["player_id"],
            "name": row["player_name"],
            "team": row["player_team"],
            "position": row["player_position"],
        },
        "archetype": row.get("archetype"),
        "survival_probability": row["survival_probability"],
        "marginal_value": row["marginal_value"],
        "rank": row["rank"],
        "expanded": row["expanded"],
        "child_node_id": row.get("child_node_id"),
    }


def _get_root_node(
    node_repo: DraftPlanNodeRepository, plan_id: str
) -> dict[str, Any]:
    rows = node_repo.list(filters={"plan_id": plan_id, "node_key": "root"}, limit=1)
    if not rows:
        raise HTTPException(status_code=404, detail=f"draft plan {plan_id} has no root node")
    return rows[0]


def create_app() -> FastAPI:
    settings = get_settings()
    api = FastAPI(title="Helmet API", version="0.1.0")
    api.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    @api.get("/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "service": "helmet-api",
            "persistence_backend": settings.persistence_backend,
        }

    @api.get("/v1/connections")
    def connections(db: Db) -> dict[str, Any]:
        rows = _list(LeagueRepository, db, limit=20)
        return envelope([_connection_payload(row) for row in rows])

    @api.post("/v1/connections")
    async def connect_league(body: ConnectLeagueInput, db: Db) -> dict[str, Any]:
        sleeper_settings = get_settings()
        try:
            async with SleeperClient(
                base_url=str(sleeper_settings.sleeper_base_url),
                requests_per_minute=sleeper_settings.sleeper_requests_per_minute,
            ) as client:
                league = await client.league(body.league_id)
            if league.season != body.season:
                raise HTTPException(
                    status_code=422,
                    detail=f"Sleeper league season is {league.season}, not {body.season}",
                )
            now = datetime.now(UTC).isoformat()
            payload = league.model_dump(mode="json")
            LeagueRepository(db.client, db.owner_user_id).create(
                {
                    "source_system": "sleeper",
                    "external_league_id": league.league_id,
                    "name": league.name,
                    "season": league.season,
                    "settings": payload,
                    "observed_at": now,
                    "effective_at": now,
                    "content_hash": canonical_content_hash(payload),
                }
            )
        except SleeperError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except RepositoryError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return envelope(
            {
                "id": league.league_id,
                "provider": "sleeper",
                "name": league.name,
                "season": league.season,
                "status": "connected",
            }
        )

    @api.get("/v1/research")
    def research(db: Db) -> dict[str, Any]:
        rows = _list(ResearchFindingRepository, db, limit=100)
        return envelope(
            [
                {
                    "id": row["id"],
                    "title": row["topic"],
                    "summary": row["claim"],
                    "source_count": len(row.get("evidence") or []),
                    "updated_at": row["observed_at"],
                    "tags": [row.get("confidence")],
                }
                for row in rows
            ]
        )

    @api.get("/v1/sources/health")
    def sources_health(db: Db) -> dict[str, Any]:
        rows = _list(IngestionRunRepository, db, limit=100)
        latest: dict[str, dict[str, Any]] = {}
        for row in rows:
            latest.setdefault(row["source_system"], row)
        return envelope(
            [
                {
                    "id": source,
                    "name": source,
                    "status": (
                        "healthy"
                        if row["status"] == "succeeded"
                        else "degraded"
                        if row["status"] in {"running", "partial"}
                        else "unavailable"
                    ),
                    "last_synced_at": row.get("completed_at"),
                    "records": row.get("records_written"),
                    "message": row.get("error_code"),
                }
                for source, row in latest.items()
            ]
        )

    @api.get("/v1/players")
    def players(db: Db) -> dict[str, Any]:
        rows = _list(PlayerIdentityRepository, db, limit=1000)
        return envelope(
            [
                {
                    "id": row["id"],
                    "name": row["canonical_name"],
                    "team": row.get("team_code") or "FA",
                    "position": row.get("position") or "UNK",
                    "status": "active" if row.get("active") else "inactive",
                }
                for row in rows
            ]
        )

    @api.get("/v1/draft")
    def draft(db: Db) -> dict[str, Any]:
        rows = _recommendations(db, "draft")
        return envelope(
            {
                "status": "active" if rows else "scheduled",
                "picks": [],
                "recommendations": [
                    _subject_player(row["subject"], row.get("score")) for row in rows
                ],
            }
        )

    @api.post("/v1/draft/plan")
    def create_draft_plan(body: DraftPlanInput, db: Db) -> dict[str, Any]:
        league_rows = LeagueRepository(
            db.client, db.owner_user_id
        ).list(filters={"external_league_id": body.league_id}, limit=1)
        if not league_rows:
            raise HTTPException(
                status_code=404, detail=f"league {body.league_id} is not connected"
            )
        league_row = league_rows[0]
        settings = league_row["settings"]
        season = league_row["season"]
        shape = derive_draft_shape(settings["roster_positions"])
        try:
            result = generate_draft_plan(
                db=db,
                sleeper_scoring_settings=settings["scoring_settings"],
                num_teams=body.num_teams or int(settings["total_rosters"]),
                my_slot=body.my_slot,
                rounds=body.rounds or shape.rounds,
                roster_targets=body.roster_targets or shape.roster_targets,
                starters_per_team=body.starters_per_team or shape.starters_per_team,
                projection_settings=ProjectionSettings(
                    target_season=season, lookback_seasons=tuple(range(season - 2, season))
                ),
                branch_policy=BranchPolicy(
                    individual_rounds=3,
                    top_k_by_round={1: 8, 2: 5, 3: 4},
                    default_top_k=3,
                    beam_width=16,
                    menu_size=10,
                ),
                league_id=league_row["id"],
                seed=body.seed,
                simulation_iterations=body.simulation_iterations,
            )
        except (ValueError, NflverseError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except RepositoryError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        node_repo = DraftPlanNodeRepository(db.client, db.owner_user_id)
        candidate_repo = DraftPlanCandidateRepository(db.client, db.owner_user_id)
        root = _get_root_node(node_repo, result["plan"]["id"])
        candidates = candidate_repo.list(
            filters={"plan_id": result["plan"]["id"], "parent_node_id": root["id"]}, limit=100
        )
        return envelope(
            {
                "plan": _plan_summary(result["plan"]),
                "created": result["created"],
                "node": _node_payload(root),
                "candidates": [
                    _candidate_payload(row) for row in sorted(candidates, key=lambda r: r["rank"])
                ],
            },
            league_id=body.league_id,
        )

    @api.post("/v1/draft/plan/{plan_id}/live-recommendations")
    def live_draft_recommendations_endpoint(
        plan_id: str, body: LiveDraftRecommendationsInput, db: Db
    ) -> dict[str, Any]:
        try:
            result = live_pick_recommendations(
                db=db,
                plan_id=plan_id,
                my_roster_player_ids=body.my_roster_player_ids,
                taken_by_others_player_ids=body.taken_by_others_player_ids,
                limit=body.limit,
            )
        except (RepositoryNotFoundError, RepositoryValidationError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (ValueError, NflverseError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except RepositoryError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return envelope(result)

    @api.get("/v1/draft/plan/{plan_id}")
    def get_draft_plan(plan_id: str, db: Db) -> dict[str, Any]:
        plans = DraftPlanRepository(db.client, db.owner_user_id)
        try:
            plan = plans.get(plan_id)
        except (RepositoryNotFoundError, RepositoryValidationError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        node_repo = DraftPlanNodeRepository(db.client, db.owner_user_id)
        candidate_repo = DraftPlanCandidateRepository(db.client, db.owner_user_id)
        root = _get_root_node(node_repo, plan_id)
        candidates = candidate_repo.list(
            filters={"plan_id": plan_id, "parent_node_id": root["id"]}, limit=100
        )
        return envelope(
            {
                "plan": _plan_summary(plan),
                "node": _node_payload(root),
                "candidates": [
                    _candidate_payload(row) for row in sorted(candidates, key=lambda r: r["rank"])
                ],
            }
        )

    @api.get("/v1/draft/plan/{plan_id}/nodes/{node_id}")
    def get_draft_plan_node(plan_id: str, node_id: str, db: Db) -> dict[str, Any]:
        node_repo = DraftPlanNodeRepository(db.client, db.owner_user_id)
        candidate_repo = DraftPlanCandidateRepository(db.client, db.owner_user_id)
        node = _get_root_node(node_repo, plan_id) if node_id == "root" else None
        if node is None:
            try:
                node = node_repo.get(node_id)
            except (RepositoryNotFoundError, RepositoryValidationError) as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            if node["plan_id"] != plan_id:
                raise HTTPException(
                    status_code=404, detail=f"node {node_id} does not belong to plan {plan_id}"
                )
        candidates = candidate_repo.list(
            filters={"plan_id": plan_id, "parent_node_id": node["id"]}, limit=100
        )
        return envelope(
            {
                "node": _node_payload(node),
                "candidates": [
                    _candidate_payload(row) for row in sorted(candidates, key=lambda r: r["rank"])
                ],
            }
        )

    @api.get("/v1/lineup")
    def lineup(db: Db, week: int = Query(default=1, ge=1, le=22)) -> dict[str, Any]:
        rows = _recommendations(db, "lineup")
        slots = [
            {
                "slot": row["subject"].get("slot", "FLEX"),
                "player": _subject_player(row["subject"], row.get("score")),
                "locked": bool(row["subject"].get("locked", False)),
            }
            for row in rows
        ]
        return envelope({"week": week, "slots": slots})

    @api.get("/v1/waivers")
    def waivers(db: Db) -> dict[str, Any]:
        return envelope(
            [
                {
                    "player": _subject_player(row["subject"], row.get("score")),
                    "priority": index,
                    "faab_bid": row["subject"].get("faab_bid"),
                    "rationale": row["rationale"],
                }
                for index, row in enumerate(_recommendations(db, "waiver"), start=1)
            ]
        )

    @api.get("/v1/trades")
    def trades(db: Db) -> dict[str, Any]:
        return envelope(
            [
                {
                    "id": row["id"],
                    "status": "proposed",
                    "partner": row["subject"].get("partner", "Unknown manager"),
                    "giving": row["subject"].get("giving", []),
                    "receiving": row["subject"].get("receiving", []),
                    "value_delta": row.get("score"),
                }
                for row in _recommendations(db, "trade")
            ]
        )

    @api.get("/v1/alerts")
    def alerts(db: Db) -> dict[str, Any]:
        return envelope(
            [
                {
                    "id": row["id"],
                    "title": row["subject"].get("title", "Roster alert"),
                    "body": row["rationale"],
                    "severity": row["subject"].get("severity", "warning"),
                    "created_at": row["observed_at"],
                    "read": False,
                }
                for row in _recommendations(db, "chaos")
            ]
        )

    @api.get("/v1/learning/reviews")
    def learning_reviews(db: Db) -> dict[str, Any]:
        rows = _list(ErrorAttributionRepository, db, limit=100)
        return envelope(
            [
                {
                    "id": row["id"],
                    "period": row["effective_at"],
                    "title": row["component"],
                    "summary": row.get("root_cause"),
                    "lessons": [
                        item.get("claim", str(item)) for item in (row.get("evidence") or [])
                    ],
                }
                for row in rows
            ]
        )

    @api.get("/v1/chat/messages")
    def chat_history() -> dict[str, Any]:
        return envelope([])

    @api.post("/v1/chat/messages")
    async def send_chat(body: ChatInput, db: Db) -> dict[str, Any]:
        records = {
            "research": _list(ResearchFindingRepository, db, limit=20),
            "recommendations": _list(RecommendationRepository, db, limit=20),
        }
        chat_settings = get_settings()
        try:
            service = GroundedChatService(
                api_key=chat_settings.require_anthropic_key(),
                model=chat_settings.anthropic_model,
            )
            content = await service.answer(body.message, context=records)
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        now = datetime.now(UTC).isoformat()
        return envelope(
            {
                "conversation_id": str(uuid4()),
                "message": {
                    "id": str(uuid4()),
                    "role": "assistant",
                    "content": content,
                    "created_at": now,
                },
            }
        )

    return api


def _subject_player(subject: dict[str, Any], score: float | None) -> dict[str, Any]:
    if not isinstance(subject, dict):
        raise HTTPException(status_code=500, detail="invalid persisted recommendation subject")
    required = {"player_id", "name", "position"}
    missing = required - subject.keys()
    if missing:
        raise HTTPException(
            status_code=500,
            detail=f"recommendation subject missing fields: {sorted(missing)}",
        )
    return {
        "id": str(subject["player_id"]),
        "name": str(subject["name"]),
        "team": str(subject.get("team") or "FA"),
        "position": str(subject["position"]),
        "value": score,
        "projection": subject.get("projection"),
        "status": subject.get("status"),
        "opponent": subject.get("opponent"),
    }


app = create_app()
