from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from helmet.api import create_app
from helmet.config import LOCAL_OWNER_USER_ID, Settings, get_settings
from helmet.persistence import open_persistence
from helmet.repositories import (
    IngestionRunRepository,
    LocalClient,
    RepositoryNotFoundError,
    RepositoryWriteError,
    SourceObservationRepository,
    canonical_content_hash,
)

OWNER = "11111111-1111-4111-8111-111111111111"
OTHER_OWNER = "22222222-2222-4222-8222-222222222222"


def _observation(entity_id: str, payload: dict[str, object]) -> dict[str, object]:
    now = datetime.now(UTC).isoformat()
    return {
        "source_system": "sleeper",
        "source_entity_type": "league_snapshot",
        "source_entity_id": entity_id,
        "payload": payload,
        "observed_at": now,
        "effective_at": now,
        "content_hash": canonical_content_hash(payload),
    }


@pytest.fixture
def client(tmp_path: Path) -> LocalClient:
    return LocalClient(tmp_path / "helmet.db")


def test_local_round_trip_populates_server_managed_fields(client: LocalClient) -> None:
    repository = SourceObservationRepository(client, OWNER)

    created = repository.create(_observation("league-1", {"name": "Helmet League"}))

    assert created["owner_user_id"] == OWNER
    assert created["created_at"] == created["updated_at"]
    assert repository.get(created["id"]) == created


def test_local_data_survives_reopening_the_database(tmp_path: Path) -> None:
    path = tmp_path / "helmet.db"
    created = SourceObservationRepository(LocalClient(path), OWNER).create(
        _observation("league-1", {"name": "Helmet League"})
    )

    reopened = SourceObservationRepository(LocalClient(path), OWNER)

    assert reopened.get(created["id"])["source_entity_id"] == "league-1"


def test_local_rows_are_scoped_to_their_owner(client: LocalClient) -> None:
    created = SourceObservationRepository(client, OWNER).create(
        _observation("league-1", {"name": "Helmet League"})
    )

    intruder = SourceObservationRepository(client, OTHER_OWNER)

    assert intruder.list() == []
    with pytest.raises(RepositoryNotFoundError):
        intruder.get(created["id"])


def test_local_enforces_unique_constraints(client: LocalClient) -> None:
    repository = IngestionRunRepository(client, OWNER)
    run = {
        "source_system": "sleeper",
        "run_type": "weekly",
        "idempotency_key": "a" * 64,
    }
    repository.create(run)

    with pytest.raises(RepositoryWriteError, match="unique constraint"):
        repository.create(run)


def test_local_update_advances_only_mutable_audit_fields(client: LocalClient) -> None:
    repository = IngestionRunRepository(client, OWNER)
    run = repository.create(
        {"source_system": "sleeper", "run_type": "weekly", "idempotency_key": "b" * 64}
    )

    updated = repository.mark_running(run["id"], started_at=datetime.now(UTC).isoformat())

    assert updated["status"] == "running"
    assert updated["created_at"] == run["created_at"]
    assert updated["updated_at"] >= run["updated_at"]


def test_local_list_orders_and_filters(client: LocalClient) -> None:
    repository = SourceObservationRepository(client, OWNER)
    first = repository.create(_observation("league-1", {"week": 1}))
    second = repository.create(_observation("league-2", {"week": 2}))

    newest_first = repository.list(order_by="created_at", descending=True)
    filtered = repository.list(filters={"source_entity_id": "league-1"})

    assert [row["id"] for row in newest_first] == [second["id"], first["id"]]
    assert [row["id"] for row in filtered] == [first["id"]]


def test_local_upsert_replaces_the_conflicting_row(client: LocalClient) -> None:
    repository = IngestionRunRepository(client, OWNER)
    values = {
        "source_system": "sleeper",
        "run_type": "weekly",
        "idempotency_key": "c" * 64,
        "status": "running",
    }
    repository.upsert(values, on_conflict="owner_user_id,idempotency_key")

    repository.upsert(
        {**values, "status": "succeeded"}, on_conflict="owner_user_id,idempotency_key"
    )

    rows = repository.list()
    assert len(rows) == 1
    assert rows[0]["status"] == "succeeded"


def test_open_persistence_defaults_to_local(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, local_database_path=tmp_path / "helmet.db")

    persistence = open_persistence(settings)

    assert persistence.backend == "local"
    assert persistence.owner_user_id == LOCAL_OWNER_USER_ID


def test_production_rejects_the_local_backend() -> None:
    with pytest.raises(ValueError, match="development only"):
        Settings(_env_file=None, environment="production", persistence_backend="local")


def test_api_serves_data_endpoints_in_local_mode(tmp_path: Path) -> None:
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: Settings(
        _env_file=None,
        persistence_backend="local",
        local_database_path=tmp_path / "helmet.db",
    )

    response = TestClient(app).get("/v1/players")

    assert response.status_code == 200
    assert response.json()["data"] == []


def test_api_reports_missing_supabase_when_that_backend_is_selected() -> None:
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: Settings(
        _env_file=None, persistence_backend="supabase"
    )

    response = TestClient(app).get("/v1/players")

    assert response.status_code == 503
    assert "Supabase is not configured" in response.json()["detail"]


def test_api_health_reports_the_active_backend() -> None:
    response = TestClient(create_app()).get("/health")

    assert response.json()["persistence_backend"] == get_settings().persistence_backend
