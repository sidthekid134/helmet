"""Construction of a league-scored draft projection pool from nflverse data.

Iteration 1 model: recency-weighted per-game stat rates from prior regular
seasons, extrapolated across a full schedule, with a season floor and ceiling
from Monte Carlo simulation of weekly outcomes.

Known limits, each an intentional extension seam rather than a hidden default:

* There is no injury or availability model. Per-game rates are extrapolated over
  a full schedule, so a player who missed games last season is projected as if
  healthy. ``ProjectionSettings.availability`` is the hook for that work.
* Players with no prior regular-season production cannot be projected and are
  returned in ``ProjectionPool.excluded`` instead of being dropped, so rookies
  missing from the draft board are visible rather than silent.
* Kickers and team defenses are out of scope; ``ProjectionSettings.positions``
  controls that.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from math import sqrt

import polars as pl

from helmet.analytics import PlayerProjection, simulate_rest_of_season
from helmet.integrations.nflverse import NflverseClient, NflverseDataset

from .modifiers import ModifierContext, apply_modifiers
from .scoring import ScoringTranslation

# nflverse schedules and player_stats agree on team codes; the FantasyPros
# rankings feed uses two different ones.
RANKING_TEAM_ALIASES: Mapping[str, str] = {"JAC": "JAX", "LAR": "LA"}

RANKINGS_PAGE_TYPE = "redraft-overall"


def projection_model_version(target_season: int) -> str:
    """The `ProjectionPool.model_version` a build for this season will carry.

    Exposed so callers (see `helmet.draft.service.generate_draft_plan`) can
    compute a plan's content hash -- and check it against already-persisted
    plans -- without paying for a full pool build first.
    """
    return f"projection-{target_season}.1.0"


@dataclass(frozen=True, slots=True)
class ProjectionSettings:
    target_season: int
    lookback_seasons: tuple[int, ...]
    positions: tuple[str, ...] = ("QB", "RB", "WR", "TE")
    recency_decay: float = 0.6
    games_per_season: int = 17
    min_prior_games: int = 4
    max_players: int = 300
    simulation_iterations: int = 1000
    seed: int = 20260827
    availability: float = 1.0

    def __post_init__(self) -> None:
        if not self.lookback_seasons:
            raise ValueError("at least one lookback season is required")
        if sorted(set(self.lookback_seasons)) != list(self.lookback_seasons):
            raise ValueError("lookback_seasons must be unique and increasing")
        if max(self.lookback_seasons) >= self.target_season:
            raise ValueError("lookback seasons must precede target_season")
        if not 0 < self.recency_decay <= 1:
            raise ValueError("recency_decay must be within (0, 1]")
        if self.games_per_season < 1:
            raise ValueError("games_per_season must be positive")
        if self.min_prior_games < 1:
            raise ValueError("min_prior_games must be positive")
        if self.max_players < 1:
            raise ValueError("max_players must be positive")
        if self.simulation_iterations < 1:
            raise ValueError("simulation_iterations must be positive")
        if not 0 < self.availability <= 1:
            raise ValueError("availability must be within (0, 1]")
        if not self.positions:
            raise ValueError("at least one position is required")


@dataclass(frozen=True, slots=True)
class ExcludedPlayer:
    name: str
    position: str
    adp: float
    reason: str


@dataclass(frozen=True, slots=True)
class ProjectionPool:
    """A scored, draftable player pool plus everything it could not cover."""

    target_season: int
    model_version: str
    generated_at: str
    players: tuple[PlayerProjection, ...]
    excluded: tuple[ExcludedPlayer, ...]
    unsupported_scoring_keys: tuple[str, ...]
    applied_modifiers: Mapping[str, float] = field(default_factory=dict)

    def by_id(self) -> dict[str, PlayerProjection]:
        return {player.player_id: player for player in self.players}


def _bye_weeks(schedules: pl.DataFrame, season: int) -> dict[str, int]:
    season_games = schedules.filter(pl.col("season") == season)
    if season_games.is_empty():
        raise ValueError(f"nflverse has no schedule for season {season}")
    weeks = set(season_games["week"].to_list())
    appearances = pl.concat(
        [
            season_games.select(pl.col("home_team").alias("team"), pl.col("week")),
            season_games.select(pl.col("away_team").alias("team"), pl.col("week")),
        ]
    )
    byes: dict[str, int] = {}
    for row in appearances.group_by("team").agg(pl.col("week").unique().alias("weeks")).iter_rows(
        named=True
    ):
        missing = sorted(weeks - set(row["weeks"]))
        if len(missing) != 1:
            raise ValueError(
                f"team {row['team']} has {len(missing)} bye weeks in {season}: {missing}"
            )
        byes[row["team"]] = missing[0]
    return byes


def _rankings(client: NflverseClient, settings: ProjectionSettings) -> pl.DataFrame:
    frame = client.load(NflverseDataset.FF_RANKINGS)
    return (
        frame.filter(pl.col("page_type") == RANKINGS_PAGE_TYPE)
        .select(
            pl.col("id").cast(pl.Int64).alias("fantasypros_id"),
            pl.col("player").alias("name"),
            pl.col("pos").alias("position"),
            pl.col("team").replace(RANKING_TEAM_ALIASES).alias("team"),
            pl.col("ecr").cast(pl.Float64).alias("adp"),
            pl.col("sd").cast(pl.Float64).alias("adp_stdev"),
        )
        .filter(
            pl.col("position").is_in(list(settings.positions))
            & pl.col("adp").is_not_null()
            & pl.col("adp").gt(0)
            & pl.col("fantasypros_id").is_not_null()
        )
        .unique(subset=["fantasypros_id"], keep="first")
        .sort("adp")
        .head(settings.max_players)
    )


def _identity_bridge(client: NflverseClient) -> pl.DataFrame:
    return (
        client.load(NflverseDataset.FF_IDS)
        .select(
            pl.col("fantasypros_id").cast(pl.Int64),
            pl.col("gsis_id").cast(pl.String),
        )
        .filter(pl.col("fantasypros_id").is_not_null() & pl.col("gsis_id").is_not_null())
        .unique(subset=["fantasypros_id"], keep="first")
    )


def _weekly_aggregates(
    client: NflverseClient, settings: ProjectionSettings, scoring: ScoringTranslation
) -> pl.DataFrame:
    """Aggregate recency-weighted per-game rates and weekly point variance."""
    stats = client.load(
        NflverseDataset.PLAYER_STATS, seasons=list(settings.lookback_seasons)
    ).filter(pl.col("season_type") == "REG")
    newest = max(settings.lookback_seasons)
    points = pl.sum_horizontal(
        [
            pl.col(rule.stat).cast(pl.Float64).fill_null(0.0) * rule.points_per_unit
            for rule in scoring.settings.rules
        ]
    )
    prepared = stats.select(
        pl.col("player_id").cast(pl.String),
        pl.col("season").cast(pl.Int64),
        *[pl.col(column).cast(pl.Float64).fill_null(0.0) for column in scoring.stat_columns],
        points.alias("_points"),
        (
            pl.lit(settings.recency_decay)
            ** (pl.lit(newest) - pl.col("season")).cast(pl.Float64)
        ).alias("_weight"),
    )
    return prepared.group_by("player_id").agg(
        pl.col("_weight").sum().alias("weight"),
        pl.len().alias("games"),
        (pl.col("season") == newest).sum().alias("prior_games"),
        *[
            (pl.col(column) * pl.col("_weight")).sum().alias(f"w_{column}")
            for column in scoring.stat_columns
        ],
        *[
            pl.col(column).filter(pl.col("season") == newest).sum().alias(f"prior_{column}")
            for column in scoring.stat_columns
        ],
        (pl.col("_points") * pl.col("_weight")).sum().alias("w_points"),
        (pl.col("_points").pow(2) * pl.col("_weight")).sum().alias("w_points_sq"),
    )


def build_projection_pool(
    *,
    scoring: ScoringTranslation,
    settings: ProjectionSettings,
    modifiers: Mapping[str, float] | None = None,
    client: NflverseClient | None = None,
) -> ProjectionPool:
    source = client or NflverseClient()
    promoted = dict(modifiers or {})
    rankings = _rankings(source, settings)
    bridge = _identity_bridge(source)
    byes = _bye_weeks(source.load(NflverseDataset.SCHEDULES), settings.target_season)
    aggregates = _weekly_aggregates(source, settings, scoring)

    board = rankings.join(bridge, on="fantasypros_id", how="left").join(
        aggregates, left_on="gsis_id", right_on="player_id", how="left"
    )

    players: list[PlayerProjection] = []
    excluded: list[ExcludedPlayer] = []
    weekly_means: dict[str, tuple[float, ...]] = {}
    weekly_stdevs: dict[str, tuple[float, ...]] = {}
    per_game: dict[str, dict[str, float]] = {}
    priors: dict[str, tuple[dict[str, float], int]] = {}
    metadata: dict[str, tuple[str, str, str, float, float | None, int]] = {}

    for row in board.iter_rows(named=True):
        name = row["name"]
        position = row["position"]
        adp = float(row["adp"])
        team = row["team"]
        if row["gsis_id"] is None:
            excluded.append(ExcludedPlayer(name, position, adp, "no nflverse identity mapping"))
            continue
        if row["weight"] is None:
            excluded.append(ExcludedPlayer(name, position, adp, "no prior regular-season stats"))
            continue
        if int(row["games"]) < settings.min_prior_games:
            excluded.append(
                ExcludedPlayer(
                    name,
                    position,
                    adp,
                    f"only {int(row['games'])} prior games; {settings.min_prior_games} required",
                )
            )
            continue
        if team not in byes:
            excluded.append(ExcludedPlayer(name, position, adp, f"no {team} bye week in schedule"))
            continue
        player_id = str(row["gsis_id"])
        weight = float(row["weight"])
        if weight <= 0:
            raise ValueError(f"{player_id} aggregated to a non-positive recency weight")
        games = settings.games_per_season * settings.availability
        per_game[player_id] = {
            column: float(row[f"w_{column}"]) / weight for column in scoring.stat_columns
        }
        priors[player_id] = (
            {column: float(row[f"prior_{column}"]) for column in scoring.stat_columns},
            int(row["prior_games"]),
        )
        mean_points = float(row["w_points"]) / weight
        variance = max(float(row["w_points_sq"]) / weight - mean_points**2, 0.0)
        week_count = max(int(round(games)), 1)
        weekly_means[player_id] = (mean_points,) * week_count
        weekly_stdevs[player_id] = (sqrt(variance),) * week_count
        metadata[player_id] = (
            name,
            position,
            team,
            adp,
            None if row["adp_stdev"] is None else float(row["adp_stdev"]),
            byes[team],
        )

    if not weekly_means:
        raise ValueError("no ranked player could be projected from the configured sources")

    simulated = simulate_rest_of_season(
        weekly_means,
        weekly_stdevs,
        iterations=settings.simulation_iterations,
        seed=settings.seed,
    )

    for player_id, (name, position, team, adp, adp_stdev, bye) in metadata.items():
        outcome = simulated[player_id]
        scale = settings.games_per_season * settings.availability
        projection = PlayerProjection(
            player_id=player_id,
            name=name,
            position=position,
            team=team,
            bye_week=bye,
            stats={column: value * scale for column, value in per_game[player_id].items()},
            floor=outcome.floor,
            ceiling=outcome.ceiling,
            adp=adp,
            adp_stdev=adp_stdev,
            availability=settings.availability,
        )
        if promoted:
            prior_totals, prior_games = priors[player_id]
            projection = apply_modifiers(
                ModifierContext(
                    player=projection,
                    prior_season_totals=prior_totals,
                    prior_season_games=prior_games,
                ),
                promoted,
            )
        players.append(projection)

    players.sort(key=lambda player: (player.adp or float("inf"), player.player_id))
    return ProjectionPool(
        target_season=settings.target_season,
        model_version=projection_model_version(settings.target_season),
        generated_at=datetime.now(UTC).isoformat(),
        players=tuple(players),
        excluded=tuple(excluded),
        unsupported_scoring_keys=scoring.unsupported_keys,
        applied_modifiers=promoted,
    )
