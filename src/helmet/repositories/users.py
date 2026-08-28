"""Repository for the authenticated user's application profile."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from .base import TableClient, TableRepository, TableSpec
from .errors import RepositoryWriteError


class UserRepository(TableRepository):
    def __init__(self, client: TableClient, owner_user_id: str | UUID) -> None:
        super().__init__(
            client,
            owner_user_id,
            TableSpec(
                "app_users",
                frozenset({"display_name", "timezone", "preferences"}),
            ),
        )

    def create_profile(
        self,
        *,
        display_name: str | None = None,
        timezone: str = "UTC",
        preferences: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        values = self._validate_write(
            {
                "display_name": display_name,
                "timezone": timezone,
                "preferences": {} if preferences is None else preferences,
            },
            creating=True,
        )
        payload = {
            "id": self.owner_user_id,
            "owner_user_id": self.owner_user_id,
            **values,
        }
        try:
            response = self._client.table(self.spec.name).insert(payload).execute()
        except Exception as exc:
            raise RepositoryWriteError(f"failed to create app user profile: {exc}") from exc
        return self._single_row(response, operation="insert", write=True)
