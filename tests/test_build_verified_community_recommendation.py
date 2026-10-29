"""Tests for the maintainer-only verified community publisher builder."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from build_verified_community_recommendation import (  # noqa: E402
    VerifiedCommunityBuildError,
    build_verified_community_recommendation,
)


class BuildVerifiedCommunityRecommendationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "data" / "weapons").mkdir(parents=True)
        (self.root / "data" / "perks").mkdir(parents=True)
        (self.root / "data" / "weapon-perk-pools").mkdir(parents=True)
        (self.root / "recommendations" / "curated" / "pve").mkdir(parents=True)
        (self.root / "recommendations" / "community" / "pve").mkdir(parents=True)
        (self.root / "recommendations" / "community" / "pvp").mkdir(parents=True)
        self._write("data/weapons/sample-weapon_s2.json", {
            "id": "sample-weapon_s2", "name": "Sample Weapon", "bungie_hash": 123456,
            "weapon_type": "Auto Rifle", "damage_type": "Solar", "ammo_type": "Primary",
            "frame": "Adaptive Frame", "rarity": "Legendary", "source_activity": "Test activity",
            "season": "Season 2", "craftable": False, "origin_trait": None,
            "verification_status": "verified", "last_verified": "2026-10-28", "notes": [],
            "season_number": 2, "breaker": [], "deepsight_harmonizable": False,
            "weapon_slot": "Energy", "foundry": None, "catalogue_category": "unknown", "variant_of": None,
        })
        perk_rows = [
            ("sample-barrel", "Sample Barrel", 2001, "barrel"),
            ("sample-magazine", "Sample Magazine", 2002, "magazine"),
            ("sample-trait-1", "Sample Trait One", 2003, "trait"),
            ("incandescent", "Incandescent", 2004, "trait"),
        ]
        for perk_id, name, perk_hash, perk_type in perk_rows:
            self._write(f"data/perks/{perk_id}.json", {
                "id": perk_id, "name": name, "bungie_hash": perk_hash, "perk_type": perk_type,
                "description": "Verified test definition.", "verification_status": "verified",
                "last_verified": "2026-10-28", "notes": [],
            })
        self._write("data/weapon-perk-pools/sample-weapon_s2.json", {
            "weapon_id": "sample-weapon_s2", "weapon_hash": 123456,
            "version_label": "Season 2", "pool_status": "complete",
            "verification_status": "verified", "last_verified": "2026-10-28",
            "slots": [
                {"id": "barrel", "label": "Barrel", "selection_mode": "choose-one", "perk_ids": ["sample-barrel"], "notes": []},
                {"id": "magazine", "label": "Magazine", "selection_mode": "choose-one", "perk_ids": ["sample-magazine"], "notes": []},
                {"id": "trait-1", "label": "Trait 1", "selection_mode": "choose-one", "perk_ids": ["sample-trait-1"], "notes": []},
                {"id": "trait-2", "label": "Trait 2", "selection_mode": "choose-one", "perk_ids": ["incandescent"], "notes": []},
            ],
            "notes": [], "sources": [
                {"url": "https://example.com/pool", "claim": "Exact pool fixture.", "accessed": "2026-10-28"}
            ],
        })
        self.issue = {
            "number": 84, "created_at": "2026-10-28T09:30:00Z", "title": "[Recommendation proposal] Solar add clear", "state": "open",
            "user": {"login": "OriginalAuthor"}, "body": "Proposal body.",
        }
        self.report = {
            "errors": [], "duplicate_matches": [], "submitted_mode": "pve",
            "resolved_weapon_id": "sample-weapon_s2",
            "canonical_recommendation": {
                "name": "Solar add clear", "barrel": "sample-barrel", "magazine": "sample-magazine",
                "perk_3": "sample-trait-1", "perk_4": "incandescent",
                "description": "Verified Solar add-clear candidate in the test sandbox.",
            },
        }
    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write(self, path: str, payload: dict) -> None:
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    def test_builds_verified_record_without_sandbox_or_external_sources(self) -> None:
        path, record = build_verified_community_recommendation(
            self.issue, self.report,
            published_by="Maintainer-1", root=self.root,
        )
        self.assertEqual(path, "recommendations/community/pve/sample-weapon_s2.json")
        self.assertEqual(record["publication_status"], "mechanically_validated")
        self.assertEqual(record["validation_scope"], "canonical-dependencies-and-perk-slots")
        self.assertEqual(record["recommendations"][0]["github_username"], "OriginalAuthor")
        self.assertEqual(record["published_by"], "Maintainer-1")
        self.assertNotIn("verified_for_sandbox", record)
        self.assertNotIn("verification_sources", record)

    def test_unverified_weapon_blocks_publication(self) -> None:
        path = self.root / "data/weapons/sample-weapon_s2.json"
        weapon = json.loads(path.read_text(encoding="utf-8"))
        weapon["verification_status"] = "unverified"
        path.write_text(json.dumps(weapon), encoding="utf-8")
        with self.assertRaisesRegex(VerifiedCommunityBuildError, "must be marked verified"):
            self._build()

    def test_missing_or_candidate_hash_blocks_publication(self) -> None:
        path = self.root / "data/perks/incandescent.json"
        perk = json.loads(path.read_text(encoding="utf-8"))
        perk["bungie_hash"] = None
        path.write_text(json.dumps(perk), encoding="utf-8")
        with self.assertRaisesRegex(VerifiedCommunityBuildError, "positive bungie_hash"):
            self._build()

    def test_incomplete_perk_pool_blocks_publication(self) -> None:
        path = self.root / "data/weapon-perk-pools/sample-weapon_s2.json"
        pool = json.loads(path.read_text(encoding="utf-8"))
        pool["pool_status"] = "partial"
        path.write_text(json.dumps(pool), encoding="utf-8")
        with self.assertRaisesRegex(VerifiedCommunityBuildError, "pool_status must be complete"):
            self._build()

    def test_wrong_slot_perk_blocks_publication(self) -> None:
        self.report["canonical_recommendation"]["perk_4"] = "sample-trait-1"
        with self.assertRaisesRegex(VerifiedCommunityBuildError, "not in the correct verified slot"):
            self._build()

    def test_duplicate_roll_blocks_publication(self) -> None:
        self._write("recommendations/curated/pve/sample-weapon_s2.json", {
            "weapon_id": "sample-weapon_s2", "mode": "pve", "recommendations": [{
                "name": "Different name", "barrel": "sample-barrel", "magazine": "sample-magazine",
                "perk_3": "sample-trait-1", "perk_4": "incandescent", "description": "Exact duplicate.",
            }]
        })
        with self.assertRaisesRegex(VerifiedCommunityBuildError, "Duplicate recommendation"):
            self._build()

    def test_closed_issue_cannot_be_published(self) -> None:
        self.issue["state"] = "closed"
        with self.assertRaisesRegex(VerifiedCommunityBuildError, "Only an open recommendation"):
            self._build()

    def _build(self):
        return build_verified_community_recommendation(
            self.issue, self.report,
            published_by="Maintainer-1", root=self.root,
        )


    def test_unverified_unused_pool_perk_does_not_block_publication(self) -> None:
        self._write("data/perks/unverified-extra.json", {
            "id": "unverified-extra", "name": "Unverified Extra", "bungie_hash": None,
            "perk_type": "trait", "description": None,
            "verification_status": "unverified", "last_verified": None, "notes": [],
        })
        pool_path = self.root / "data" / "weapon-perk-pools" / "sample-weapon_s2.json"
        pool = json.loads(pool_path.read_text(encoding="utf-8"))
        pool["slots"][3]["perk_ids"].append("unverified-extra")
        pool_path.write_text(json.dumps(pool), encoding="utf-8")

        path, record = self._build()
        self.assertEqual(path, "recommendations/community/pve/sample-weapon_s2.json")
        self.assertEqual(record["publication_status"], "mechanically_validated")
        selected = record["recommendations"][0]
        self.assertEqual(
            {selected["barrel"], selected["magazine"], selected["perk_3"], selected["perk_4"]},
            {"sample-barrel", "sample-magazine", "sample-trait-1", "incandescent"},
        )

    def test_unverified_selected_perk_blocks_publication(self) -> None:
        path = self.root / "data" / "perks" / "incandescent.json"
        perk = json.loads(path.read_text(encoding="utf-8"))
        perk.update({"verification_status": "unverified", "last_verified": None})
        path.write_text(json.dumps(perk), encoding="utf-8")
        with self.assertRaisesRegex(VerifiedCommunityBuildError, "must be marked verified before community publication"):
            self._build()

    def test_automation_login_is_allowed_for_gated_publication(self) -> None:
        _, record = build_verified_community_recommendation(
            self.issue, self.report, published_by="github-actions[bot]",
            root=self.root,
        )
        self.assertEqual(record["published_by"], "github-actions[bot]")

if __name__ == "__main__":
    unittest.main()
