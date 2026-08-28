"""Domain-specific repositories backed by the temporal persistence schema."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from .base import TableClient, TableRepository, TableSpec
from .errors import RepositoryValidationError


def _fields(value: str) -> frozenset[str]:
    return frozenset(value.split())


class ConfiguredRepository(TableRepository):
    SPEC: TableSpec

    def __init__(self, client: TableClient, owner_user_id: str | UUID) -> None:
        super().__init__(client, owner_user_id, self.SPEC)


class IngestionRunRepository(ConfiguredRepository):
    SPEC = TableSpec(
        "ingestion_runs",
        _fields(
            "source_system run_type idempotency_key status cursor_before cursor_after records_seen "
            "records_written started_at completed_at error_code error_detail metadata"
        ),
        _fields("source_system run_type idempotency_key"),
    )

    def mark_running(self, run_id: str | UUID, *, started_at: str) -> dict[str, Any]:
        return self.update(run_id, {"status": "running", "started_at": started_at})

    def mark_succeeded(
        self,
        run_id: str | UUID,
        *,
        completed_at: str,
        records_seen: int,
        records_written: int,
        cursor_after: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if records_seen < 0 or records_written < 0 or records_written > records_seen:
            raise RepositoryValidationError(
                "record counts must be non-negative and writes cannot exceed records seen"
            )
        return self.update(
            run_id,
            {
                "status": "succeeded",
                "completed_at": completed_at,
                "records_seen": records_seen,
                "records_written": records_written,
                "cursor_after": cursor_after,
            },
        )

    def mark_failed(
        self,
        run_id: str | UUID,
        *,
        completed_at: str,
        error_code: str,
        error_detail: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not error_code.strip():
            raise RepositoryValidationError("error_code is required")
        return self.update(
            run_id,
            {
                "status": "failed",
                "completed_at": completed_at,
                "error_code": error_code,
                "error_detail": error_detail,
            },
        )


class SourceObservationRepository(ConfiguredRepository):
    SPEC = TableSpec(
        "source_observations",
        _fields(
            "ingestion_run_id source_system source_entity_type source_entity_id payload "
            "source_url observed_at effective_at content_hash"
        ),
        _fields(
            "source_system source_entity_type source_entity_id payload observed_at "
            "effective_at content_hash"
        ),
        temporal=True,
    )


class PlayerIdentityRepository(ConfiguredRepository):
    SPEC = TableSpec(
        "player_identities",
        _fields(
            "canonical_name normalized_name sport position team_code birth_date active "
            "attributes observed_at effective_at content_hash"
        ),
        _fields("canonical_name normalized_name observed_at effective_at content_hash"),
        temporal=True,
    )


class PlayerExternalIdRepository(ConfiguredRepository):
    SPEC = TableSpec(
        "player_external_ids",
        _fields(
            "player_identity_id source_system external_player_id confidence valid_from "
            "valid_to observed_at effective_at content_hash"
        ),
        _fields(
            "player_identity_id source_system external_player_id valid_from observed_at "
            "effective_at content_hash"
        ),
        temporal=True,
    )


class PlayerMappingReviewRepository(ConfiguredRepository):
    SPEC = TableSpec(
        "player_mapping_reviews",
        _fields(
            "source_system external_player_id proposed_player_identity_id status confidence "
            "rationale reviewed_at reviewed_by observed_at effective_at content_hash"
        ),
        _fields("source_system external_player_id observed_at effective_at content_hash"),
        temporal=True,
    )

    def resolve(
        self,
        review_id: str | UUID,
        *,
        status: str,
        reviewed_at: str,
        reviewed_by: str | UUID,
        rationale: str,
    ) -> dict[str, Any]:
        if status not in {"approved", "rejected", "superseded"}:
            raise RepositoryValidationError("resolved status is invalid")
        normalized_reviewer = self._validate_uuid(reviewed_by, "reviewed_by")
        return self.update(
            review_id,
            {
                "status": status,
                "reviewed_at": reviewed_at,
                "reviewed_by": normalized_reviewer,
                "rationale": rationale,
            },
        )


class LeagueRepository(ConfiguredRepository):
    SPEC = TableSpec(
        "leagues",
        _fields(
            "source_system external_league_id name season settings observed_at "
            "effective_at content_hash"
        ),
        _fields(
            "source_system external_league_id name season observed_at effective_at content_hash"
        ),
        temporal=True,
    )


class LeagueMemberRepository(ConfiguredRepository):
    SPEC = TableSpec(
        "league_members",
        _fields(
            "league_id external_manager_id team_name is_user_team metadata observed_at "
            "effective_at content_hash"
        ),
        _fields("league_id external_manager_id team_name observed_at effective_at content_hash"),
        temporal=True,
    )


class RosterRepository(ConfiguredRepository):
    SPEC = TableSpec(
        "rosters",
        _fields("league_id league_member_id season"),
        _fields("league_id league_member_id season"),
    )


class RosterSnapshotRepository(ConfiguredRepository):
    SPEC = TableSpec(
        "roster_snapshots",
        _fields("roster_id week source_observation_id observed_at effective_at content_hash"),
        _fields("roster_id week observed_at effective_at content_hash"),
        temporal=True,
    )


class RosterSnapshotPlayerRepository(ConfiguredRepository):
    SPEC = TableSpec(
        "roster_snapshot_players",
        _fields("roster_snapshot_id player_identity_id slot acquisition_type"),
        _fields("roster_snapshot_id player_identity_id slot"),
    )


class DraftRepository(ConfiguredRepository):
    SPEC = TableSpec(
        "drafts",
        _fields(
            "league_id external_draft_id draft_type status starts_at settings observed_at "
            "effective_at content_hash"
        ),
        _fields("league_id draft_type status observed_at effective_at content_hash"),
        temporal=True,
    )


class DraftPickRepository(ConfiguredRepository):
    SPEC = TableSpec(
        "draft_picks",
        _fields(
            "draft_id league_member_id player_identity_id round pick_in_round overall_pick "
            "observed_at effective_at content_hash"
        ),
        _fields(
            "draft_id player_identity_id round pick_in_round overall_pick observed_at "
            "effective_at content_hash"
        ),
        temporal=True,
    )


class TransactionRepository(ConfiguredRepository):
    SPEC = TableSpec(
        "transactions",
        _fields(
            "league_id external_transaction_id transaction_type status processed_at details "
            "observed_at effective_at content_hash"
        ),
        _fields(
            "league_id external_transaction_id transaction_type status observed_at "
            "effective_at content_hash"
        ),
        temporal=True,
    )


class TransactionPlayerRepository(ConfiguredRepository):
    SPEC = TableSpec(
        "transaction_players",
        _fields("transaction_id player_identity_id from_member_id to_member_id action faab_amount"),
        _fields("transaction_id player_identity_id action"),
    )


class WeeklyPlayerStatRepository(ConfiguredRepository):
    SPEC = TableSpec(
        "weekly_player_stats",
        _fields(
            "player_identity_id source_system season week stat_line fantasy_points observed_at "
            "effective_at content_hash"
        ),
        _fields(
            "player_identity_id source_system season week stat_line observed_at effective_at "
            "content_hash"
        ),
        temporal=True,
    )


class InjuryRepository(ConfiguredRepository):
    SPEC = TableSpec(
        "injuries",
        _fields(
            "player_identity_id source_system status body_part practice_status detail "
            "expected_return_at observed_at effective_at content_hash"
        ),
        _fields("player_identity_id source_system status observed_at effective_at content_hash"),
        temporal=True,
    )


class ProjectionRepository(ConfiguredRepository):
    SPEC = TableSpec(
        "projections",
        _fields(
            "player_identity_id league_id source_system model_version season week "
            "projected_points distribution observed_at effective_at content_hash"
        ),
        _fields(
            "player_identity_id source_system season projected_points observed_at effective_at "
            "content_hash"
        ),
        temporal=True,
    )


class RecommendationRepository(ConfiguredRepository):
    SPEC = TableSpec(
        "recommendations",
        _fields(
            "league_id policy_version_id recommendation_type status subject rationale score "
            "expires_at observed_at effective_at content_hash"
        ),
        _fields(
            "league_id recommendation_type subject rationale observed_at effective_at content_hash"
        ),
        temporal=True,
    )


class RecommendationOutcomeRepository(ConfiguredRepository):
    SPEC = TableSpec(
        "recommendation_outcomes",
        _fields(
            "recommendation_id action_taken outcome_type realized_value outcome observed_at "
            "effective_at content_hash"
        ),
        _fields(
            "recommendation_id action_taken outcome_type observed_at effective_at content_hash"
        ),
        temporal=True,
    )


class ResearchFindingRepository(ConfiguredRepository):
    SPEC = TableSpec(
        "research_findings",
        _fields(
            "source_observation_id topic claim evidence confidence valid_from valid_to observed_at "
            "effective_at content_hash"
        ),
        _fields("topic claim evidence confidence valid_from observed_at effective_at content_hash"),
        temporal=True,
    )


class ErrorPatternRepository(ConfiguredRepository):
    SPEC = TableSpec(
        "error_patterns",
        _fields(
            "pattern_key category description signature severity first_observed_at "
            "last_observed_at occurrence_count observed_at effective_at content_hash"
        ),
        _fields(
            "pattern_key category description signature severity first_observed_at "
            "last_observed_at observed_at effective_at content_hash"
        ),
        temporal=True,
    )


class ErrorAttributionRepository(ConfiguredRepository):
    SPEC = TableSpec(
        "error_attributions",
        _fields(
            "error_pattern_id ingestion_run_id recommendation_id component root_cause "
            "responsibility_weight evidence observed_at effective_at content_hash"
        ),
        _fields(
            "error_pattern_id component responsibility_weight observed_at effective_at content_hash"
        ),
        temporal=True,
    )


class ManagerProfileRepository(ConfiguredRepository):
    SPEC = TableSpec(
        "manager_profiles",
        _fields(
            "league_member_id sample_size tendencies confidence observed_at "
            "effective_at content_hash"
        ),
        _fields("league_member_id tendencies confidence observed_at effective_at content_hash"),
        temporal=True,
    )


class PolicyVersionRepository(ConfiguredRepository):
    SPEC = TableSpec(
        "policy_versions",
        _fields(
            "policy_key version status definition evaluation_metrics parent_policy_version_id "
            "observed_at effective_at content_hash"
        ),
        _fields("policy_key version definition observed_at effective_at content_hash"),
        temporal=True,
    )


class PolicyPromotionRepository(ConfiguredRepository):
    SPEC = TableSpec(
        "policy_promotions",
        _fields(
            "policy_version_id from_status to_status promoted_at promoted_by reason evidence "
            "observed_at effective_at content_hash"
        ),
        _fields(
            "policy_version_id from_status to_status promoted_at promoted_by reason observed_at "
            "effective_at content_hash"
        ),
        temporal=True,
    )


class DraftPlanRepository(ConfiguredRepository):
    SPEC = TableSpec(
        "draft_plans",
        _fields(
            "league_id draft_id research_policy_version_id num_teams my_slot rounds seed "
            "simulation_iterations node_count status config observed_at effective_at content_hash"
        ),
        _fields(
            "num_teams my_slot rounds seed simulation_iterations node_count config "
            "observed_at effective_at content_hash"
        ),
        temporal=True,
    )


class DraftPlanNodeRepository(ConfiguredRepository):
    SPEC = TableSpec(
        "draft_plan_nodes",
        _fields(
            "plan_id parent_node_id node_key depth overall_pick round chosen_player_id "
            "chosen_player_name chosen_player_team chosen_player_position chosen_archetype "
            "board_state_hash reach_probability roster_player_ids ev ev_floor ev_ceiling rationale"
        ),
        _fields(
            "plan_id node_key depth board_state_hash reach_probability roster_player_ids ev "
            "ev_floor ev_ceiling"
        ),
    )


class DraftPlanCandidateRepository(ConfiguredRepository):
    SPEC = TableSpec(
        "draft_plan_candidates",
        _fields(
            "plan_id parent_node_id player_id player_name player_team player_position archetype "
            "survival_probability marginal_value rank expanded child_node_id"
        ),
        _fields(
            "plan_id parent_node_id player_id player_name player_team player_position "
            "survival_probability marginal_value rank expanded"
        ),
    )
