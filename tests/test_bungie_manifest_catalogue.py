"""Tests for the Bungie-manifest-backed catalogue source."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from catalogue_source import (  # noqa: E402
    OVERLAY_RELATIVE,
    SNAPSHOT_RELATIVE,
    BungieManifestCatalogue,
    CatalogueError,
    JsonCatalogue,
    SupabaseCatalogue,
    open_catalogue,
)
from export_catalogue_overlay import build_overlay  # noqa: E402
from export_dim_wishlist import generate_wishlist  # noqa: E402
from pool_slot_mapping import matching_pool_slots  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "manifest_catalogue"
BUILDER = ROOT / "scripts" / "build_manifest_catalogue.mjs"
NODE = shutil.which("node")


def schema_validator(name: str) -> Draft202012Validator:
    schema = json.loads((ROOT / "schemas" / f"{name}.schema.json").read_text(encoding="utf-8"))
    return Draft202012Validator(schema, format_checker=FormatChecker())


@unittest.skipUnless(NODE, "Node.js is required to build the manifest catalogue")
class BungieManifestCatalogueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        (cls.root / OVERLAY_RELATIVE).parent.mkdir(parents=True)
        shutil.copy(FIXTURES / "curated-overlay.json", cls.root / OVERLAY_RELATIVE)
        subprocess.run(
            [NODE, str(BUILDER), "fetch",
             "--overlay", str(cls.root / OVERLAY_RELATIVE),
             "--snapshot", str(cls.root / SNAPSHOT_RELATIVE),
             "--manifest-dir", str(FIXTURES / "manifest"),
             "--captured", "2026-10-31"],
            check=True, capture_output=True, text=True,
        )
        cls.catalogue = BungieManifestCatalogue(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_snapshot_is_a_slim_subset_of_the_manifest(self):
        snapshot = json.loads((self.root / SNAPSHOT_RELATIVE).read_text(encoding="utf-8"))
        items = snapshot["tables"]["DestinyInventoryItemDefinition"]
        self.assertEqual(snapshot["manifest_version"], "fixture-1")
        self.assertEqual(snapshot["captured"], "2026-10-31")
        self.assertNotIn("7777", items)
        self.assertEqual(len(items), 14)

    def test_open_catalogue_prefers_the_manifest_when_its_files_exist(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CATALOGUE_SOURCE", None)
            catalogue = open_catalogue(self.root)
        self.assertIsInstance(catalogue, BungieManifestCatalogue)
        self.assertFalse(catalogue.is_local)

    def test_serves_all_three_record_types(self):
        counts = self.catalogue.counts()
        self.assertEqual(counts, {"weapons": 3, "perks": 10, "pools": 3})
        self.assertEqual(self.catalogue.get_record("weapons", "test-rifle_s1")["bungie_hash"], 1001)
        self.assertIsNone(self.catalogue.get_record("weapons", "missing_s1"))

    def test_generated_pool_maps_onto_every_roll_column(self):
        pool = self.catalogue.get_record("pools", "test-rifle_s1")
        mapped = {
            column: [perk for slot in matching_pool_slots(pool, column) for perk in slot["perk_ids"]]
            for column in ("barrel", "sight", "magazine", "perk_3", "perk_4")
        }
        self.assertEqual(mapped["barrel"], ["arrowhead-brake", "smallbore"])
        self.assertEqual(mapped["sight"], [])
        self.assertEqual(mapped["magazine"], ["tactical-mag", "extended-mag"])
        self.assertEqual(mapped["perk_3"], ["rampage", "kill-clip", "surplus"])
        self.assertEqual(mapped["perk_4"], ["outlaw", "frenzy"])

    def test_verified_pool_and_curated_perks_survive_unchanged(self):
        overlay = json.loads((self.root / OVERLAY_RELATIVE).read_text(encoding="utf-8"))
        self.assertEqual(self.catalogue.get_record("pools", "verified-rifle_s1"), overlay["verified_pools"]["verified-rifle_s1"])
        self.assertEqual(self.catalogue.get_record("perks", "rampage"), overlay["perks"]["rampage"])
        seeded = self.catalogue.get_record("perks", "surplus")
        self.assertEqual(seeded["verification_status"], "unverified")

    def test_unresolvable_weapon_is_unknown_never_verified(self):
        pool = self.catalogue.get_record("pools", "ghost-rifle_s1")
        self.assertEqual((pool["pool_status"], pool["verification_status"]), ("unknown", "unverified"))

    def test_drift_between_verified_pools_and_the_manifest_is_reported(self):
        self.catalogue.counts()
        drift = self.catalogue.report["verified_pool_drift"]
        self.assertEqual([entry["weapon_id"] for entry in drift], ["verified-rifle_s1"])
        self.assertEqual(self.catalogue.report["weapons_missing_from_manifest"], ["ghost-rifle_s1"])

    def test_every_served_record_satisfies_the_repository_schemas(self):
        validators = {"weapons": "weapon", "perks": "perk", "pools": "weapon-perk-pool"}
        for record_type, schema_name in validators.items():
            validator = schema_validator(schema_name)
            for record in self.catalogue.list_records(record_type):
                errors = [error.message for error in validator.iter_errors(record)]
                self.assertEqual(errors, [], f"{record_type}:{record.get('id') or record.get('weapon_id')}")

    def test_dim_wishlist_export_runs_unchanged_on_the_manifest_catalogue(self):
        recommendation = {
            "weapon_id": "verified-rifle_s1", "mode": "pve", "verification_status": "verified",
            "verified_for_sandbox": None, "last_reviewed": "2026-10-31", "rating": None,
            "recommendations": [{
                "name": "Fixture roll", "barrel": "arrowhead-brake", "magazine": "tactical-mag",
                "perk_3": "rampage", "perk_4": "outlaw", "description": "Fixture roll for the manifest catalogue.",
            }],
            "weapon_perk_pool_id": "verified-rifle_s1",
        }
        directory = self.root / "recommendations" / "curated" / "pve"
        directory.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.root / "recommendations", ignore_errors=True)
        (directory / "verified-rifle_s1.json").write_text(json.dumps(recommendation), encoding="utf-8")
        with patch.dict(os.environ, {k: v for k, v in os.environ.items() if k != "CATALOGUE_SOURCE"}, clear=True):
            wishlist = generate_wishlist(self.root, source="curated")
        self.assertEqual(
            wishlist,
            "title:Weapon Wishlist — Curated\n"
            "description:Curated PvE and PvP weapon rolls.\n\n"
            "dimwishlist:item=1002&perks=5101,5201,5301,5401#notes:PVE | Fixture roll\n",
        )

    def test_build_failure_is_reported_as_a_catalogue_error(self):
        broken = BungieManifestCatalogue(self.root)
        broken.snapshot_path = self.root / "catalogue" / "missing.json"
        with self.assertRaises(CatalogueError) as raised:
            broken.list_records("weapons")
        self.assertIn("failed", str(raised.exception))

    def test_missing_node_is_reported_as_a_catalogue_error(self):
        without_node = BungieManifestCatalogue(self.root, node="definitely-not-installed-node")
        with self.assertRaises(CatalogueError) as raised:
            without_node.list_records("weapons")
        self.assertIn("Node.js", str(raised.exception))

    def test_node_regression_tests_pass(self):
        completed = subprocess.run(
            [NODE, "--test", str(ROOT / "tests" / "test_build_manifest_catalogue.mjs")],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout[-1500:] + completed.stderr[-500:])


class CatalogueSourceSelectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def _env(self, value=None):
        environment = {key: val for key, val in os.environ.items() if key != "CATALOGUE_SOURCE"}
        if value is not None:
            environment["CATALOGUE_SOURCE"] = value
        return patch.dict(os.environ, environment, clear=True)

    def _add_manifest_files(self):
        (self.root / "catalogue").mkdir()
        (self.root / OVERLAY_RELATIVE).write_text("{}", encoding="utf-8")
        (self.root / SNAPSHOT_RELATIVE).write_text("{}", encoding="utf-8")

    def test_without_manifest_files_the_default_is_still_supabase(self):
        with self._env():
            self.assertIsInstance(open_catalogue(self.root), SupabaseCatalogue)

    def test_manifest_files_switch_the_default_source(self):
        self._add_manifest_files()
        with self._env():
            self.assertIsInstance(open_catalogue(self.root), BungieManifestCatalogue)

    def test_supabase_can_be_forced_even_when_manifest_files_exist(self):
        self._add_manifest_files()
        with self._env("supabase"):
            self.assertIsInstance(open_catalogue(self.root), SupabaseCatalogue)

    def test_forcing_the_manifest_without_files_explains_how_to_create_them(self):
        with self._env("bungie-manifest"):
            with self.assertRaises(CatalogueError) as raised:
                open_catalogue(self.root)
        self.assertIn("export_catalogue_overlay.py", str(raised.exception))

    def test_unknown_source_is_rejected(self):
        with self._env("postgres"):
            with self.assertRaises(CatalogueError):
                open_catalogue(self.root)

    def test_local_json_fixtures_still_take_precedence(self):
        for folder in ("weapons", "perks", "weapon-perk-pools"):
            (self.root / "data" / folder).mkdir(parents=True)
        self._add_manifest_files()
        with self._env("bungie-manifest"):
            self.assertIsInstance(open_catalogue(self.root), JsonCatalogue)


class ExportOverlayTests(unittest.TestCase):
    def test_exports_curated_records_and_only_verified_known_pools(self):
        class FakeCatalogue:
            records = {
                "weapons": [{"id": "b_s1"}, {"id": "a_s1"}],
                "perks": [{"id": "z"}, {"id": "y"}],
                "pools": [
                    {"weapon_id": "a_s1", "verification_status": "verified", "pool_status": "complete"},
                    {"weapon_id": "b_s1", "verification_status": "unverified", "pool_status": "complete"},
                    {"weapon_id": "c_s1", "verification_status": "verified", "pool_status": "unknown"},
                ],
            }

            def list_records(self, record_type):
                return list(self.records[record_type])

        overlay = build_overlay(FakeCatalogue(), "2026-10-31")
        self.assertEqual(overlay["format"], 1)
        self.assertEqual(list(overlay["weapons"]), ["a_s1", "b_s1"])
        self.assertEqual(list(overlay["perks"]), ["y", "z"])
        self.assertEqual(list(overlay["verified_pools"]), ["a_s1"])


if __name__ == "__main__":
    unittest.main()
