import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from build_community_recommendation import (  # noqa: E402
    CommunityRecommendationBuildError,
    build_community_recommendation,
)


class BuildCommunityRecommendationTests(unittest.TestCase):
    def setUp(self):
        self.issue = {
            "number": 84,
            "created_at": "2026-10-28T09:30:00Z",
            "title": "[Recommendation proposal] Solar add clear",
            "state": "open",
            "user": {"login": "OriginalAuthor"},
            "body": "The GitHub author is not a body field.",
        }
        self.report = {
            "errors": [],
            "submitted_mode": "pve",
            "resolved_weapon_id": "sample-weapon_s2",
            "canonical_recommendation": {
                "name": "Solar add clear",
                "barrel": "sample-barrel",
                "magazine": "sample-magazine",
                "perk_3": "sample-trait-1",
                "perk_4": "incandescent",
                "description": "A candidate roll for Solar support and add clear.",
            },
        }

    def test_record_uses_github_issue_creator_and_aggregate_weapon_path(self):
        path, record = build_community_recommendation(self.issue, self.report)
        self.assertEqual(path, "recommendations/community/pve/sample-weapon_s2.json")
        self.assertNotIn("github_username", record)
        self.assertNotIn("mode", record)
        self.assertEqual(record["weapon_id"], "sample-weapon_s2")
        self.assertEqual(record["weapon_perk_pool_id"], "sample-weapon_s2")
        self.assertEqual(record["publication_status"], "pending")
        self.assertEqual(record["validation_scope"], "canonical-dependencies-and-perk-slots")
        self.assertIsNone(record["published_by"])
        self.assertEqual(len(record["recommendations"]), 1)
        roll = record["recommendations"][0]
        self.assertEqual(roll["github_username"], "OriginalAuthor")
        self.assertEqual(roll["mode"], "pve")
        self.assertEqual(roll["issue_number"], 84)
        self.assertEqual(roll["submitted_at"], "2026-10-28T09:30:00Z")
        self.assertEqual(roll["barrel"], "sample-barrel")

    def test_record_preserves_sight_component_for_legacy_weapon(self):
        import tempfile

        self.report["canonical_recommendation"].pop("barrel")
        self.report["canonical_recommendation"]["sight"] = "sample-sight"
        with tempfile.TemporaryDirectory() as directory:
            path, record = build_community_recommendation(
                self.issue, self.report, root=Path(directory)
            )
        self.assertEqual(path, "recommendations/community/pve/sample-weapon_s2.json")
        roll = record["recommendations"][0]
        self.assertEqual(roll["sight"], "sample-sight")
        self.assertNotIn("barrel", roll)
        self.assertEqual(roll["magazine"], "sample-magazine")

    def test_body_cannot_override_source_username(self):
        self.issue["body"] = "github_username: forged-user"
        _, record = build_community_recommendation(self.issue, self.report)
        self.assertEqual(record["recommendations"][0]["github_username"], "OriginalAuthor")

    def test_mechanical_errors_block_integration(self):
        self.report["errors"] = ["Perk does not belong to the weapon pool."]
        with self.assertRaisesRegex(CommunityRecommendationBuildError, "Mechanical validation must pass"):
            build_community_recommendation(self.issue, self.report)

    def test_incomplete_slot_mapping_blocks_integration(self):
        self.report["canonical_recommendation"].pop("perk_4")
        with self.assertRaisesRegex(CommunityRecommendationBuildError, "missing required field perk_4"):
            build_community_recommendation(self.issue, self.report)

    def test_invalid_login_is_rejected(self):
        self.issue["user"]["login"] = "../../main"
        with self.assertRaisesRegex(CommunityRecommendationBuildError, "valid GitHub login"):
            build_community_recommendation(self.issue, self.report)

    def test_closed_issue_is_not_integrated(self):
        self.issue["state"] = "closed"
        with self.assertRaisesRegex(CommunityRecommendationBuildError, "open recommendation"):
            build_community_recommendation(self.issue, self.report)

    def test_duplicate_roll_cannot_be_built_even_with_a_clean_report(self):
        import json
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            curated_path = root / "recommendations" / "curated" / "pve" / "sample-weapon_s2.json"
            curated_path.parent.mkdir(parents=True)
            curated_path.write_text(json.dumps({
                "weapon_id": "sample-weapon_s2",
                "mode": "pve",
                "recommendations": [{
                    "name": "Different name",
                    "barrel": "sample-barrel",
                    "magazine": "sample-magazine",
                    "perk_3": "sample-trait-1",
                    "perk_4": "incandescent",
                    "description": "Same exact roll.",
                }],
            }), encoding="utf-8")
            with self.assertRaisesRegex(CommunityRecommendationBuildError, "Duplicate recommendation"):
                build_community_recommendation(self.issue, self.report, root=root)


if __name__ == "__main__":
    unittest.main()
