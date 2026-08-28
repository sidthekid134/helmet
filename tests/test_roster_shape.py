from __future__ import annotations

import pytest

from helmet.draft.roster_shape import (
    derive_draft_shape,
    derive_roster_targets,
    derive_starters_per_team,
)

STANDARD_ROSTER = (
    "QB",
    "RB",
    "RB",
    "WR",
    "WR",
    "TE",
    "FLEX",
    "DEF",
    "K",
    "BN",
    "BN",
    "BN",
    "BN",
    "BN",
    "BN",
)


def test_derive_starters_counts_exact_positions() -> None:
    starters = derive_starters_per_team(("QB", "RB", "RB", "WR", "WR", "TE"))

    assert starters == {"QB": 1, "RB": 2, "WR": 2, "TE": 1}


def test_derive_starters_apportions_a_single_flex_slot() -> None:
    starters = derive_starters_per_team(STANDARD_ROSTER)

    # FLEX (RB/WR/TE) round-robins to RB first.
    assert starters == {"QB": 1, "RB": 3, "WR": 2, "TE": 1}


def test_derive_starters_cycles_multiple_flex_slots_of_the_same_type() -> None:
    starters = derive_starters_per_team(("QB", "FLEX", "FLEX", "FLEX"))

    # Cursor for "FLEX" cycles RB -> WR -> TE across the three flex slots.
    assert starters == {"QB": 1, "RB": 1, "WR": 1, "TE": 1}


def test_derive_starters_handles_superflex() -> None:
    starters = derive_starters_per_team(("QB", "SUPER_FLEX", "RB", "WR"))

    assert starters == {"QB": 2, "RB": 1, "WR": 1}


def test_derive_starters_ignores_bench_k_def_and_unknown_slots() -> None:
    starters = derive_starters_per_team(("QB", "BN", "BN", "K", "DEF", "IDP", "TAXI"))

    assert starters == {"QB": 1}


def test_derive_starters_rejects_a_roster_with_no_modeled_positions() -> None:
    with pytest.raises(ValueError, match="no QB/RB/WR/TE"):
        derive_starters_per_team(("K", "DEF", "BN"))


def test_derive_roster_targets_scales_by_position_and_never_undercuts_starters() -> None:
    targets = derive_roster_targets({"QB": 1, "RB": 3, "WR": 2, "TE": 1})

    assert targets["QB"] >= 1
    assert targets["RB"] >= 3
    assert targets["WR"] >= 2
    assert targets["TE"] >= 1
    # Matches the ratios the draft-plan UI previously hardcoded.
    assert targets == {"QB": 2, "RB": 8, "WR": 6, "TE": 2}


def test_derive_draft_shape_rounds_equals_total_roster_slots() -> None:
    shape = derive_draft_shape(STANDARD_ROSTER)

    assert shape.rounds == len(STANDARD_ROSTER)
    assert shape.starters_per_team == {"QB": 1, "RB": 3, "WR": 2, "TE": 1}
    assert shape.roster_targets == {"QB": 2, "RB": 8, "WR": 6, "TE": 2}
