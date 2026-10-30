#!/usr/bin/env python3
"""Validate weapon, perk, perk-pool and recommendation data."""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from pathlib import Path
from typing import Any

from catalogue_source import CatalogueError, open_catalogue
from pool_slot_mapping import matching_pool_slots as _matching_pool_slots
from recommendation_uniqueness import recommendation_signature

try:
    from jsonschema import Draft202012Validator, FormatChecker
except ImportError:  # pragma: no cover - exercised only without dev dependencies
    Draft202012Validator = None  # type: ignore[assignment,misc]
    FormatChecker = None  # type: ignore[assignment,misc]


ROOT = Path(__file__).resolve().parents[1]

# In production, canonical records are loaded from Supabase and exposed through
# virtual legacy paths so the validation rules below remain shared with fixture tests.
_REMOTE_RECORD_CACHE: dict[Path, Any] = {}
_LAST_COUNTS = {"weapons": 0, "perks": 0, "pools": 0}


def _has_season_suffix(weapon_id: str) -> bool:
    """Return whether a weapon ID names a specific seasonal item version."""
    return re.search(r"_s\d+(?:-\d+)?$", weapon_id) is not None


def _valid_bungie_hash(value: Any) -> bool:
    """Return whether a Bungie item hash is a valid positive integer."""
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


