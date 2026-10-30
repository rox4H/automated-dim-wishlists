import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from validate_recommendation_issue import validate_issue


BODY = """### Mode

PvE

### Name

Solar add clear

### Description

A simple recommendation for reliable damage and clearing groups of enemies.

### Weapon

sample-weapon_s2

### Perk 1

incandescente

### Perk 2

sample-trait-1

### Perk 3

Sample Magazine

### Perk 4

sample-barrel

"""


def base_repo(root):
    for directory in ("data/weapons", "data/perks", "data/weapon-perk-pools"):
        (root / directory).mkdir(parents=True, exist_ok=True)

    (root / "data/weapons/sample-weapon_s2.json").write_text(
        json.dumps({
            "id": "sample-weapon_s2",
            "name": "Sample Weapon",
            "bungie_hash": 123,
            "season_number": 2,
            "verification_status": "unverified",
        }),
        encoding="utf-8",
    )

    perks = [
        ("sample-barrel", "Sample Barrel"),
        ("sample-sight", "Sample Sight"),
        ("sample-magazine", "Sample Magazine"),
        ("sample-trait-1", "Sample Trait 1"),
        ("sample-trait-2", "Sample Trait 2"),
        ("incandescent", "Incandescent"),
        ("not-in-pool", "Not In Pool"),
    ]
    for perk_id, name in perks:
        (root / "data" / "perks" / (perk_id + ".json")).write_text(
            json.dumps({
                "id": perk_id,
                "name": name,
                "verification_status": "unverified",
            }),
            encoding="utf-8",
        )

    pool = {
        "weapon_id": "sample-weapon_s2",
        "weapon_hash": 123,
        "pool_status": "complete",
        "verification_status": "unverified",
        "slots": [
            {"id": "barrel", "label": "Barrel", "perk_ids": ["sample-barrel"]},
            {"id": "magazine", "label": "Magazine", "perk_ids": ["sample-magazine"]},
            {"id": "trait-1", "label": "Trait 1", "perk_ids": ["sample-trait-1"]},
            {"id": "trait-2", "label": "Trait 2", "perk_ids": ["sample-trait-2", "incandescent"]},
        ],
    }
    (root / "data" / "weapon-perk-pools" / "sample-weapon_s2.json").write_text(
        json.dumps(pool), encoding="utf-8"
    )


class IssueValidationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        base_repo(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_valid_issue_accepts_perks_in_any_order(self):
        report = validate_issue(BODY, self.root, "[Recommendation proposal] Solar add clear", 84)
        self.assertEqual(report["errors"], [], report)
        self.assertEqual(report["resolved_weapon_id"], "sample-weapon_s2")

    def test_localized_incandescente_alias_resolves(self):
        report = validate_issue(BODY, self.root)
        checks = [item for item in report["checks"] if item["name"] == "Perk 1"]
        self.assertEqual(len(checks), 1)
        self.assertIn("incandescent", checks[0]["detail"])

    def test_exact_weapon_id_is_required(self):
        body = BODY.replace("sample-weapon_s2", "Sample Weapon")
        report = validate_issue(body, self.root)
        self.assertTrue(any("filename" in error or "exact data/weapons/" in error for error in report["errors"]), report)

    def test_weapon_id_must_not_include_json_extension(self):
        report = validate_issue(BODY.replace("sample-weapon_s2", "sample-weapon_s2.json"), self.root)
        self.assertTrue(any("without the .json extension" in error for error in report["errors"]), report)

    def test_unknown_weapon_is_rejected(self):
        report = validate_issue(BODY.replace("sample-weapon_s2", "does-not-exist"), self.root)
        self.assertTrue(any("No exact data/weapons/" in error for error in report["errors"]), report)

    def test_unknown_perk_is_rejected(self):
        report = validate_issue(BODY.replace("Sample Magazine", "Unknown Perk"), self.root)
        self.assertTrue(any("not a canonical perk ID" in error for error in report["errors"]), report)

    def test_perk_must_belong_to_selected_weapon_pool(self):
        report = validate_issue(BODY.replace("sample-barrel", "not-in-pool"), self.root)
        self.assertTrue(any("not listed in any slot" in error for error in report["errors"]), report)

    def test_partial_pool_allows_known_membership_with_warning(self):
        path = self.root / "data/weapon-perk-pools/sample-weapon_s2.json"
        pool = json.loads(path.read_text(encoding="utf-8"))
        pool["pool_status"] = "partial"
        path.write_text(json.dumps(pool), encoding="utf-8")
        report = validate_issue(BODY, self.root)
        self.assertEqual(report["errors"], [], report)
        self.assertTrue(any("not complete" in warning for warning in report["warnings"]), report)

    def test_at_least_one_perk_is_required(self):
        body = BODY
        for field in ("Perk 1", "Perk 2", "Perk 3", "Perk 4"):
            body = body.replace("### " + field + "\n\n" + {
                "Perk 1": "incandescente",
                "Perk 2": "sample-trait-1",
                "Perk 3": "Sample Magazine",
                "Perk 4": "sample-barrel",
            }[field], "### " + field + "\n\n_No response_")
        report = validate_issue(body, self.root)
        self.assertTrue(any("at least one perk" in error for error in report["errors"]), report)

    def test_blank_description_is_rejected(self):
        report = validate_issue(BODY.replace("A simple recommendation for reliable damage and clearing groups of enemies.", "_No response_"), self.root)
        self.assertTrue(any("Description" in error for error in report["errors"]), report)

    def test_wrong_mode_is_rejected(self):
        report = validate_issue(BODY.replace("PvE", "Gambit"), self.root)
        self.assertTrue(any("Mode must be PvE or PvP" in error for error in report["errors"]), report)

    def test_pool_hash_mismatch_is_rejected(self):
        path = self.root / "data/weapon-perk-pools/sample-weapon_s2.json"
        pool = json.loads(path.read_text(encoding="utf-8"))
        pool["weapon_hash"] = 456
        path.write_text(json.dumps(pool), encoding="utf-8")
        report = validate_issue(BODY, self.root)
        self.assertTrue(any("hash mismatch" in error for error in report["errors"]), report)

    def test_ambiguous_display_name_requires_exact_id(self):
        for perk_id in ("shared-name-one", "shared-name-two"):
            (self.root / "data" / "perks" / (perk_id + ".json")).write_text(
                json.dumps({"id": perk_id, "name": "Shared Name"}),
                encoding="utf-8",
            )
        report = validate_issue(BODY.replace("incandescente", "Shared Name"), self.root)
        self.assertTrue(any("multiple catalogue records" in error for error in report["errors"]), report)

    def test_battery_slot_maps_to_canonical_magazine_field(self):
        (self.root / "data" / "perks" / "enhanced-battery.json").write_text(
            json.dumps({
                "id": "enhanced-battery",
                "name": "Enhanced Battery",
                "verification_status": "unverified",
            }),
            encoding="utf-8",
        )
        pool_path = self.root / "data" / "weapon-perk-pools" / "sample-weapon_s2.json"
        pool = json.loads(pool_path.read_text(encoding="utf-8"))
        pool["slots"][1] = {
            "id": "battery-enhanced-battery",
            "label": "Battery / Enhanced Battery",
            "selection_mode": "choose-one",
            "perk_ids": ["enhanced-battery"],
            "notes": [],
        }
        pool_path.write_text(json.dumps(pool), encoding="utf-8")

        report = validate_issue(
            BODY.replace("Sample Magazine", "Enhanced Battery"),
            self.root,
            "[Recommendation proposal] Solar add clear",
            84,
        )
        self.assertEqual(report["errors"], [], report)
        self.assertEqual(report["canonical_recommendation"]["magazine"], "enhanced-battery")

    def test_valid_issue_maps_generic_perks_to_exact_json_slots(self):
        report = validate_issue(BODY, self.root, "[Recommendation proposal] Solar add clear", 84)
        self.assertEqual(report["errors"], [], report)
        self.assertEqual(report["canonical_recommendation"], {
            "name": "Solar add clear",
            "barrel": "sample-barrel",
            "magazine": "sample-magazine",
            "perk_3": "sample-trait-1",
            "perk_4": "incandescent",
            "description": "A simple recommendation for reliable damage and clearing groups of enemies.",
        })

    def test_scope_sight_component_is_valid_for_legacy_weapon_slot(self):
        pool_path = self.root / "data" / "weapon-perk-pools" / "sample-weapon_s2.json"
        pool = json.loads(pool_path.read_text(encoding="utf-8"))
        pool["slots"][0] = {
            "id": "scope-sight",
            "label": "Scope / Sight",
            "selection_mode": "choose-one",
            "perk_ids": ["sample-sight"],
            "notes": [],
        }
        pool_path.write_text(json.dumps(pool), encoding="utf-8")

        report = validate_issue(
            BODY.replace("sample-barrel", "sample-sight"),
            self.root,
            "[Recommendation proposal] Solar add clear",
            84,
        )
        self.assertEqual(report["errors"], [], report)
        self.assertEqual(report["canonical_recommendation"], {
            "name": "Solar add clear",
            "sight": "sample-sight",
            "magazine": "sample-magazine",
            "perk_3": "sample-trait-1",
            "perk_4": "incandescent",
            "description": "A simple recommendation for reliable damage and clearing groups of enemies.",
        })

    def test_duplicate_generic_perks_for_one_slot_are_rejected(self):
        body = BODY.replace("sample-trait-1", "incandescente")
        report = validate_issue(body, self.root)
        self.assertTrue(
            any("More than one submitted perk maps to the perk_4 slot" in error for error in report["errors"]),
            report,
        )

    def test_duplicate_roll_in_curated_or_community_blocks_issue(self):
        existing = {
            "weapon_id": "sample-weapon_s2",
            "weapon_perk_pool_id": "sample-weapon_s2",
            "mode": "pve",
            "verification_status": "unverified",
            "recommendations": [{
                "name": "A different label",
                "barrel": "sample-barrel",
                "magazine": "sample-magazine",
                "perk_3": "sample-trait-1",
                "perk_4": "incandescent",
                "description": "Different wording does not change roll identity.",
            }],
        }
        source_paths = (
            self.root / "recommendations" / "curated" / "pve" / "sample-weapon_s2.json",
            self.root / "recommendations" / "community" / "pve" / "sample-weapon_s2--issue-77.json",
        )
        for source_path in source_paths:
            with self.subTest(source_path=source_path):
                source_path.parent.mkdir(parents=True, exist_ok=True)
                record = dict(existing)
                if "community" in source_path.parts:
                    record["github_username"] = "another-user"
                source_path.write_text(json.dumps(record), encoding="utf-8")
                report = validate_issue(BODY, self.root, "[Recommendation proposal] Solar add clear", 84)
                self.assertTrue(any("Duplicate recommendation:" in error for error in report["errors"]), report)
                duplicate_checks = [check for check in report["checks"] if check["name"] == "Duplicate recommendation guard"]
                self.assertEqual(len(duplicate_checks), 1)
                self.assertEqual(duplicate_checks[0]["status"], "fail")
                source_path.unlink()
                try:
                    source_path.parent.rmdir()
                except OSError:
                    pass

    def test_edit_ignores_only_its_own_issue_record(self):
        record = {
            "weapon_id": "sample-weapon_s2",
            "weapon_perk_pool_id": "sample-weapon_s2",
            "mode": "pve",
            "verification_status": "unverified",
            "github_username": "original-author",
            "recommendations": [{
                "name": "Same issue roll",
                "barrel": "sample-barrel",
                "magazine": "sample-magazine",
                "perk_3": "sample-trait-1",
                "perk_4": "incandescent",
                "description": "Existing generated record for this issue.",
            }],
        }
        path = self.root / "recommendations" / "community" / "pve" / "sample-weapon_s2--issue-84.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(record), encoding="utf-8")
        report = validate_issue(BODY, self.root, "[Recommendation proposal] Solar add clear", 84)
        self.assertEqual(report["errors"], [], report)
        self.assertEqual(report["duplicate_matches"], [])


    def _mark_all_dependencies_verified(self):
        weapon_path = self.root / "data" / "weapons" / "sample-weapon_s2.json"
        weapon = json.loads(weapon_path.read_text(encoding="utf-8"))
        weapon.update({
            "verification_status": "verified",
            "last_verified": "2026-10-28",
        })
        weapon_path.write_text(json.dumps(weapon), encoding="utf-8")
        pool_path = self.root / "data" / "weapon-perk-pools" / "sample-weapon_s2.json"
        pool = json.loads(pool_path.read_text(encoding="utf-8"))
        pool.update({
            "verification_status": "verified",
            "pool_status": "complete",
            "last_verified": "2026-10-28",
            "weapon_hash": 123,
        })
        pool_path.write_text(json.dumps(pool), encoding="utf-8")
        for path in (self.root / "data" / "perks").glob("*.json"):
            perk = json.loads(path.read_text(encoding="utf-8"))
            perk.update({
                "verification_status": "verified",
                "bungie_hash": 2000 + len(perk["id"]),
                "last_verified": "2026-10-28",
            })
            path.write_text(json.dumps(perk), encoding="utf-8")

    def test_verified_dependencies_are_ready_without_sandbox_or_sources(self):
        self._mark_all_dependencies_verified()
        report = validate_issue(BODY, self.root, "[Recommendation proposal] Solar add clear", 84)
        self.assertEqual(report["errors"], [], report)
        self.assertTrue(report["publication_ready"], report)
        self.assertEqual(report["publication_blockers"], [], report)
        self.assertEqual(report["status"], "canonical-dependencies-ready-to-publish")

    def test_legacy_sandbox_and_source_sections_are_ignored(self):
        self._mark_all_dependencies_verified()
        body = BODY + """
### Verified for sandbox

latest

### Verification sources

not a URL
"""
        report = validate_issue(body, self.root, "[Recommendation proposal] Solar add clear", 84)
        self.assertEqual(report["errors"], [], report)
        self.assertTrue(report["publication_ready"], report)
        self.assertEqual(report["status"], "canonical-dependencies-ready-to-publish")
        self.assertIsNone(report["verified_for_sandbox"])
        self.assertEqual(report["verification_sources"], [])
        self.assertEqual(report["publication_blockers"], [])
        self.assertFalse(any(check["name"] in {"Verified for sandbox", "Verification sources"} for check in report["checks"]))

    def test_unverified_unselected_pool_perk_does_not_block_publication(self):
        self._mark_all_dependencies_verified()
        extra = {
            "id": "unverified-extra",
            "name": "Unverified Extra",
            "bungie_hash": None,
            "perk_type": "trait",
            "description": None,
            "verification_status": "unverified",
            "last_verified": None,
            "notes": [],
        }
        (self.root / "data" / "perks" / "unverified-extra.json").write_text(
            json.dumps(extra), encoding="utf-8"
        )
        pool_path = self.root / "data" / "weapon-perk-pools" / "sample-weapon_s2.json"
        pool = json.loads(pool_path.read_text(encoding="utf-8"))
        pool["slots"][3]["perk_ids"].append("unverified-extra")
        pool_path.write_text(json.dumps(pool), encoding="utf-8")

        report = validate_issue(BODY, self.root, "[Recommendation proposal] Solar add clear", 84)
        self.assertEqual(report["errors"], [], report)
        self.assertTrue(report["publication_ready"], report)
        self.assertEqual(report["publication_blockers"], [], report)

    def test_numbered_test_name_is_rejected(self):
        self._mark_all_dependencies_verified()
        body = re.sub(r"(?s)(### Name\s*\n).*?(?=\n### Description)", r"\1Test2", BODY, count=1)
        report = validate_issue(body, self.root, "[Recommendation proposal] Test2", 84)
        self.assertTrue(any("Name looks like a placeholder" in item for item in report["errors"]), report)
        self.assertFalse(report["publication_ready"], report)

    def test_abbreviated_numbered_test_description_is_rejected(self):
        self._mark_all_dependencies_verified()
        body = re.sub(r"(?s)(### Description\s*\n).*?(?=\n### Weapon)", r"\1TT2", BODY, count=1)
        report = validate_issue(body, self.root, "[Recommendation proposal] Solar add clear", 84)
        self.assertTrue(any("Description looks like a placeholder" in item for item in report["errors"]), report)
        self.assertFalse(report["publication_ready"], report)

    def test_unverified_weapon_keeps_issue_in_maintainer_review(self):
        self._mark_all_dependencies_verified()
        path = self.root / "data" / "weapons" / "sample-weapon_s2.json"
        weapon = json.loads(path.read_text(encoding="utf-8"))
        weapon.update({"verification_status": "unverified", "last_verified": None})
        path.write_text(json.dumps(weapon), encoding="utf-8")

        report = validate_issue(BODY, self.root, "[Recommendation proposal] Solar add clear", 84)
        self.assertEqual(report["errors"], [], report)
        self.assertFalse(report["publication_ready"], report)
        self.assertTrue(any("Weapon sample-weapon_s2 is not marked verified" in item for item in report["publication_blockers"]), report)

    def test_unverified_pool_keeps_issue_in_maintainer_review(self):
        self._mark_all_dependencies_verified()
        path = self.root / "data" / "weapon-perk-pools" / "sample-weapon_s2.json"
        pool = json.loads(path.read_text(encoding="utf-8"))
        pool.update({"verification_status": "unverified", "last_verified": None})
        path.write_text(json.dumps(pool), encoding="utf-8")

        report = validate_issue(BODY, self.root, "[Recommendation proposal] Solar add clear", 84)
        self.assertEqual(report["errors"], [], report)
        self.assertFalse(report["publication_ready"], report)
        self.assertTrue(any("Perk pool sample-weapon_s2 is not marked verified" in item for item in report["publication_blockers"]), report)

    def test_unverified_selected_perk_keeps_issue_in_maintainer_review(self):
        self._mark_all_dependencies_verified()
        perk_path = self.root / "data" / "perks" / "incandescent.json"
        perk = json.loads(perk_path.read_text(encoding="utf-8"))
        perk.update({"verification_status": "unverified", "last_verified": None})
        perk_path.write_text(json.dumps(perk), encoding="utf-8")

        report = validate_issue(BODY, self.root, "[Recommendation proposal] Solar add clear", 84)
        self.assertEqual(report["errors"], [], report)
        self.assertFalse(report["publication_ready"], report)
        self.assertTrue(any("Selected perk incandescent is not marked verified" in item for item in report["publication_blockers"]), report)
        self.assertEqual(report["status"], "mechanical-checks-passed-needs-canonical-data-review")

if __name__ == "__main__":
    unittest.main()
