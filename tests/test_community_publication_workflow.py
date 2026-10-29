"""Static regression tests for automatic, community-only recommendation publication."""
from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PUBLISH = (ROOT / ".github" / "workflows" / "publish-verified-community-recommendation.yml").read_text(encoding="utf-8")
INTAKE = (ROOT / ".github" / "workflows" / "validate-recommendation-issue.yml").read_text(encoding="utf-8")
SCOPE = (ROOT / "scripts" / "check_community_publication_scope.py").read_text(encoding="utf-8")
MERGE = (ROOT / "scripts" / "merge_community_recommendation.py").read_text(encoding="utf-8")


class CommunityPublicationWorkflowTests(unittest.TestCase):
    def test_only_narrow_dispatch_job_has_actions_write(self):
        self.assertIn("dispatch-publication:", INTAKE)
        validate_job = INTAKE.split("  validate:", 1)[1].split("  dispatch-publication:", 1)[0]
        self.assertNotIn("actions: write", validate_job)
        self.assertIn("needs.validate.outputs.publication_ready == 'true'", INTAKE)
        self.assertIn("github.rest.actions.createWorkflowDispatch", INTAKE)
        self.assertIn("guard-submission-mutation:", INTAKE)
        self.assertIn("intake:submission-invalidated", INTAKE)
        self.assertIn("recommendation_snapshot.cjs", INTAKE)
        self.assertIn("canonical dependency checks passed", INTAKE.lower())
        self.assertIn("--published-by", PUBLISH)
        self.assertNotIn("--last-reviewed", PUBLISH)
        self.assertIn("mechanically_validated", PUBLISH)
        self.assertIn("no maintainer review", PUBLISH.lower())

    def test_publisher_is_dispatch_only_and_rechecks_snapshot(self):
        self.assertIn("workflow_dispatch:", PUBLISH)
        self.assertNotIn("\n  issues:\n    types: [labeled]", PUBLISH)
        self.assertNotIn("github.event.label.name", PUBLISH)
        self.assertIn("intake:mechanical-checks-passed", PUBLISH)
        self.assertIn("intake:ready-to-publish", PUBLISH)
        self.assertIn("recommendation_snapshot.cjs", PUBLISH)
        self.assertIn("intake:submission-invalidated", PUBLISH)
        self.assertIn("REQUESTED_ISSUE_NUMBER: ${{ inputs.issue_number }}", PUBLISH)

    def test_edited_or_reopened_issues_are_invalidated_and_never_republished(self):
        self.assertIn("github.event.action == 'opened'", INTAKE)
        self.assertIn("github.event.action == 'edited' || github.event.action == 'reopened'", INTAKE)
        self.assertIn("snapshot.verifySnapshot", INTAKE)
        self.assertIn("ISSUE_UPDATED_AT", PUBLISH)

    def test_builder_output_path_matches_record_path(self):
        expected = "${{ runner.temp }}/mechanically-validated-community-recommendation.json"
        self.assertIn(f"OUTPUT_PATH: {expected}", PUBLISH)
        self.assertIn(f"RECORD_PATH: {expected}", PUBLISH)

    def test_publisher_only_generates_and_stages_community_outputs(self):
        self.assertIn("python scripts/export_dim_wishlist.py --source community --output exports/d2wd_community.txt", PUBLISH)
        self.assertIn("python scripts/export_dim_wishlist.py --source community --output exports/d2wd_community.txt --check", PUBLISH)
        self.assertNotIn("--source curated", PUBLISH)
        git_add_lines = [line.strip() for line in PUBLISH.splitlines() if line.strip().startswith("git add")]
        self.assertTrue(all("d2wd_curated.txt" not in line and "recommendations/curated" not in line for line in git_add_lines))
        self.assertIn('git add -A -- "$TARGET_PATH" exports/d2wd_community.txt', PUBLISH)
        self.assertIn("scripts/merge_community_recommendation.py", PUBLISH)
        self.assertNotIn("*--issue-${ISSUE_NUMBER}.json", PUBLISH)
        self.assertIn('recommendations/community/(pve|pvp)/', SCOPE)
        self.assertIn("recommendations/community/pve|pvp/<weapon-id>.json", MERGE)
        self.assertIn("check_community_publication_scope.py", PUBLISH)
        self.assertIn("git diff --quiet HEAD -- recommendations/curated exports/d2wd_curated.txt", PUBLISH)

    def test_publisher_never_pushes_directly_to_main(self):
        self.assertIn('git push origin "HEAD:refs/heads/$BRANCH_NAME"', PUBLISH)
        self.assertNotIn("git push origin HEAD:main", PUBLISH)
        self.assertIn("permission-pull-requests: write", PUBLISH)

    def test_publisher_waits_for_all_required_checks_and_merges_a_pr(self):
        self.assertIn("github.rest.pulls.create", PUBLISH)
        self.assertIn("github.rest.checks.listForRef", PUBLISH)
        self.assertIn("github.rest.pulls.merge", PUBLISH)
        self.assertIn("Python 3.11", PUBLISH)
        self.assertIn("Python 3.12", PUBLISH)
        self.assertIn("Python 3.13", PUBLISH)
        self.assertIn("Node.js weapon perk-pool generator tests", PUBLISH)
        self.assertIn("run.app.id === 15368", PUBLISH)
        self.assertIn("EXPECTED_BASE_SHA", PUBLISH)

    def test_final_recheck_and_app_merge_are_separate_steps(self):
        recheck_step = PUBLISH.split(
            "- name: Recheck issue snapshot and publication freshness before merge", 1
        )[1].split("\n      - name:", 1)[0]
        merge_step = PUBLISH.split(
            "- name: Merge publication pull request through the publisher app", 1
        )[1].split("\n      - name:", 1)[0]

        self.assertIn("github-token: ${{ github.token }}", recheck_step)
        self.assertIn("github.rest.issues.get", recheck_step)
        self.assertIn("github.rest.issues.listComments", recheck_step)
        self.assertIn("github.rest.pulls.get", recheck_step)
        self.assertIn("github.rest.repos.getBranch", recheck_step)
        self.assertIn("snapshot.verifySnapshot", recheck_step)
        self.assertIn("intake:ready-to-publish", recheck_step)
        self.assertIn("core.setOutput('approved', 'true')", recheck_step)
        self.assertNotIn("github.rest.pulls.merge", recheck_step)
        self.assertNotIn("PUBLISHER_APP_TOKEN", recheck_step)

        self.assertIn("github-token: ${{ steps.publisher-token.outputs.token }}", merge_step)
        self.assertNotIn("github-token: ${{ github.token }}", merge_step)
        self.assertIn("github.rest.pulls.get", merge_step)
        self.assertIn("github.rest.repos.getBranch", merge_step)
        self.assertIn("github.rest.pulls.merge", merge_step)
        self.assertIn("sha: expectedHead", merge_step)
        self.assertNotIn("github.rest.issues.", merge_step)
        self.assertNotIn("require('@actions/github')", merge_step)
        self.assertNotIn("PUBLISHER_APP_TOKEN", merge_step)
        self.assertNotIn("publisherAppGithub", PUBLISH)

    def test_merged_publication_branch_is_deleted_only_after_identity_checks(self):
        cleanup_step = PUBLISH.split(
            "- name: Delete the merged publication branch safely", 1
        )[1].split("\\n      - name:", 1)[0]

        self.assertIn("steps.merge.outputs.commit_sha != ''", cleanup_step)
        self.assertIn("github-token: ${{ steps.publisher-token.outputs.token }}", cleanup_step)
        self.assertIn("pull.merged", cleanup_step)
        self.assertIn("pull.head.sha !== expectedHead", cleanup_step)
        self.assertIn("branch.commit.sha !== expectedHead", cleanup_step)
        self.assertIn("github.rest.git.deleteRef", cleanup_step)
        self.assertIn("already deleted", cleanup_step)
        self.assertIn("leaving it untouched", cleanup_step)

    def test_publisher_token_is_scoped_to_the_current_repository(self):
        self.assertIn("repositories: ${{ github.event.repository.name }}", PUBLISH)
        self.assertNotIn("repositories: destiny-2-weapon-database", PUBLISH)

    def test_codeowners_covers_the_repository_owner(self):
        codeowners = (ROOT / ".github" / "CODEOWNERS").read_text(encoding="utf-8")
        self.assertRegex(codeowners, r"(?m)^\*\s+@rox4H\s*$")


if __name__ == "__main__":
    unittest.main()
