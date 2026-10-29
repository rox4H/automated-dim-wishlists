#!/usr/bin/env python3
"""Safely upsert a mechanically validated issue recommendation into one weapon-and-mode JSON."""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
WEAPON_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*_s[0-9]+(?:-[0-9]+)?$")
GITHUB_LOGIN_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")


class CommunityMergeError(ValueError):
    """Raised when an aggregate community update cannot be safely applied."""


def _read_record(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CommunityMergeError(f"Could not read valid JSON from {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise CommunityMergeError(f"{path}: expected a JSON object")
    return payload


def _valid_issue_number(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _valid_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def merge_community_recommendation(
    root: Path,
    *,
    record_path: Path,
    target_path: str,
    issue_number: int,
    github_username: str,
) -> Path:
    """Append one mechanically validated issue entry, or replace only that issue on a rerun."""
    root = Path(root).resolve()
    if not _valid_issue_number(issue_number):
        raise CommunityMergeError("issue_number must be a positive integer")
    if not isinstance(github_username, str) or not GITHUB_LOGIN_RE.fullmatch(github_username):
        raise CommunityMergeError("github_username must be a valid GitHub login")

    target_rel = PurePosixPath(target_path)
    if (
        target_rel.is_absolute()
        or target_rel.as_posix() != target_path
        or len(target_rel.parts) != 4
        or target_rel.parts[:2] != ("recommendations", "community")
        or target_rel.parts[2] not in {"pve", "pvp"}
    ):
        raise CommunityMergeError("target_path must be recommendations/community/pve|pvp/<weapon-id>.json")

    incoming = _read_record(Path(record_path))
    weapon_id = incoming.get("weapon_id")
    if not isinstance(weapon_id, str) or not WEAPON_ID_RE.fullmatch(weapon_id):
        raise CommunityMergeError("incoming record has an invalid versioned weapon_id")
    new_items = incoming.get("recommendations")
    if not isinstance(new_items, list) or len(new_items) != 1 or not isinstance(new_items[0], dict):
        raise CommunityMergeError("incoming record must contain exactly one issue recommendation")
    new_item = new_items[0]
    target_mode = target_rel.parts[2]
    if new_item.get("mode") != target_mode:
        raise CommunityMergeError("target mode directory must match the incoming recommendation mode")
    expected_path = PurePosixPath("recommendations") / "community" / target_mode / f"{weapon_id}.json"
    if target_rel != expected_path:
        raise CommunityMergeError("target_path does not match the incoming exact weapon version and mode")
    if incoming.get("weapon_perk_pool_id") != weapon_id:
        raise CommunityMergeError("incoming weapon_perk_pool_id must match weapon_id")
    if incoming.get("publication_status") != "mechanically_validated":
        raise CommunityMergeError("incoming aggregate must be mechanically validated")
    if incoming.get("validation_scope") != "canonical-dependencies-and-perk-slots":
        raise CommunityMergeError("incoming aggregate has an unsupported validation scope")
    publisher = incoming.get("published_by")
    if not isinstance(publisher, str) or not (
        GITHUB_LOGIN_RE.fullmatch(publisher) or publisher == "github-actions[bot]"
    ):
        raise CommunityMergeError("incoming aggregate requires a valid published_by login")

    if not _valid_issue_number(new_item.get("issue_number")) or new_item.get("issue_number") != issue_number:
        raise CommunityMergeError("incoming recommendation issue_number does not match this workflow run")
    if new_item.get("github_username") != github_username:
        raise CommunityMergeError("incoming recommendation author does not match the GitHub issue creator")
    if new_item.get("mode") not in {"pve", "pvp"}:
        raise CommunityMergeError("incoming recommendation mode must be pve or pvp")
    if new_item.get("publication_status") != "mechanically_validated":
        raise CommunityMergeError("incoming recommendation must be mechanically validated")
    if new_item.get("validation_scope") != incoming.get("validation_scope"):
        raise CommunityMergeError("incoming recommendation validation scope must match its aggregate")
    if new_item.get("published_by") != incoming.get("published_by"):
        raise CommunityMergeError("incoming recommendation publisher must match its aggregate")
    if not _valid_timestamp(new_item.get("submitted_at")):
        raise CommunityMergeError("incoming recommendation requires the source issue creation timestamp")

    target = root / target_rel.as_posix()
    community_root = root / "recommendations" / "community"
    for mode_folder in ("pve", "pvp"):
        mode_root = community_root / mode_folder
        if not mode_root.is_dir():
            continue
        for other_path in sorted(mode_root.glob("*.json")):
            if other_path.resolve() == target.resolve():
                continue
            other_record = _read_record(other_path)
            other_recommendations = other_record.get("recommendations")
            if not isinstance(other_recommendations, list):
                raise CommunityMergeError(
                    f"{other_path}: existing community recommendations must be a list"
                )
            for other_item in other_recommendations:
                if not isinstance(other_item, dict):
                    raise CommunityMergeError(
                        f"{other_path}: existing community recommendation is malformed"
                    )
                if other_item.get("issue_number") == issue_number:
                    raise CommunityMergeError(
                        f"source issue #{issue_number} is already published in "
                        f"{other_path.relative_to(root)}; refusing to publish it again "
                        "under a different weapon/mode file"
                    )

    if target.exists():
        previous = _read_record(target)
        if (
            previous.get("weapon_id") != weapon_id
            or previous.get("weapon_perk_pool_id") != weapon_id
            or previous.get("publication_status") != "mechanically_validated"
            or previous.get("validation_scope") != "canonical-dependencies-and-perk-slots"
        ):
            raise CommunityMergeError("existing aggregate has inconsistent weapon identity or publication status")
        old_items = previous.get("recommendations")
        if not isinstance(old_items, list) or not old_items:
            raise CommunityMergeError("existing aggregate must contain a non-empty recommendations list")

        recommendations: list[dict[str, Any]] = []
        seen_issues: set[int] = set()
        matching_indexes: list[int] = []
        for index, old_item in enumerate(old_items):
            if not isinstance(old_item, dict):
                raise CommunityMergeError(f"existing recommendation #{index + 1} is malformed")
            old_issue = old_item.get("issue_number")
            old_author = old_item.get("github_username")
            if not _valid_issue_number(old_issue):
                raise CommunityMergeError(f"existing recommendation #{index + 1} has invalid issue provenance")
            if old_issue in seen_issues:
                raise CommunityMergeError(f"existing aggregate has duplicate source issue #{old_issue}")
            seen_issues.add(old_issue)
            if not isinstance(old_author, str) or not GITHUB_LOGIN_RE.fullmatch(old_author):
                raise CommunityMergeError(f"existing recommendation #{index + 1} has invalid author provenance")
            if (
                old_item.get("mode") != target_mode
                or old_item.get("publication_status") != "mechanically_validated"
                or old_item.get("validation_scope") != previous.get("validation_scope")
                or old_item.get("published_by") != previous.get("published_by")
            ):
                raise CommunityMergeError(
                    f"existing recommendation #{index + 1} must match community/{target_mode} "
                    "and use the aggregate's mechanical validation metadata"
                )
            recommendations.append(old_item)
            if old_issue == issue_number:
                matching_indexes.append(index)

        if len(matching_indexes) > 1:
            raise CommunityMergeError(f"existing aggregate contains multiple entries for issue #{issue_number}")
        if matching_indexes:
            index = matching_indexes[0]
            if recommendations[index]["github_username"] != github_username:
                raise CommunityMergeError("refusing to overwrite an existing entry attributed to another user")
            recommendations[index] = new_item
        else:
            recommendations.append(new_item)
        incoming["recommendations"] = recommendations

    incoming["recommendations"].sort(
        key=lambda entry: (entry["issue_number"], entry["github_username"])
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(incoming, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record-path", type=Path, required=True)
    parser.add_argument("--target-path", required=True)
    parser.add_argument("--issue-number", type=int, required=True)
    parser.add_argument("--github-username", required=True)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    try:
        target = merge_community_recommendation(
            args.root,
            record_path=args.record_path,
            target_path=args.target_path,
            issue_number=args.issue_number,
            github_username=args.github_username,
        )
        print(json.dumps({"target_path": target.relative_to(args.root.resolve()).as_posix()}))
    except (OSError, CommunityMergeError) as exc:
        print(f"Community merge failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
