#!/usr/bin/env python3
"""Fail CI if privileged workflow write protections regress."""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = ROOT / ".github" / "workflows"
CODEOWNERS_PATH = ROOT / ".github" / "CODEOWNERS"
PINNED_USE = re.compile(r"^\s*uses:\s*([^\s#]+)(?:\s+#.*)?\s*$", re.MULTILINE)
FULL_SHA = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")
PUBLISHER_SECRETS = ("secrets.D2WD_PUBLISHER_APP_ID", "secrets.D2WD_PUBLISHER_APP_PRIVATE_KEY")


def main() -> int:
    errors: list[str] = []
    workflows = sorted(WORKFLOW_DIR.glob("*.yml")) + sorted(WORKFLOW_DIR.glob("*.yaml"))
    contents: dict[Path, str] = {}
    for path in workflows:
        content = path.read_text(encoding="utf-8")
        contents[path] = content
        for reference in PINNED_USE.findall(content):
            if not FULL_SHA.fullmatch(reference):
                errors.append(f"{path.relative_to(ROOT)}: action is not pinned to a full commit SHA: {reference}")
        if "pull_request_target:" in content:
            errors.append(f"{path.relative_to(ROOT)}: privileged repository workflows must not use pull_request_target.")

    publisher_path = WORKFLOW_DIR / "publish-verified-community-recommendation.yml"
    publisher = contents.get(publisher_path, "")
    if not publisher:
        errors.append("Dedicated community publisher workflow is missing.")
    if "github.ref == 'refs/heads/main'" not in publisher:
        errors.append("Publisher must refuse workflow_dispatch runs from non-main refs.")
    if re.search(r"(?m)^  issues:\s*$", publisher):
        errors.append("Publisher must not be triggerable by issue labels; only a trusted dispatch job may start it.")
    if "recommendation_snapshot.cjs" not in publisher:
        errors.append("Publisher must verify the original bot-recorded recommendation snapshot.")
    if not re.search(r"(?m)^\s+contents:\s*read\s*$", publisher):
        errors.append("Publisher GITHUB_TOKEN must have contents: read.")
    if re.search(r"(?m)^\s+contents:\s*write\s*$", publisher):
        errors.append("Publisher GITHUB_TOKEN must not have contents: write.")
    for permission, error in (
        ("checks: read", "Publisher GITHUB_TOKEN must have read access to checks."),
        ("pull-requests: read", "Publisher GITHUB_TOKEN must have read access to pull requests."),
    ):
        if permission not in publisher:
            errors.append(error)
    if "name: main-publisher" not in publisher:
        errors.append("Publisher must use the main-publisher environment.")
    if "actions/create-github-app-token@" not in publisher:
        errors.append("Publisher must obtain its write token from the dedicated GitHub App.")
    if "permission-contents: write" not in publisher:
        errors.append("Publisher App token must request contents: write.")
    if "permission-pull-requests: write" not in publisher:
        errors.append("Publisher App token must request pull-requests: write for its isolated publication PR.")
    if "persist-credentials: false" not in publisher:
        errors.append("Publisher checkout must not persist Git credentials.")
    if 'git push origin "HEAD:refs/heads/$BRANCH_NAME"' not in publisher:
        errors.append("Publisher must push to a uniquely named automation branch, not main.")
    if "git push origin HEAD:main" in publisher or "git push origin HEAD:refs/heads/main" in publisher:
        errors.append("Publisher must never push directly to main.")
    for marker in ("github.rest.pulls.create", "github.rest.checks.listForRef", "github.rest.pulls.merge"):
        if marker not in publisher:
            errors.append(f"Publisher must create a PR, wait for required checks, and merge via the dedicated app ({marker} missing).")
    recheck_step_match = re.search(
        r"(?ms)^      - name: Recheck issue snapshot and publication freshness before merge\n(.*?)(?=^      - name:|\Z)",
        publisher,
    )
    if not recheck_step_match:
        errors.append("Publisher must have a separate final issue/snapshot recheck step.")
    else:
        recheck_step = recheck_step_match.group(1)
        if "github-token: ${{ github.token }}" not in recheck_step:
            errors.append("Final issue/snapshot reads must use the scoped workflow token.")
        for marker in (
            "github.rest.issues.get",
            "github.rest.issues.listComments",
            "github.rest.pulls.get",
            "github.rest.repos.getBranch",
            "snapshot.verifySnapshot",
            "intake:ready-to-publish",
        ):
            if marker not in recheck_step:
                errors.append(f"Final issue/snapshot recheck is missing: {marker}.")
        if "github.rest.pulls.merge" in recheck_step:
            errors.append("The workflow-token recheck step must not merge the publication PR.")
        if "PUBLISHER_APP_TOKEN" in recheck_step:
            errors.append("The publisher App token must not be exposed to the issue/snapshot recheck.")

    merge_step_match = re.search(
        r"(?ms)^      - name: Merge publication pull request through the publisher app\n(.*?)(?=^      - name:|\Z)",
        publisher,
    )
    if not merge_step_match:
        errors.append("Publisher must have a dedicated final PR merge step.")
    else:
        merge_step = merge_step_match.group(1)
        if "github-token: ${{ steps.publisher-token.outputs.token }}" not in merge_step:
            errors.append("The final merge step must configure github-script with the dedicated publisher App token.")
        if "github-token: ${{ github.token }}" in merge_step:
            errors.append("The workflow token must not be used to merge the publication PR.")
        if "require('@actions/github')" in merge_step or "PUBLISHER_APP_TOKEN" in merge_step:
            errors.append("The merge step must not import @actions/github or construct a second Octokit client.")
        for marker in (
            "github.rest.pulls.get",
            "github.rest.repos.getBranch",
            "github.rest.pulls.merge",
        ):
            if marker not in merge_step:
                errors.append(f"Final App-token merge step is missing: {marker}.")
        if "github.rest.issues." in merge_step:
            errors.append("The publisher App token must not be used to read or mutate issues.")
        if "publisherAppGithub.rest.pulls.merge" in merge_step:
            errors.append("Use the github client already configured with the publisher App token.")
    for check in ("Python 3.11", "Python 3.12", "Python 3.13", "Node.js weapon perk-pool generator tests"):
        if check not in publisher:
            errors.append(f"Publisher must wait for the required check: {check}.")
    if "GITHUB_TOKEN: ${{ github.token }}" in publisher:
        errors.append("Publisher must not expose GITHUB_TOKEN to preflight/test/push code.")

    # The catalogue key in workflows must be read-only; the maintainer-only
    # database write secret is permitted only in a local CLI environment.
    for path, content in contents.items():
        if "SUPABASE_SECRET_KEY" in content or "service_role" in content or "sb_secret_" in content:
            errors.append(f"{path.relative_to(ROOT)}: workflows must not contain Supabase write/service-role credentials.")
        if path == publisher_path:
            continue
        for marker in (*PUBLISHER_SECRETS, "actions/create-github-app-token@", "PUBLISHER_TOKEN"):
            if marker in content:
                errors.append(f"{path.relative_to(ROOT)}: must not access publisher App credentials or tokens ({marker}).")
        if re.search(r"git\s+push\s+origin\s+.*(?:refs/heads/main|HEAD:main)", content):
            errors.append(f"{path.relative_to(ROOT)}: must not push directly to main.")

    validator_path = WORKFLOW_DIR / "validate-recommendation-issue.yml"
    validator = contents.get(validator_path, "")
    if "github.ref == 'refs/heads/main'" not in validator:
        errors.append("Recommendation intake must only use trusted main workflow code.")
    if re.search(r"(?m)^\s+contents:\s*write\s*$", validator):
        errors.append("Recommendation intake workflow must not have contents: write.")
    if "dispatch-publication:" not in validator:
        errors.append("Recommendation intake must use a separate publication-dispatch job.")
    validate_job = re.search(r"(?ms)^  validate:\n(.*?)(?=^  [a-z0-9_-]+:\n|\Z)", validator)
    if validate_job and re.search(r"(?m)^\s+actions:\s*write\s*$", validate_job.group(1)):
        errors.append("The job that parses untrusted issue content must not have actions: write.")
    dispatch_job = re.search(r"(?ms)^  dispatch-publication:\n(.*?)(?=^  [a-z0-9_-]+:\n|\Z)", validator)
    if not dispatch_job or not re.search(r"(?m)^\s+actions:\s*write\s*$", dispatch_job.group(1)):
        errors.append("Only the separate dispatch-publication job may have actions: write.")
    if "guard-submission-mutation:" not in validator:
        errors.append("Recommendation intake must invalidate edited/reopened submissions.")
    if "recommendation_snapshot.cjs" not in validator:
        errors.append("Recommendation intake must record and recheck an immutable submission snapshot.")
    if "intake:submission-invalidated" not in validator:
        errors.append("Recommendation intake must block edited/reopened submissions.")
    if "persist-credentials: false" not in validator:
        errors.append("Recommendation intake checkout must not persist Git credentials.")

    try:
        codeowners = CODEOWNERS_PATH.read_text(encoding="utf-8")
    except OSError:
        codeowners = ""
        errors.append(".github/CODEOWNERS is required for the main-branch security policy.")
    if not re.search(r"(?m)^\*\s+@rox4H\s*$", codeowners):
        errors.append(".github/CODEOWNERS must assign repository-wide code ownership to @rox4H.")

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"Workflow security checks passed ({len(workflows)} workflow files); publisher writes are isolated to PR-based publication.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
