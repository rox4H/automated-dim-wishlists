"""Regression tests for repository data validation."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from validate_json import DuplicateKeyError, parse_json_text, validate_repository  # noqa: E402


def _fixture_weapon() -> dict:
    return {
        "id": "sample-weapon_s1",
        "name": "Sample Weapon",
        "bungie_hash": 12345,
        "weapon_type": None,
        "damage_type": None,
        "ammo_type": None,
        "frame": None,
        "rarity": None,
        "source_activity": None,
        "season": "Season 1: The Red War",
        "craftable": None,
        "origin_trait": None,
        "verification_status": "unverified",
        "last_verified": None,
        "notes": [],
        "season_number": 1,
        "breaker": None,
        "deepsight_harmonizable": None,
    }


def _fixture_perk(
    perk_id: str = "sample-perk",
    name: str = "Sample Perk",
    perk_type: str = "trait",
) -> dict:
    return {
        "id": perk_id,
        "name": name,
        "bungie_hash": None,
        "perk_type": perk_type,
        "description": None,
        "verification_status": "unverified",
        "last_verified": None,
        "notes": [],
    }


def _fixture_pool() -> dict:
    return {
        "weapon_id": "sample-weapon_s1",
        "weapon_hash": 12345,
        "version_label": "Sample historical version",
        "pool_status": "complete",
        "verification_status": "unverified",
        "last_verified": None,
        "slots": [
            {
                "id": "barrel",
                "label": "Barrel",
                "selection_mode": "choose-one",
                "perk_ids": ["sample-barrel"],
                "notes": [],
            },
            {
                "id": "magazine",
                "label": "Magazine",
                "selection_mode": "choose-one",
                "perk_ids": ["sample-magazine"],
                "notes": [],
            },
            {
                "id": "trait-1",
                "label": "Trait 1",
                "selection_mode": "choose-one",
                "perk_ids": ["sample-trait-1", "sample-trait-1-alternative"],
                "notes": [],
            },
            {
                "id": "trait-2",
                "label": "Trait 2",
                "selection_mode": "choose-one",
                "perk_ids": ["sample-trait-2"],
                "notes": [],
            },
        ],
        "notes": [],
        "sources": [],
    }


def _fixture_recommendation() -> dict:
    return {
        "weapon_id": "sample-weapon_s1",
        "weapon_perk_pool_id": "sample-weapon_s1",
        "mode": "pve",
        "verification_status": "unverified",
        "verified_for_sandbox": None,
        "last_reviewed": None,
        "rating": None,
        "recommendations": [
            {
                "name": "Fixture roll",
                "barrel": "sample-barrel",
                "magazine": "sample-magazine",
                "perk_3": "sample-trait-1",
                "perk_4": "sample-trait-2",
                "description": "Fixture roll description used to test validation.",
            }
        ],
    }

class ValidateJsonTests(unittest.TestCase):
    def _make_fixture_repo(self, root: Path) -> None:
        (root / "schemas").mkdir(parents=True)
        (root / "data" / "weapons").mkdir(parents=True)
        (root / "data" / "perks").mkdir(parents=True)
        (root / "data" / "weapon-perk-pools").mkdir(parents=True)
        (root / "recommendations" / "curated" / "pve").mkdir(parents=True)
        for schema_name in (
            "weapon.schema.json",
            "recommendation.schema.json",
            "community-recommendations.schema.json",
            "perk.schema.json",
            "weapon-perk-pool.schema.json",
        ):
            shutil.copy2(ROOT / "schemas" / schema_name, root / "schemas")
        (root / "data" / "weapons" / "sample-weapon_s1.json").write_text(
            json.dumps(_fixture_weapon(), indent=2) + "\n", encoding="utf-8"
        )
        (root / "data" / "perks" / "sample-perk.json").write_text(
            json.dumps(_fixture_perk(), indent=2) + "\n", encoding="utf-8"
        )

        for perk_id, name, perk_type in (
            ("sample-barrel", "Sample Barrel", "barrel"),
            ("sample-magazine", "Sample Magazine", "magazine"),
            ("sample-trait-1", "Sample Trait 1", "trait"),
            ("sample-trait-2", "Sample Trait 2", "trait"),
            ("sample-trait-1-alternative", "Sample Trait 1 Alternative", "trait"),
        ):
            (root / "data" / "perks" / f"{perk_id}.json").write_text(
                json.dumps(_fixture_perk(perk_id, name, perk_type), indent=2) + "\n",
                encoding="utf-8",
            )
        (root / "data" / "weapon-perk-pools" / "sample-weapon_s1.json").write_text(
            json.dumps(_fixture_pool(), indent=2) + "\n", encoding="utf-8"
        )
        (root / "recommendations" / "curated" / "pve" / "sample-weapon_s1.json").write_text(
            json.dumps(_fixture_recommendation(), indent=2) + "\n", encoding="utf-8"
        )

    def test_fixture_repository_is_valid_before_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._make_fixture_repo(root)
            self.assertEqual(validate_repository(root), [])

    def test_one_slot_accepts_multiple_alternative_perks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._make_fixture_repo(root)
            pool_path = root / "data" / "weapon-perk-pools" / "sample-weapon_s1.json"
            pool = json.loads(pool_path.read_text(encoding="utf-8"))

            matching_slots = [slot for slot in pool["slots"] if slot["id"] == "trait-1"]
            self.assertEqual(len(matching_slots), 1, "alternatives must stay in one slot")
            self.assertEqual(
                matching_slots[0]["perk_ids"],
                ["sample-trait-1", "sample-trait-1-alternative"],
            )
            self.assertEqual(validate_repository(root), [])

    def test_weapon_record_rejects_legacy_sources_property(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._make_fixture_repo(root)
            path = root / "data" / "weapons" / "sample-weapon_s1.json"
            record = json.loads(path.read_text(encoding="utf-8"))
            record["sources"] = []
            path.write_text(json.dumps(record), encoding="utf-8")

            errors = validate_repository(root)
            self.assertTrue(
                any("sources" in error and "schema violation" in error for error in errors),
                errors,
            )

    def test_verified_pool_may_omit_sources(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._make_fixture_repo(root)
            path = root / "data" / "weapon-perk-pools" / "sample-weapon_s1.json"
            pool = json.loads(path.read_text(encoding="utf-8"))
            pool["verification_status"] = "verified"
            pool["last_verified"] = "2026-10-28"
            del pool["sources"]
            path.write_text(json.dumps(pool), encoding="utf-8")

            self.assertEqual(validate_repository(root), [])

    def test_verified_pool_requires_verification_date(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._make_fixture_repo(root)
            path = root / "data" / "weapon-perk-pools" / "sample-weapon_s1.json"
            pool = json.loads(path.read_text(encoding="utf-8"))
            pool["verification_status"] = "verified"
            del pool["sources"]
            path.write_text(json.dumps(pool), encoding="utf-8")

            errors = validate_repository(root)
            self.assertTrue(
                any("sample-weapon_s1.json" in error and "last_verified" in error for error in errors),
                errors,
            )

    def test_current_repository_is_valid(self) -> None:
        self.assertEqual(validate_repository(ROOT), [])

    def test_duplicate_json_object_keys_are_rejected(self) -> None:
        with self.assertRaisesRegex(DuplicateKeyError, "duplicate JSON object key"):
            parse_json_text('{"id":"first","id":"second"}')

    def test_breaker_is_a_single_nullable_enum_value(self) -> None:
        for value in (None, "unstoppable", "overload", "barrier"):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                self._make_fixture_repo(root)
                path = root / "data" / "weapons" / "sample-weapon_s1.json"
                record = json.loads(path.read_text(encoding="utf-8"))
                record["breaker"] = value
                path.write_text(json.dumps(record), encoding="utf-8")
                self.assertEqual(validate_repository(root), [])

        for value in ([], ["barrier"], ["barrier", "overload"], "anti-barrier"):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                self._make_fixture_repo(root)
                path = root / "data" / "weapons" / "sample-weapon_s1.json"
                record = json.loads(path.read_text(encoding="utf-8"))
                record["breaker"] = value
                path.write_text(json.dumps(record), encoding="utf-8")
                errors = validate_repository(root)
                self.assertTrue(
                    any("schema violation at $.breaker" in error for error in errors),
                    errors,
                )

    def test_unversioned_weapon_id_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._make_fixture_repo(root)

            versioned_weapon_path = root / "data" / "weapons" / "sample-weapon_s1.json"
            weapon = json.loads(versioned_weapon_path.read_text(encoding="utf-8"))
            weapon["id"] = "sample-weapon"
            versioned_weapon_path.unlink()
            (root / "data" / "weapons" / "sample-weapon.json").write_text(
                json.dumps(weapon, indent=2) + "\n", encoding="utf-8"
            )

            versioned_pool_path = root / "data" / "weapon-perk-pools" / "sample-weapon_s1.json"
            pool = json.loads(versioned_pool_path.read_text(encoding="utf-8"))
            pool["weapon_id"] = "sample-weapon"
            versioned_pool_path.unlink()
            (root / "data" / "weapon-perk-pools" / "sample-weapon.json").write_text(
                json.dumps(pool, indent=2) + "\n", encoding="utf-8"
            )

            errors = validate_repository(root)
            self.assertTrue(
                any("must use an underscore season suffix" in error for error in errors), errors
            )

    def test_underscored_seasonal_weapon_id_is_valid(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._make_fixture_repo(root)
            record = _fixture_weapon()
            record.update({
                "id": "sample-weapon_s2",
                "season": "Season 2: Curse of Osiris",
                "season_number": 2,
            })
            (root / "data" / "weapons" / "sample-weapon_s2.json").write_text(
                json.dumps(record, indent=2) + "\n", encoding="utf-8"
            )
            pool = _fixture_pool()
            pool["weapon_id"] = "sample-weapon_s2"
            pool["weapon_hash"] = record["bungie_hash"]
            (root / "data" / "weapon-perk-pools" / "sample-weapon_s2.json").write_text(
                json.dumps(pool, indent=2) + "\n", encoding="utf-8"
            )
            self.assertEqual(validate_repository(root), [])

    def test_hyphenated_seasonal_weapon_id_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._make_fixture_repo(root)

            versioned_weapon_path = root / "data" / "weapons" / "sample-weapon_s1.json"
            weapon = json.loads(versioned_weapon_path.read_text(encoding="utf-8"))
            weapon["id"] = "sample-weapon-s1"
            versioned_weapon_path.unlink()
            (root / "data" / "weapons" / "sample-weapon-s1.json").write_text(
                json.dumps(weapon, indent=2) + "\n", encoding="utf-8"
            )

            versioned_pool_path = root / "data" / "weapon-perk-pools" / "sample-weapon_s1.json"
            pool = json.loads(versioned_pool_path.read_text(encoding="utf-8"))
            pool["weapon_id"] = "sample-weapon-s1"
            versioned_pool_path.unlink()
            (root / "data" / "weapon-perk-pools" / "sample-weapon-s1.json").write_text(
                json.dumps(pool, indent=2) + "\n", encoding="utf-8"
            )

            errors = validate_repository(root)
            self.assertTrue(
                any("must use an underscore season suffix" in error for error in errors),
                errors,
            )
            self.assertTrue(
                any("schema violation at $.id" in error for error in errors),
                errors,
            )

    def test_orphan_perk_reference_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._make_fixture_repo(root)
            path = root / "data" / "weapon-perk-pools" / "sample-weapon_s1.json"
            record = json.loads(path.read_text(encoding="utf-8"))
            record["slots"][0]["perk_ids"] = ["missing-perk"]
            path.write_text(json.dumps(record), encoding="utf-8")

            errors = validate_repository(root)
            self.assertTrue(
                any("references missing perk 'missing-perk'" in error for error in errors),
                errors,
            )

    def test_weapon_without_perk_pool_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._make_fixture_repo(root)
            pool_path = root / "data" / "weapon-perk-pools" / "sample-weapon_s1.json"
            pool_path.unlink()

            errors = validate_repository(root)
            self.assertTrue(
                any("missing perk-pool record for weapon version 'sample-weapon_s1'" in error for error in errors),
                errors,
            )

    def test_weapon_perk_pool_hash_must_match_weapon_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._make_fixture_repo(root)
            weapon_path = root / "data" / "weapons" / "sample-weapon_s1.json"
            weapon = json.loads(weapon_path.read_text(encoding="utf-8"))
            weapon["bungie_hash"] = 111
            weapon_path.write_text(json.dumps(weapon), encoding="utf-8")

            pool_path = root / "data" / "weapon-perk-pools" / "sample-weapon_s1.json"
            pool = json.loads(pool_path.read_text(encoding="utf-8"))
            pool["weapon_hash"] = 222
            pool_path.write_text(json.dumps(pool), encoding="utf-8")

            errors = validate_repository(root)
            self.assertTrue(
                any("weapon_hash 222 does not match" in error for error in errors),
                errors,
            )

    def test_complete_pool_cannot_contain_an_empty_slot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._make_fixture_repo(root)
            path = root / "data" / "weapon-perk-pools" / "sample-weapon_s1.json"
            record = json.loads(path.read_text(encoding="utf-8"))
            record["pool_status"] = "complete"
            record["slots"][0]["perk_ids"] = []
            path.write_text(json.dumps(record), encoding="utf-8")

            errors = validate_repository(root)
            self.assertTrue(any("complete pool slot" in error for error in errors), errors)

    def test_orphan_recommendation_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._make_fixture_repo(root)
            path = root / "recommendations" / "curated" / "pve" / "sample-weapon_s1.json"
            record = json.loads(path.read_text(encoding="utf-8"))
            record["weapon_id"] = "missing-weapon"
            path.write_text(json.dumps(record), encoding="utf-8")

            errors = validate_repository(root)
            self.assertTrue(
                any("references missing weapon 'missing-weapon'" in error for error in errors),
                errors,
            )

    def test_verified_weapon_requires_verification_date(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._make_fixture_repo(root)
            path = root / "data" / "weapons" / "sample-weapon_s1.json"
            record = json.loads(path.read_text(encoding="utf-8"))
            record["verification_status"] = "verified"
            path.write_text(json.dumps(record), encoding="utf-8")

            errors = validate_repository(root)
            self.assertTrue(
                any("data/weapons/sample-weapon_s1.json" in error and "last_verified" in error for error in errors),
                errors,
            )

    def test_missing_adept_base_reference_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._make_fixture_repo(root)
            path = root / "data" / "weapons" / "sample-weapon_s1.json"
            record = json.loads(path.read_text(encoding="utf-8"))
            record["variant_of"] = "missing-base"
            path.write_text(json.dumps(record), encoding="utf-8")

            errors = validate_repository(root)
            self.assertTrue(
                any("references missing base weapon variant_of='missing-base'" in error for error in errors),
                errors,
            )

    def test_recommendation_requires_matching_complete_versioned_pool(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._make_fixture_repo(root)
            rec_path = root / "recommendations" / "curated" / "pve" / "sample-weapon_s1.json"
            rec = json.loads(rec_path.read_text(encoding="utf-8"))
            rec["weapon_perk_pool_id"] = "missing-pool"
            rec_path.write_text(json.dumps(rec), encoding="utf-8")
            errors = validate_repository(root)
            self.assertTrue(
                any("references missing weapon perk pool 'missing-pool'" in error for error in errors),
                errors,
            )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._make_fixture_repo(root)
            pool_path = root / "data" / "weapon-perk-pools" / "sample-weapon_s1.json"
            pool = json.loads(pool_path.read_text(encoding="utf-8"))
            pool["pool_status"] = "partial"
            pool_path.write_text(json.dumps(pool), encoding="utf-8")
            errors = validate_repository(root)
            self.assertTrue(any("recommendation requires a complete perk pool" in error for error in errors), errors)

    def test_recommendation_perk_must_exist_and_match_its_exact_slot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._make_fixture_repo(root)
            rec_path = root / "recommendations" / "curated" / "pve" / "sample-weapon_s1.json"
            rec = json.loads(rec_path.read_text(encoding="utf-8"))
            rec["recommendations"][0]["perk_3"] = "missing-perk"
            rec_path.write_text(json.dumps(rec), encoding="utf-8")
            errors = validate_repository(root)
            self.assertTrue(any("references missing canonical perk 'missing-perk'" in error for error in errors), errors)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._make_fixture_repo(root)
            rec_path = root / "recommendations" / "curated" / "pve" / "sample-weapon_s1.json"
            rec = json.loads(rec_path.read_text(encoding="utf-8"))
            rec["recommendations"][0]["barrel"] = "sample-trait-1"
            rec_path.write_text(json.dumps(rec), encoding="utf-8")
            errors = validate_repository(root)
            self.assertTrue(
                any("perk 'sample-trait-1' is not listed in matching pool slot(s)" in error for error in errors),
                errors,
            )

    def test_recommendation_sight_matches_scope_sight_pool_slot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._make_fixture_repo(root)

            source_perk = json.loads(
                (root / "data" / "perks" / "sample-barrel.json").read_text(encoding="utf-8")
            )
            source_perk.update({"id": "sample-sight", "name": "Sample Sight", "perk_type": "sight"})
            (root / "data" / "perks" / "sample-sight.json").write_text(
                json.dumps(source_perk, indent=2) + "\n", encoding="utf-8"
            )

            pool_path = root / "data" / "weapon-perk-pools" / "sample-weapon_s1.json"
            pool = json.loads(pool_path.read_text(encoding="utf-8"))
            pool["slots"][0] = {
                "id": "scope-sight",
                "label": "Scope / Sight",
                "selection_mode": "choose-one",
                "perk_ids": ["sample-sight"],
                "notes": [],
            }
            pool_path.write_text(json.dumps(pool, indent=2) + "\n", encoding="utf-8")

            recommendation_path = root / "recommendations" / "curated" / "pve" / "sample-weapon_s1.json"
            record = json.loads(recommendation_path.read_text(encoding="utf-8"))
            roll = record["recommendations"][0]
            roll["sight"] = roll.pop("barrel")
            roll["sight"] = "sample-sight"
            recommendation_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")

            self.assertEqual(validate_repository(root), [])

    def test_recommendation_object_requires_name_and_description_and_rejects_extra_fields(self) -> None:
        for required_field in ("name", "description"):
            with self.subTest(required_field=required_field), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                self._make_fixture_repo(root)
                rec_path = root / "recommendations" / "curated" / "pve" / "sample-weapon_s1.json"
                rec = json.loads(rec_path.read_text(encoding="utf-8"))
                rec["recommendations"][0].pop(required_field)
                rec_path.write_text(json.dumps(rec), encoding="utf-8")
                errors = validate_repository(root)
                self.assertTrue(
                    any(
                        "schema violation at $.recommendations[0]" in error
                        and required_field in error
                        for error in errors
                    ),
                    errors,
                )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._make_fixture_repo(root)
            rec_path = root / "recommendations" / "curated" / "pve" / "sample-weapon_s1.json"
            rec = json.loads(rec_path.read_text(encoding="utf-8"))
            rec["recommendations"][0]["masterwork"] = "Range"
            rec_path.write_text(json.dumps(rec), encoding="utf-8")
            errors = validate_repository(root)
            self.assertTrue(
                any(
                    "schema violation at $.recommendations[0]" in error and "masterwork" in error
                    for error in errors
                ),
                errors,
            )

    def test_verified_recommendation_requires_verified_underlying_records(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._make_fixture_repo(root)
            rec_path = root / "recommendations" / "curated" / "pve" / "sample-weapon_s1.json"
            rec = json.loads(rec_path.read_text(encoding="utf-8"))
            rec["verification_status"] = "verified"
            rec["verified_for_sandbox"] = "Test sandbox"
            rec["last_reviewed"] = "2026-10-27"
            rec_path.write_text(json.dumps(rec), encoding="utf-8")
            errors = validate_repository(root)
            self.assertTrue(
                any("cannot be marked verified until its weapon, complete perk pool, and selected perk definitions are verified" in error for error in errors),
                errors,
            )

    def test_recommendation_mode_must_match_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._make_fixture_repo(root)
            path = root / "recommendations" / "curated" / "pve" / "sample-weapon_s1.json"
            record = json.loads(path.read_text(encoding="utf-8"))
            record["mode"] = "pvp"
            path.write_text(json.dumps(record), encoding="utf-8")

            errors = validate_repository(root)
            self.assertTrue(any("mode must match its directory" in error for error in errors), errors)

    def test_community_recommendation_requires_author_and_one_weapon_filename(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._make_fixture_repo(root)
            curated_path = root / "recommendations" / "curated" / "pve" / "sample-weapon_s1.json"
            curated_record = json.loads(curated_path.read_text(encoding="utf-8"))
            bad_curated = dict(curated_record)
            bad_curated["github_username"] = "Contributor"
            curated_path.write_text(json.dumps(bad_curated, indent=2) + "\n", encoding="utf-8")
            errors = validate_repository(root)
            self.assertTrue(any("github_username is reserved for" in error for error in errors), errors)

            roll = dict(curated_record["recommendations"][0])
            roll.update({
                "mode": "pve",
                "issue_number": 84,
                "submitted_at": "2026-10-28T10:00:00Z",
                "verification_status": "verified",
                "verified_for_sandbox": None,
                "last_reviewed": "2026-10-28",
                "verified_by": "github-actions[bot]",
                "verification_sources": [],
            })
            aggregate = {
                "weapon_id": "sample-weapon_s1",
                "weapon_perk_pool_id": "sample-weapon_s1",
                "verification_status": "verified",
                "verified_for_sandbox": None,
                "last_reviewed": "2026-10-28",
                "rating": None,
                "verified_by": "github-actions[bot]",
                "verification_sources": [],
                "recommendations": [roll],
            }
            community_root = root / "recommendations" / "community" / "pve"
            community_root.mkdir(parents=True, exist_ok=True)
            target = community_root / "sample-weapon_s1.json"
            target.write_text(json.dumps(aggregate, indent=2) + "\n", encoding="utf-8")
            errors = validate_repository(root)
            self.assertTrue(
                any("github_username" in error and "required property" in error for error in errors),
                errors,
            )

            roll["github_username"] = "Contributor"
            wrong_path = community_root / "another-weapon_s1.json"
            wrong_path.write_text(json.dumps(aggregate, indent=2) + "\n", encoding="utf-8")
            errors = validate_repository(root)
            self.assertTrue(any("community filename must be" in error for error in errors), errors)


    def test_community_weapon_json_is_not_allowed_in_community_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._make_fixture_repo(root)
            community_path = root / "recommendations" / "community" / "sample-weapon_s1.json"
            community_path.parent.mkdir(parents=True, exist_ok=True)
            community_path.write_text("{}", encoding="utf-8")

            errors = validate_repository(root)
            self.assertTrue(
                any(
                    "recommendation files must be under" in error
                    and "recommendations/community/pve|pvp/<weapon-id>.json" in error
                    for error in errors
                ),
                errors,
            )

    def test_mechanically_validated_community_recommendation_needs_no_sandbox_or_external_sources(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._make_fixture_repo(root)

            weapon_path = root / "data" / "weapons" / "sample-weapon_s1.json"
            weapon = json.loads(weapon_path.read_text(encoding="utf-8"))
            weapon.update({"verification_status": "verified", "last_verified": "2026-10-28"})
            weapon_path.write_text(json.dumps(weapon), encoding="utf-8")

            pool_path = root / "data" / "weapon-perk-pools" / "sample-weapon_s1.json"
            pool = json.loads(pool_path.read_text(encoding="utf-8"))
            pool.update({"verification_status": "verified", "last_verified": "2026-10-28"})
            pool_path.write_text(json.dumps(pool), encoding="utf-8")

            for perk_path in (root / "data" / "perks").glob("*.json"):
                perk = json.loads(perk_path.read_text(encoding="utf-8"))
                perk.update({
                    "verification_status": "verified",
                    "bungie_hash": 2000 + len(perk["id"]),
                    "last_verified": "2026-10-28",
                })
                perk_path.write_text(json.dumps(perk), encoding="utf-8")

            curated_path = root / "recommendations" / "curated" / "pve" / "sample-weapon_s1.json"
            record = json.loads(curated_path.read_text(encoding="utf-8"))
            roll = record["recommendations"][0]
            roll.update({
                "mode": "pve",
                "github_username": "Contributor-1",
                "issue_number": 84,
                "submitted_at": "2026-10-28T10:00:00Z",
                "publication_status": "mechanically_validated",
                "validation_scope": "canonical-dependencies-and-perk-slots",
                "published_by": "github-actions[bot]",
            })
            for field in (
                "verification_status", "verified_for_sandbox", "last_reviewed",
                "verified_by", "verification_sources",
            ):
                roll.pop(field, None)
            record.pop("mode", None)
            for field in (
                "verification_status", "verified_for_sandbox", "last_reviewed",
                "rating", "verified_by", "verification_sources",
            ):
                record.pop(field, None)
            record.update({
                "publication_status": "mechanically_validated",
                "validation_scope": "canonical-dependencies-and-perk-slots",
                "published_by": "github-actions[bot]",
            })
            community_path = root / "recommendations" / "community" / "pve" / "sample-weapon_s1.json"
            community_path.parent.mkdir(parents=True, exist_ok=True)
            curated_path.unlink()
            community_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")

            self.assertEqual(validate_repository(root), [])

    def test_verified_curated_recommendation_still_requires_sandbox_and_sources(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._make_fixture_repo(root)
            path = root / "recommendations" / "curated" / "pve" / "sample-weapon_s1.json"
            record = json.loads(path.read_text(encoding="utf-8"))
            record.update({
                "verification_status": "verified",
                "verified_for_sandbox": None,
                "last_reviewed": "2026-10-28",
                "verified_by": "Maintainer-1",
                "verification_sources": [],
            })
            path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")

            errors = validate_repository(root)
            self.assertTrue(any("schema violation at $.verified_for_sandbox" in error for error in errors), errors)
            self.assertTrue(any("schema violation at $.verification_sources" in error for error in errors), errors)

    def test_community_recommendation_requires_mechanical_validation_status(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._make_fixture_repo(root)
            curated_path = root / "recommendations" / "curated" / "pve" / "sample-weapon_s1.json"
            record = json.loads(curated_path.read_text(encoding="utf-8"))
            roll = record["recommendations"][0]
            roll.update({
                "mode": "pve",
                "github_username": "Contributor-1",
                "issue_number": 84,
                "submitted_at": "2026-10-28T10:00:00Z",
                "publication_status": "pending",
                "validation_scope": "canonical-dependencies-and-perk-slots",
                "published_by": "github-actions[bot]",
            })
            for field in (
                "verification_status", "verified_for_sandbox", "last_reviewed",
                "verified_by", "verification_sources",
            ):
                roll.pop(field, None)
            record.pop("mode", None)
            for field in (
                "verification_status", "verified_for_sandbox", "last_reviewed",
                "rating", "verified_by", "verification_sources",
            ):
                record.pop(field, None)
            record.update({
                "publication_status": "pending",
                "validation_scope": "canonical-dependencies-and-perk-slots",
                "published_by": "github-actions[bot]",
            })
            community_path = root / "recommendations" / "community" / "pve" / "sample-weapon_s1.json"
            community_path.parent.mkdir(parents=True, exist_ok=True)
            curated_path.unlink()
            community_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
            errors = validate_repository(root)
            self.assertTrue(
                any("community publication_status must be mechanically_validated" in error or "publication_status" in error and "mechanically_validated" in error for error in errors),
                errors,
            )


if __name__ == "__main__":
    unittest.main()
