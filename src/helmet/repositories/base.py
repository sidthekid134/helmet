"""Validated, owner-scoped repository primitives shared by every backend."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from .errors import (
    RepositoryNotFoundError,
    RepositoryReadError,
    RepositoryValidationError,
    RepositoryWriteError,
)

SERVER_MANAGED_FIELDS = frozenset(
    {"id", "owner_user_id", "created_at", "created_by", "updated_at", "updated_by"}
)


class TableClient(Protocol):
    """The query surface shared by the Supabase and local SQLite backends."""

    def table(self, name: str) -> Any: ...


@dataclass(frozen=True, slots=True)
class TableSpec:
    name: str
    writable_fields: frozenset[str]
    required_create_fields: frozenset[str] = frozenset()
    temporal: bool = False

    def __post_init__(self) -> None:
        if not self.name or not self.name.replace("_", "").isalnum():
            raise ValueError(f"invalid table name: {self.name!r}")
        unknown_required = self.required_create_fields - self.writable_fields
        if unknown_required:
            raise ValueError(
                f"{self.name} required fields are not writable: {sorted(unknown_required)}"
            )


def canonical_content_hash(payload: Mapping[str, Any]) -> str:
    """Return a stable SHA-256 hash for a JSON-compatible mapping."""
    try:
        encoded = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise RepositoryValidationError("payload must be JSON serializable") from exc
    return hashlib.sha256(encoded).hexdigest()


class TableRepository:
    """A strict table repository with user scoping and validated writes."""

    def __init__(self, client: TableClient, owner_user_id: str | UUID, spec: TableSpec) -> None:
        if client is None:
            raise RepositoryValidationError("client is required")
        self._client = client
        self.owner_user_id = self._validate_uuid(owner_user_id, "owner_user_id")
        self.spec = spec

    def create(self, values: Mapping[str, Any]) -> dict[str, Any]:
        payload = self._validate_write(values, creating=True)
        payload["owner_user_id"] = self.owner_user_id
        try:
            response = self._client.table(self.spec.name).insert(payload).execute()
        except Exception as exc:
            raise RepositoryWriteError(f"failed to insert into {self.spec.name}: {exc}") from exc
        return self._single_row(response, operation="insert", write=True)

    def upsert(self, values: Mapping[str, Any], *, on_conflict: str) -> dict[str, Any]:
        if not on_conflict or any(
            field not in self.spec.writable_fields | SERVER_MANAGED_FIELDS
            for field in on_conflict.split(",")
        ):
            raise RepositoryValidationError(
                f"invalid conflict target for {self.spec.name}: {on_conflict!r}"
            )
        payload = self._validate_write(values, creating=True)
        payload["owner_user_id"] = self.owner_user_id
        try:
            response = (
                self._client.table(self.spec.name)
                .upsert(payload, on_conflict=on_conflict)
                .execute()
            )
        except Exception as exc:
            raise RepositoryWriteError(f"failed to upsert {self.spec.name}: {exc}") from exc
        return self._single_row(response, operation="upsert", write=True)

    def update(self, row_id: str | UUID, values: Mapping[str, Any]) -> dict[str, Any]:
        normalized_id = self._validate_uuid(row_id, "row_id")
        payload = self._validate_write(values, creating=False)
        if not payload:
            raise RepositoryValidationError("update requires at least one field")
        try:
            response = (
                self._client.table(self.spec.name)
                .update(payload)
                .eq("id", normalized_id)
                .eq("owner_user_id", self.owner_user_id)
                .execute()
            )
        except Exception as exc:
            raise RepositoryWriteError(
                f"failed to update {self.spec.name} {normalized_id}: {exc}"
            ) from exc
        return self._single_row(response, operation="update", write=True)

    def get(self, row_id: str | UUID) -> dict[str, Any]:
        normalized_id = self._validate_uuid(row_id, "row_id")
        try:
            response = (
                self._client.table(self.spec.name)
                .select("*")
                .eq("id", normalized_id)
                .eq("owner_user_id", self.owner_user_id)
                .limit(1)
                .execute()
            )
        except Exception as exc:
            raise RepositoryReadError(
                f"failed to read {self.spec.name} {normalized_id}: {exc}"
            ) from exc
        rows = self._response_data(response, "read")
        if not rows:
            raise RepositoryNotFoundError(f"{self.spec.name} row {normalized_id} was not found")
        if len(rows) != 1:
            raise RepositoryReadError(
                f"read of {self.spec.name} {normalized_id} returned multiple rows"
            )
        return dict(rows[0])

    def list(
        self,
        *,
        filters: Mapping[str, Any] | None = None,
        order_by: str = "created_at",
        descending: bool = True,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        if order_by not in self.spec.writable_fields | SERVER_MANAGED_FIELDS:
            raise RepositoryValidationError(f"invalid order field: {order_by!r}")
        if limit < 1 or limit > 1000:
            raise RepositoryValidationError("limit must be between 1 and 1000")
        if offset < 0:
            raise RepositoryValidationError("offset cannot be negative")

        try:
            query = (
                self._client.table(self.spec.name)
                .select("*")
                .eq("owner_user_id", self.owner_user_id)
            )
            for field, value in (filters or {}).items():
                if field not in self.spec.writable_fields | SERVER_MANAGED_FIELDS:
                    raise RepositoryValidationError(f"invalid filter field: {field!r}")
                query = query.eq(field, value)
            response = (
                query.order(order_by, desc=descending).range(offset, offset + limit - 1).execute()
            )
        except RepositoryValidationError:
            raise
        except Exception as exc:
            raise RepositoryReadError(f"failed to list {self.spec.name}: {exc}") from exc
        return [dict(row) for row in self._response_data(response, "list")]

    def _validate_write(self, values: Mapping[str, Any], *, creating: bool) -> dict[str, Any]:
        if not isinstance(values, Mapping):
            raise RepositoryValidationError("write values must be a mapping")
        payload = dict(values)
        forbidden = payload.keys() & SERVER_MANAGED_FIELDS
        if forbidden:
            raise RepositoryValidationError(
                f"server-managed fields cannot be written: {sorted(forbidden)}"
            )
        unknown = payload.keys() - self.spec.writable_fields
        if unknown:
            raise RepositoryValidationError(
                f"unknown fields for {self.spec.name}: {sorted(unknown)}"
            )
        if creating:
            missing = self.spec.required_create_fields - payload.keys()
            if missing:
                raise RepositoryValidationError(
                    f"missing required fields for {self.spec.name}: {sorted(missing)}"
                )
        if self.spec.temporal:
            self._validate_temporal(payload, creating=creating)
        try:
            json.dumps(payload)
        except (TypeError, ValueError) as exc:
            raise RepositoryValidationError("write values must be JSON serializable") from exc
        return payload

    @staticmethod
    def _validate_temporal(payload: Mapping[str, Any], *, creating: bool) -> None:
        temporal_fields = {"observed_at", "effective_at", "content_hash"}
        if creating:
            missing = temporal_fields - payload.keys()
            if missing:
                raise RepositoryValidationError(f"missing temporal fields: {sorted(missing)}")
        for field in ("observed_at", "effective_at"):
            if field not in payload:
                continue
            value = payload[field]
            if not isinstance(value, str):
                raise RepositoryValidationError(f"{field} must be an ISO-8601 string")
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError as exc:
                raise RepositoryValidationError(f"{field} must be an ISO-8601 string") from exc
            if parsed.tzinfo is None:
                raise RepositoryValidationError(f"{field} must include a timezone")
        if "content_hash" in payload:
            content_hash = payload["content_hash"]
            if (
                not isinstance(content_hash, str)
                or len(content_hash) != 64
                or any(character not in "0123456789abcdef" for character in content_hash)
            ):
                raise RepositoryValidationError(
                    "content_hash must be a lowercase SHA-256 hex digest"
                )

    @staticmethod
    def _validate_uuid(value: str | UUID, field: str) -> str:
        try:
            return str(UUID(str(value)))
        except (ValueError, TypeError, AttributeError) as exc:
            raise RepositoryValidationError(f"{field} must be a UUID") from exc

    def _single_row(self, response: Any, *, operation: str, write: bool) -> dict[str, Any]:
        rows = self._response_data(response, operation)
        error_type = RepositoryWriteError if write else RepositoryReadError
        if len(rows) != 1:
            raise error_type(
                f"{operation} on {self.spec.name} returned {len(rows)} rows; expected one"
            )
        return dict(rows[0])

    def _response_data(self, response: Any, operation: str) -> Sequence[Mapping[str, Any]]:
        data = getattr(response, "data", None)
        if not isinstance(data, list) or any(not isinstance(row, Mapping) for row in data):
            error_type = (
                RepositoryWriteError
                if operation in {"insert", "upsert", "update"}
                else RepositoryReadError
            )
            raise error_type(f"{operation} on {self.spec.name} returned an invalid response")
        return data
