"""Shared mapping between Bungie weapon perk slots and recommendation fields."""

from __future__ import annotations

from typing import Any


_COLUMN_TOKENS = {
    "barrel": ("barrel",),
    "sight": ("scope", "sight", "optic"),
    # Bungie calls this slot "Battery" on some weapons; recommendations
    # and DIM exports use the canonical "magazine" field.
    "magazine": ("magazine", "battery"),
}


def matching_pool_slots(pool: dict[str, Any], column: str) -> list[dict[str, Any]]:
    """Return pool slots matching a canonical recommendation column."""
    slots = pool.get("slots")
    if not isinstance(slots, list):
        return []

    if column in {"perk_3", "perk_4"}:
        suffix = "trait-1" if column == "perk_3" else "trait-2"
        label_suffix = " 1" if column == "perk_3" else " 2"
        matches = []
        for slot in slots:
            if not isinstance(slot, dict):
                continue
            slot_id = slot.get("id")
            label = slot.get("label")
            slot_id = slot_id.lower() if isinstance(slot_id, str) else ""
            label = label.lower().strip() if isinstance(label, str) else ""
            if slot_id.endswith(suffix) or ("trait" in label and label.endswith(label_suffix)):
                matches.append(slot)
        return matches

    tokens = _COLUMN_TOKENS.get(column)
    if tokens is None:
        return []

    matches = []
    for slot in slots:
        if not isinstance(slot, dict):
            continue
        slot_id = slot.get("id")
        label = slot.get("label")
        slot_id = slot_id.lower() if isinstance(slot_id, str) else ""
        label = label.lower().strip() if isinstance(label, str) else ""
        if any(token in value for value in (slot_id, label) for token in tokens):
            matches.append(slot)
    return matches
