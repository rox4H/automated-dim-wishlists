from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from catalogue_source import CatalogueError, JsonCatalogue, SupabaseCatalogue, open_catalogue


class FakeResponse:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.payload


def row(key, record):
    return {key: record.get(key), "data": record}


class SupabaseCatalogueTests(unittest.TestCase):
    def test_reads_exact_version_from_rest_with_publishable_key_header(self):
        weapon = {"id": "avalanche_s5", "name": "Avalanche", "bungie_hash": 66875353}
        seen = []

        def fake_urlopen(request, timeout):
            seen.append(request)
            return FakeResponse([row("id", weapon)])

        catalogue = SupabaseCatalogue(
            "https://example.supabase.co", "sb_publishable_test", timeout=1
        )
        with patch("catalogue_source.urlopen", side_effect=fake_urlopen):
            self.assertEqual(catalogue.get_record("weapons", "avalanche_s5"), weapon)

        self.assertEqual(seen[0].get_header("Apikey"), "sb_publishable_test")
        self.assertIsNone(seen[0].get_header("Authorization"))
        self.assertIn("id=eq.avalanche_s5", seen[0].full_url)

    def test_paginates_lists_without_truncating_catalogue(self):
        records = [
            {"id": "weapon-a_s1", "name": "A"},
            {"id": "weapon-b_s1", "name": "B"},
            {"id": "weapon-c_s1", "name": "C"},
        ]
        ranges = []

        def fake_urlopen(request, timeout):
            ranges.append(request.get_header("Range"))
            offset = int(request.get_header("Range").split("-")[0])
            page = records[offset:offset + 2]
            return FakeResponse([row("id", record) for record in page])

        catalogue = SupabaseCatalogue(
            "https://example.supabase.co", "sb_publishable_test",
            timeout=1, page_size=2,
        )
        with patch("catalogue_source.urlopen", side_effect=fake_urlopen):
            result = catalogue.list_records("weapons")

        self.assertEqual([record["id"] for record in result], [
            "weapon-a_s1", "weapon-b_s1", "weapon-c_s1"
        ])
        self.assertEqual(ranges, ["0-1", "2-3"])

    def test_rejects_database_key_that_disagrees_with_json_id(self):
        catalogue = SupabaseCatalogue(
            "https://example.supabase.co", "sb_publishable_test", timeout=1
        )
        with self.assertRaisesRegex(CatalogueError, "does not match"):
            catalogue._decode_row("weapons", {"id": "sample_s1", "data": {"id": "other_s1"}})

    def test_local_json_provider_is_only_selected_for_complete_fixture_layout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative in ("weapons", "perks", "weapon-perk-pools"):
                (root / "data" / relative).mkdir(parents=True)
            (root / "data" / "weapons" / "sample_s1.json").write_text(
                json.dumps({"id": "sample_s1", "name": "Sample"}), encoding="utf-8"
            )
            provider = open_catalogue(root)
            self.assertIsInstance(provider, JsonCatalogue)
            self.assertEqual(provider.get_record("weapons", "sample_s1")["name"], "Sample")

    def test_partial_or_absent_json_tree_uses_supabase(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "data" / "weapons").mkdir(parents=True)
            provider = open_catalogue(root)
            self.assertIsInstance(provider, SupabaseCatalogue)


if __name__ == "__main__":
    unittest.main()
