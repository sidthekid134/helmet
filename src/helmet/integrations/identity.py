"""Deterministic player identity matching with explicit review outcomes."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from helmet.domain import Player


def normalize_name(value: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", ascii_name.lower())


@dataclass(frozen=True, slots=True)
class IdentityMatch:
    external_id: str
    canonical_id: str | None
    confidence: float
    requires_review: bool
    reason: str


class PlayerIdentityResolver:
    def __init__(
        self,
        canonical_players: Iterable[Player],
        *,
        explicit_crosswalk: Mapping[str, str] | None = None,
    ) -> None:
        self._players = {player.source_id: player for player in canonical_players}
        if not self._players:
            raise ValueError("canonical_players cannot be empty")
        self._crosswalk = dict(explicit_crosswalk or {})
        unknown = set(self._crosswalk.values()) - set(self._players)
        if unknown:
            raise ValueError(f"crosswalk references unknown canonical IDs: {sorted(unknown)}")
        self._by_name: dict[str, list[Player]] = {}
        for player in self._players.values():
            self._by_name.setdefault(normalize_name(player.full_name), []).append(player)

    def resolve(
        self,
        *,
        external_id: str,
        full_name: str,
        position: str | None,
        team: str | None,
    ) -> IdentityMatch:
        if external_id in self._crosswalk:
            return IdentityMatch(
                external_id, self._crosswalk[external_id], 1, False, "explicit crosswalk"
            )
        candidates = self._by_name.get(normalize_name(full_name), [])
        if position:
            candidates = [candidate for candidate in candidates if candidate.position == position]
        if team and len(candidates) > 1:
            candidates = [candidate for candidate in candidates if candidate.team == team]
        if len(candidates) == 1:
            return IdentityMatch(
                external_id, candidates[0].source_id, 0.95, False, "unique normalized match"
            )
        reason = "no deterministic match" if not candidates else "ambiguous deterministic match"
        return IdentityMatch(external_id, None, 0, True, reason)
