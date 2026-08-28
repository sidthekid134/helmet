"""Translation from provider scoring settings into nflverse stat columns.

Sleeper names its scoring keys after fantasy concepts while nflverse names its
columns after box-score stats. This module owns that mapping so the analytics
engine only ever sees ``ScoringSettings`` built from real column names.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from helmet.analytics import ScoringRule, ScoringSettings

# Sleeper scoring key -> nflverse player_stats column. Keys absent here are
# reported through ScoringTranslation.unsupported_keys rather than dropped, so a
# league using them is a visible gap instead of a silently wrong projection.
SLEEPER_STAT_COLUMNS: Mapping[str, str] = {
    "pass_att": "attempts",
    "pass_cmp": "completions",
    "pass_yd": "passing_yards",
    "pass_td": "passing_tds",
    "pass_int": "passing_interceptions",
    "pass_2pt": "passing_2pt_conversions",
    "pass_fd": "passing_first_downs",
    "pass_sack": "sacks_suffered",
    "rush_att": "carries",
    "rush_yd": "rushing_yards",
    "rush_td": "rushing_tds",
    "rush_2pt": "rushing_2pt_conversions",
    "rush_fd": "rushing_first_downs",
    "rec": "receptions",
    "rec_tgt": "targets",
    "rec_yd": "receiving_yards",
    "rec_td": "receiving_tds",
    "rec_2pt": "receiving_2pt_conversions",
    "rec_fd": "receiving_first_downs",
    "fum": "fumbles_total",
    "fum_lost": "fumbles_lost_total",
    "fum_rec_td": "fumble_recovery_tds",
    "st_td": "special_teams_tds",
}


@dataclass(frozen=True, slots=True)
class ScoringTranslation:
    """A provider scoring block expressed against nflverse columns."""

    settings: ScoringSettings
    stat_columns: tuple[str, ...]
    unsupported_keys: tuple[str, ...]


def translate_sleeper_scoring(scoring_settings: Mapping[str, float]) -> ScoringTranslation:
    """Convert Sleeper scoring settings into nflverse-column scoring rules.

    Keys carrying zero weight are skipped because they cannot change a score.
    Non-zero keys with no known column are returned in ``unsupported_keys`` so
    the caller can record exactly what the projection ignores.
    """
    if not scoring_settings:
        raise ValueError("scoring_settings cannot be empty")
    weights: dict[str, float] = {}
    unsupported: list[str] = []
    for key, raw in scoring_settings.items():
        weight = float(raw)
        if weight == 0:
            continue
        column = SLEEPER_STAT_COLUMNS.get(key)
        if column is None:
            unsupported.append(key)
            continue
        weights[column] = weights.get(column, 0.0) + weight
    if not weights:
        raise ValueError("no supported scoring rules were found in scoring_settings")
    columns = tuple(sorted(weights))
    rules = tuple(ScoringRule(stat=column, points_per_unit=weights[column]) for column in columns)
    return ScoringTranslation(
        settings=ScoringSettings(rules=rules),
        stat_columns=columns,
        unsupported_keys=tuple(sorted(unsupported)),
    )
