#!/usr/bin/env python3
"""Export the human-curated catalogue records from Supabase to catalogue/curated-overlay.json.

Run this once (and again whenever curated records change in Supabase) from a machine
with network access. The overlay is what lets the Bungie manifest catalogue replace
Supabase at read time without losing curated data:

* every weapon-version record (IDs, season, verification status, ...);
* every perk record;
* only the human-verified, non-unknown perk pools. All other pools are regenerated
  from the manifest snapshot, so they are deliberately not exported.

Usage:
    python scripts/export_catalogue_overlay.py [--output catalogue/curated-overlay.json]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

from catalogue_source import OVERLAY_RELATIVE, CatalogueError, SupabaseCatalogue

ROOT = Path(__file__).resolve().parents[1]
OVERLAY_FORMAT = 1


def _by_key(records: list[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    return {record[key]: record for record in sorted(records, key=lambda item: item[key])}


def build_overlay(catalogue: Any, exported_at: str) -> dict[str, Any]:
    """Return the overlay document for the records ``catalogue`` serves."""
    pools = catalogue.list_records("pools")
    verified = [
        pool for pool in pools
        if pool.get("verification_status") == "verified" and pool.get("pool_status") != "unknown"
    ]
    return {
        "format": OVERLAY_FORMAT,
        "exported_at": exported_at,
        "weapons": _by_key(catalogue.list_records("weapons"), "id"),
        "perks": _by_key(catalogue.list_records("perks"), "id"),
        "verified_pools": _by_key(verified, "weapon_id"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", type=Path, default=ROOT / OVERLAY_RELATIVE)
    args = parser.parse_args(argv)
    try:
        overlay = build_overlay(SupabaseCatalogue(), date.today().isoformat())
    except CatalogueError as exc:
        print("Could not export the curated catalogue: " + str(exc), file=sys.stderr)
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(overlay, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(
        f"Wrote {args.output}: {len(overlay['weapons'])} weapons, {len(overlay['perks'])} perks, "
        f"{len(overlay['verified_pools'])} verified pools."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
