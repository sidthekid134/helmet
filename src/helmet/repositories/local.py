"""SQLite persistence that satisfies the same table contract as Supabase.

This backend exists so Helmet can run locally before a Supabase project is
provisioned. It implements the small query surface the repositories use and
enforces the invariants the migration relies on: owner scoping, immutable audit
columns, and the unique keys that make ingestion idempotent.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

IMMUTABLE_FIELDS = frozenset({"id", "owner_user_id", "created_at", "created_by"})

# Mirrors the unique constraints in supabase/migrations. Postgres treats NULLs as
# distinct, so a constraint is skipped when any of its fields is absent or null.
UNIQUE_CONSTRAINTS: dict[str, tuple[tuple[str, ...], ...]] = {
    "app_users": (("id",),),
    "ingestion_runs": (("owner_user_id", "idempotency_key"),),
    "source_observations": (
        (
            "owner_user_id",
            "source_system",
            "source_entity_type",
            "source_entity_id",
            "effective_at",
            "content_hash",
        ),
    ),
    "player_identities": (("owner_user_id", "normalized_name", "effective_at", "content_hash"),),
    "player_external_ids": (
        ("owner_user_id", "source_system", "external_player_id", "valid_from"),
    ),
    "leagues": (
        ("owner_user_id", "source_system", "external_league_id", "season", "effective_at"),
    ),
    "league_members": (("owner_user_id", "league_id", "external_manager_id", "effective_at"),),
    "rosters": (("owner_user_id", "league_member_id", "season"),),
    "roster_snapshots": (("owner_user_id", "roster_id", "week", "effective_at", "content_hash"),),
    "roster_snapshot_players": (("roster_snapshot_id", "player_identity_id"),),
    "drafts": (("owner_user_id", "league_id", "external_draft_id", "effective_at"),),
    "draft_picks": (
        ("draft_id", "overall_pick"),
        ("draft_id", "player_identity_id"),
    ),
    "transactions": (("owner_user_id", "league_id", "external_transaction_id", "content_hash"),),
    "transaction_players": (("transaction_id", "player_identity_id", "action"),),
    "weekly_player_stats": (
        (
            "owner_user_id",
            "player_identity_id",
            "source_system",
            "season",
            "week",
            "effective_at",
            "content_hash",
        ),
    ),
    "injuries": (
        ("owner_user_id", "player_identity_id", "source_system", "effective_at", "content_hash"),
    ),
    "projections": (
        (
            "owner_user_id",
            "player_identity_id",
            "league_id",
            "source_system",
            "season",
            "week",
            "effective_at",
            "content_hash",
        ),
    ),
    "recommendation_outcomes": (
        ("owner_user_id", "recommendation_id", "outcome_type", "effective_at", "content_hash"),
    ),
    "error_patterns": (("owner_user_id", "pattern_key", "effective_at", "content_hash"),),
    "manager_profiles": (("owner_user_id", "league_member_id", "effective_at", "content_hash"),),
    "policy_versions": (
        ("owner_user_id", "policy_key", "version"),
        ("owner_user_id", "policy_key", "content_hash"),
    ),
    "policy_promotions": (("owner_user_id", "policy_version_id", "promoted_at"),),
    "draft_plans": (("owner_user_id", "content_hash"),),
    "draft_plan_nodes": (("plan_id", "node_key"),),
    "draft_plan_candidates": (("parent_node_id", "player_id"),),
}

_SCHEMA = """
create table if not exists records (
    table_name text not null,
    id text not null,
    owner_user_id text not null,
    created_at text not null,
    updated_at text not null,
    data text not null,
    primary key (table_name, id)
);
create index if not exists records_owner_idx on records (table_name, owner_user_id);
"""


class LocalIntegrityError(RuntimeError):
    """A local write violated a schema invariant."""


@dataclass(frozen=True, slots=True)
class LocalResponse:
    """Mirrors the ``data`` attribute the repositories read from Supabase."""

    data: list[dict[str, Any]]


class LocalClient:
    """A ``table``-oriented client backed by a single SQLite database."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = str(database_path)
        in_memory = self.database_path == ":memory:"
        if not in_memory:
            Path(self.database_path).expanduser().parent.mkdir(parents=True, exist_ok=True)
            self.database_path = str(Path(self.database_path).expanduser())
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(self.database_path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        with self._lock:
            if not in_memory:
                self._connection.execute("pragma journal_mode=WAL")
            self._connection.executescript(_SCHEMA)
            self._connection.commit()

    def table(self, name: str) -> LocalQuery:
        if not name or not name.replace("_", "").isalnum():
            raise LocalIntegrityError(f"invalid table name: {name!r}")
        return LocalQuery(self, name)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Hold the connection so a read-then-write stays atomic."""
        with self._lock:
            yield

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def _rows(self, table: str) -> list[dict[str, Any]]:
        with self._lock:
            cursor = self._connection.execute(
                "select data from records where table_name = ? order by created_at, id",
                (table,),
            )
            return [json.loads(row["data"]) for row in cursor.fetchall()]

    def _write(self, table: str, rows: Sequence[Mapping[str, Any]]) -> None:
        with self._lock:
            self._connection.executemany(
                """
                insert into records (table_name, id, owner_user_id, created_at, updated_at, data)
                values (?, ?, ?, ?, ?, ?)
                on conflict (table_name, id) do update set
                    updated_at = excluded.updated_at,
                    data = excluded.data
                """,
                [
                    (
                        table,
                        row["id"],
                        row["owner_user_id"],
                        row["created_at"],
                        row["updated_at"],
                        json.dumps(row, sort_keys=True),
                    )
                    for row in rows
                ],
            )
            self._connection.commit()


class LocalQuery:
    """Builds and runs one operation against a local table."""

    def __init__(self, client: LocalClient, table: str) -> None:
        self._client = client
        self._table = table
        self._operation: str | None = None
        self._payload: dict[str, Any] | None = None
        self._on_conflict: tuple[str, ...] = ()
        self._filters: list[tuple[str, Any]] = []
        self._order: tuple[str, bool] | None = None
        self._limit: int | None = None
        self._range: tuple[int, int] | None = None

    def insert(self, payload: Mapping[str, Any]) -> LocalQuery:
        return self._start("insert", payload)

    def upsert(self, payload: Mapping[str, Any], on_conflict: str | None = None) -> LocalQuery:
        query = self._start("upsert", payload)
        query._on_conflict = tuple(
            field.strip() for field in (on_conflict or "").split(",") if field.strip()
        )
        if not query._on_conflict:
            raise LocalIntegrityError("upsert requires a conflict target")
        return query

    def update(self, payload: Mapping[str, Any]) -> LocalQuery:
        return self._start("update", payload)

    def select(self, _columns: str = "*") -> LocalQuery:
        self._operation = "select"
        return self

    def eq(self, field: str, value: Any) -> LocalQuery:
        self._filters.append((field, value))
        return self

    def order(self, field: str, desc: bool = False) -> LocalQuery:
        self._order = (field, desc)
        return self

    def limit(self, count: int) -> LocalQuery:
        self._limit = count
        return self

    def range(self, start: int, end: int) -> LocalQuery:
        self._range = (start, end)
        return self

    def execute(self) -> LocalResponse:
        if self._operation == "select":
            return LocalResponse(self._read())
        with self._client.transaction():
            if self._operation == "insert":
                return LocalResponse([self._insert()])
            if self._operation == "upsert":
                return LocalResponse([self._upsert()])
            if self._operation == "update":
                return LocalResponse(self._update())
        raise LocalIntegrityError("no operation was requested")

    def _start(self, operation: str, payload: Mapping[str, Any]) -> LocalQuery:
        if not isinstance(payload, Mapping):
            raise LocalIntegrityError(f"{operation} payload must be a mapping")
        self._operation = operation
        self._payload = dict(payload)
        return self

    def _read(self) -> list[dict[str, Any]]:
        rows = [row for row in self._client._rows(self._table) if self._matches(row)]
        if self._order is not None:
            field, desc = self._order
            rows.sort(key=lambda row: _sort_key(row.get(field)))
            if desc:
                rows.reverse()
        if self._range is not None:
            start, end = self._range
            rows = rows[start : end + 1]
        if self._limit is not None:
            rows = rows[: self._limit]
        return rows

    def _insert(self) -> dict[str, Any]:
        payload = dict(self._payload or {})
        owner = payload.get("owner_user_id")
        if not owner:
            raise LocalIntegrityError(f"{self._table} rows require owner_user_id")
        now = datetime.now(UTC).isoformat()
        row = {
            "id": str(payload.pop("id", None) or uuid4()),
            "owner_user_id": owner,
            "created_at": now,
            "created_by": owner,
            "updated_at": now,
            "updated_by": owner,
            **payload,
        }
        self._assert_unique(row)
        self._client._write(self._table, [row])
        return row

    def _upsert(self) -> dict[str, Any]:
        payload = dict(self._payload or {})
        conflict_values = {field: payload.get(field) for field in self._on_conflict}
        if any(value is None for value in conflict_values.values()):
            raise LocalIntegrityError(
                f"upsert on {self._table} requires values for {list(self._on_conflict)}"
            )
        existing = [
            row
            for row in self._client._rows(self._table)
            if all(row.get(field) == value for field, value in conflict_values.items())
        ]
        if not existing:
            return self._insert()
        if len(existing) > 1:
            raise LocalIntegrityError(
                f"upsert on {self._table} matched {len(existing)} rows; expected one"
            )
        target = existing[0]
        updates = {key: value for key, value in payload.items() if key not in IMMUTABLE_FIELDS}
        row = {
            **target,
            **updates,
            "updated_at": datetime.now(UTC).isoformat(),
            "updated_by": target["owner_user_id"],
        }
        self._assert_unique(row)
        self._client._write(self._table, [row])
        return row

    def _update(self) -> list[dict[str, Any]]:
        payload = dict(self._payload or {})
        forbidden = payload.keys() & IMMUTABLE_FIELDS
        if forbidden:
            raise LocalIntegrityError(
                f"immutable audit columns cannot be changed: {sorted(forbidden)}"
            )
        if not self._filters:
            raise LocalIntegrityError(f"update on {self._table} requires a filter")
        now = datetime.now(UTC).isoformat()
        updated = []
        for row in self._client._rows(self._table):
            if not self._matches(row):
                continue
            candidate = {
                **row,
                **payload,
                "updated_at": now,
                "updated_by": row["owner_user_id"],
            }
            self._assert_unique(candidate)
            updated.append(candidate)
        if updated:
            self._client._write(self._table, updated)
        return updated

    def _matches(self, row: Mapping[str, Any]) -> bool:
        return all(row.get(field) == value for field, value in self._filters)

    def _assert_unique(self, candidate: Mapping[str, Any]) -> None:
        constraints = UNIQUE_CONSTRAINTS.get(self._table, ())
        if not constraints:
            return
        rows = [row for row in self._client._rows(self._table) if row["id"] != candidate["id"]]
        for fields in constraints:
            values = {field: candidate.get(field) for field in fields}
            if any(value is None for value in values.values()):
                continue
            for row in rows:
                if all(row.get(field) == value for field, value in values.items()):
                    raise LocalIntegrityError(
                        f"{self._table} unique constraint {list(fields)} already holds "
                        f"{list(values.values())}"
                    )


def _sort_key(value: Any) -> tuple[int, Any]:
    """Rank by type first so mixed-type columns stay comparable."""
    if value is None:
        return (0, 0)
    if isinstance(value, bool):
        return (1, int(value))
    if isinstance(value, int | float):
        return (1, value)
    if isinstance(value, str):
        return (2, value)
    return (3, json.dumps(value, sort_keys=True))
