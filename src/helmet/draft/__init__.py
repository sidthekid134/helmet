"""Expectimax draft-plan tree: branching decisions with simulated opponents."""

from .branch import (
    DEFAULT_ARCHETYPES,
    BranchCandidate,
    BranchPolicy,
    rank_available,
    select_branch_candidates,
)
from .context import DraftContext
from .opponent import (
    DEFAULT_ADP_STDEV,
    DEFAULT_CONSIDERATION_WINDOW,
    AdpOpponentModel,
    OpponentModel,
    simulate_gap_survival,
)
from .persistence import find_existing_plan, persist_draft_tree
from .roster_shape import (
    CORE_POSITIONS,
    DraftShape,
    derive_draft_shape,
    derive_roster_targets,
    derive_starters_per_team,
)
from .service import (
    default_branch_policy,
    generate_draft_plan,
    live_pick_recommendations,
    precompute_all_draft_plans,
)
from .tree import DraftTree, DraftTreeCandidate, DraftTreeNode, build_draft_tree
from .value import RosterValue, ValueModel, VorpValueModel

__all__ = [
    "CORE_POSITIONS",
    "DEFAULT_ADP_STDEV",
    "DEFAULT_ARCHETYPES",
    "DEFAULT_CONSIDERATION_WINDOW",
    "AdpOpponentModel",
    "BranchCandidate",
    "BranchPolicy",
    "DraftContext",
    "DraftShape",
    "DraftTree",
    "DraftTreeCandidate",
    "DraftTreeNode",
    "OpponentModel",
    "RosterValue",
    "ValueModel",
    "VorpValueModel",
    "build_draft_tree",
    "default_branch_policy",
    "derive_draft_shape",
    "derive_roster_targets",
    "derive_starters_per_team",
    "find_existing_plan",
    "generate_draft_plan",
    "live_pick_recommendations",
    "persist_draft_tree",
    "precompute_all_draft_plans",
    "rank_available",
    "select_branch_candidates",
    "simulate_gap_survival",
]
