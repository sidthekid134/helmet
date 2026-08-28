from __future__ import annotations

import re
import unittest
from pathlib import Path

MIGRATION = (
    Path(__file__).parents[2] / "supabase" / "migrations" / "20260827050000_draft_plans.sql"
)

EXPECTED_TABLES = {"draft_plans", "draft_plan_nodes", "draft_plan_candidates"}


class DraftPlanSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sql = MIGRATION.read_text()

    def test_all_draft_plan_tables_are_declared(self) -> None:
        declared = set(re.findall(r"create table public\.([a-z_]+)", self.sql))
        self.assertEqual(declared, EXPECTED_TABLES)

    def test_draft_plans_is_temporal_and_idempotent(self) -> None:
        definitions = {
            name: body
            for name, body in re.findall(
                r"create table public\.([a-z_]+) \((.*?)\n\);", self.sql, flags=re.DOTALL
            )
        }
        body = definitions["draft_plans"]
        self.assertIn("observed_at timestamptz not null", body)
        self.assertIn("effective_at timestamptz not null", body)
        self.assertIn("content_hash text not null", body)
        self.assertIn("unique (owner_user_id, content_hash)", body)

    def test_nodes_and_candidates_are_immutable_children(self) -> None:
        definitions = {
            name: body
            for name, body in re.findall(
                r"create table public\.([a-z_]+) \((.*?)\n\);", self.sql, flags=re.DOTALL
            )
        }
        nodes = definitions["draft_plan_nodes"]
        self.assertIn("unique (plan_id, node_key)", nodes)
        self.assertIn("draft_plan_nodes_root_has_no_pick", nodes)
        candidates = definitions["draft_plan_candidates"]
        self.assertIn("unique (parent_node_id, player_id)", candidates)
        self.assertIn("draft_plan_candidates_child_requires_expanded", candidates)

    def test_every_table_is_rls_and_audit_managed(self) -> None:
        loop_tables_match = re.search(
            r"foreach table_name in array array\[(.*?)\]\s+loop", self.sql, flags=re.DOTALL
        )
        self.assertIsNotNone(loop_tables_match)
        loop_tables = set(re.findall(r"'([a-z_]+)'", loop_tables_match.group(1)))
        self.assertEqual(loop_tables, EXPECTED_TABLES)
        self.assertIn("enable row level security", self.sql)
        self.assertIn("owner_user_id = auth.uid()", self.sql)


if __name__ == "__main__":
    unittest.main()
