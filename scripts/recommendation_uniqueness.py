#!/usr/bin/env python3
"""Shared uniqueness checks for curated and community weapon recommendations."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator

ROLL_FIELDS = ("magazine", "perk_3", "perk_4")
COMPONENT_FIELDS = ("barrel", "sight")


class RecommendationScanError(ValueError):
    """Raised when duplicate detection cannot safely inspect the repository."""


def recommendation_signature(record: Any, recommendation: Any) -> tuple[str, ...] | None:
    """Build a stable identity from mode, exact weapon version and four perk slots."""
    if not isinstance(record, dict) or not isinstance(recommendation, dict):
        return None
    mode = recommendation.get("mode") if recommendation.get("mode") is not None else record.get("mode")
    weapon_id = record.get("weapon_id")
    if mode not in {"pve", "pvp"} or not isinstance(weapon_id, str) or not weapon_id:
        return None
    components = [
        recommendation.get(field)
        for field in COMPONENT_FIELDS
        if isinstance(recommendation.get(field), str) and recommendation[field]
    ]
    if len(components) != 1:
        return None
    perks = (components[0], *(recommendation.get(field) for field in ROLL_FIELDS))
    if any(not isinstance(perk, str) or not perk for perk in perks):
        return None
    return (mode, weapon_id, *perks)


def _is_same_issue_file(path: Path, root: Path, issue_number: int | None) -> bool:
    if issue_number is None:
        return False
    parts = path.relative_to(root).parts
    return (
        len(parts) == 4
        and parts[:2] == ("recommendations", "community")
        and path.stem.endswith(f"--issue-{issue_number}")
    )


def iter_recommendation_entries(
    root: Path,
    *,
    ignore_issue_number: int | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield recommendation signatures; fail closed if recommendation JSON is unreadable."""
    root = Path(root).resolve()
    recommendations_root = root / "recommendations"
    if not recommendations_root.is_dir():
        return
    for path in sorted(recommendations_root.rglob("*.json")):
        if _is_same_issue_file(path, root, ignore_issue_number):
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RecommendationScanError(
                f"Cannot safely check recommendation uniqueness because "
                f"{path.relative_to(root)} cannot be read as JSON: {exc}"
            ) from exc
        if not isinstance(payload, dict):
            continue
        recommendations = payload.get("recommendations")
        if not isinstance(recommendations, list):
            continue
        for index, recommendation in enumerate(recommendations):
            # Ignore only the nested entry belonging to this issue during a retry.
            if ignore_issue_number is not None and isinstance(recommendation, dict):
                source_issue = recommendation.get("issue_number")
                if isinstance(source_issue, int) and not isinstance(source_issue, bool) and source_issue == ignore_issue_number:
                    continue
            signature = recommendation_signature(payload, recommendation)
            if signature is None:
                continue
            yield {
                "path": str(path.relative_to(root)),
                "recommendation_index": index,
                "name": recommendation.get("name") if isinstance(recommendation, dict) else None,
                "signature": signature,
            }


def find_duplicate_recommendations(
    root: Path,
    candidate_record: dict[str, Any],
    *,
    ignore_issue_number: int | None = None,
) -> list[dict[str, Any]]:
    """Find matching roll signatures in existing curated or community records."""
    candidate_signatures = {
        signature
        for recommendation in candidate_record.get("recommendations", [])
        if (signature := recommendation_signature(candidate_record, recommendation)) is not None
    }
    if not candidate_signatures:
        return []
    return [
        entry
        for entry in iter_recommendation_entries(root, ignore_issue_number=ignore_issue_number)
        if entry["signature"] in candidate_signatures
    ]


def find_duplicate_pairs(root: Path) -> list[dict[str, Any]]:
    """Return every duplicated roll signature in the repository."""
    seen: dict[tuple[str, ...], dict[str, Any]] = {}
    duplicates: list[dict[str, Any]] = []
    for entry in iter_recommendation_entries(root):
        previous = seen.get(entry["signature"])
        if previous is None:
            seen[entry["signature"]] = entry
        else:
            duplicates.append({"first": previous, "duplicate": entry})
    return duplicates
