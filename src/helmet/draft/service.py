"""End-to-end draft-plan generation: projections -> tree -> persistence.

This is the seam the API and CLI both call through. It intentionally does not
hide failures behind defaults: a league with unsupported scoring keys, a
projection pool that can't be built, or a tree with no legal continuation all
raise rather than silently falling back to something plausible-looking.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from random import Random
from typing import Any, Literal

from helmet.analytics import (
    PlayerProjection,
    ScoredProjection,
    replacement_levels,
    value_over_replacement,
)
from helmet.persistence import PersistenceContext
from helmet.projections import (
    ProjectionPool,
    ProjectionSettings,
    ScoringTranslation,
    build_projection_pool,
    projection_model_version,
    translate_sleeper_scoring,
)
from helmet.repositories import DraftPlanRepository, LeagueRepository, canonical_content_hash
from helmet.research import active_projection_modifiers

from .branch import BranchPolicy
from .context import DraftContext
from .opponent import (
    DEFAULT_ADP_STDEV,
    DEFAULT_CONSIDERATION_WINDOW,
    AdpOpponentModel,
    OpponentModel,
    simulate_gap_survival,
)
from .persistence import find_existing_plan, persist_draft_tree
from .roster_shape import derive_draft_shape
from .tree import build_draft_tree

# A missing starter is weighted far above a bench slot -- and above almost any
# single-player VORP gap this early in a draft -- because leaving a starting
# lineup spot empty costs a team every single week, while wanting one more
# bench body at an already-covered position is a mild tiebreaker at best.
_STARTER_SHORTFALL_WEIGHT = 20.0
_BENCH_SHORTFALL_WEIGHT = 2.0
_BYE_OVERLAP_PENALTY = 1.0
_ADP_REACH_WEIGHT = 0.1
# VONA is the snake-draft question: how many points do you lose at this
# position if you wait a round. Added at full weight so a cliff outweighs a
# similar VORP at a position that will still be there.
_VONA_SURVIVAL_FLOOR = 0.5
_URGENCY_WEIGHT = 8.0
_TAKE_NOW_SURVIVAL = 0.4
_WAIT_SURVIVAL = 0.7

LiveUrgency = Literal["take_now", "wait", "even"]

DEFAULT_LIVE_SIMULATION_ITERATIONS = 40
DEFAULT_LIVE_LIMIT = 80

# In-process projection pools keyed by plan_id. Live ranking recomputes VORP
# and survival on every mark-taken; reloading nflverse for that would make
# the board pop instead of shuffle.
_POOL_CACHE: dict[str, tuple[str, ProjectionPool]] = {}


@dataclass(frozen=True, slots=True)
class LiveBoardRank:
    """One ranked live-board row, with the snake-draft numbers behind the why."""

    score: float
    item: ScoredProjection
    vona: float
    survival_to_next: float
    urgency: LiveUrgency
    reasons: tuple[str, ...]


DEFAULT_TOP_K_BY_ROUND: Mapping[int, int] = {1: 8, 2: 5, 3: 4}
DEFAULT_INDIVIDUAL_ROUNDS = 3
DEFAULT_BEAM_WIDTH = 16
DEFAULT_MENU_SIZE = 10
DEFAULT_SIMULATION_ITERATIONS = 150
DEFAULT_SEED = 20260827


def default_branch_policy() -> BranchPolicy:
    return BranchPolicy(
        individual_rounds=DEFAULT_INDIVIDUAL_ROUNDS,
        top_k_by_round=dict(DEFAULT_TOP_K_BY_ROUND),
        default_top_k=3,
        beam_width=DEFAULT_BEAM_WIDTH,
        menu_size=DEFAULT_MENU_SIZE,
    )


def generate_draft_plan(
    *,
    db: PersistenceContext,
    sleeper_scoring_settings: Mapping[str, float],
    num_teams: int,
    my_slot: int,
    rounds: int,
    roster_targets: Mapping[str, int],
    starters_per_team: Mapping[str, int],
    projection_settings: ProjectionSettings,
    league_id: str | None = None,
    draft_id: str | None = None,
    branch_policy: BranchPolicy | None = None,
    opponent_default_stdev: float = DEFAULT_ADP_STDEV,
    opponent_consideration_window: int = DEFAULT_CONSIDERATION_WINDOW,
    simulation_iterations: int = DEFAULT_SIMULATION_ITERATIONS,
    seed: int = DEFAULT_SEED,
) -> dict[str, Any]:
    """Plan the draft tree and persist it, reusing a precomputed plan if one exists.

    `helmet research warm-start` precomputes a plan for every slot of every
    connected league (see `precompute_all_draft_plans`). Everything this
    config depends on -- scoring translation, the active modifier policy, and
    the projection model version -- is derivable without touching nflverse,
    so the content hash is checked *before* building a projection pool or
    searching the tree. A cache hit skips both entirely; only a genuine miss
    pays for the full build.

    Returns a dict with the persisted ``plan`` row and whether it was newly
    ``created`` or an idempotent replay of an identical prior plan. ``tree``
    and ``pool`` are the in-memory objects from a fresh build, or ``None``
    on a cache hit (nothing was built).
    """
    scoring = translate_sleeper_scoring(sleeper_scoring_settings)
    modifiers = active_projection_modifiers(db)
    policy = branch_policy or default_branch_policy()

    config = {
        "sleeper_scoring_settings": dict(sorted(sleeper_scoring_settings.items())),
        "unsupported_scoring_keys": list(scoring.unsupported_keys),
        "num_teams": num_teams,
        "my_slot": my_slot,
        "rounds": rounds,
        "roster_targets": dict(sorted(roster_targets.items())),
        "starters_per_team": dict(sorted(starters_per_team.items())),
        "projection_model_version": projection_model_version(projection_settings.target_season),
        "applied_modifiers": dict(sorted(modifiers.items())),
        "branch_policy": {
            "individual_rounds": policy.individual_rounds,
            "top_k_by_round": dict(sorted(policy.top_k_by_round.items())),
            "default_top_k": policy.default_top_k,
            "archetypes": sorted(policy.archetypes),
            "beam_width": policy.beam_width,
            "menu_size": policy.menu_size,
            "need_weight": policy.need_weight,
        },
        "opponent_model": {
            "kind": "adp_gaussian",
            "default_stdev": opponent_default_stdev,
            "consideration_window": opponent_consideration_window,
        },
        "simulation_iterations": simulation_iterations,
        "seed": seed,
    }

    existing = find_existing_plan(db, canonical_content_hash(config))
    if existing is not None:
        return {"tree": None, "pool": None, "plan": existing, "created": False}

    pool = build_projection_pool(
        scoring=scoring, settings=projection_settings, modifiers=modifiers
    )
    context = DraftContext(
        num_teams=num_teams,
        my_slot=my_slot,
        rounds=rounds,
        roster_targets=roster_targets,
        starters_per_team=starters_per_team,
        scoring=scoring.settings,
    )
    opponent_model = AdpOpponentModel(
        default_stdev=opponent_default_stdev,
        consideration_window=opponent_consideration_window,
    )
    tree = build_draft_tree(
        context=context,
        pool=pool.players,
        branch_policy=policy,
        opponent_model=opponent_model,
        simulation_iterations=simulation_iterations,
        seed=seed,
    )

    persisted = persist_draft_tree(
        tree,
        db=db,
        config=config,
        league_id=league_id,
        draft_id=draft_id,
    )
    return {"tree": tree, "pool": pool, **persisted}


def precompute_all_draft_plans(
    db: PersistenceContext,
    *,
    branch_policy: BranchPolicy | None = None,
    simulation_iterations: int = DEFAULT_SIMULATION_ITERATIONS,
    seed: int = DEFAULT_SEED,
) -> list[dict[str, Any]]:
    """Generate a draft plan for every slot of every connected league.

    Intended to run once per `helmet research warm-start`, so that whatever
    the projection pool looks like after that research pass, every draft
    slot already has a plan sitting in `draft_plans` by content hash before
    anyone opens the dashboard. The on-demand `generate_draft_plan` call the
    API makes when a user clicks "Generate" is idempotent by that same
    content hash, so as long as the caller doesn't override the shape this
    derives, it becomes a cache hit instead of a multi-second tree build.

    Raises on the first league that fails (e.g. missing `total_rosters` on a
    league connected before Helmet tracked it, or unsupported scoring keys)
    rather than silently skipping it -- a partially-precomputed run should be
    visible, not swallowed.
    """
    leagues = LeagueRepository(db.client, db.owner_user_id).list(limit=1000)
    results: list[dict[str, Any]] = []
    for league_row in leagues:
        settings = league_row["settings"]
        total_rosters = int(settings["total_rosters"])
        shape = derive_draft_shape(settings["roster_positions"])
        season = league_row["season"]
        projection_settings = ProjectionSettings(
            target_season=season, lookback_seasons=tuple(range(season - 2, season))
        )
        for slot in range(1, total_rosters + 1):
            result = generate_draft_plan(
                db=db,
                sleeper_scoring_settings=settings["scoring_settings"],
                num_teams=total_rosters,
                my_slot=slot,
                rounds=shape.rounds,
                roster_targets=shape.roster_targets,
                starters_per_team=shape.starters_per_team,
                projection_settings=projection_settings,
                branch_policy=branch_policy,
                league_id=league_row["id"],
                seed=seed,
                simulation_iterations=simulation_iterations,
            )
            results.append(
                {
                    "league_id": league_row["external_league_id"],
                    "num_teams": total_rosters,
                    "slot": slot,
                    "plan_id": result["plan"]["id"],
                    "created": result["created"],
                }
            )
    return results


def clear_projection_pool_cache() -> None:
    """Drop cached live-board pools. Tests call this between cases."""
    _POOL_CACHE.clear()


def _pool_fingerprint(plan_id: str, config: Mapping[str, Any], season: int) -> str:
    return canonical_content_hash(
        {
            "plan_id": plan_id,
            "season": season,
            "projection_model_version": config["projection_model_version"],
            "applied_modifiers": config["applied_modifiers"],
            "sleeper_scoring_settings": config["sleeper_scoring_settings"],
        }
    )


def _projection_pool_for_plan(
    plan_id: str,
    *,
    config: Mapping[str, Any],
    season: int,
    scoring: ScoringTranslation,
) -> ProjectionPool:
    fingerprint = _pool_fingerprint(plan_id, config, season)
    cached = _POOL_CACHE.get(plan_id)
    if cached is not None and cached[0] == fingerprint:
        return cached[1]
    pool = build_projection_pool(
        scoring=scoring,
        settings=ProjectionSettings(
            target_season=season, lookback_seasons=tuple(range(season - 2, season))
        ),
        modifiers=config["applied_modifiers"],
    )
    _POOL_CACHE[plan_id] = (fingerprint, pool)
    return pool


def _next_at_position(
    item: ScoredProjection,
    by_position: Mapping[str, Sequence[ScoredProjection]],
    survival: Mapping[str, float],
) -> ScoredProjection | None:
    """The next-best same-position player likely to still be there next round.

    Players are already sorted by projected points. Majority-survival (≥ 0.5)
    is the same canonical-pool rule the tree uses; if nobody clears it, the
    next player at the position is the fallback so VONA still measures a
    real drop-off instead of inventing a replacement.
    """
    others = [
        candidate
        for candidate in by_position[item.player.position]
        if candidate.player.player_id != item.player.player_id
    ]
    likely = [
        candidate
        for candidate in others
        if survival.get(candidate.player.player_id, 0.0) >= _VONA_SURVIVAL_FLOOR
    ]
    if likely:
        return likely[0]
    return others[0] if others else None


def _slot_reason(
    position: str,
    *,
    have: int,
    starters_needed: int,
    bench_target: int,
) -> str:
    next_count = have + 1
    if have < starters_needed:
        return f"Fills open {position}{next_count} starter"
    if have < bench_target:
        return f"{position} depth {next_count} of {bench_target}"
    return f"{position} target already met"


def _survival_reason(survival: float, gap_size: int) -> str:
    if gap_size <= 0:
        return "On the clock now — no wait"
    percent = round(survival * 100)
    gone = 100 - percent
    if survival < _TAKE_NOW_SURVIVAL:
        return f"Gone by next pick ({gone}%)"
    if survival > _WAIT_SURVIVAL:
        return f"Likely waits (survival {percent}%)"
    return f"Risky — {percent}% survive to next pick"


def _adp_reason(overall_pick: int, adp: float) -> str:
    delta = overall_pick - adp
    if delta >= 2:
        return f"falling to you (ADP {adp:.0f})"
    if delta <= -2:
        return f"slight reach (ADP {adp:.0f})"
    return f"on ADP ({adp:.0f})"


def _urgency_for(survival: float, gap_size: int) -> LiveUrgency:
    if gap_size <= 0:
        return "even"
    if survival < _TAKE_NOW_SURVIVAL:
        return "take_now"
    if survival > _WAIT_SURVIVAL:
        return "wait"
    return "even"


def _complete_live_board(context: DraftContext) -> dict[str, Any]:
    return {
        "overall_pick": None,
        "round": None,
        "complete": True,
        "picks_until_next": 0,
        "starters_per_team": dict(context.starters_per_team),
        "roster_targets": dict(context.roster_targets),
        "recommendations": [],
    }


def _recommendation_payload(rank: int, entry: LiveBoardRank) -> dict[str, Any]:
    player = entry.item.player
    if player.adp is None:
        raise ValueError(f"{player.player_id} has no ADP; live ranking requires it")
    return {
        "player": {
            "id": player.player_id,
            "name": player.name,
            "team": player.team,
            "position": player.position,
        },
        "rank": rank,
        "score": entry.score,
        "vorp": entry.item.vorp,
        "vona": entry.vona,
        "survival_to_next": entry.survival_to_next,
        "adp": player.adp,
        "projected_points": entry.item.projected_points,
        "urgency": entry.urgency,
        "reasons": list(entry.reasons),
    }


def rank_for_live_board(
    available: Sequence[ScoredProjection],
    *,
    roster: Sequence[PlayerProjection],
    roster_targets: Mapping[str, int],
    starters_per_team: Mapping[str, int],
    overall_pick: int,
    survival: Mapping[str, float],
    gap_size: int,
) -> list[LiveBoardRank]:
    """Rank remaining players the way a snake-draft aid should.

    Score is VORP plus VONA (drop-off vs the next same-position player likely
    to survive the gap) plus starter/bench need, a small gone-before-next
    urgency term, ADP-reach, and a bye-overlap penalty. Reasons are the
    human "why this pick" strings, not a metric dump.
    """
    if not available:
        raise ValueError("no players remain available")
    have_counts = Counter(player.position for player in roster)
    bye_counts = Counter(player.bye_week for player in roster)
    by_position: dict[str, list[ScoredProjection]] = defaultdict(list)
    for item in available:
        by_position[item.player.position].append(item)
    for position, rows in by_position.items():
        by_position[position] = sorted(
            rows, key=lambda item: (-item.projected_points, item.player.player_id)
        )

    ranked: list[LiveBoardRank] = []
    for item in available:
        player = item.player
        if player.adp is None:
            raise ValueError(f"{player.player_id} has no ADP; live ranking requires it")
        survival_to_next = survival.get(player.player_id)
        if survival_to_next is None:
            raise ValueError(f"{player.player_id} is missing from the survival map")
        nxt = _next_at_position(item, by_position, survival)
        vona = (
            item.projected_points - nxt.projected_points
            if nxt is not None
            else item.projected_points
        )
        have = have_counts[player.position]
        starters_needed = starters_per_team.get(player.position, 0)
        bench_target = roster_targets.get(player.position, starters_needed)
        starter_shortfall = max(starters_needed - have, 0)
        bench_shortfall = max(bench_target - max(have, starters_needed), 0)
        need_bonus = (
            starter_shortfall * _STARTER_SHORTFALL_WEIGHT
            + bench_shortfall * _BENCH_SHORTFALL_WEIGHT
        )
        bye_overlap = bye_counts[player.bye_week]
        bye_cost = bye_overlap * _BYE_OVERLAP_PENALTY
        reach = max(overall_pick - player.adp, 0.0)
        urgency = _urgency_for(survival_to_next, gap_size)
        score = (
            item.vorp
            + vona
            + need_bonus
            + (1.0 - survival_to_next) * _URGENCY_WEIGHT
            + reach * _ADP_REACH_WEIGHT
            - bye_cost
        )
        vona_label = f"{vona:+.0f} VONA vs next {player.position}"
        reasons = (
            vona_label,
            _slot_reason(
                player.position,
                have=have,
                starters_needed=starters_needed,
                bench_target=bench_target,
            ),
            _survival_reason(survival_to_next, gap_size),
            _adp_reason(overall_pick, player.adp),
            *((f"bye overlap {bye_overlap}",) if bye_overlap else ()),
        )
        ranked.append(
            LiveBoardRank(
                score=score,
                item=item,
                vona=vona,
                survival_to_next=survival_to_next,
                urgency=urgency,
                reasons=reasons,
            )
        )
    ranked.sort(key=lambda entry: (-entry.score, entry.item.player.player_id))
    return ranked


def live_board_from_pool(
    *,
    context: DraftContext,
    pool: Sequence[PlayerProjection],
    my_roster_player_ids: Sequence[str],
    taken_by_others_player_ids: Sequence[str],
    opponent_model: OpponentModel | None = None,
    simulation_iterations: int = DEFAULT_LIVE_SIMULATION_ITERATIONS,
    seed: int = DEFAULT_SEED,
    limit: int = DEFAULT_LIVE_LIMIT,
) -> dict[str, Any]:
    """Rank a known pool for the caller's next snake pick.

    Split out from `live_pick_recommendations` so tests can exercise VONA,
    need, survival, and the completed-draft payload without rebuilding
    nflverse or touching the database.
    """
    my_picks = context.my_picks()
    picks_made = len(my_roster_player_ids)
    if picks_made >= len(my_picks):
        return _complete_live_board(context)

    overall_pick = my_picks[picks_made]
    next_pick = my_picks[picks_made + 1] if picks_made + 1 < len(my_picks) else None
    gap_size = (next_pick - overall_pick - 1) if next_pick is not None else 0

    by_id = {player.player_id: player for player in pool}
    roster: list[PlayerProjection] = []
    for player_id in my_roster_player_ids:
        try:
            roster.append(by_id[player_id])
        except KeyError as exc:
            raise ValueError(
                f"roster player {player_id} is not in this plan's projection pool"
            ) from exc

    taken = set(my_roster_player_ids) | set(taken_by_others_player_ids)
    available = [player for player in pool if player.player_id not in taken]
    if not available:
        raise ValueError("no players remain available")
    replacement_points = replacement_levels(
        available, context.scoring, context.num_teams, context.starters_per_team
    )
    scored_available = value_over_replacement(available, context.scoring, replacement_points)

    start_pick = overall_pick + 1
    slots = [context.team_slot_for_pick(start_pick + offset) for offset in range(gap_size)]
    survival = simulate_gap_survival(
        opponent_model=opponent_model or AdpOpponentModel(),
        available=available,
        start_pick=start_pick,
        gap_size=gap_size,
        slots=slots,
        iterations=simulation_iterations,
        rng=Random(seed),
    )
    ranked = rank_for_live_board(
        scored_available,
        roster=roster,
        roster_targets=context.roster_targets,
        starters_per_team=context.starters_per_team,
        overall_pick=overall_pick,
        survival=survival,
        gap_size=gap_size,
    )
    return {
        "overall_pick": overall_pick,
        "round": context.round_of(overall_pick),
        "complete": False,
        "picks_until_next": gap_size,
        "starters_per_team": dict(context.starters_per_team),
        "roster_targets": dict(context.roster_targets),
        "recommendations": [
            _recommendation_payload(rank, entry)
            for rank, entry in enumerate(ranked[:limit], start=1)
        ],
    }


def live_pick_recommendations(
    *,
    db: PersistenceContext,
    plan_id: str,
    my_roster_player_ids: Sequence[str],
    taken_by_others_player_ids: Sequence[str],
    limit: int = DEFAULT_LIVE_LIMIT,
) -> dict[str, Any]:
    """Rank the actual remaining pool for the picker's actual next turn.

    A precomputed plan's candidate menus model opponents from ADP alone --
    the moment a real opponent reaches or a real teammate takes someone off
    the board, that menu is stale. This recomputes rankings from scratch
    against the *true* set of drafted players (the caller's own roster and
    everyone else's, as logged by the caller) instead of the plan's
    simulated one, and figures out which of the caller's own picks is next
    from the plan's snake-draft shape (`DraftContext.my_picks`) rather than
    trusting a client-supplied pick number -- so "what should I take" always
    lines up with a real upcoming turn, not an arbitrary guess.

    Replacement levels are recomputed against the *currently available*
    pool, not the plan's original one, so VORP reflects real draft-time
    scarcity. Survival through the gap to the next pick is simulated with
    the same ADP opponent model the tree uses, at a smaller iteration count
    so the board can re-rank as picks come off.

    The projection pool is cached per plan so marking a player taken does
    not reload nflverse.
    """
    plan_row = DraftPlanRepository(db.client, db.owner_user_id).get(plan_id)
    league_id = plan_row["league_id"]
    if not league_id:
        raise ValueError(f"plan {plan_id} has no associated league; cannot rebuild its pool")
    league_row = LeagueRepository(db.client, db.owner_user_id).get(league_id)
    season = league_row["season"]

    config = plan_row["config"]
    scoring = translate_sleeper_scoring(config["sleeper_scoring_settings"])
    pool = _projection_pool_for_plan(plan_id, config=config, season=season, scoring=scoring)
    context = DraftContext(
        num_teams=config["num_teams"],
        my_slot=config["my_slot"],
        rounds=config["rounds"],
        roster_targets=config["roster_targets"],
        starters_per_team=config["starters_per_team"],
        scoring=scoring.settings,
    )
    opponent = config.get("opponent_model", {})
    return live_board_from_pool(
        context=context,
        pool=pool.players,
        my_roster_player_ids=my_roster_player_ids,
        taken_by_others_player_ids=taken_by_others_player_ids,
        opponent_model=AdpOpponentModel(
            default_stdev=opponent.get("default_stdev", DEFAULT_ADP_STDEV),
            consideration_window=opponent.get(
                "consideration_window", DEFAULT_CONSIDERATION_WINDOW
            ),
        ),
        simulation_iterations=DEFAULT_LIVE_SIMULATION_ITERATIONS,
        seed=config.get("seed", DEFAULT_SEED),
        limit=limit,
    )
