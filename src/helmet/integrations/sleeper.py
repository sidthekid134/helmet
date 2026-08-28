"""Strict asynchronous client for Sleeper's official read-only API."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Mapping
from time import monotonic
from typing import Any

import httpx

from helmet.domain import DraftPick, League, Player, Roster


class SleeperError(RuntimeError):
    """Raised when Sleeper cannot provide a valid required response."""


class SleeperClient:
    def __init__(
        self,
        *,
        base_url: str = "https://api.sleeper.app/v1",
        requests_per_minute: int = 900,
        timeout: float = 20,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not 1 <= requests_per_minute <= 1000:
            raise ValueError("requests_per_minute must be between 1 and 1000")
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            transport=transport,
            headers={"User-Agent": "helmet/0.1"},
        )
        self._limit = requests_per_minute
        self._requests: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def __aenter__(self) -> SleeperClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def close(self) -> None:
        await self._client.aclose()

    async def _throttle(self) -> None:
        async with self._lock:
            now = monotonic()
            while self._requests and now - self._requests[0] >= 60:
                self._requests.popleft()
            if len(self._requests) >= self._limit:
                await asyncio.sleep(60 - (now - self._requests[0]))
                now = monotonic()
                while self._requests and now - self._requests[0] >= 60:
                    self._requests.popleft()
            self._requests.append(monotonic())

    async def _get(self, path: str) -> Any:
        await self._throttle()
        try:
            response = await self._client.get(path)
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise SleeperError(f"Sleeper request failed for {path}: {exc}") from exc

    async def user(self, username_or_id: str) -> dict[str, Any]:
        return self._require_mapping(await self._get(f"/user/{username_or_id}"), "user")

    async def user_leagues(self, user_id: str, season: int) -> list[dict[str, Any]]:
        return self._require_list(
            await self._get(f"/user/{user_id}/leagues/nfl/{season}"), "user leagues"
        )

    async def league(self, league_id: str) -> League:
        payload = self._require_mapping(await self._get(f"/league/{league_id}"), "league")
        try:
            return League(
                league_id=str(payload["league_id"]),
                name=str(payload["name"]),
                season=int(payload["season"]),
                status=str(payload["status"]),
                total_rosters=int(payload["total_rosters"]),
                roster_positions=tuple(payload["roster_positions"]),
                scoring_settings={
                    str(key): float(value) for key, value in payload["scoring_settings"].items()
                },
                settings=dict(payload.get("settings") or {}),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise SleeperError(f"invalid league payload for {league_id}: {exc}") from exc

    async def league_users(self, league_id: str) -> list[dict[str, Any]]:
        return self._require_list(await self._get(f"/league/{league_id}/users"), "league users")

    async def rosters(self, league_id: str) -> list[Roster]:
        rows = self._require_list(await self._get(f"/league/{league_id}/rosters"), "rosters")
        try:
            return [
                Roster(
                    roster_id=int(row["roster_id"]),
                    owner_id=row.get("owner_id"),
                    player_ids=tuple(row.get("players") or ()),
                    starter_ids=tuple(
                        player_id
                        for player_id in (row.get("starters") or ())
                        if player_id not in {"0", None}
                    ),
                    settings=dict(row.get("settings") or {}),
                )
                for row in rows
            ]
        except (KeyError, TypeError, ValueError) as exc:
            raise SleeperError(f"invalid roster payload for {league_id}: {exc}") from exc

    async def matchups(self, league_id: str, week: int) -> list[dict[str, Any]]:
        if week < 1:
            raise ValueError("week must be positive")
        return self._require_list(
            await self._get(f"/league/{league_id}/matchups/{week}"), "matchups"
        )

    async def transactions(self, league_id: str, week: int) -> list[dict[str, Any]]:
        if week < 1:
            raise ValueError("week must be positive")
        return self._require_list(
            await self._get(f"/league/{league_id}/transactions/{week}"),
            "transactions",
        )

    async def league_drafts(self, league_id: str) -> list[dict[str, Any]]:
        return self._require_list(await self._get(f"/league/{league_id}/drafts"), "drafts")

    async def draft_picks(self, draft_id: str) -> list[DraftPick]:
        rows = self._require_list(await self._get(f"/draft/{draft_id}/picks"), "draft picks")
        try:
            return [
                DraftPick(
                    draft_id=draft_id,
                    pick_number=int(row["pick_no"]),
                    round=int(row["round"]),
                    roster_id=int(row["roster_id"]),
                    player_id=str(row["player_id"]),
                )
                for row in rows
            ]
        except (KeyError, TypeError, ValueError) as exc:
            raise SleeperError(f"invalid draft picks payload for {draft_id}: {exc}") from exc

    async def players(self) -> dict[str, Player]:
        payload = self._require_mapping(await self._get("/players/nfl"), "players")
        players: dict[str, Player] = {}
        try:
            for source_id, row in payload.items():
                if not isinstance(row, Mapping):
                    raise TypeError(f"player {source_id} is not an object")
                name = row.get("full_name") or " ".join(
                    part for part in (row.get("first_name"), row.get("last_name")) if part
                )
                if not name:
                    raise ValueError(f"player {source_id} has no name")
                players[str(source_id)] = Player(
                    source_id=str(source_id),
                    full_name=str(name),
                    position=row.get("position"),
                    team=row.get("team"),
                    status=row.get("status"),
                    age=int(row["age"]) if row.get("age") is not None else None,
                    metadata=dict(row),
                )
        except (TypeError, ValueError) as exc:
            raise SleeperError(f"invalid players payload: {exc}") from exc
        return players

    async def nfl_state(self) -> dict[str, Any]:
        return self._require_mapping(await self._get("/state/nfl"), "NFL state")

    @staticmethod
    def _require_mapping(value: Any, label: str) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            raise SleeperError(f"{label} response must be an object")
        return dict(value)

    @staticmethod
    def _require_list(value: Any, label: str) -> list[dict[str, Any]]:
        if not isinstance(value, list) or any(not isinstance(item, Mapping) for item in value):
            raise SleeperError(f"{label} response must be a list of objects")
        return [dict(item) for item in value]
