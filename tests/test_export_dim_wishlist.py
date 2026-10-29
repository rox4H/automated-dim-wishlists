"""Regression tests for the DIM wishlist export script."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from export_dim_wishlist import WishlistExportError, generate_wishlist  # noqa: E402


class ExportDimWishlistTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "data" / "weapons").mkdir(parents=True)
        (self.root / "data" / "perks").mkdir(parents=True)
        (self.root / "data" / "weapon-perk-pools").mkdir(parents=True)
        (self.root / "recommendations" / "curated" / "pve").mkdir(parents=True)
        (self.root / "recommendations" / "curated" / "pvp").mkdir(parents=True)

        mapping = {
            "barrel-one": {"name": "Barrel One", "hash": 1001},
            "sight-one": {"name": "Sight One", "hash": 1005},
            "mag-one": {"name": "Mag One", "hash": 1002},
            "perk-one": {"name": "Perk One", "hash": 1003},
            "perk-two": {"name": "Perk Two", "hash": 1004},
        }
        for perk_id, entry in mapping.items():
            self._write_json(
                self.root / "data" / "perks" / f"{perk_id}.json",
                {
                    "id": perk_id,
                    "name": entry["name"],
                    "bungie_hash": entry["hash"],
                    "verification_status": "verified",
                    "last_verified": "2026-10-28",
                },
            )
        self._write_json(
            self.root / "data" / "weapons" / "sample-weapon_s1.json",
            {
                "id": "sample-weapon_s1",
                "name": "Sample Weapon",
                "bungie_hash": 123456,
                "verification_status": "verified",
                "last_verified": "2026-10-28",
            },
        )
        self._write_json(
            self.root / "data" / "weapon-perk-pools" / "sample-weapon_s1.json",
            {
                "weapon_id": "sample-weapon_s1",
                "weapon_hash": 123456,
                "version_label": "Sample Season 1",
                "pool_status": "complete",
                "verification_status": "verified",
                "last_verified": "2026-10-28",
                "slots": [
                    {"id": "barrel", "label": "Barrel", "selection_mode": "choose-one", "perk_ids": ["barrel-one"], "notes": []},
                    {"id": "scope-sight", "label": "Scope / Sight", "selection_mode": "choose-one", "perk_ids": ["sight-one"], "notes": []},
                    {"id": "magazine", "label": "Magazine", "selection_mode": "choose-one", "perk_ids": ["mag-one"], "notes": []},
                    {"id": "trait-1", "label": "Trait 1", "selection_mode": "choose-one", "perk_ids": ["perk-one", "perk-two"], "notes": []},
                    {"id": "trait-2", "label": "Trait 2", "selection_mode": "choose-one", "perk_ids": ["perk-one", "perk-two"], "notes": []},
                ],
                "notes": [],
                "sources": [{"url": "https://example.com/weapon-pool", "claim": "Fixture for exporter tests.", "accessed": "2026-10-28"}],
            },
        )
        self._write_json(
            self.root / "recommendations" / "curated" / "pve" / "sample-weapon_s1.json",
            {
                "weapon_id": "sample-weapon_s1",
                "weapon_perk_pool_id": "sample-weapon_s1",
                "mode": "pve",
                "verification_status": "unverified",
                "recommendations": [
                    {
                        "name": "Test roll",
                        "barrel": "barrel-one",
                        "magazine": "mag-one",
                        "perk_3": "perk-one",
                        "perk_4": "perk-two",
                        "description": "Candidate only.",
                    }
                ],
            },
        )
        self._write_json(
            self.root / "recommendations" / "curated" / "pvp" / "sample-weapon_s1.json",
            {
                "weapon_id": "sample-weapon_s1",
                "weapon_perk_pool_id": "sample-weapon_s1",
                "mode": "pvp",
                "verification_status": "verified",
                "recommendations": [
                    {
                        "name": "PvP roll",
                        "barrel": "barrel-one",
                        "magazine": "mag-one",
                        "perk_3": "perk-one",
                        "perk_4": "perk-two",
                        "description": "Verified test roll.",
                    }
                ],
            },
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _community_recommendation(
        self, mode: str, *, issue_number: int = 84, username: str | None = "community-user",
        name: str = "Community roll", barrel: str | None = "barrel-one",
        sight: str | None = None, perk_3: str = "perk-two", perk_4: str = "perk-one",
        description: str = "Community candidate.",
    ) -> dict:
        item = {
            "mode": mode,
            "name": name,
            "magazine": "mag-one",
            "perk_3": perk_3,
            "perk_4": perk_4,
            "description": description,
            "issue_number": issue_number,
            "submitted_at": "2026-10-28T10:00:00Z",
            "publication_status": "mechanically_validated",
            "validation_scope": "canonical-dependencies-and-perk-slots",
            "published_by": "github-actions[bot]",
        }
        if barrel is not None:
            item["barrel"] = barrel
        if sight is not None:
            item["sight"] = sight
        if username is not None:
            item["github_username"] = username
        return item

    def _community_record(
        self, mode: str, recommendations: list[dict], *, publication_status: str = "mechanically_validated",
    ) -> dict:
        return {
            "weapon_id": "sample-weapon_s1",
            "weapon_perk_pool_id": "sample-weapon_s1",
            "publication_status": publication_status,
            "validation_scope": "canonical-dependencies-and-perk-slots",
            "published_by": "github-actions[bot]",
            "recommendations": recommendations,
        }

    @staticmethod
    def _write_json(path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    def test_generates_native_dim_lines_in_recommendation_perk_order(self) -> None:
        content = generate_wishlist(self.root)
        self.assertIn("title:Weapon Wishlist — All", content)
        self.assertIn(
            "dimwishlist:item=123456&perks=1001,1002,1003,1004"
            "#notes:PVE | Test roll",
            content,
        )
        self.assertIn(
            "dimwishlist:item=123456&perks=1001,1002,1003,1004"
            "#notes:PVP | PvP roll",
            content,
        )
        self.assertEqual(content.count("\ndimwishlist:item="), 2)
        self.assertNotIn("UNVERIFIED", content)
        self.assertNotIn("MECHANICALLY VALIDATED", content)
        self.assertNotIn("Verified test roll", content)

    def test_legacy_sight_component_exports_in_first_perk_position(self) -> None:
        path = self.root / "recommendations" / "curated" / "pve" / "sample-weapon_s1.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        roll = payload["recommendations"][0]
        roll["sight"] = "sight-one"
        roll.pop("barrel")
        self._write_json(path, payload)

        content = generate_wishlist(self.root, mode="pve")
        self.assertIn(
            "dimwishlist:item=123456&perks=1005,1002,1003,1004"
            "#notes:PVE | Test roll",
            content,
        )

    def test_mode_filter_exports_only_the_requested_mode(self) -> None:
        content = generate_wishlist(self.root, mode="pve")
        self.assertEqual(content.count("\ndimwishlist:item="), 1)
        self.assertIn("PVE | Test roll", content)
        self.assertNotIn("PVP | PvP roll", content)

    def test_verified_only_excludes_unverified_recommendation_files(self) -> None:
        content = generate_wishlist(self.root, verified_only=True)
        self.assertEqual(content.count("\ndimwishlist:item="), 1)
        self.assertIn("PVP | PvP roll", content)
        self.assertNotIn("PVE | Test roll", content)

    def test_missing_canonical_perk_record_fails_instead_of_silently_dropping_the_roll(self) -> None:
        path = self.root / "recommendations" / "curated" / "pve" / "sample-weapon_s1.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["recommendations"][0]["perk_4"] = "not-mapped"
        self._write_json(path, payload)
        with self.assertRaisesRegex(WishlistExportError, "Missing canonical perk record"):
            generate_wishlist(self.root, mode="pve")

    def test_perk_hash_is_resolved_from_canonical_perk_record(self) -> None:
        path = self.root / "data" / "perks" / "barrel-one.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["bungie_hash"] = 9001
        self._write_json(path, payload)
        content = generate_wishlist(self.root, mode="pve")
        self.assertIn("dimwishlist:item=123456&perks=9001,1002,1003,1004", content)
        self.assertFalse((self.root / "data" / "dim-perk-hashes.json").exists())

    def test_missing_canonical_perk_hash_fails_closed(self) -> None:
        path = self.root / "data" / "perks" / "perk-two.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["bungie_hash"] = None
        self._write_json(path, payload)
        rec_path = self.root / "recommendations" / "curated" / "pve" / "sample-weapon_s1.json"
        rec = json.loads(rec_path.read_text(encoding="utf-8"))
        rec["recommendations"][0]["perk_4"] = "perk-two"
        self._write_json(rec_path, rec)
        with self.assertRaisesRegex(WishlistExportError, "no valid Bungie perk hash"):
            generate_wishlist(self.root, mode="pve")

    def test_invalid_weapon_hash_fails_closed(self) -> None:
        path = self.root / "data" / "weapons" / "sample-weapon_s1.json"
        self._write_json(path, {"id": "sample-weapon_s1", "name": "Sample Weapon", "bungie_hash": None})
        with self.assertRaisesRegex(WishlistExportError, "no valid Bungie item hash"):
            generate_wishlist(self.root, mode="pve")

    def test_community_recommendation_is_exported_with_github_username(self) -> None:
        payload = self._community_record(
            "pve",
            [self._community_recommendation("pve", name="Community roll", description="Community candidate.")],
                    )
        self._write_json(self.root / "recommendations" / "community" / "pve" / "sample-weapon_s1.json", payload)
        content = generate_wishlist(self.root, mode="pve")
        self.assertIn(
            "dimwishlist:item=123456&perks=1001,1002,1004,1003"
            "#notes:PVE | BY @community-user | Community roll",
            content,
        )
    def test_community_recommendation_exports_without_sandbox_or_external_sources(self) -> None:
        payload = self._community_record(
            "pve",
            [self._community_recommendation(
                "pve", name="Community roll",
                description="A boss damage-focused option.",
            )],
        )
        self._write_json(self.root / "recommendations" / "community" / "pve" / "sample-weapon_s1.json", payload)
        content = generate_wishlist(self.root, source="community", mode="pve")
        self.assertIn(
            "dimwishlist:item=123456&perks=1001,1002,1004,1003"
            "#notes:PVE | BY @community-user | Community roll",
            content,
        )
    def test_community_recommendation_requires_author_and_uses_weapon_filename(self) -> None:
        item = self._community_recommendation("pve", username=None)
        payload = self._community_record("pve", [item])
        self._write_json(self.root / "recommendations" / "community" / "pve" / "sample-weapon_s1.json", payload)
        with self.assertRaisesRegex(WishlistExportError, "requires a valid github_username"):
            generate_wishlist(self.root, mode="pve")

    def test_community_export_rejects_a_roll_with_the_wrong_mode_for_its_directory(self) -> None:
        payload = self._community_record("pve", [self._community_recommendation("pvp")])
        self._write_json(self.root / "recommendations" / "community" / "pve" / "sample-weapon_s1.json", payload)
        with self.assertRaisesRegex(WishlistExportError, "mode must match community directory"):
            generate_wishlist(self.root, mode="pve")
    def test_exports_separate_curated_and_community_sources(self) -> None:
        payload = self._community_record(
            "pve",
            [self._community_recommendation("pve", name="Community roll", description="Community candidate.")],
        )
        self._write_json(self.root / "recommendations" / "community" / "pve" / "sample-weapon_s1.json", payload)
        curated = generate_wishlist(self.root, source="curated")
        community = generate_wishlist(self.root, source="community")
        self.assertEqual(curated.count("\ndimwishlist:item="), 2)
        self.assertEqual(community.count("\ndimwishlist:item="), 1)
        self.assertNotIn("BY @community-user", curated)
        self.assertIn("BY @community-user", community)
    def test_unverified_community_record_is_rejected(self) -> None:
        payload = self._community_record(
            "pve", [self._community_recommendation("pve")], publication_status="pending",
        )
        self._write_json(self.root / "recommendations" / "community" / "pve" / "sample-weapon_s1.json", payload)
        with self.assertRaisesRegex(WishlistExportError, "only accepts mechanically validated recommendations"):
            generate_wishlist(self.root, source="community")
    def test_duplicate_roll_across_curated_and_community_blocks_both_exports(self) -> None:
        payload = self._community_record(
            "pve",
            [self._community_recommendation(
                "pve", name="Duplicate name does not matter",
                perk_3="perk-one", perk_4="perk-two",
                description="Different wording does not create a new roll.",
            )],
            publication_status="pending",
        )
        self._write_json(self.root / "recommendations" / "community" / "pve" / "sample-weapon_s1.json", payload)
        for source in ("curated", "community"):
            with self.subTest(source=source), self.assertRaisesRegex(
                WishlistExportError, "Duplicate recommendation found"
            ):
                generate_wishlist(self.root, source=source)
    def test_community_export_allows_unverified_unused_pool_alternatives(self) -> None:
        self._write_json(
            self.root / "data" / "perks" / "unverified-extra.json",
            {
                "id": "unverified-extra", "name": "Unverified Extra", "bungie_hash": 9999,
                "verification_status": "unverified", "last_verified": None,
            },
        )
        pool_path = self.root / "data" / "weapon-perk-pools" / "sample-weapon_s1.json"
        pool = json.loads(pool_path.read_text(encoding="utf-8"))
        pool["slots"][4]["perk_ids"].append("unverified-extra")
        self._write_json(pool_path, pool)
        payload = self._community_record(
            "pve",
            [self._community_recommendation("pve", name="Community roll", description="Community candidate.")],
        )
        self._write_json(self.root / "recommendations" / "community" / "pve" / "sample-weapon_s1.json", payload)
        output = generate_wishlist(self.root, source="community")
        self.assertIn("BY @community-user", output)
        self.assertIn("dimwishlist:item=", output)
    def test_community_json_exports_pve_and_pvp_from_separate_files(self) -> None:
        pve = self._community_record(
            "pve",
            [self._community_recommendation(
                "pve", issue_number=84, username="alice", name="Community PvE",
                description="PvE community roll.",
            )],
        )
        pvp = self._community_record(
            "pvp",
            [self._community_recommendation(
                "pvp", issue_number=86, username="bob", name="Community PvP",
                sight="sight-one", barrel=None, perk_3="perk-one", perk_4="perk-two",
                description="PvP community roll.",
            )],
        )
        self._write_json(self.root / "recommendations" / "community" / "pve" / "sample-weapon_s1.json", pve)
        self._write_json(self.root / "recommendations" / "community" / "pvp" / "sample-weapon_s1.json", pvp)
        community = generate_wishlist(self.root, source="community")
        pve_output = generate_wishlist(self.root, source="community", mode="pve")
        pvp_output = generate_wishlist(self.root, source="community", mode="pvp")
        self.assertEqual(community.count("\ndimwishlist:item="), 2)
        self.assertEqual(pve_output.count("\ndimwishlist:item="), 1)
        self.assertEqual(pvp_output.count("\ndimwishlist:item="), 1)
        self.assertIn("BY @alice", pve_output)
        self.assertNotIn("BY @bob", pve_output)
        self.assertIn("BY @bob", pvp_output)
        self.assertNotIn("BY @alice", pvp_output)

if __name__ == "__main__":
    unittest.main()
