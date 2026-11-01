# Bungie Manifest → Supabase sync

This workflow refreshes **generated weapon perk pools** from Bungie's live Destiny 2 Manifest. It is intentionally separate from the DIM export workflow.

## What it updates

- Rebuilds generated weapon-version perk pools when the Manifest component fingerprint, Manifest version, or pool extractor version changes.
- Inserts a new perk record only when an exact Bungie plug item hash is not already represented.
- Records sync runs and field-level provenance in the private `d2wd_internal` schema.

It does **not** update existing weapon metadata, overwrite existing perk names/descriptions, delete records, or promote any record to `verified`.

A pool marked `complete` means the extractor resolved the relevant sockets and plug references. It does not establish historical availability, roll legality, or human verification.

## Safety model

1. The script defaults to read-only dry-run mode.
2. Generated perk identity is resolved by exact Bungie plug hash. Display-name similarity is not used to attach a plug to an existing perk.
3. Existing `verified` pools are immutable. Existing non-generated/manual pools are also immutable, including when the optional `--force-generated` planner flag is used.
4. Generated unverified pools are rebuilt when their source content or generator changes. Only pools whose socket/perk payload changes are uploaded.
5. Unknown extraction results stay `unknown`; unresolved or ambiguous perks remain unresolved.
6. All generated records remain `unverified`. No automated path sets `last_verified` or `verification_status=verified`.
7. No catalogue records are deleted.

The Manifest fingerprint is SHA-256 over the sorted content of these four Bungie Manifest components:

- `DestinyInventoryItemDefinition`
- `DestinyPlugSetDefinition`
- `DestinySocketCategoryDefinition`
- `DestinySocketTypeDefinition`

The sync run stores the fingerprint globally. This detects content changes even when Bungie reuses the same Manifest version label. When the fingerprint changes, the planner re-extracts generated pools, compares their actual payloads, and writes only changed pools.

## Authentication and permissions

GitHub Actions does not receive a Supabase service-role or secret key.

The workflow obtains a short-lived GitHub OIDC token with audience `urn:rox4h:d2wd-supabase-sync`. The `d2wd-sync-import` Supabase Edge Function verifies the token signature against GitHub's published JWKS and checks all of the following:

- issuer `https://token.actions.githubusercontent.com`
- the exact repository and repository ID
- `refs/heads/main`
- the exact workflow path on `main`
- the `d2wd-catalogue-sync` GitHub Environment and subject
- the `schedule` or `workflow_dispatch` event
- the immutable subject for this repository:
  `repo:rox4H@93459526/automated-dim-wishlists@1438586516:environment:d2wd-catalogue-sync`
- token time claims

GitHub repositories created after 15 July 2026 use immutable owner/repository IDs in the OIDC `sub` claim. This repository was created on 29 October 2026, so the gateway validates the ID-based subject rather than the older name-only format. and the expected audience

The Edge Function uses the server-side Supabase secret key from its own environment. That key never enters GitHub Actions or the repository. The function is deployed with platform JWT verification disabled because it performs this stricter GitHub OIDC verification itself; do not remove or bypass the verification code in `supabase/functions/d2wd-sync-import/index.ts`.

The Edge Function only accepts bounded batches of generated, unverified pool/perk data. It checks each pool's weapon hash against the current weapon row before writing. Verified/manual pools cannot be overwritten.

The audit tables are in the non-public `d2wd_internal` schema. RLS is enabled and access is restricted to `service_role`.

## Enable the workflow

The workflow is deliberately **dry-run by default**.

1. Merge the reviewed sync PR into `main`.
2. In GitHub, open **Settings → Environments** and create or inspect `d2wd-catalogue-sync`. Restrict deployments to the `main` branch. Add required reviewers if you want a manual approval before every production write.
3. Open **Settings → Secrets and variables → Actions → Variables** and create the repository variable:
   - Name: `D2WD_SUPABASE_SYNC_ENABLED`
   - Value: `true`
4. Do not add a Supabase service-role key, secret key, database password, or access token to GitHub Secrets.

With the variable absent or anything other than `true`, the daily job runs a read-only dry run. With the variable set to `true`, the job writes through the OIDC-protected Edge Function.

The workflow runs daily at **05:13 UTC** and can also be started manually from **Actions → Sync Bungie Manifest to Supabase → Run workflow**. It only operates on `main`.

## Run locally

A local run is read-only by default:

```bash
node --max-old-space-size=8192 scripts/sync_manifest_to_supabase.mjs
```

The script needs network access to Bungie and read access to the public Supabase catalogue. It does not need a Supabase write key.

The production `--write` mode is intentionally restricted to the GitHub Actions OIDC environment on the main branch. It refuses to run from a local shell.

## Current known coverage gaps

The database audit on 1 November 2026 recorded these unresolved areas:

- 318 weapon records still lack `ammo_type` and `weapon_slot`; these are concentrated in Seasons 2–6 and need historical definitions rather than modern defaults.
- 367 weapon records still have no uniquely resolved frame.
- 464 perk records still lack an exact Bungie hash.
- Some perk descriptions were originally matched by display name; exact-hash identity must be checked before relying on those descriptions.

The sync workflow does not silently fill these fields. They remain in the private review queue until evidence is sufficient.