class DuplicateKeyError(ValueError):
    """Raised when a JSON object contains the same key more than once."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateKeyError(f"duplicate JSON object key {key!r}")
        result[key] = value
    return result


def parse_json_text(content: str) -> Any:
    """Parse JSON while rejecting duplicate keys instead of silently overwriting them."""
    return json.loads(content, object_pairs_hook=_unique_object)


def _json_location(parts: Any) -> str:
    location = "$"
    for part in parts:
        location += f"[{part}]" if isinstance(part, int) else f".{part}"
    return location


def _read_json(path: Path, root: Path, errors: list[str]) -> Any | None:
    virtual_path = path.resolve()
    if virtual_path in _REMOTE_RECORD_CACHE:
        return copy.deepcopy(_REMOTE_RECORD_CACHE[virtual_path])
    try:
        return parse_json_text(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, DuplicateKeyError) as exc:
        errors.append(f"{path.relative_to(root)}: invalid JSON: {exc}")
        return None



def validate_repository(root: Path = ROOT) -> list[str]:
    """Return validation errors for all records in a repository checkout."""
    root = root.resolve()
    errors: list[str] = []

    if Draft202012Validator is None or FormatChecker is None:
        return [
            "Missing development dependency 'jsonschema'. "
            "Install it with: python -m pip install -r requirements-dev.txt"
        ]

    schema_paths = {
        "weapon": root / "schemas" / "weapon.schema.json",
        "recommendation": root / "schemas" / "recommendation.schema.json",
        "community-recommendation": root / "schemas" / "community-recommendations.schema.json",
        "perk": root / "schemas" / "perk.schema.json",
        "weapon-perk-pool": root / "schemas" / "weapon-perk-pool.schema.json",
    }
    validators: dict[str, Any] = {}

    for name, path in schema_paths.items():
        if not path.exists():
            errors.append(f"{path.relative_to(root)}: schema file is missing")
            continue
        schema = _read_json(path, root, errors)
        if not isinstance(schema, dict):
            continue
        try:
            Draft202012Validator.check_schema(schema)
            validators[name] = Draft202012Validator(
                schema, format_checker=FormatChecker()
            )
        except Exception as exc:
            errors.append(f"{path.relative_to(root)}: invalid JSON Schema: {exc}")

    global _LAST_COUNTS
    _REMOTE_RECORD_CACHE.clear()
    try:
        catalogue = open_catalogue(root)
        if catalogue.is_local:
            weapon_records_source = None
            perk_records_source = None
            pool_records_source = None
            weapon_dir = root / "data" / "weapons"
            weapon_files = sorted(weapon_dir.glob("*.json")) if weapon_dir.exists() else []
        else:
            weapon_records_source = catalogue.list_records("weapons")
            perk_records_source = catalogue.list_records("perks")
            pool_records_source = catalogue.list_records("pools")

            def virtual_files(records, relative_dir, key_field):
                paths = []
                for item in records:
                    record_id = item.get(key_field)
                    if (
                        not isinstance(record_id, str)
                        or not record_id
                        or record_id in {".", ".."}
                        or "/" in record_id
                        or "\\" in record_id
                    ):
                        raise CatalogueError(
                            f"Supabase returned an unsafe {key_field} for {relative_dir}: {record_id!r}"
                        )
                    virtual_path = root / "data" / relative_dir / (record_id + ".json")
                    _REMOTE_RECORD_CACHE[virtual_path.resolve()] = item
                    paths.append(virtual_path)
                return sorted(paths)

            weapon_files = virtual_files(weapon_records_source, "weapons", "id")
    except CatalogueError as exc:
        errors.append("Canonical Supabase catalogue unavailable: " + str(exc))
        return errors

    if not weapon_files:
        errors.append("Canonical weapon catalogue contains no weapon records.")

    weapon_records: dict[str, tuple[Path, dict[str, Any]]] = {}
    for path in weapon_files:
        record = _read_json(path, root, errors)
        if not isinstance(record, dict):
            continue

        validator = validators.get("weapon")
        if validator:
            issues = sorted(
                validator.iter_errors(record),
                key=lambda issue: tuple(str(part) for part in issue.absolute_path),
            )
            for issue in issues:
                errors.append(
                    f"{path.relative_to(root)}: schema violation at "
                    f"{_json_location(issue.absolute_path)}: {issue.message}"
                )

        weapon_id = record.get("id")
        if isinstance(weapon_id, str):
            if path.stem != weapon_id:
                errors.append(
                    f"{path.relative_to(root)}: filename must match record id "
                    f"{weapon_id!r}"
                )
            if not _has_season_suffix(weapon_id):
                errors.append(
                    f"{path.relative_to(root)}: weapon id must use an underscore season suffix such as _s19 "
                    "(for example, 'avalanche_s19')"
                )
            if weapon_id in weapon_records:
                previous = weapon_records[weapon_id][0].relative_to(root)
                errors.append(
                    f"{path.relative_to(root)}: duplicate weapon id {weapon_id!r}; "
                    f"also present in {previous}"
                )
            else:
                weapon_records[weapon_id] = (path, record)

    # Adept variants must point at a base weapon record that exists.
    for path, record in weapon_records.values():
        variant_of = record.get("variant_of")
        if isinstance(variant_of, str) and variant_of not in weapon_records:
            errors.append(
                f"{path.relative_to(root)}: references missing base weapon "
                f"variant_of={variant_of!r}"
            )

    # Perk definitions are canonical records; weapon-specific availability is
    # represented separately by weapon-perk-pool records.
    perks_root = root / "data" / "perks"
    if catalogue.is_local:
        perk_files = sorted(perks_root.glob("*.json")) if perks_root.exists() else []
    else:
        perk_files = virtual_files(perk_records_source, "perks", "id")
    perk_records: dict[str, tuple[Path, dict[str, Any]]] = {}

    for path in perk_files:
        record = _read_json(path, root, errors)
        if not isinstance(record, dict):
            continue

        validator = validators.get("perk")
        if validator:
            issues = sorted(
                validator.iter_errors(record),
                key=lambda issue: tuple(str(part) for part in issue.absolute_path),
            )
            for issue in issues:
                errors.append(
                    f"{path.relative_to(root)}: schema violation at "
                    f"{_json_location(issue.absolute_path)}: {issue.message}"
                )

        perk_id = record.get("id")
        if isinstance(perk_id, str):
            if path.stem != perk_id:
                errors.append(
                    f"{path.relative_to(root)}: filename must match perk id "
                    f"{perk_id!r}"
                )
            if perk_id in perk_records:
                previous = perk_records[perk_id][0].relative_to(root)
                errors.append(
                    f"{path.relative_to(root)}: duplicate perk id {perk_id!r}; "
                    f"also present in {previous}"
                )
            else:
                perk_records[perk_id] = (path, record)

    pools_root = root / "data" / "weapon-perk-pools"
    pool_records: dict[str, tuple[Path, dict[str, Any]]] = {}
    if catalogue.is_local:
        pool_files = sorted(pools_root.glob("*.json")) if pools_root.exists() else []
    else:
        pool_files = virtual_files(pool_records_source, "weapon-perk-pools", "weapon_id")

    for path in pool_files:
        record = _read_json(path, root, errors)
        if not isinstance(record, dict):
            continue

        validator = validators.get("weapon-perk-pool")
        if validator:
            issues = sorted(
                validator.iter_errors(record),
                key=lambda issue: tuple(str(part) for part in issue.absolute_path),
            )
            for issue in issues:
                errors.append(
                    f"{path.relative_to(root)}: schema violation at "
                    f"{_json_location(issue.absolute_path)}: {issue.message}"
                )

        weapon_id = record.get("weapon_id")
        if isinstance(weapon_id, str):
            if path.stem != weapon_id:
                errors.append(
                    f"{path.relative_to(root)}: filename must match weapon_id "
                    f"{weapon_id!r}"
                )
            if weapon_id in pool_records:
                previous = pool_records[weapon_id][0].relative_to(root)
                errors.append(
                    f"{path.relative_to(root)}: duplicate weapon perk pool "
                    f"for {weapon_id!r}; also present in {previous}"
                )
            else:
                pool_records[weapon_id] = (path, record)

            if weapon_id not in weapon_records:
                errors.append(
                    f"{path.relative_to(root)}: references missing weapon "
                    f"{weapon_id!r}"
                )
            else:
                linked_weapon = weapon_records[weapon_id][1]
                pool_hash = record.get("weapon_hash")
                linked_hash = linked_weapon.get("bungie_hash")
                if (
                    isinstance(pool_hash, int)
                    and not isinstance(pool_hash, bool)
                    and isinstance(linked_hash, int)
                    and not isinstance(linked_hash, bool)
                    and pool_hash != linked_hash
                ):
                    errors.append(
                        f"{path.relative_to(root)}: weapon_hash {pool_hash} "
                        f"does not match linked Supabase weapon hash {linked_hash}"
                    )

        slots = record.get("slots")
        if isinstance(slots, list):
            seen_slot_ids: set[str] = set()
            for slot_index, slot in enumerate(slots):
                if not isinstance(slot, dict):
                    continue
                slot_id = slot.get("id")
                if isinstance(slot_id, str):
                    if slot_id in seen_slot_ids:
                        errors.append(
                            f"{path.relative_to(root)}: duplicate slot id "
                            f"{slot_id!r}"
                        )
                    seen_slot_ids.add(slot_id)

                perk_ids = slot.get("perk_ids")
                if not isinstance(perk_ids, list):
                    continue
                if record.get("pool_status") == "complete" and not perk_ids:
                    errors.append(
                        f"{path.relative_to(root)}: complete pool slot "
                        f"{slot_id if isinstance(slot_id, str) else slot_index!r} "
                        "must contain at least one perk id"
                    )
                for perk_id in perk_ids:
                    if isinstance(perk_id, str) and perk_id not in perk_records:
                        errors.append(
                            f"{path.relative_to(root)}: slot "
                            f"{slot_id if isinstance(slot_id, str) else slot_index!r} "
                            f"references missing perk {perk_id!r}"
                        )

    # Every weapon definition/version must have a matching pool record, even when its pool is unknown.
    for weapon_id in weapon_records:
        if weapon_id not in pool_records:
            errors.append(
                f"Supabase public.d2wd_weapon_perk_pools: missing perk-pool record "
                f"for weapon version {weapon_id!r}"
            )

    recommendations_root = root / "recommendations"
    recommendation_files = (
        sorted(recommendations_root.rglob("*.json"))
        if recommendations_root.exists()
        else []
    )
    if not recommendation_files:
        errors.append("recommendations: no recommendation JSON files found")

    seen_recommendations: dict[tuple[str, ...], tuple[Path, int]] = {}
    for path in recommendation_files:
        record = _read_json(path, root, errors)
        if not isinstance(record, dict):
            continue

        relative_parts = path.relative_to(root).parts
        is_curated = (
            len(relative_parts) == 4
            and relative_parts[0] == "recommendations"
            and relative_parts[1] == "curated"
            and relative_parts[2] in {"pve", "pvp"}
        )
        is_community = (
            len(relative_parts) == 4
            and relative_parts[0] == "recommendations"
            and relative_parts[1] == "community"
            and relative_parts[2] in {"pve", "pvp"}
        )
        validator = validators.get("community-recommendation" if is_community else "recommendation")
        if validator:
            issues = sorted(
                validator.iter_errors(record),
                key=lambda issue: tuple(str(part) for part in issue.absolute_path),
            )
            for issue in issues:
                errors.append(
                    f"{path.relative_to(root)}: schema violation at "
                    f"{_json_location(issue.absolute_path)}: {issue.message}"
                )

        if not (is_curated or is_community):
            errors.append(
                f"{path.relative_to(root)}: recommendation files must be under "
                "recommendations/curated/pve|pvp/ or "
                "recommendations/community/pve|pvp/<weapon-id>.json"
            )
            continue

        if is_community:
            if record.get("publication_status") != "mechanically_validated":
                errors.append(
                    f"{path.relative_to(root)}: community publication_status must be mechanically_validated"
                )
            if record.get("validation_scope") != "canonical-dependencies-and-perk-slots":
                errors.append(
                    f"{path.relative_to(root)}: community validation_scope is unsupported"
                )
            if "mode" in record:
                errors.append(f"{path.relative_to(root)}: community mode belongs to each nested recommendation")
            if "github_username" in record:
                errors.append(f"{path.relative_to(root)}: community authorship belongs to each nested recommendation")
            recommendations = record.get("recommendations")
            seen_issue_numbers: set[int] = set()
            if isinstance(recommendations, list):
                for index, recommendation in enumerate(recommendations):
                    if not isinstance(recommendation, dict):
                        continue
                    rec_location = f"recommendations[{index}]"
                    if recommendation.get("publication_status") != "mechanically_validated":
                        errors.append(f"{path.relative_to(root)}: {rec_location}.publication_status must be mechanically_validated")
                    if recommendation.get("validation_scope") != record.get("validation_scope"):
                        errors.append(f"{path.relative_to(root)}: {rec_location}.validation_scope must match the aggregate")
                    if recommendation.get("published_by") != record.get("published_by"):
                        errors.append(f"{path.relative_to(root)}: {rec_location}.published_by must match the aggregate")
                    if recommendation.get("mode") not in {"pve", "pvp"}:
                        errors.append(f"{path.relative_to(root)}: {rec_location}.mode must be 'pve' or 'pvp'")
                    elif recommendation.get("mode") != relative_parts[2]:
                        errors.append(
                            f"{path.relative_to(root)}: {rec_location}.mode must match community directory "
                            f"({relative_parts[2]!r})"
                        )
                    github_username = recommendation.get("github_username")
                    if not isinstance(github_username, str) or not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?", github_username):
                        errors.append(f"{path.relative_to(root)}: {rec_location} requires a valid github_username from the GitHub issue author")
                    issue_number = recommendation.get("issue_number")
                    if not isinstance(issue_number, int) or isinstance(issue_number, bool) or issue_number < 1:
                        errors.append(f"{path.relative_to(root)}: {rec_location}.issue_number must be a positive integer")
                    elif issue_number in seen_issue_numbers:
                        errors.append(f"{path.relative_to(root)}: duplicate source issue number {issue_number}")
                    else:
                        seen_issue_numbers.add(issue_number)
        else:
            expected_mode = relative_parts[-2]
            if "github_username" in record:
                errors.append(f"{path.relative_to(root)}: github_username is reserved for recommendations/community/ records")
            if record.get("mode") != expected_mode:
                errors.append(f"{path.relative_to(root)}: mode must match its directory ({expected_mode!r})")

        weapon_id = record.get("weapon_id")
        weapon_entry = weapon_records.get(weapon_id) if isinstance(weapon_id, str) else None
        if isinstance(weapon_id, str):
            if is_community:
                if path.stem != weapon_id:
                    errors.append(
                        f"{path.relative_to(root)}: community filename must be "
                        f"{weapon_id}.json (one aggregate file per weapon version and mode)"
                    )
            elif path.stem != weapon_id:
                errors.append(
                    f"{path.relative_to(root)}: filename must match weapon_id "
                    f"{weapon_id!r}"
                )
            if weapon_entry is None:
                errors.append(
                    f"{path.relative_to(root)}: references missing weapon "
                    f"{weapon_id!r}"
                )

        pool_id = record.get("weapon_perk_pool_id")
        pool_entry = pool_records.get(pool_id) if isinstance(pool_id, str) else None
        if not isinstance(pool_id, str):
            errors.append(
                f"{path.relative_to(root)}: weapon_perk_pool_id is required and must "
                "identify the exact version-specific perk pool"
            )
        else:
            if pool_id != weapon_id:
                errors.append(
                    f"{path.relative_to(root)}: weapon_perk_pool_id "
                    f"{pool_id!r} must match weapon_id {weapon_id!r}"
                )
            if pool_entry is None:
                errors.append(
                    f"{path.relative_to(root)}: references missing weapon perk pool "
                    f"{pool_id!r}"
                )

        pool_record = pool_entry[1] if pool_entry else None
        if pool_record is not None and pool_record.get("pool_status") != "complete":
            errors.append(
                f"{path.relative_to(root)}: recommendation requires a complete "
                f"perk pool; {pool_id!r} has pool_status={pool_record.get('pool_status')!r}"
            )

        recommendations = record.get("recommendations")
        if not isinstance(recommendations, list):
            continue

        for recommendation_index, recommendation in enumerate(recommendations):
            if not isinstance(recommendation, dict):
                continue
            location = f"recommendations[{recommendation_index}]"
            signature = recommendation_signature(record, recommendation)
            if signature is not None:
                previous = seen_recommendations.get(signature)
                if previous is not None:
                    previous_path, previous_index = previous
                    errors.append(
                        f"{path.relative_to(root)}: duplicate recommendation "
                        f"(same mode, weapon version, and four perk slots) as "
                        f"{previous_path.relative_to(root)} recommendations[{previous_index}]"
                    )
                else:
                    seen_recommendations[signature] = (path, recommendation_index)
            selected_perk_ids: list[str] = []
            component_columns = [
                column for column in ("barrel", "sight")
                if isinstance(recommendation.get(column), str) and recommendation[column]
            ]
            if len(component_columns) != 1:
                errors.append(
                    f"{path.relative_to(root)}: {location} must contain exactly one of "
                    "'barrel' or 'sight'"
                )
            recommendation_columns = component_columns + ["magazine", "perk_3", "perk_4"]

            for column in recommendation_columns:
                perk_id = recommendation.get(column)
                if not isinstance(perk_id, str) or not perk_id:
                    errors.append(
                        f"{path.relative_to(root)}: {location}.{column} "
                        "must be a canonical perk ID from Supabase public.d2wd_perks"
                    )
                    continue

                selected_perk_ids.append(perk_id)
                perk_entry = perk_records.get(perk_id)
                if perk_entry is None:
                    errors.append(
                        f"{path.relative_to(root)}: {location}.{column} "
                        f"references missing canonical perk {perk_id!r}"
                    )

                if pool_record is None:
                    continue
                matching_slots = _matching_pool_slots(pool_record, column)
                if not matching_slots:
                    errors.append(
                        f"{path.relative_to(root)}: {location}.{column} "
                        f"has no corresponding slot in perk pool {pool_id!r}"
                    )
                    continue
                if not any(
                    perk_id in slot.get("perk_ids", [])
                    for slot in matching_slots
                    if isinstance(slot.get("perk_ids"), list)
                ):
                    matching_slot_ids = [
                        slot.get("id") for slot in matching_slots
                    ]
                    errors.append(
                        f"{path.relative_to(root)}: {location}.{column} "
                        f"perk {perk_id!r} is not listed in matching pool slot(s) "
                        f"{matching_slot_ids!r} of {pool_id!r}"
                    )

            # Verification lives at file level. A verified recommendation file
            # requires the weapon, pool, and four selected perk definitions to
            # be verified as well.
            canonical_dependencies_required = (
                record.get("verification_status") == "verified"
                or (is_community and record.get("publication_status") == "mechanically_validated")
            )
            if canonical_dependencies_required:
                unverified_records: list[str] = []
                if weapon_entry:
                    weapon_record = weapon_entry[1]
                    if weapon_record.get("verification_status") != "verified":
                        unverified_records.append(f"weapon {weapon_id!r}")
                    if not _valid_bungie_hash(weapon_record.get("bungie_hash")):
                        errors.append(
                            f"{path.relative_to(root)}: {location} cannot be verified until "
                            f"weapon {weapon_id!r} has a positive canonical bungie_hash"
                        )
                if pool_record:
                    pool_data = pool_record
                    if pool_data.get("verification_status") != "verified":
                        unverified_records.append(f"perk pool {pool_id!r}")
                    if pool_data.get("pool_status") != "complete":
                        unverified_records.append(f"complete perk pool {pool_id!r}")
                    if not _valid_bungie_hash(pool_data.get("weapon_hash")):
                        errors.append(
                            f"{path.relative_to(root)}: {location} cannot be verified until "
                            f"perk pool {pool_id!r} has a positive weapon_hash"
                        )
                for perk_id in selected_perk_ids:
                    perk_entry = perk_records.get(perk_id)
                    if perk_entry:
                        perk_data = perk_entry[1]
                        if perk_data.get("verification_status") != "verified":
                            unverified_records.append(f"perk {perk_id!r}")
                        if not _valid_bungie_hash(perk_data.get("bungie_hash")):
                            errors.append(
                                f"{path.relative_to(root)}: {location} cannot be verified until "
                                f"perk {perk_id!r} has a positive canonical bungie_hash"
                            )
                if unverified_records:
                    if is_community:
                        errors.append(
                            f"{path.relative_to(root)}: {location} cannot be marked mechanically_validated "
                            "until its weapon, complete perk pool, and selected perk definitions pass "
                            f"the required canonical checks; still unverified: {unverified_records!r}"
                        )
                    else:
                        errors.append(
                            f"{path.relative_to(root)}: {location} cannot be marked verified until its weapon, "
                            "complete perk pool, and selected perk definitions are verified; "
                            f"still unverified: {unverified_records!r}"
                        )

    _LAST_COUNTS = {
        "weapons": len(weapon_records),
        "perks": len(perk_records),
        "pools": len(pool_records),
    }
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate weapon data, recommendation schemas, and cross-references."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=ROOT,
        help="repository root (defaults to the root containing this script)",
    )
    args = parser.parse_args(argv)

    errors = validate_repository(args.root)
    if errors:
        print("Validation failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        print(f"\n{len(errors)} error(s) found.", file=sys.stderr)
        return 1

    weapon_count = _LAST_COUNTS["weapons"]
    perk_count = _LAST_COUNTS["perks"]
    pool_count = _LAST_COUNTS["pools"]
    recommendation_count = len(list((args.root / "recommendations").rglob("*.json")))
    print(
        "Validation passed: "
        f"{weapon_count} weapon record(s), "
        f"{perk_count} perk record(s), "
        f"{pool_count} weapon perk pool(s), "
        f"{recommendation_count} recommendation file(s); "
        "schemas and cross-references are valid; canonical catalogue is "
        + ("local JSON fixtures." if all((args.root / "data" / folder).is_dir() for folder in ("weapons", "perks", "weapon-perk-pools")) else "Supabase.")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
