#!/usr/bin/env python3
"""Build one versioned community recommendation record from a mechanically valid issue."""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path

from recommendation_uniqueness import find_duplicate_recommendations, RecommendationScanError

WEAPON_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*_s[0-9]+(?:-[0-9]+)?$")
GITHUB_LOGIN_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")
ROOT = Path(__file__).resolve().parents[1]
SLOT_FIELDS = ("magazine", "perk_3", "perk_4")
COMPONENT_FIELDS = ("barrel", "sight")


class CommunityRecommendationBuildError(ValueError):
    """Raised when an issue cannot be safely materialized as community data."""


def build_community_recommendation(issue: dict, report: dict, root: Path = ROOT) -> tuple[str, dict]:
    """Return a repository path and JSON record; never accept an author from issue text."""
    if not isinstance(issue, dict) or not isinstance(report, dict):
        raise CommunityRecommendationBuildError("Issue and validation report must be JSON objects.")
    if not str(issue.get("title") or "").casefold().startswith("[recommendation proposal]"):
        raise CommunityRecommendationBuildError("Issue title must start with [Recommendation proposal].")
    if issue.get("state", "open") != "open":
        raise CommunityRecommendationBuildError("Only an open recommendation proposal can be integrated.")
    errors = report.get("errors")
    if not isinstance(errors, list) or errors:
        raise CommunityRecommendationBuildError("Mechanical validation must pass before integration.")

    number = issue.get("number")
    if not isinstance(number, int) or isinstance(number, bool) or number < 1:
        raise CommunityRecommendationBuildError("Issue number must be a positive integer.")
    user = issue.get("user")
    username = user.get("login") if isinstance(user, dict) else None
    if not isinstance(username, str) or not GITHUB_LOGIN_RE.fullmatch(username):
        raise CommunityRecommendationBuildError("Could not resolve a valid GitHub login from the issue creator.")
    created_at = issue.get("created_at")
    if not isinstance(created_at, str) or not created_at.strip():
        raise CommunityRecommendationBuildError("Issue creation timestamp is required for community provenance.")
    try:
        created_at_dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CommunityRecommendationBuildError("Issue creation timestamp must be an ISO 8601 datetime.") from exc
    if created_at_dt.tzinfo is None:
        raise CommunityRecommendationBuildError("Issue creation timestamp must include a timezone.")

    mode = report.get("submitted_mode")
    weapon_id = report.get("resolved_weapon_id")
    recommendation = report.get("canonical_recommendation")
    if mode not in {"pve", "pvp"}:
        raise CommunityRecommendationBuildError("Validation report must resolve mode to pve or pvp.")
    if not isinstance(weapon_id, str) or not WEAPON_ID_RE.fullmatch(weapon_id):
        raise CommunityRecommendationBuildError("Validation report contains an invalid canonical weapon ID.")
    if not isinstance(recommendation, dict):
        raise CommunityRecommendationBuildError("Validation report is missing the canonical recommendation.")
    for field in ("name", "description", *SLOT_FIELDS):
        if not isinstance(recommendation.get(field), str) or not recommendation[field].strip():
            raise CommunityRecommendationBuildError("Canonical recommendation is missing required field " + field + ".")
    component_fields = [
        field for field in COMPONENT_FIELDS
        if isinstance(recommendation.get(field), str) and recommendation[field].strip()
    ]
    if len(component_fields) != 1:
        raise CommunityRecommendationBuildError(
            "Canonical recommendation must contain exactly one component field: barrel or sight."
        )
    component_field = component_fields[0]
    canonical_roll = {
        "mode": mode,
        "name": recommendation["name"].strip(),
        component_field: recommendation[component_field],
        "magazine": recommendation["magazine"],
        "perk_3": recommendation["perk_3"],
        "perk_4": recommendation["perk_4"],
        "description": recommendation["description"].strip(),
        "github_username": username,
        "issue_number": number,
        "submitted_at": created_at,
    }

    record = {
        "weapon_id": weapon_id,
        "weapon_perk_pool_id": weapon_id,
        "publication_status": "pending",
        "validation_scope": "canonical-dependencies-and-perk-slots",
        "recommendations": [canonical_roll],
        "published_by": None,
    }
    try:
        duplicates = find_duplicate_recommendations(
            root, record, ignore_issue_number=number
        )
    except RecommendationScanError as exc:
        raise CommunityRecommendationBuildError(
            "Could not safely check for duplicates; refusing to publish: " + str(exc)
        ) from exc
    if duplicates:
        existing_paths = sorted({match["path"] for match in duplicates})
        raise CommunityRecommendationBuildError(
            "Duplicate recommendation already exists in " + ", ".join(existing_paths) +
            "; refusing to publish it to main."
        )

    path = f"recommendations/community/{mode}/{weapon_id}.json"
    return path, record


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--event-path", type=Path, required=True)
    parser.add_argument("--report-path", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args()

    event = json.loads(args.event_path.read_text(encoding="utf-8"))
    issue = event.get("issue", event)
    report = json.loads(args.report_path.read_text(encoding="utf-8"))
    path, record = build_community_recommendation(issue, report)
    args.output.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if args.github_output:
        with args.github_output.open("a", encoding="utf-8") as handle:
            handle.write("ready=true\n")
            handle.write("target_path=" + path + "\n")
    print(json.dumps({"target_path": path, "github_username": record["recommendations"][0]["github_username"], "issue_number": record["recommendations"][0]["issue_number"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
