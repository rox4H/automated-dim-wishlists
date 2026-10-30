#!/usr/bin/env python3
"""Export the repository's roll recommendations to DIM's native wishlist format."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from recommendation_uniqueness import find_duplicate_pairs, RecommendationScanError
from catalogue_source import CatalogueError, open_catalogue
from pool_slot_mapping import matching_pool_slots as _matching_pool_slots

ROOT = Path(__file__).resolve().parents[1]
ROLL_FIELDS = ("magazine", "perk_3", "perk_4")
ALLOWED_MODES = {"all", "pve", "pvp"}
ALLOWED_SOURCES = {"all", "curated", "community"}


class WishlistExportError(ValueError):
    """Raised when the data cannot be exported without silently losing recommendations."""


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WishlistExportError(f"Could not read valid JSON from {path}: {exc}") from exc


def _valid_hash(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _clean_note(value: Any) -> str:
    """Keep the DIM note on one line and prevent note text from injecting another field."""
    text = str(value or "").replace("#", "").replace("\r", " ").replace("\n", " ")
    return " ".join(text.split())


def _resolve_perk_hash(
    root: Path,
    perk_id: str,
    *,
    require_verified: bool = False,
    catalogue=None,
) -> int:
    """Resolve the Inventory Item hash from the canonical Supabase row (or a test fixture)."""
    catalogue = catalogue or open_catalogue(root)
    perk_path = (
        root / "data" / "perks" / f"{perk_id}.json"
        if catalogue.is_local else f"Supabase d2wd_perks.id={perk_id}"
    )
    try:
        perk_record = catalogue.get_record("perks", perk_id)
    except CatalogueError as exc:
        raise WishlistExportError("Could not read canonical perk catalogue: " + str(exc)) from exc
    if not isinstance(perk_record, dict):
        raise WishlistExportError(
            f"Missing canonical perk record {perk_path}; create or restore the canonical perk before exporting."
        )
    if perk_record.get("id") != perk_id:
        raise WishlistExportError(f"{perk_path}: canonical perk ID does not match its key")

    value = perk_record.get("bungie_hash")
    if not _valid_hash(value):
        raise WishlistExportError(
            f"{perk_path}: no valid Bungie perk hash; set a positive canonical bungie_hash "
            "after resolving the exact Inventory Item definition."
        )

    status = perk_record.get("verification_status")
    if status not in {"verified", "unverified"}:
        raise WishlistExportError(f"{perk_path}: verification_status must be verified or unverified")
    if require_verified:
        if status != "verified":
            raise WishlistExportError(
                f"{perk_path}: community wishlist requires verification_status='verified'"
            )
        if not isinstance(perk_record.get("last_verified"), str) or not perk_record["last_verified"].strip():
            raise WishlistExportError(f"{perk_path}: verified perk requires last_verified")

    return value



def _validate_mechanically_validated_community_record(
    root: Path, payload: dict[str, Any], path: Path, weapon: dict[str, Any], weapon_hash: int,
    *, catalogue=None,
) -> None:
    """Validate publication metadata and the underlying canonical weapon/perk dependencies."""
    if payload.get("publication_status") != "mechanically_validated":
        raise WishlistExportError(
            f"{path}: community wishlist only accepts mechanically validated recommendations; "
            "this status reflects canonical dependency and perk-slot checks, not maintainer review."
        )
    if payload.get("validation_scope") != "canonical-dependencies-and-perk-slots":
        raise WishlistExportError(f"{path}: unsupported community validation_scope")
    value = payload.get("published_by")
    if not isinstance(value, str) or not re.fullmatch(
        r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?|github-actions\[bot\])", value
    ):
        raise WishlistExportError(f"{path}: mechanically validated community record requires a valid published_by login")
    if weapon.get("verification_status") != "verified":
        raise WishlistExportError(f"{root / 'data' / 'weapons' / (str(payload.get('weapon_id')) + '.json')}: community wishlist requires a verified weapon")
    if not isinstance(weapon.get("last_verified"), str) or not weapon["last_verified"].strip():
        raise WishlistExportError(f"{root / 'data' / 'weapons' / (str(payload.get('weapon_id')) + '.json')}: verified weapon requires last_verified")

    pool_id = payload.get("weapon_perk_pool_id")
    weapon_id = payload.get("weapon_id")
    if not isinstance(pool_id, str) or pool_id != weapon_id:
        raise WishlistExportError(f"{path}: weapon_perk_pool_id must match the exact weapon version")
    catalogue = catalogue or open_catalogue(root)
    pool_path = (
        root / "data" / "weapon-perk-pools" / f"{pool_id}.json"
        if catalogue.is_local else f"Supabase d2wd_weapon_perk_pools.weapon_id={pool_id}"
    )
    try:
        pool = catalogue.get_record("pools", pool_id)
    except CatalogueError as exc:
        raise WishlistExportError("Could not read canonical weapon perk pool: " + str(exc)) from exc
    if not isinstance(pool, dict) or pool.get("weapon_id") != weapon_id:
        raise WishlistExportError(f"{pool_path}: missing or mismatched exact weapon-version perk pool")
    if pool.get("verification_status") != "verified" or pool.get("pool_status") != "complete":
        raise WishlistExportError(f"{pool_path}: community wishlist requires a complete, verified perk pool")
    if not isinstance(pool.get("last_verified"), str) or not pool["last_verified"].strip():
        raise WishlistExportError(f"{pool_path}: verified perk pool requires last_verified")
    pool_hash = pool.get("weapon_hash")
    if not _valid_hash(pool_hash) or pool_hash != weapon_hash:
        raise WishlistExportError(f"{pool_path}: verified pool hash must match the exact weapon hash")

    slots = pool.get("slots")
    if not isinstance(slots, list) or not slots:
        raise WishlistExportError(f"{pool_path}: verified complete pool must contain slots")
    for slot in slots:
        if not isinstance(slot, dict) or not isinstance(slot.get("perk_ids"), list) or not slot["perk_ids"]:
            raise WishlistExportError(f"{pool_path}: every complete pool slot must list its perk IDs")
        if any(not isinstance(perk_id, str) or not perk_id for perk_id in slot["perk_ids"]):
            raise WishlistExportError(f"{pool_path}: complete pool contains a malformed perk ID")

    recommendations = payload.get("recommendations")
    if not isinstance(recommendations, list) or not recommendations:
        raise WishlistExportError(f"{path}: verified community record requires at least one recommendation")
    rel_parts = path.relative_to(root).parts
    is_aggregate = len(rel_parts) == 4 and rel_parts[:3] in {("recommendations", "community", "pve"), ("recommendations", "community", "pvp")}
    seen_issue_numbers: set[int] = set()
    for index, recommendation in enumerate(recommendations, start=1):
        if not isinstance(recommendation, dict):
            raise WishlistExportError(f"{path}: recommendation #{index} must be an object")
        if is_aggregate:
            expected_mode = rel_parts[2]
            if recommendation.get("mode") not in {"pve", "pvp"}:
                raise WishlistExportError(f"{path}: recommendation #{index} requires mode pve or pvp")
            if recommendation.get("mode") != expected_mode:
                raise WishlistExportError(f"{path}: recommendation #{index} mode must match community directory {expected_mode!r}")
            if recommendation.get("publication_status") != "mechanically_validated":
                raise WishlistExportError(f"{path}: recommendation #{index} is not mechanically validated")
            if recommendation.get("validation_scope") != payload.get("validation_scope"):
                raise WishlistExportError(f"{path}: recommendation #{index} validation_scope must match its aggregate")
            if recommendation.get("published_by") != payload.get("published_by"):
                raise WishlistExportError(f"{path}: recommendation #{index} published_by must match its aggregate")
            github_username = recommendation.get("github_username")
            if not isinstance(github_username, str) or not re.fullmatch(
                r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?", github_username
            ):
                raise WishlistExportError(f"{path}: recommendation #{index} requires a valid github_username")
            issue_number = recommendation.get("issue_number")
            if not isinstance(issue_number, int) or isinstance(issue_number, bool) or issue_number < 1:
                raise WishlistExportError(f"{path}: recommendation #{index} requires a positive issue_number")
            if issue_number in seen_issue_numbers:
                raise WishlistExportError(f"{path}: duplicate source issue number {issue_number}")
            seen_issue_numbers.add(issue_number)
            if not isinstance(recommendation.get("submitted_at"), str) or not recommendation["submitted_at"].strip():
                raise WishlistExportError(f"{path}: recommendation #{index} requires submitted_at provenance")
        component_fields = [
            field for field in ("barrel", "sight")
            if isinstance(recommendation.get(field), str) and recommendation[field]
        ]
        if len(component_fields) != 1:
            raise WishlistExportError(f"{path}: recommendation #{index} must contain exactly one of barrel or sight")
        for column in (component_fields[0], *ROLL_FIELDS):
            perk_id = recommendation.get(column)
            if not isinstance(perk_id, str) or not perk_id:
                raise WishlistExportError(f"{path}: recommendation #{index} is missing perk field {column!r}")
            matching_slots = _matching_pool_slots(pool, column)
            if not matching_slots or not any(
                perk_id in slot.get("perk_ids", [])
                for slot in matching_slots
                if isinstance(slot.get("perk_ids"), list)
            ):
                raise WishlistExportError(f"{pool_path}: selected perk {perk_id!r} is not in the correct {column!r} slot")


def generate_wishlist(
    root: Path = ROOT,
    mode: str = "all",
    verified_only: bool = False,
    source: str = "all",
) -> str:
    """Return a deterministic DIM wishlist for curated or aggregated community recommendations."""
    root = Path(root).resolve()
    if mode not in ALLOWED_MODES:
        raise WishlistExportError(f"Unsupported mode {mode!r}; choose all, pve, or pvp")
    if source not in ALLOWED_SOURCES:
        raise WishlistExportError(f"Unsupported source {source!r}; choose all, curated, or community")

    try:
        duplicate_pairs = find_duplicate_pairs(root)
    except RecommendationScanError as exc:
        raise WishlistExportError(str(exc)) from exc
    if duplicate_pairs:
        first = duplicate_pairs[0]
        signature = first["duplicate"]["signature"]
        raise WishlistExportError(
            "Duplicate recommendation found for "
            + f"{signature[1]!r} ({signature[0].upper()}): "
            + f"{first['first']['path']} recommendations[{first['first']['recommendation_index']}] and "
            + f"{first['duplicate']['path']} recommendations[{first['duplicate']['recommendation_index']}]. "
            + "Curated and community wishlists are blocked until duplicate rolls are resolved."
        )

    try:
        catalogue = open_catalogue(root)
    except CatalogueError as exc:
        raise WishlistExportError("Could not open canonical catalogue: " + str(exc)) from exc

    recommendations_root = root / "recommendations"
    if not recommendations_root.is_dir():
        raise WishlistExportError(f"Recommendations directory not found: {recommendations_root}")
    recommendation_files = sorted(recommendations_root.rglob("*.json"))
    if not recommendation_files:
        raise WishlistExportError(f"No recommendation JSON files found under {recommendations_root}")

    roll_lines: list[str] = []
    for path in recommendation_files:
        payload = _read_json(path)
        if not isinstance(payload, dict):
            raise WishlistExportError(f"{path}: expected a JSON object")

        relative_parts = path.relative_to(recommendations_root).parts
        is_curated = (
            len(relative_parts) == 3
            and relative_parts[0] == "curated"
            and relative_parts[1] in {"pve", "pvp"}
        )
        is_community_aggregate = (
            len(relative_parts) == 3
            and relative_parts[0] == "community"
            and relative_parts[1] in {"pve", "pvp"}
        )
        is_community = is_community_aggregate
        if not (is_curated or is_community):
            raise WishlistExportError(
                f"{path}: recommendation files must be under recommendations/curated/pve|pvp/ "
                "or recommendations/community/pve|pvp/<weapon-id>.json"
            )
        recommendation_source = "community" if is_community else "curated"
        if source != "all" and recommendation_source != source:
            continue

        weapon_id = payload.get("weapon_id")
        if not isinstance(weapon_id, str) or not weapon_id:
            raise WishlistExportError(f"{path}: missing weapon_id")
        if is_community_aggregate:
            if path.stem != weapon_id:
                raise WishlistExportError(f"{path}: community filename must match weapon_id {weapon_id!r} within its mode directory")
            if "mode" in payload or "github_username" in payload:
                raise WishlistExportError(f"{path}: aggregate community records store mode and author per recommendation")
            record_mode = relative_parts[1]
        else:
            record_mode = payload.get("mode")
            if record_mode not in {"pve", "pvp"}:
                raise WishlistExportError(f"{path}: mode must be 'pve' or 'pvp'")
            if relative_parts[-2] != record_mode:
                raise WishlistExportError(f"{path}: mode {record_mode!r} does not match directory {relative_parts[-2]!r}")
            if path.stem != weapon_id:
                raise WishlistExportError(f"{path}: filename must match weapon_id {weapon_id!r}")
            if "github_username" in payload:
                raise WishlistExportError(f"{path}: github_username is reserved for community recommendations")

        weapon_path = (
            root / "data" / "weapons" / f"{weapon_id}.json"
            if catalogue.is_local else f"Supabase d2wd_weapons.id={weapon_id}"
        )
        try:
            weapon = catalogue.get_record("weapons", weapon_id)
        except CatalogueError as exc:
            raise WishlistExportError("Could not read canonical weapon catalogue: " + str(exc)) from exc
        if not isinstance(weapon, dict) or weapon.get("id") != weapon_id:
            raise WishlistExportError(f"{weapon_path}: canonical weapon ID does not match its key")
        weapon_hash = weapon.get("bungie_hash")
        if not _valid_hash(weapon_hash):
            raise WishlistExportError(f"{weapon_path}: no valid Bungie item hash; refusing to emit an invalid DIM item line")
        if is_community:
            _validate_mechanically_validated_community_record(
                root, payload, path, weapon, weapon_hash, catalogue=catalogue
            )

        recommendations = payload.get("recommendations")
        if not isinstance(recommendations, list):
            raise WishlistExportError(f"{path}: recommendations must be a list")
        status = payload.get("publication_status") if is_community else payload.get("verification_status")
        if is_community:
            if status != "mechanically_validated":
                raise WishlistExportError(f"{path}: community publication_status must be mechanically_validated")
        elif status not in {"verified", "unverified"}:
            raise WishlistExportError(f"{path}: verification_status must be verified or unverified")

        for index, recommendation in enumerate(recommendations, start=1):
            if not isinstance(recommendation, dict):
                raise WishlistExportError(f"{path}: recommendation #{index} must be an object")
            recommendation_mode = recommendation.get("mode") if is_community else record_mode
            if recommendation_mode not in {"pve", "pvp"}:
                raise WishlistExportError(f"{path}: recommendation #{index} mode must be 'pve' or 'pvp'")
            if mode != "all" and recommendation_mode != mode:
                continue
            recommendation_status = recommendation.get("publication_status") if is_community else status
            if is_community and recommendation_status != "mechanically_validated":
                raise WishlistExportError(f"{path}: recommendation #{index} has an invalid publication_status")
            if not is_community and recommendation_status not in {"verified", "unverified"}:
                raise WishlistExportError(f"{path}: recommendation #{index} has an invalid verification_status")
            if verified_only and (
                (is_community and recommendation_status != "mechanically_validated")
                or (not is_community and recommendation_status != "verified")
            ):
                continue

            label = _clean_note(recommendation.get("name"))
            description = _clean_note(recommendation.get("description"))
            if not label or not description:
                raise WishlistExportError(f"{path}: recommendation #{index} needs a non-empty name and description")
            component_fields = [
                field for field in ("barrel", "sight")
                if isinstance(recommendation.get(field), str) and recommendation[field]
            ]
            if len(component_fields) != 1:
                raise WishlistExportError(f"{path}: recommendation #{index} must contain exactly one of 'barrel' or 'sight'")
            perk_hashes: list[int] = []
            for field in (component_fields[0], *ROLL_FIELDS):
                perk_id = recommendation.get(field)
                if not isinstance(perk_id, str) or not perk_id:
                    raise WishlistExportError(f"{path}: recommendation #{index} is missing perk field {field!r}")
                perk_hashes.append(
                    _resolve_perk_hash(
                        root, perk_id, require_verified=is_community, catalogue=catalogue
                    )
                )

            note_parts = [recommendation_mode.upper()]
            if is_community:
                author = recommendation.get("github_username")
                if isinstance(author, str) and author:
                    note_parts.append(f"BY @{author}")
            note_parts.append(label)
            note = _clean_note(" | ".join(note_parts))
            roll_lines.append(
                f"dimwishlist:item={weapon_hash}&perks={','.join(str(value) for value in perk_hashes)}"
                f"#notes:{note}"
            )

    if source == "curated":
        title_suffix = "Curated"
        description = "Curated PvE and PvP weapon rolls."
    elif source == "community":
        title_suffix = "Community"
        description = "Player-submitted PvE and PvP weapon rolls."
    else:
        title_suffix = "All"
        description = "PvE and PvP weapon rolls."
    header = [
        f"title:Weapon Wishlist — {title_suffix}",
        "description:" + description,
    ]
    if not roll_lines:
        header.append("// No wishlist entries.")
    return "\n".join(header + [""] + roll_lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate a DIM-compatible wishlist text file from recommendations/."
    )
    parser.add_argument(
        "--source",
        choices=("curated", "community"),
        default="curated",
        help="which dataset to export; writes exports/d2wd_curated.txt or exports/d2wd_community.txt by default",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="output path, relative to the repository root unless absolute",
    )
    parser.add_argument(
        "--mode",
        choices=sorted(ALLOWED_MODES),
        default="all",
        help="export both modes or only PvE/PvP recommendations",
    )
    parser.add_argument(
        "--verified-only",
        action="store_true",
        help="exclude recommendation files not marked verified",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="check whether the output file is up to date without changing it",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=ROOT,
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args(argv)

    root = args.root.resolve()
    default_outputs = {
        "curated": "exports/d2wd_curated.txt",
        "community": "exports/d2wd_community.txt",
    }
    output = Path(args.output or default_outputs[args.source])
    if not output.is_absolute():
        output = root / output

    try:
        content = generate_wishlist(root, mode=args.mode, verified_only=args.verified_only, source=args.source)
        roll_count = content.count("\ndimwishlist:item=")
        if args.check:
            try:
                current = output.read_text(encoding="utf-8")
            except OSError as exc:
                print(f"Wishlist output is missing or unreadable: {output}: {exc}", file=sys.stderr)
                return 1
            if current != content:
                print(
                    f"Wishlist output is out of date: {output}. Run scripts/export_dim_wishlist.py.",
                    file=sys.stderr,
                )
                return 1
            print(f"DIM wishlist is up to date: {output} ({roll_count} roll(s)).")
            return 0

        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(content, encoding="utf-8", newline="\n")
    except (OSError, WishlistExportError, CatalogueError) as exc:
        print(f"DIM wishlist export failed: {exc}", file=sys.stderr)
        return 1

    print(f"Wrote DIM wishlist: {output} ({roll_count} roll(s)).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
