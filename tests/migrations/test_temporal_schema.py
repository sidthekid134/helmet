from __future__ import annotations

import re
import unittest
from pathlib import Path

MIGRATION = (
    Path(__file__).parents[2]
    / "supabase"
    / "migrations"
    / "20260826200000_temporal_persistence.sql"
)

EXPECTED_TABLES = {
    "app_users",
    "ingestion_runs",
    "source_observations",
    "player_identities",
    "player_external_ids",
    "player_mapping_reviews",
    "leagues",
    "league_members",
    "rosters",
    "roster_snapshots",
    "roster_snapshot_players",
    "drafts",
    "draft_picks",
    "transactions",
    "transaction_players",
    "weekly_player_stats",
    "injuries",
    "projections",
    "recommendations",
    "recommendation_outcomes",
    "research_findings",
    "error_patterns",
    "error_attributions",
    "manager_profiles",
    "policy_versions",
    "policy_promotions",
}

TEMPORAL_TABLES = EXPECTED_TABLES - {
    "app_users",
    "ingestion_runs",
    "rosters",
    "roster_snapshot_players",
    "transaction_players",
}


class TemporalSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sql = MIGRATION.read_text()

    def test_all_persistence_tables_are_declared(self) -> None:
        declared = set(re.findall(r"create table public\.([a-z_]+)", self.sql))
        self.assertEqual(declared, EXPECTED_TABLES)

    def test_temporal_tables_have_observation_fields(self) -> None:
        definitions = {
            name: body
            for name, body in re.findall(
                r"create table public\.([a-z_]+) \((.*?)\n\);",
                self.sql,
                flags=re.DOTALL,
            )
        }
        for table in TEMPORAL_TABLES:
            with self.subTest(table=table):
                body = definitions[table]
                self.assertIn("observed_at timestamptz not null", body)
                self.assertIn("effective_at timestamptz not null", body)
                self.assertIn("content_hash text not null", body)

    def test_every_table_is_rls_and_audit_managed(self) -> None:
        loop_tables_match = re.search(
            r"foreach table_name in array array\[(.*?)\]\s+loop",
            self.sql,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(loop_tables_match)
        loop_tables = set(re.findall(r"'([a-z_]+)'", loop_tables_match.group(1)))
        self.assertEqual(loop_tables, EXPECTED_TABLES)
        self.assertIn("enable row level security", self.sql)
        self.assertIn("owner_user_id = auth.uid()", self.sql)
        self.assertIn("immutable audit columns cannot be changed", self.sql)

    def test_active_policy_is_unique_per_owner_and_key(self) -> None:
        self.assertIn("policy_versions_one_active_idx", self.sql)
        self.assertIn("where status = 'active'", self.sql)


if __name__ == "__main__":
    unittest.main()
