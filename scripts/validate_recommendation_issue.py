#!/usr/bin/env python3
"""Mechanically validate a compact community recommendation issue; never edits JSON."""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from datetime import date
from pathlib import Path

from recommendation_uniqueness import find_duplicate_recommendations, RecommendationScanError
from catalogue_source import CatalogueError, open_catalogue

ROOT = Path(__file__).resolve().parents[1]
HEADINGS = {
    "mode": "mode",
    "name": "name",
    "description": "description",
    "weapon": "weapon",
    "perk 1": "perk1",
    "perk 2": "perk2",
    "perk 3": "perk3",
    "perk 4": "perk4",
}
PERK_FIELDS = ("perk1", "perk2", "perk3", "perk4")
PLACEHOLDERS = {"", "no response", "na", "n/a", "none", "null", "-", "todo", "tbd", "test", "not sure"}
PLACEHOLDER_WITH_NUMBER_RE = re.compile(r"^(?:test(?:ing)?|sample|example|placeholder|dummy|todo|tbd|lorem|tt)\s*\d+$")

# The repository catalog uses canonical English IDs/names. Keep explicit, reviewable
# aliases for commonly used localized display names rather than guessing fuzzy matches.
LOCALIZED_PERK_ALIASES = {
    "incandescente": "incandescent",
}


