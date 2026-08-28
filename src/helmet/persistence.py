"""Backend selection for Helmet's persistence layer."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from helmet.config import Settings, get_settings
from helmet.repositories.local import LocalClient


@dataclass(frozen=True, slots=True)
class PersistenceContext:
    """A ready table client plus the owner every query is scoped to."""

    client: Any
    owner_user_id: str
    backend: str


@lru_cache(maxsize=4)
def _local_client(database_path: str) -> LocalClient:
    return LocalClient(database_path)


def open_persistence(settings: Settings | None = None) -> PersistenceContext:
    """Open the configured backend, raising when it cannot be used."""
    resolved = settings or get_settings()
    if resolved.persistence_backend == "local":
        return PersistenceContext(
            client=_local_client(str(resolved.local_database_path)),
            owner_user_id=resolved.resolve_owner_user_id(),
            backend="local",
        )

    from supabase import create_client

    url, key, owner = resolved.require_supabase()
    return PersistenceContext(
        client=create_client(url, key),
        owner_user_id=owner,
        backend="supabase",
    )
