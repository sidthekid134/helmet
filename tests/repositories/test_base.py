from __future__ import annotations

import unittest
from dataclasses import dataclass
from unittest.mock import MagicMock
from uuid import uuid4

from helmet.repositories import (
    RepositoryNotFoundError,
    RepositoryValidationError,
    RepositoryWriteError,
    SourceObservationRepository,
    canonical_content_hash,
)


@dataclass
class Response:
    data: object


class SupabaseRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = MagicMock()
        self.query = MagicMock()
        self.client.table.return_value = self.query
        self.owner_id = str(uuid4())
        self.repository = SourceObservationRepository(self.client, self.owner_id)
        self.valid_values = {
            "source_system": "sleeper",
            "source_entity_type": "player",
            "source_entity_id": "123",
            "payload": {"name": "Example Player"},
            "observed_at": "2026-08-26T20:00:00Z",
            "effective_at": "2026-08-26T19:55:00+00:00",
            "content_hash": "a" * 64,
        }

    def test_create_validates_and_scopes_insert(self) -> None:
        expected = {"id": str(uuid4()), **self.valid_values}
        self.query.insert.return_value.execute.return_value = Response([expected])

        result = self.repository.create(self.valid_values)

        inserted = self.query.insert.call_args.args[0]
        self.assertEqual(inserted["owner_user_id"], self.owner_id)
        self.assertEqual(result, expected)

    def test_create_rejects_missing_temporal_field_without_calling_client(self) -> None:
        values = dict(self.valid_values)
        del values["content_hash"]

        with self.assertRaisesRegex(RepositoryValidationError, "content_hash"):
            self.repository.create(values)

        self.client.table.assert_not_called()

    def test_create_rejects_naive_timestamp(self) -> None:
        values = {**self.valid_values, "observed_at": "2026-08-26T20:00:00"}

        with self.assertRaisesRegex(RepositoryValidationError, "timezone"):
            self.repository.create(values)

    def test_create_rejects_server_managed_fields(self) -> None:
        values = {**self.valid_values, "owner_user_id": str(uuid4())}

        with self.assertRaisesRegex(RepositoryValidationError, "server-managed"):
            self.repository.create(values)

    def test_supabase_write_error_is_explicit_and_chained(self) -> None:
        self.query.insert.side_effect = RuntimeError("database unavailable")

        with self.assertRaisesRegex(RepositoryWriteError, "database unavailable") as raised:
            self.repository.create(self.valid_values)

        self.assertIsInstance(raised.exception.__cause__, RuntimeError)

    def test_get_raises_not_found_for_empty_result(self) -> None:
        limited_query = (
            self.query.select.return_value.eq.return_value.eq.return_value.limit.return_value
        )
        limited_query.execute.return_value = Response([])

        with self.assertRaises(RepositoryNotFoundError):
            self.repository.get(uuid4())

    def test_canonical_hash_is_stable_across_key_order(self) -> None:
        self.assertEqual(
            canonical_content_hash({"b": 2, "a": 1}),
            canonical_content_hash({"a": 1, "b": 2}),
        )
        self.assertEqual(len(canonical_content_hash({"a": 1})), 64)


if __name__ == "__main__":
    unittest.main()
