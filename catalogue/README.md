# Bungie manifest catalogue

Replaces the Supabase read path with data derived from Bungie's manifest. Validators, the DIM
export, recommendation JSON and the GitHub Actions workflows are unchanged; only
`scripts/catalogue_source.py` chooses a different source.

| File | What it is |
| --- | --- |
| `curated-overlay.json` | Human-curated records exported once from Supabase: every weapon version, every perk, and the human-verified pools. |
| `manifest-snapshot.json` | A slim, verbatim subset of Bungie's manifest: only definitions reachable from the weapons in the overlay, plus the manifest version. |

Neither file exists until you create them, and **until both are committed nothing changes**:
`CATALOGUE_SOURCE` defaults to `auto`, which falls back to Supabase.

## How records are served

- Weapon and perk records come from the overlay and are never rewritten.
- A human-verified pool is served exactly as exported. The pool the manifest would produce is
  compared with it; differences are reported (`verified_pool_drift`), never applied.
- Every other pool is generated from the snapshot with the same extraction code as
  `scripts/generate_weapon_perk_pools.mjs`, and stays `unverified`.
- Perks that generation has to seed are served only when a served pool references them.

## Set up

```bash
# 1. Export curated records from Supabase (needs network).
python scripts/export_catalogue_overlay.py

# 2. Download the manifest and write the slim snapshot (several minutes, large heap).
node --max-old-space-size=8192 scripts/build_manifest_catalogue.mjs fetch \
  --overlay catalogue/curated-overlay.json --snapshot catalogue/manifest-snapshot.json

# 3. Check the result offline. The JSON report lists drift, missing weapons and seeded perks.
node scripts/build_manifest_catalogue.mjs build \
  --overlay catalogue/curated-overlay.json --snapshot catalogue/manifest-snapshot.json --out /tmp/catalogue
CATALOGUE_SOURCE=bungie-manifest python scripts/validate_json.py
CATALOGUE_SOURCE=bungie-manifest python scripts/export_dim_wishlist.py --source curated --check
CATALOGUE_SOURCE=bungie-manifest python scripts/export_dim_wishlist.py --source community --check

# 4. Commit both files. CI then uses the manifest catalogue automatically.
```

Refresh the snapshot by repeating step 2; the pull request diff shows what Bungie changed.
Roll back by deleting the two files or setting `CATALOGUE_SOURCE=supabase`.

## Selecting a source

`CATALOGUE_SOURCE` is `auto` (default), `bungie-manifest` or `supabase`. Local `data/` JSON
fixtures still win when all three `data/` directories exist.

## Known limits

- Building needs Node.js (preinstalled on GitHub-hosted runners); it makes no network calls.
- Some validator and intake messages still say "Supabase"; only their wording is affected.
- `tests/test_build_manifest_catalogue.mjs` runs from `tests/test_bungie_manifest_catalogue.py`, so
  the existing workflow already covers it.