def norm(value):
    value = unicodedata.normalize("NFKD", str(value or "")).casefold()
    value = "".join(char for char in value if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def slug(value):
    return norm(value).replace(" ", "-")


def is_placeholder(value):
    """Reject empty/template text and common numbered test values such as Test2 or TT2."""
    normalized = norm(value)
    return normalized in PLACEHOLDERS or PLACEHOLDER_WITH_NUMBER_RE.fullmatch(normalized) is not None


def _valid_date(value):
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _valid_hash(value):
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def verified_dependency_blockers(weapon, pool, perks, selected_perk_ids):
    """Require the exact weapon, its complete verified pool, and only the four selected perks."""
    blockers = []

    if not isinstance(weapon, dict):
        blockers.append("Exact weapon record is unresolved.")
        return blockers
    weapon_id = weapon.get("id")
    weapon_hash = weapon.get("bungie_hash")
    if weapon.get("verification_status") != "verified":
        blockers.append("Weapon " + str(weapon_id) + " is not marked verified.")
    if not _valid_hash(weapon_hash):
        blockers.append("Weapon " + str(weapon_id) + " has no positive canonical Bungie hash.")
    if not _valid_date(weapon.get("last_verified")):
        blockers.append("Weapon " + str(weapon_id) + " has no valid last_verified date.")
    if not isinstance(pool, dict):
        blockers.append("Exact weapon-version perk pool is unresolved.")
        return list(dict.fromkeys(blockers))
    pool_id = pool.get("weapon_id")
    if pool_id != weapon_id:
        blockers.append("Perk pool " + str(pool_id) + " does not reference exact weapon " + str(weapon_id) + ".")
    if pool.get("verification_status") != "verified":
        blockers.append("Perk pool " + str(pool_id) + " is not marked verified.")
    if pool.get("pool_status") != "complete":
        blockers.append("Perk pool " + str(pool_id) + " is not marked complete.")
    if not _valid_date(pool.get("last_verified")):
        blockers.append("Perk pool " + str(pool_id) + " has no valid last_verified date.")
    pool_hash = pool.get("weapon_hash")
    if not _valid_hash(pool_hash) or not _valid_hash(weapon_hash) or pool_hash != weapon_hash:
        blockers.append("Perk pool " + str(pool_id) + " does not have the exact verified weapon hash.")

    slots = pool.get("slots")
    if not isinstance(slots, list) or not slots:
        blockers.append("Perk pool " + str(pool_id) + " has no slots to verify.")
        return list(dict.fromkeys(blockers))
    for slot in slots:
        slot_label = (slot.get("label") or slot.get("id") or "unnamed slot") if isinstance(slot, dict) else "unnamed slot"
        perk_ids = slot.get("perk_ids") if isinstance(slot, dict) else None
        if not isinstance(perk_ids, list) or not perk_ids:
            blockers.append("Perk pool " + str(pool_id) + " contains an empty or invalid slot: " + str(slot_label) + ".")
            continue
        if any(not isinstance(perk_id, str) or not perk_id for perk_id in perk_ids):
            blockers.append("Perk pool " + str(pool_id) + " contains a malformed perk ID in " + str(slot_label) + ".")

    selected_ids = list(selected_perk_ids) if isinstance(selected_perk_ids, (list, tuple)) else []
    if len(selected_ids) != 4:
        blockers.append("Exactly four selected canonical perks are required for automatic publication.")
    valid_selected_ids = [perk_id for perk_id in selected_ids if isinstance(perk_id, str) and perk_id]
    if len(valid_selected_ids) != len(selected_ids):
        blockers.append("One or more selected perks do not resolve to a canonical perk ID.")
    if len(set(valid_selected_ids)) != len(valid_selected_ids):
        blockers.append("The four selected perks must be distinct canonical perk records.")

    perk_rows_by_id = {}
    for perk in perks:
        perk_id = perk.get("id") if isinstance(perk, dict) else None
        if isinstance(perk_id, str):
            perk_rows_by_id.setdefault(perk_id, []).append(perk)

    for perk_id in dict.fromkeys(valid_selected_ids):
        matches = perk_rows_by_id.get(perk_id, [])
        if len(matches) != 1:
            blockers.append("Selected perk " + repr(perk_id) + " does not resolve to exactly one canonical perk record.")
            continue
        perk = matches[0]
        if perk.get("verification_status") != "verified":
            blockers.append("Selected perk " + str(perk_id) + " is not marked verified.")
        if not _valid_hash(perk.get("bungie_hash")):
            blockers.append("Selected perk " + str(perk_id) + " has no positive canonical Bungie hash.")
        if not _valid_date(perk.get("last_verified")):
            blockers.append("Selected perk " + str(perk_id) + " has no valid last_verified date.")

    return list(dict.fromkeys(blockers))

def parse_body(body):
    matches = list(re.finditer(r"^###\s+(.+?)\s*$", body or "", re.M))
    fields = {}
    lookup = {norm(label): key for label, key in HEADINGS.items()}
    for index, match in enumerate(matches):
        key = lookup.get(norm(match.group(1)))
        if not key:
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        value = (body[match.end():end] or "").strip()
        if norm(value) in {"no response", ""}:
            value = ""
        fields[key] = value
    return fields


def load_records(folder):
    records = []
    for path in sorted(folder.glob("*.json")) if folder.exists() else []:
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(item, dict):
                records.append(item)
        except (OSError, json.JSONDecodeError):
            continue
    return records


def resolve_weapon(weapon_id, weapons, root, catalogue=None):
    submitted = str(weapon_id or "").strip()
    if not submitted:
        return None, "exact filename stem", ["Required form field is empty: Weapon."]
    if submitted.casefold().endswith(".json"):
        return None, "exact filename stem", [
            "Enter the exact weapon filename without the .json extension (for example, avalanche_s5)."
        ]
    if "/" in submitted or "\\" in submitted or submitted in {".", ".."}:
        return None, "exact filename stem", [
            "Weapon must be a filename stem only, not a path."
        ]

    matches = [weapon for weapon in weapons if weapon.get("id") == submitted]
    if catalogue is not None and not catalogue.is_local:
        if not matches:
            return None, "Supabase d2wd_weapons.id", [
                "No exact data/weapons/" + submitted + ".json catalogue ID exists in Supabase d2wd_weapons. Use the exact weapon-version ID without an extension."
            ]
        if len(matches) != 1:
            return None, "Supabase d2wd_weapons.id", [
                "Supabase d2wd_weapons does not resolve " + submitted + " to exactly one canonical weapon record."
            ]
        return matches[0], "Supabase d2wd_weapons.id", []

    path = root / "data" / "weapons" / (submitted + ".json")
    if not path.is_file():
        return None, "exact filename stem", [
            "No exact data/weapons/" + submitted + ".json file exists. Use the JSON filename without its extension."
        ]
    if len(matches) != 1:
        return None, "exact filename stem", [
            "The file data/weapons/" + submitted + ".json does not resolve to exactly one weapon record with id " + submitted + "."
        ]
    return matches[0], "exact filename stem", []


def resolve_perk(value, perks):
    text = str(value or "").strip()
    by_id = {perk.get("id"): perk for perk in perks if perk.get("id")}
    if text in by_id:
        return by_id[text], []
    candidate_slug = slug(text)
    if candidate_slug in by_id:
        return by_id[candidate_slug], []

    alias = LOCALIZED_PERK_ALIASES.get(norm(text))
    if alias:
        if alias in by_id:
            return by_id[alias], []
        alias_matches = [
            perk for perk in perks
            if norm(perk.get("name")) == norm(alias) or norm(perk.get("id")) == norm(alias)
        ]
        if len(alias_matches) == 1:
            return alias_matches[0], []

    matches = [perk for perk in perks if norm(perk.get("name")) == norm(text)]
    if len(matches) == 1:
        return matches[0], []
    if len(matches) > 1:
        ids = ", ".join(str(perk.get("id")) for perk in matches[:8])
        return None, ["Perk " + text + " matches multiple catalogue records: " + ids + ". Provide the exact perk ID."]
    return None, [
        "Perk " + text + " is not a canonical perk ID or recognised display name in data/perks/. Check the spelling or provide its ID."
    ]


def _matching_pool_slots(pool_record, column):
    """Return exact pool slots matching a recommendation JSON column."""
    slots = pool_record.get("slots")
    if not isinstance(slots, list):
        return []
    matches = []
    for slot in slots:
        if not isinstance(slot, dict):
            continue
        slot_id = slot.get("id")
        label = slot.get("label")
        slot_id = slot_id.lower() if isinstance(slot_id, str) else ""
        label = label.lower().strip() if isinstance(label, str) else ""
        if column == "barrel" and ("barrel" in slot_id or "barrel" in label):
            matches.append(slot)
        elif column == "sight" and any(
            token in value
            for value in (slot_id, label)
            for token in ("scope", "sight", "optic")
        ):
            matches.append(slot)
        elif column == "magazine" and ("magazine" in slot_id or "magazine" in label):
            matches.append(slot)
        elif column == "perk_3" and (slot_id.endswith("trait-1") or ("trait" in label and label.endswith(" 1"))):
            matches.append(slot)
        elif column == "perk_4" and (slot_id.endswith("trait-2") or ("trait" in label and label.endswith(" 2"))):
            matches.append(slot)
    return matches


def validate_issue(body, root=ROOT, title="[Recommendation proposal]", issue_number=None):
    fields = parse_body(body)
    errors = []
    warnings = []
    checks = []
    def check(name, status, detail): checks.append({"name": name, "status": status, "detail": detail})

    if not str(title or "").casefold().startswith("[recommendation proposal]"):
        errors.append("Issue title must start with [Recommendation proposal].")
    required = {
        "mode": "Mode",
        "name": "Name",
        "description": "Description",
        "weapon": "Weapon",
    }
    for key, label in required.items():
        if not fields.get(key, "").strip():
            errors.append("Required form field is empty: " + label + ".")

    # Sandbox/build and source evidence are intentionally outside issue intake.
    # The dedicated maintainer-dispatched publisher collects and validates them.
    sandbox = None
    verification_sources = []
    mode = norm(fields.get("mode", ""))
    if mode not in {"pve", "pvp"}: errors.append("Mode must be PvE or PvP.")
    else: check("Mode", "pass", "Mode is " + ("PvE" if mode == "pve" else "PvP") + ".")
    name = fields.get("name", "").strip()
    description = fields.get("description", "").strip()
    if is_placeholder(name): errors.append("Name looks like a placeholder; provide a meaningful roll name.")
    if is_placeholder(description): errors.append("Description looks like a placeholder; briefly explain the suggested roll.")

    catalogue = None
    weapons = []
    perks = []
    pools = []
    try:
        catalogue = open_catalogue(root)
        weapons = catalogue.list_records("weapons")
        perks = catalogue.list_records("perks")
        pools = catalogue.list_records("pools")
    except CatalogueError as exc:
        errors.append("Canonical catalogue could not be loaded: " + str(exc))
        check("Canonical catalogue", "fail", "Weapons, perks and perk pools could not all be loaded.")
    else:
        check(
            "Canonical catalogue",
            "pass",
            "Loaded exact weapon versions, perks and pools from "
            + ("local JSON fixtures." if catalogue.is_local else "Supabase public catalogue tables."),
        )
    weapon, method, weapon_errors = resolve_weapon(fields.get("weapon", ""), weapons, root, catalogue)
    errors.extend(weapon_errors)
    weapon_id = weapon.get("id") if weapon else None
    weapon_name = weapon.get("name") if weapon else None
    canonical_slots = {}
    pool = None
    if weapon:
        weapon_detail = (
            "Resolved data/weapons/" + str(weapon_id) + ".json — " + str(weapon_name) + "."
            if catalogue is None or catalogue.is_local
            else "Resolved Supabase d2wd_weapons.id=" + str(weapon_id) + " — " + str(weapon_name) + "."
        )
        check("Exact weapon version", "pass", weapon_detail)
        if weapon.get("verification_status") != "verified": warnings.append("Weapon " + str(weapon_id) + " is not marked verified; its historical identity and metadata still need maintainer review.")
        pool_matches = [pool for pool in pools if pool.get("weapon_id") == weapon_id]
        pool = pool_matches[0] if len(pool_matches) == 1 else None
        if not pool:
            errors.append("No unique perk-pool record exists for exact weapon version " + str(weapon_id) + ".")
            check("Weapon perk pool", "fail", "Could not resolve one pool for " + str(weapon_id) + ".")
        else:
            check("Weapon perk pool", "pass", "Found the perk pool for " + str(weapon_id) + ".")
            if pool.get("pool_status") != "complete": warnings.append("Pool " + str(weapon_id) + " is marked " + str(pool.get("pool_status")) + ", not complete. A listed perk can be checked for membership, but the catalogue may omit other options.")
            if pool.get("verification_status") != "verified": warnings.append("Pool " + str(weapon_id) + " is not human-verified; membership is a mechanical catalogue check, not proof of historical in-game legality.")
            weapon_hash, pool_hash = weapon.get("bungie_hash"), pool.get("weapon_hash")
            if weapon_hash is not None and pool_hash is not None and weapon_hash != pool_hash: errors.append("Weapon/pool hash mismatch for " + str(weapon_id) + ": weapon=" + str(weapon_hash) + ", pool=" + str(pool_hash) + ".")
            supplied_perks = [(field, fields.get(field, "").strip()) for field in PERK_FIELDS]
            supplied_perks = [(field, value) for field, value in supplied_perks if value]
            if not supplied_perks: errors.append("Enter at least one perk and supply all four roll slots: one barrel or scope/sight, magazine, trait 1, and trait 2.")
            slot_names = {"barrel": "Barrel", "sight": "Scope / Sight", "magazine": "Magazine", "perk_3": "Trait 1", "perk_4": "Trait 2"}
            for field, raw in supplied_perks:
                display_label = "Perk " + field.removeprefix("perk")
                perk, perk_errors = resolve_perk(raw, perks)
                if not perk:
                    errors.extend(perk_errors); check(display_label, "fail", "Could not resolve " + raw + " to one canonical perk."); continue
                canonical_id = perk.get("id")
                matching_columns = []
                for column in ("barrel", "sight", "magazine", "perk_3", "perk_4"):
                    slots = _matching_pool_slots(pool, column)
                    if any(canonical_id in slot.get("perk_ids", []) for slot in slots if isinstance(slot.get("perk_ids"), list)): matching_columns.append(column)
                if not matching_columns:
                    errors.append("Perk " + str(canonical_id) + " is not listed in any slot matching a recommendation column in " + str(weapon_id) + "'s weapon-perk pool.")
                    check(display_label, "fail", str(canonical_id) + " is not listed in a recommendation slot."); continue
                if len(matching_columns) > 1:
                    errors.append("Perk " + str(canonical_id) + " matches multiple recommendation slots in " + str(weapon_id) + "'s pool: " + ", ".join(matching_columns) + ". The roll cannot be mapped unambiguously.")
                    check(display_label, "fail", str(canonical_id) + " matches multiple roll columns."); continue
                column = matching_columns[0]
                if column in canonical_slots:
                    errors.append("More than one submitted perk maps to the " + column + " slot; provide exactly one perk for each recommendation column.")
                    check(display_label, "fail", str(canonical_id) + " duplicates the " + column + " slot."); continue
                canonical_slots[column] = canonical_id
                check(display_label, "pass", str(canonical_id) + " mapped to " + slot_names[column] + " from the exact weapon-version pool.")
                if perk.get("verification_status") != "verified": warnings.append("Perk " + str(canonical_id) + " is not marked verified; its underlying definition still needs maintainer review.")
            if not any(column in canonical_slots for column in ("barrel", "sight")):
                errors.append("Missing required Barrel or Scope/Sight perk. Supply one component perk, one Magazine, Trait 1, and Trait 2.")
            for column in ("magazine", "perk_3", "perk_4"):
                if column not in canonical_slots: errors.append("Missing required " + slot_names[column] + " perk. Supply exactly one perk for each of the four roll slots.")
    else:
        check(
            "Exact weapon version",
            "fail",
            "The submitted weapon did not match an exact data/weapons/<id>.json filename or canonical Supabase weapon ID."
            if catalogue is None or catalogue.is_local
            else "The submitted weapon ID did not match a record in Supabase d2wd_weapons.",
        )

    canonical_recommendation = None
    duplicate_matches = []
    if not errors and weapon:
        component_column = "barrel" if "barrel" in canonical_slots else "sight"
        canonical_recommendation = {
            "name": name,
            component_column: canonical_slots[component_column],
            "magazine": canonical_slots["magazine"],
            "perk_3": canonical_slots["perk_3"],
            "perk_4": canonical_slots["perk_4"],
            "description": description,
        }
        candidate_record = {
            "mode": mode,
            "weapon_id": weapon_id,
            "recommendations": [canonical_recommendation],
        }
        try:
            duplicate_matches = find_duplicate_recommendations(
                root, candidate_record, ignore_issue_number=issue_number
            )
        except RecommendationScanError as exc:
            errors.append("Could not safely enforce recommendation uniqueness: " + str(exc))
            check("Duplicate recommendation guard", "fail", "Could not scan all existing recommendations; publication is blocked.")
        else:
            if duplicate_matches:
                existing_paths = sorted({match["path"] for match in duplicate_matches})
                errors.append(
                    "Duplicate recommendation: the same mode, weapon version, and four perk slots "
                    "already exist in " + ", ".join(existing_paths) +
                    ". This issue will not add or change a recommendation in main."
                )
                check(
                    "Duplicate recommendation guard",
                    "fail",
                    "The exact roll already exists at " + ", ".join(existing_paths) +
                    "; nothing will be integrated.",
                )
            else:
                check(
                    "Duplicate recommendation guard",
                    "pass",
                    "No identical roll exists in curated or community recommendations.",
                )
        warnings.append("Mechanical checks confirm the selected perks belong to the exact weapon-version pool; they do not claim that the roll is optimal or tested in a particular sandbox.")
    publication_blockers = verified_dependency_blockers(weapon, pool, perks, list(canonical_slots.values()))
    # A proposal is eligible for publication only when the issue is mechanically
    # valid and the exact weapon, its complete versioned pool, and all four
    # selected perk records pass the canonical verification gate. Sandbox claims
    # and external source URLs are deliberately not required for this gate.
    publication_ready = not errors and not publication_blockers
    if duplicate_matches:
        status = "duplicate-blocked"
    elif errors:
        status = "action-required"
    elif publication_ready:
        status = "canonical-dependencies-ready-to-publish"
    else:
        status = "mechanical-checks-passed-needs-canonical-data-review"

    return {
        "status": status,
        "issue_number": issue_number, "title": title,
        "submitted_mode": mode if mode in {"pve", "pvp"} else fields.get("mode", "").strip(),
        "submitted_name": name, "resolved_weapon_id": weapon_id, "resolved_weapon_name": weapon_name,
        "weapon_resolution_method": method, "canonical_recommendation": canonical_recommendation,
        "verified_for_sandbox": sandbox,
        "verification_sources": verification_sources,
        "publication_ready": publication_ready,
        "publication_blockers": publication_blockers,
        "errors": errors, "warnings": list(dict.fromkeys(warnings)), "checks": checks,
        "duplicate_matches": duplicate_matches,
    }

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--event-path", type=Path)
    parser.add_argument("--body-file", type=Path)
    parser.add_argument("--title", default="[Recommendation proposal]")
    parser.add_argument("--issue-number", type=int)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--github-output", type=Path)
    parser.add_argument("--fail-on-errors", action="store_true")
    args = parser.parse_args()

    if args.event_path:
        event = json.loads(args.event_path.read_text(encoding="utf-8"))
        issue = event.get("issue", event)
        body = issue.get("body") or ""
        title = issue.get("title") or args.title
        number = issue.get("number", args.issue_number)
    else:
        body = args.body_file.read_text(encoding="utf-8") if args.body_file else sys.stdin.read()
        title = args.title
        number = args.issue_number

    report = validate_issue(body, args.root, title, number)
    output = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.write_text(output, encoding="utf-8")
    print(output, end="")
    if args.github_output:
        with args.github_output.open("a", encoding="utf-8") as handle:
            handle.write("has_errors=" + str(bool(report["errors"])).lower() + "\n")
            handle.write("publication_ready=" + str(bool(report["publication_ready"])).lower() + "\n")
            handle.write("intake_status=" + report["status"].replace("\n", " ") + "\n")
    return 1 if args.fail_on_errors and report["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
