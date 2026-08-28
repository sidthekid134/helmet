"""Point-in-time aware access to nflverse datasets through nflreadpy."""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Any

import nflreadpy as nfl
import polars as pl


class NflverseDataset(StrEnum):
    PLAYERS = "players"
    ROSTERS = "rosters"
    WEEKLY_ROSTERS = "weekly_rosters"
    PLAYER_STATS = "player_stats"
    PLAY_BY_PLAY = "play_by_play"
    SCHEDULES = "schedules"
    SNAP_COUNTS = "snap_counts"
    INJURIES = "injuries"
    NEXTGEN_STATS = "nextgen_stats"
    PARTICIPATION = "participation"
    FF_IDS = "ff_ids"
    FF_RANKINGS = "ff_rankings"
    FF_OPPORTUNITY = "ff_opportunity"


class NflverseError(RuntimeError):
    pass


class NflverseClient:
    """Loads explicitly requested datasets and rejects future-season access."""

    _LOADERS = {
        NflverseDataset.PLAYERS: nfl.load_players,
        NflverseDataset.ROSTERS: nfl.load_rosters,
        NflverseDataset.WEEKLY_ROSTERS: nfl.load_rosters_weekly,
        NflverseDataset.PLAYER_STATS: nfl.load_player_stats,
        NflverseDataset.PLAY_BY_PLAY: nfl.load_pbp,
        NflverseDataset.SCHEDULES: nfl.load_schedules,
        NflverseDataset.SNAP_COUNTS: nfl.load_snap_counts,
        NflverseDataset.INJURIES: nfl.load_injuries,
        NflverseDataset.NEXTGEN_STATS: nfl.load_nextgen_stats,
        NflverseDataset.PARTICIPATION: nfl.load_participation,
        NflverseDataset.FF_IDS: nfl.load_ff_playerids,
        NflverseDataset.FF_RANKINGS: nfl.load_ff_rankings,
        NflverseDataset.FF_OPPORTUNITY: nfl.load_ff_opportunity,
    }

    def load(
        self,
        dataset: NflverseDataset,
        *,
        seasons: int | list[int] | None = None,
        as_of: date | None = None,
        **kwargs: Any,
    ) -> pl.DataFrame:
        if not isinstance(dataset, NflverseDataset):
            raise TypeError("dataset must be an NflverseDataset")
        requested = [seasons] if isinstance(seasons, int) else seasons
        if requested and as_of and any(season > as_of.year for season in requested):
            raise ValueError("requested season is after the point-in-time cutoff")
        loader = self._LOADERS[dataset]
        try:
            if (
                dataset
                in {
                    NflverseDataset.PLAYERS,
                    NflverseDataset.FF_IDS,
                    NflverseDataset.FF_RANKINGS,
                }
                or seasons is None
            ):
                frame = loader(**kwargs)
            else:
                frame = loader(seasons=seasons, **kwargs)
        except Exception as exc:
            raise NflverseError(f"failed to load {dataset.value}: {exc}") from exc
        if not isinstance(frame, pl.DataFrame):
            raise NflverseError(
                f"{dataset.value} returned {type(frame).__name__}, expected DataFrame"
            )
        if as_of is not None:
            frame = self._filter_as_of(frame, as_of)
        if frame.is_empty():
            raise NflverseError(f"{dataset.value} returned no rows for the requested window")
        return frame

    @staticmethod
    def _filter_as_of(frame: pl.DataFrame, as_of: date) -> pl.DataFrame:
        for field in ("gameday", "game_date", "date", "report_date"):
            if field not in frame.columns:
                continue
            dtype = frame.schema[field]
            expression = pl.col(field)
            if dtype == pl.String:
                expression = expression.str.to_date(strict=False)
            elif dtype == pl.Datetime:
                expression = expression.dt.date()
            return frame.filter(expression <= pl.lit(as_of))
        return frame
