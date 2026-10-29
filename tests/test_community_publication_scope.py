"""Regression tests for the community-only publication write set."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from check_community_publication_scope import (  # noqa: E402
    PublicationScopeError,
    validate_publication_scope,
)


class CommunityPublicationScopeTests(unittest.TestCase):
    issue_number = 84
    target = "recommendations/community/pve/abyss-defiant_s22.json"

    def test_allows_target_weapon_json_and_community_export(self):
        validate_publication_scope(self.issue_number, self.target, [self.target, "exports/d2wd_community.txt"])

    def test_rejects_curated_json_even_if_other_paths_are_valid(self):
        with self.assertRaisesRegex(PublicationScopeError, "only the target weapon/mode community JSON"):
            validate_publication_scope(self.issue_number, self.target, [self.target, "recommendations/curated/pve/abyss-defiant_s22.json"])

    def test_rejects_curated_wishlist(self):
        with self.assertRaisesRegex(PublicationScopeError, "only the target weapon/mode community JSON"):
            validate_publication_scope(self.issue_number, self.target, [self.target, "exports/d2wd_curated.txt"])

    def test_rejects_unrelated_community_json(self):
        with self.assertRaisesRegex(PublicationScopeError, "only the target weapon/mode community JSON"):
            validate_publication_scope(self.issue_number, self.target, [self.target, "recommendations/community/pve/other-weapon_s22.json"])

    def test_rejects_community_file_directly_in_root(self):
        root_path = "recommendations/community/abyss-defiant_s22.json"
        with self.assertRaisesRegex(PublicationScopeError, "recommendations/community/pve|pvp"):
            validate_publication_scope(self.issue_number, root_path, [root_path])

    def test_rejects_community_path_without_a_season_version(self):
        invalid_path = "recommendations/community/pve/abyss-defiant.json"
        with self.assertRaisesRegex(PublicationScopeError, "safe community JSON path"):
            validate_publication_scope(self.issue_number, invalid_path, [invalid_path])

    def test_rejects_curated_tracked_or_untracked_worktree_changes(self):
        for kwargs in (
            {"tracked_curated_changes": ["exports/d2wd_curated.txt"]},
            {"untracked_curated_paths": ["recommendations/curated/pve/unexpected.json"]},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaisesRegex(PublicationScopeError, "curated files changed"):
                validate_publication_scope(self.issue_number, self.target, [self.target], **kwargs)

    def test_issue_number_must_be_positive_even_for_weapon_scoped_paths(self):
        with self.assertRaisesRegex(PublicationScopeError, "positive integer"):
            validate_publication_scope(0, self.target, [self.target])

    def test_allows_a_no_op_only_when_target_is_already_published_unchanged(self):
        validate_publication_scope(self.issue_number, self.target, [], target_exists_in_head=True, target_unchanged_from_head=True)

    def test_rejects_an_empty_publication_without_an_existing_target(self):
        with self.assertRaisesRegex(PublicationScopeError, "not staged"):
            validate_publication_scope(self.issue_number, self.target, [], target_exists_in_head=False, target_unchanged_from_head=True)


if __name__ == "__main__":
    unittest.main()
