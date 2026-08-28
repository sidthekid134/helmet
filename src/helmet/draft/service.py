"""End-to-end draft-plan generation: projections -> tree -> persistence.

This is the seam the API and CLI both call through. It intentionally does not
hide failures behind defaults: a league with unsupported scoring keys, a
projection pool that can't be built, or a tree with no legal continuation all
raise rather than silently falling back to something plausible-looking.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from helmet.analytics import (
    PlayerProjection,
    ScoredProjection,
    replacement_levels,
    value_over_replacement,
)
from helmet.persistence import PersistenceContext
from helmet.projections import (
    ProjectionSettings,
    build_projection_pool,
    projection_model_version,
    translate_sleeper_scoring,
)
from helmet.repositories import DraftPlanRepository, LeagueRepository, canonical_content_hash
from helmet.research import active_projection_modifiers

from .branch import BranchPolicy
from .context import DraftContext
from .opponent import DEFAULT_ADP_STDEV, DEFAULT_CONSIDERATION_WINDOW, AdpOpponentModel
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


def _rank_for_live_board(
    available: Sequence[ScoredProjection],
    *,
    roster: Sequence[PlayerProjection],
    roster_targets: Mapping[str, int],
    starters_per_team: Mapping[str, int],
    overall_pick: int,
) -> list[tuple[float, ScoredProjection, tuple[str, ...]]]:
    """Rank by VORP plus a need bonus that treats an empty starting spot as
    urgent and a wanted bench spot as a mild tiebreaker -- unlike a single
    flat need bonus, this can actually outweigh a small VORP gap.
    """
    have_counts = Counter(player.position for player in roster)
    bye_counts = Counter(player.bye_week for player in roster)
    ranked: list[tuple[float, ScoredProjection, tuple[str, ...]]] = []
    for item in available:
        player = item.player
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
        reach = max(overall_pick - player.adp, 0.0) if player.adp is not None else 0.0
        score = item.vorp + need_bonus + reach * _ADP_REACH_WEIGHT - bye_cost
        reasons = [f"VORP {item.vorp:.1f}"]
        if starter_shortfall > 0:
            reasons.append(f"fills an open {player.position} starting spot")
        elif bench_shortfall > 0:
            reasons.append(f"{player.position} bench need {bench_shortfall}")
        else:
            reasons.append(f"{player.position} target already met")
        if bye_overlap:
            reasons.append(f"bye overlap {bye_overlap}")
        ranked.append((score, item, tuple(reasons)))
    ranked.sort(key=lambda entry: (-entry[0], entry[1].player.player_id))
    return ranked


def live_pick_recommendations(
    *,
    db: PersistenceContext,
    plan_id: str,
    my_roster_player_ids: Sequence[str],
    taken_by_others_player_ids: Sequence[str],
    limit: int = 25,
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
    scarcity (a thinning position's replacement level rises as it empties
    out) instead of a value fixed at generation time.
    """
    plan_row = DraftPlanRepository(db.client, db.owner_user_id).get(plan_id)
    league_id = plan_row["league_id"]
    if not league_id:
        raise ValueError(f"plan {plan_id} has no associated league; cannot rebuild its pool")
    league_row = LeagueRepository(db.client, db.owner_user_id).get(league_id)
    season = league_row["season"]

    config = plan_row["config"]
    scoring = translate_sleeper_scoring(config["sleeper_scoring_settings"])
    pool = build_projection_pool(
        scoring=scoring,
        settings=ProjectionSettings(
            target_season=season, lookback_seasons=tuple(range(season - 2, season))
        ),
        modifiers=config["applied_modifiers"],
    )
    by_id = pool.by_id()

    context = DraftContext(
        num_teams=config["num_teams"],
        my_slot=config["my_slot"],
        rounds=config["rounds"],
        roster_targets=config["roster_targets"],
        starters_per_team=config["starters_per_team"],
        scoring=scoring.settings,
    )
    my_picks = context.my_picks()
    picks_made = len(my_roster_player_ids)
    if picks_made >= len(my_picks):
        raise ValueError(f"all {len(my_picks)} of your picks in this plan are already logged")
    overall_pick = my_picks[picks_made]

    taken = set(my_roster_player_ids) | set(taken_by_others_player_ids)
    available = [player for player in pool.players if player.player_id not in taken]
    if not available:
        raise ValueError("no players remain available")
    replacement_points = replacement_levels(
        available, scoring.settings, config["num_teams"], config["starters_per_team"]
    )
    scored_available = value_over_replacement(available, scoring.settings, replacement_points)

    roster = [by_id[player_id] for player_id in my_roster_player_ids]
    ranked = _rank_for_live_board(
        scored_available,
        roster=roster,
        roster_targets=config["roster_targets"],
        starters_per_team=config["starters_per_team"],
        overall_pick=overall_pick,
    )

    recommendations = []
    for _score, item, reasons in ranked[:limit]:
        player = item.player
        recommendations.append(
            {
                "player": {
                    "id": player.player_id,
                    "name": player.name,
                    "team": player.team,
                    "position": player.position,
                },
                "score": _score,
                "reasons": list(reasons),
            }
        )
    return {
        "overall_pick": overall_pick,
        "round": context.round_of(overall_pick),
        "recommendations": recommendations,
    }
