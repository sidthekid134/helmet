"""Point-in-time historical analysis for initial policy calibration."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from statistics import mean
from typing import Any

import polars as pl

from helmet.integrations.nflverse import NflverseClient, NflverseDataset


class FindingStatus(StrEnum):
    SUPPORTED = "supported"
    NOT_SUPPORTED = "not_supported"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


@dataclass(frozen=True, slots=True)
class ResearchFinding:
    hypothesis: str
    status: FindingStatus
    effect_size: float | None
    sample_size: int
    confidence: float
    evidence: str
    required_sources: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class WarmStartReport:
    model_version: str
    generated_at: str
    training_seasons: tuple[int, ...]
    target_season: int
    findings: tuple[ResearchFinding, ...]
    promoted_modifiers: dict[str, float]


def _column(frame: pl.DataFrame, options: tuple[str, ...]) -> str:
    for option in options:
        if option in frame.columns:
            return option
    raise ValueError(f"required column missing; expected one of {options}")


def analyze_high_touch_hangover(stats: pl.DataFrame) -> ResearchFinding:
    """Compare following-year points per touch after 350+ touch seasons."""
    player = _column(stats, ("player_id", "gsis_id"))
    season = _column(stats, ("season",))
    position = _column(stats, ("position", "position_group"))
    carries = _column(stats, ("carries", "rushing_attempts"))
    receptions = _column(stats, ("receptions",))
    points = _column(stats, ("fantasy_points_ppr", "fantasy_points"))
    yearly = (
        stats.filter(pl.col(position) == "RB")
        .group_by([player, season])
        .agg(
            (pl.col(carries).fill_null(0).sum() + pl.col(receptions).fill_null(0).sum()).alias(
                "touches"
            ),
            pl.col(points).fill_null(0).sum().alias("points"),
        )
        .sort([player, season])
        .with_columns(
            pl.col("season").shift(-1).over(player).alias("next_season"),
            pl.col("points").shift(-1).over(player).alias("next_points"),
            pl.col("touches").shift(-1).over(player).alias("next_touches"),
        )
        .filter(pl.col("next_season") == pl.col(season) + 1)
        .filter(pl.col("next_touches") > 0)
        .with_columns((pl.col("next_points") / pl.col("next_touches")).alias("next_ppt"))
    )
    high = yearly.filter(pl.col("touches") >= 350)["next_ppt"].to_list()
    comparison = yearly.filter((pl.col("touches") >= 150) & (pl.col("touches") < 350))[
        "next_ppt"
    ].to_list()
    sample_size = len(high) + len(comparison)
    if len(high) < 5 or len(comparison) < 20:
        return ResearchFinding(
            "350+ touch RB hangover",
            FindingStatus.INSUFFICIENT_EVIDENCE,
            None,
            sample_size,
            0,
            f"Only {len(high)} high-touch and {len(comparison)} comparison seasons",
            ("nflverse.player_stats",),
        )
    effect = mean(high) - mean(comparison)
    return ResearchFinding(
        "350+ touch RB hangover",
        FindingStatus.SUPPORTED if effect < 0 else FindingStatus.NOT_SUPPORTED,
        round(effect, 4),
        sample_size,
        min(0.95, len(high) / 20),
        "Difference in next-season PPR points per touch versus 150–349 touch RBs",
        ("nflverse.player_stats",),
    )


def _unavailable(hypothesis: str, sources: tuple[str, ...], reason: str) -> ResearchFinding:
    return ResearchFinding(
        hypothesis,
        FindingStatus.INSUFFICIENT_EVIDENCE,
        None,
        0,
        0,
        reason,
        sources,
    )


def build_warm_start(
    *,
    seasons: tuple[int, ...] = (2024, 2025),
    target_season: int = 2026,
    client: NflverseClient | None = None,
) -> WarmStartReport:
    if not seasons or sorted(set(seasons)) != list(seasons):
        raise ValueError("seasons must be unique and increasing")
    if max(seasons) >= target_season:
        raise ValueError("training seasons must precede target_season")
    source = client or NflverseClient()
    stats = source.load(NflverseDataset.PLAYER_STATS, seasons=list(seasons))
    findings = (
        analyze_high_touch_hangover(stats),
        _unavailable(
            "RB peak-age curve",
            ("nflverse.rosters", "nflverse.player_stats"),
            "Birth-date coverage and cohort survival adjustment require validation",
        ),
        _unavailable(
            "Offensive-line continuity",
            ("validated.offensive_line_starters",),
            "No validated offensive-line starter source is configured",
        ),
        _unavailable(
            "Pressure-to-sack volatility",
            ("nflverse.play_by_play",),
            "Pressure attribution coverage has not been validated",
        ),
        _unavailable(
            "Wind penalty above threshold",
            ("validated.weather", "nflverse.schedules"),
            "No validated point-in-time weather source is configured",
        ),
        _unavailable(
            "Opponent manager behavior",
            ("sleeper.transactions", "sleeper.drafts"),
            "Requires the connected league's historical transaction data",
        ),
    )
    modifiers: dict[str, float] = {}
    hangover = findings[0]
    if (
        hangover.status is FindingStatus.SUPPORTED
        and hangover.effect_size is not None
        and hangover.confidence >= 0.75
    ):
        modifiers["rb_350_touch_next_year_points_per_touch"] = hangover.effect_size
    return WarmStartReport(
        model_version=f"{target_season}.0.0",
        generated_at=datetime.now(UTC).isoformat(),
        training_seasons=seasons,
        target_season=target_season,
        findings=findings,
        promoted_modifiers=modifiers,
    )


def write_report(report: WarmStartReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = asdict(report)
    path.write_text(json.dumps(payload, indent=2) + "\n")
