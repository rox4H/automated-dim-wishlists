"""Regression tests for Bungie-to-recommendation perk slot mapping."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from pool_slot_mapping import matching_pool_slots


class PoolSlotMappingTests(unittest.TestCase):
    def test_battery_slot_matches_magazine_column(self):
        pool = {"slots": [{"id": "battery-enhanced-battery", "label": "Battery / Enhanced Battery", "perk_ids": ["enhanced-battery"]}]}
        self.assertEqual(matching_pool_slots(pool, "magazine"), pool["slots"])

    def test_conventional_magazine_slot_still_matches(self):
        slot = {"id": "magazine", "label": "Magazine", "perk_ids": ["tactical-mag"]}
        self.assertEqual(matching_pool_slots({"slots": [slot]}, "magazine"), [slot])

    def test_unrelated_slot_does_not_match_magazine(self):
        slot = {"id": "origin-trait", "label": "Origin Trait", "perk_ids": ["soul-drinker"]}
        self.assertEqual(matching_pool_slots({"slots": [slot]}, "magazine"), [])

    def test_unknown_column_returns_no_slots(self):
        self.assertEqual(matching_pool_slots({"slots": [{"id": "barrel"}]}, "unknown"), [])

    def test_invalid_slots_are_ignored(self):
        self.assertEqual(matching_pool_slots({"slots": [None, "battery"]}, "magazine"), [])


if __name__ == "__main__":
    unittest.main()
