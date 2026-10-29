#!/usr/bin/env python3
"""Build a community recommendation after canonical dependency verification."""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

from build_community_recommendation import (
    CommunityRecommendationBuildError,
    build_community_recommendation,
)
from validate_recommendation_issue import _matching_pool_slots
from catalogue_source import CatalogueError, open_catalogue

ROOT = Path(__file__).resolve().parents[1]
GITHUB_LOGIN_RE = re.compile(r"^(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?|github-actions\[bot\])$")


class VerifiedCommunityBuildError(ValueError):
    """Raised when a recommendation is not ready for verified publication."""


def _read_json(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise VerifiedCommunityBuildError(f"Could not read valid JSON from {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise VerifiedCommunityBuildError(f"{path}: expected a JSON object")
    return payload


def _valid_date(value) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _valid_hash(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _require_verified_payload(
    record: dict | None,
    label: str,
    record_id: str,
    id_field: str = "id",
) -> dict:
    if not isinstance(record, dict):
        raise VerifiedCommunityBuildError(f"{label}: canonical record was not found.")
    if record.get(id_field) != record_id:
        raise VerifiedCommunityBuildError(f"{label}: {id_field} does not match {record_id!r}")
    if record.get("verification_status") != "verified":
        raise VerifiedCommunityBuildError(f"{label}: record must be marked verified before community publication")
    if not _valid_date(record.get("last_verified")):
        raise VerifiedCommunityBuildError(f"{label}: verified record requires a valid last_verified date")
    hash_field = "weapon_hash" if id_field == "weapon_id" and "weapon_hash" in record else "bungie_hash"
    if not _valid_hash(record.get(hash_field)):
        raise VerifiedCommunityBuildError(f"{label}: verified record requires a positive {hash_field}")
    return record


def build_verified_community_recommendation(
    issue: dict,
    report: dict,
    *,
    published_by: str,
    root: Path = ROOT,
) -> tuple[str, dict]:
    """Build a community record after validating canonical dependencies and perk slots.

    This is mechanical validation, not maintainer review of the submitted roll.
    No sandbox-specific performance or optimality claim is made.
    """
    if not GITHUB_LOGIN_RE.fullmatch(str(published_by or "")):
        raise VerifiedCommunityBuildError("published_by must be a valid GitHub login (automatic publication uses github-actions[bot]).")

    try:
        target_path, record = build_community_recommendation(issue, report, root=root)
    except CommunityRecommendationBuildError as exc:
        raise VerifiedCommunityBuildError(str(exc)) from exc

    try:
        catalogue = open_catalogue(root)
        weapon_id = record["weapon_id"]
        weapon_label = (
            root / "data" / "weapons" / f"{weapon_id}.json"
            if catalogue.is_local else f"Supabase d2wd_weapons.id={weapon_id}"
        )
        weapon = _require_verified_payload(
            catalogue.get_record("weapons", weapon_id), str(weapon_label), weapon_id
        )
        weapon_hash = weapon["bungie_hash"]

        pool_id = record.get("weapon_perk_pool_id")
        if pool_id != weapon_id:
            raise VerifiedCommunityBuildError("weapon_perk_pool_id must match the exact selected weapon version.")
        pool_label = (
            root / "data" / "weapon-perk-pools" / f"{pool_id}.json"
            if catalogue.is_local else f"Supabase d2wd_weapon_perk_pools.weapon_id={pool_id}"
        )
        pool = _require_verified_payload(
            catalogue.get_record("pools", pool_id), str(pool_label), pool_id, id_field="weapon_id"
        )
    except CatalogueError as exc:
        raise VerifiedCommunityBuildError("Could not read canonical catalogue from Supabase: " + str(exc)) from exc

    if pool.get("pool_status") != "complete":
        raise VerifiedCommunityBuildError(f"{pool_label}: pool_status must be complete.")
    if pool.get("weapon_hash") != weapon_hash:
        raise VerifiedCommunityBuildError(f"{pool_label}: weapon_hash must match the exact verified weapon hash.")

    pool_slots = pool.get("slots")
    if not isinstance(pool_slots, list) or not pool_slots:
        raise VerifiedCommunityBuildError(f"{pool_label}: a complete verified pool must contain slots.")
    for slot in pool_slots:
        if not isinstance(slot, dict) or not isinstance(slot.get("perk_ids"), list) or not slot["perk_ids"]:
            raise VerifiedCommunityBuildError(f"{pool_label}: every slot in a complete pool must contain perk IDs.")
        if any(not isinstance(perk_id, str) or not perk_id for perk_id in slot["perk_ids"]):
            raise VerifiedCommunityBuildError(f"{pool_label}: pool contains a malformed perk ID.")

    recommendation = record["recommendations"][0]
    columns = [column for column in ("barrel", "sight") if recommendation.get(column)]
    if len(columns) != 1:
        raise VerifiedCommunityBuildError("The recommendation must use exactly one of barrel or sight.")
    columns.extend(("magazine", "perk_3", "perk_4"))
    for column in columns:
        perk_id = recommendation.get(column)
        if not isinstance(perk_id, str) or not perk_id:
            raise VerifiedCommunityBuildError(f"Recommendation is missing canonical perk field {column}.")
        perk_label = (
            root / "data" / "perks" / f"{perk_id}.json"
            if catalogue.is_local else f"Supabase d2wd_perks.id={perk_id}"
        )
        _require_verified_payload(catalogue.get_record("perks", perk_id), str(perk_label), perk_id)
        matching_slots = _matching_pool_slots(pool, column)
        if not matching_slots or not any(
            perk_id in slot.get("perk_ids", [])
            for slot in matching_slots
            if isinstance(slot.get("perk_ids"), list)
        ):
            raise VerifiedCommunityBuildError(
                f"Perk {perk_id!r} is not in the correct verified slot of {pool_id!r}."
            )

    publication_fields = {
        "publication_status": "mechanically_validated",
        "validation_scope": "canonical-dependencies-and-perk-slots",
        "published_by": published_by,
    }
    record.update(publication_fields)
    recommendation.update(publication_fields)
    return target_path, record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event-path", type=Path, required=True)
    parser.add_argument("--report-path", type=Path, required=True)
    parser.add_argument("--published-by", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--github-output", type=Path)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()

    try:
        event = json.loads(args.event_path.read_text(encoding="utf-8"))
        issue = event.get("issue", event)
        report = json.loads(args.report_path.read_text(encoding="utf-8"))
        path, record = build_verified_community_recommendation(
            issue, report,
            published_by=args.published_by,
            root=args.root,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        if args.github_output:
            with args.github_output.open("a", encoding="utf-8") as handle:
                handle.write("ready=true\n")
                handle.write("target_path=" + path + "\n")
                handle.write("github_username=" + record["recommendations"][0]["github_username"] + "\n")
                handle.write("issue_number=" + str(issue["number"]) + "\n")
        print(json.dumps({"target_path": path, "published_by": args.published_by}, ensure_ascii=False))
    except (OSError, json.JSONDecodeError, VerifiedCommunityBuildError, CommunityRecommendationBuildError) as exc:
        print(f"Verified community build failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
