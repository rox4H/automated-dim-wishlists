"""Tests for idempotent, provenance-safe aggregate community publishing."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from merge_community_recommendation import CommunityMergeError, merge_community_recommendation  # noqa: E402


class MergeCommunityRecommendationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.record_path = self.root / "incoming.json"
        self.target_path = "recommendations/community/pve/sample-weapon_s5.json"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _record(self, issue_number: int, username: str, name: str, mode: str = "pve") -> dict:
        return {
            "weapon_id": "sample-weapon_s5",
            "weapon_perk_pool_id": "sample-weapon_s5",
            "publication_status": "mechanically_validated",
            "validation_scope": "canonical-dependencies-and-perk-slots",
            "published_by": "github-actions[bot]",
            "recommendations": [{
                "mode": mode,
                "name": name,
                "barrel": "sample-barrel",
                "magazine": "sample-magazine",
                "perk_3": "sample-trait-1",
                "perk_4": "sample-trait-2",
                "description": f"Description for {name}.",
                "github_username": username,
                "issue_number": issue_number,
                "submitted_at": "2026-10-29T07:00:00Z",
                "publication_status": "mechanically_validated",
                "validation_scope": "canonical-dependencies-and-perk-slots",
                "published_by": "github-actions[bot]",
            }],
        }

    def _publish(self, issue_number: int, username: str, name: str, mode: str = "pve") -> Path:
        self.record_path.write_text(json.dumps(self._record(issue_number, username, name, mode)), encoding="utf-8")
        target_path = f"recommendations/community/{mode}/sample-weapon_s5.json"
        return merge_community_recommendation(
            self.root,
            record_path=self.record_path,
            target_path=target_path,
            issue_number=issue_number,
            github_username=username,
        )

    def test_appends_multiple_users_to_one_weapon_and_mode_file(self) -> None:
        self._publish(84, "Alice", "PvE roll", "pve")
        target = self._publish(91, "Bob", "Another PvE roll", "pve")
        payload = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(target.relative_to(self.root).as_posix(), "recommendations/community/pve/sample-weapon_s5.json")
        self.assertEqual(len(payload["recommendations"]), 2)
        self.assertEqual(
            [(item["issue_number"], item["github_username"], item["mode"]) for item in payload["recommendations"]],
            [(84, "Alice", "pve"), (91, "Bob", "pve")],
        )

    def test_pve_and_pvp_are_written_to_separate_files(self) -> None:
        pve_path = self._publish(84, "Alice", "PvE roll", "pve")
        pvp_path = self._publish(91, "Bob", "PvP roll", "pvp")
        pve = json.loads(pve_path.read_text(encoding="utf-8"))
        pvp = json.loads(pvp_path.read_text(encoding="utf-8"))
        self.assertEqual(pve_path.relative_to(self.root).as_posix(), "recommendations/community/pve/sample-weapon_s5.json")
        self.assertEqual(pvp_path.relative_to(self.root).as_posix(), "recommendations/community/pvp/sample-weapon_s5.json")
        self.assertEqual([entry["mode"] for entry in pve["recommendations"]], ["pve"])
        self.assertEqual([entry["mode"] for entry in pvp["recommendations"]], ["pvp"])

    def test_same_issue_rerun_replaces_only_its_own_entry(self) -> None:
        self._publish(84, "Alice", "Old name")
        self._publish(91, "Bob", "Other user's roll")
        target = self._publish(84, "Alice", "Updated name")
        payload = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(len(payload["recommendations"]), 2)
        self.assertEqual(payload["recommendations"][0]["name"], "Updated name")
        self.assertEqual(payload["recommendations"][1]["name"], "Other user's roll")

    def test_same_issue_cannot_be_published_again_under_another_mode(self) -> None:
        self._publish(84, "Alice", "Original PvE roll", "pve")
        self.record_path.write_text(json.dumps(self._record(84, "Alice", "Changed to PvP", "pvp")), encoding="utf-8")
        with self.assertRaisesRegex(CommunityMergeError, "already published"):
            merge_community_recommendation(
                self.root,
                record_path=self.record_path,
                target_path="recommendations/community/pvp/sample-weapon_s5.json",
                issue_number=84,
                github_username="Alice",
            )

    def test_same_issue_cannot_replace_another_user(self) -> None:
        self._publish(84, "Alice", "Original")
        self.record_path.write_text(json.dumps(self._record(84, "Mallory", "Forged")), encoding="utf-8")
        with self.assertRaisesRegex(CommunityMergeError, "another user"):
            merge_community_recommendation(
                self.root,
                record_path=self.record_path,
                target_path=self.target_path,
                issue_number=84,
                github_username="Mallory",
            )

    def test_rejects_path_for_another_weapon(self) -> None:
        self.record_path.write_text(json.dumps(self._record(84, "Alice", "Original")), encoding="utf-8")
        with self.assertRaisesRegex(CommunityMergeError, "does not match"):
            merge_community_recommendation(
                self.root,
                record_path=self.record_path,
                target_path="recommendations/community/pve/other-weapon_s5.json",
                issue_number=84,
                github_username="Alice",
            )

    def test_rejects_mode_mismatch_between_directory_and_recommendation(self) -> None:
        self.record_path.write_text(json.dumps(self._record(84, "Alice", "PvP roll", "pvp")), encoding="utf-8")
        with self.assertRaisesRegex(CommunityMergeError, "directory must match"):
            merge_community_recommendation(
                self.root,
                record_path=self.record_path,
                target_path="recommendations/community/pve/sample-weapon_s5.json",
                issue_number=84,
                github_username="Alice",
            )

    def test_rejects_duplicate_issue_numbers_already_in_aggregate(self) -> None:
        target = self._publish(84, "Alice", "First")
        payload = json.loads(target.read_text(encoding="utf-8"))
        payload["recommendations"].append(dict(payload["recommendations"][0]))
        target.write_text(json.dumps(payload), encoding="utf-8")
        self.record_path.write_text(json.dumps(self._record(91, "Bob", "Incoming")), encoding="utf-8")
        with self.assertRaisesRegex(CommunityMergeError, "duplicate source issue"):
            merge_community_recommendation(
                self.root,
                record_path=self.record_path,
                target_path=self.target_path,
                issue_number=91,
                github_username="Bob",
            )


if __name__ == "__main__":
    unittest.main()
