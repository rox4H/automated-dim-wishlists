#!/usr/bin/env node
/**
 * Synchronise unverified weapon perk pools from Bungie's current Manifest.
 *
 * This script is deliberately non-destructive:
 * - dry-run is the default;
 * - exact Bungie plug hashes are required to resolve perk records;
 * - verified and manually curated pools are immutable;
 * - existing perk records are never edited;
 * - obsolete perk records are never deleted;
 * - generated pools stay unverified, even when extraction is complete.
 *
 * Writes are sent through the d2wd-sync-import Edge Function. GitHub Actions
 * authenticates with a short-lived GitHub OIDC token; no Supabase write key is
 * stored in GitHub or exposed to this process.
 */
import {
  buildPoolForWeapon,
  createPerkResolver,
  fetchManifestTables,
  fingerprintManifestTables,
  GENERATOR_VERSION,
} from "./generate_weapon_perk_pools.mjs";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { fetchCatalogueRows, getSupabaseReadConfiguration } from "./supabase_catalogue.mjs";

export const OIDC_AUDIENCE = "urn:rox4h:d2wd-supabase-sync";
export const EDGE_FUNCTION_NAME = "d2wd-sync-import";
export const BATCH_SIZE = 100;

function notesOf(record) {
  return Array.isArray(record?.notes) ? record.notes.map(String) : [];
}

export function isGeneratedPool(record) {
  return notesOf(record).some((note) =>
    note.startsWith("Generated from Bungie's live manifest snapshot "),
  );
}

function poolNeedsRegeneration(existing, {
  manifestVersion,
  manifestFingerprintChanged,
  forceGenerated = false,
} = {}) {
  if (!existing) return true;
  if (existing.verification_status === "verified") return false;
  if (existing.pool_status === "unknown") {
    return isGeneratedPool(existing) || notesOf(existing).some((note) =>
      note.startsWith("Unknown means the perk pool could not be resolved"),
    );
  }
  if (!isGeneratedPool(existing)) return false;

  const notes = notesOf(existing);
  const generatorVersion = notes.includes("Pool extraction generator version: " + GENERATOR_VERSION + ".");
  const manifestVersionMatches = notes.some((note) =>
    note.startsWith("Generated from Bungie's live manifest snapshot " + manifestVersion + " for this exact weapon item hash."),
  );

  return forceGenerated || manifestFingerprintChanged || !generatorVersion || !manifestVersionMatches;
}

function generatedNote(note) {
  return note.startsWith("Generated from Bungie's live manifest snapshot ") ||
    note.startsWith("Pool extraction generator version: ") ||
    note.startsWith("Pool completeness describes ") ||
    note.startsWith("Intrinsic, weapon-perk, origin-trait ") ||
    note.startsWith("Candidate entries from default, inline reusable, ") ||
    note.startsWith("Unknown means the perk pool could not be resolved") ||
    note.startsWith("Manifest component fingerprint (SHA-256): ");
}

function preserveAdditionalNotes(existing, fresh) {
  const retained = notesOf(existing).filter((note) => !generatedNote(note));
  const freshNotes = notesOf(fresh);
  fresh.notes = [...freshNotes, ...retained.filter((note) => !freshNotes.includes(note))];
  return fresh;
}

function poolSignature(pool) {
  return JSON.stringify({
    weapon_id: pool.weapon_id,
    weapon_hash: pool.weapon_hash,
    version_label: pool.version_label ?? null,
    pool_status: pool.pool_status,
    slots: pool.slots ?? [],
  });
}

export function buildSyncPlan({
  weapons,
  perks,
  pools,
  manifest,
  previousManifestFingerprint = null,
  accessed = new Date().toISOString().slice(0, 10),
  forceGenerated = false,
}) {
  const manifestFingerprint = fingerprintManifestTables(manifest.tables);
  const manifestFingerprintChanged = previousManifestFingerprint !== manifestFingerprint;
  const items = manifest.tables?.DestinyInventoryItemDefinition ?? {};
  const existingPoolsById = new Map(pools.map((row) => [row.record.weapon_id, row.record]));
  const generatedPerks = new Map();
  const resolver = createPerkResolver(
    perks,
    items,
    manifest.version,
    generatedPerks,
    { strictHashOnly: true },
  );

  const finalPools = [];
  const changedPools = [];
  const stats = {
    weapons: weapons.length,
    exactWeaponDefinitions: 0,
    weaponsMissingFromManifest: [],
    poolsPreservedVerified: 0,
    poolsPreservedManual: 0,
    poolsPreservedCurrent: 0,
    poolsRebuilt: 0,
    poolsChanged: 0,
    complete: 0,
    partial: 0,
    unknown: 0,
    generatedPerks: 0,
    weaponsMissingAmmoType: 0,
    weaponsMissingWeaponSlot: 0,
    weaponsMissingFrame: 0,
    perksMissingHash: 0,
  };

  for (const { record: weapon } of weapons) {
    const existing = existingPoolsById.get(weapon.id) ?? null;
    const definition = Number.isSafeInteger(weapon.bungie_hash) ? items[String(weapon.bungie_hash)] : null;
    if (definition) stats.exactWeaponDefinitions++;
    else stats.weaponsMissingFromManifest.push(weapon.id);
    if (weapon.ammo_type === null || weapon.ammo_type === undefined) stats.weaponsMissingAmmoType++;
    if (weapon.weapon_slot === null || weapon.weapon_slot === undefined) stats.weaponsMissingWeaponSlot++;
    if (weapon.frame === null || weapon.frame === undefined) stats.weaponsMissingFrame++;

    const rebuild = poolNeedsRegeneration(existing, {
      manifestVersion: manifest.version,
      manifestFingerprintChanged,
      forceGenerated,
    });

    if (!rebuild) {
      finalPools.push(existing);
      if (existing.verification_status === "verified") stats.poolsPreservedVerified++;
      else if (!isGeneratedPool(existing)) stats.poolsPreservedManual++;
      else stats.poolsPreservedCurrent++;
      continue;
    }

    const fresh = buildPoolForWeapon(weapon, manifest, resolver, { accessed });
    const pool = existing ? preserveAdditionalNotes(existing, fresh) : fresh;
    finalPools.push(pool);
    stats.poolsRebuilt++;

    if (!existing || poolSignature(existing) !== poolSignature(pool)) {
      changedPools.push(pool);
      stats.poolsChanged++;
    }
  }

  const referencedPerkIds = new Set();
  for (const pool of finalPools) {
    for (const slot of pool.slots ?? []) {
      for (const id of slot.perk_ids ?? []) referencedPerkIds.add(id);
    }
  }
  const newPerks = [...generatedPerks.values()]
    .filter((record) => referencedPerkIds.has(record.id))
    .sort((a, b) => a.id.localeCompare(b.id));
  stats.generatedPerks = newPerks.length;
  stats.complete = finalPools.filter((pool) => pool.pool_status === "complete").length;
  stats.partial = finalPools.filter((pool) => pool.pool_status === "partial").length;
  stats.unknown = finalPools.filter((pool) => pool.pool_status === "unknown").length;
  stats.perksMissingHash = perks.filter(({ record }) =>
    !Number.isSafeInteger(record.bungie_hash) || record.bungie_hash <= 0,
  ).length;

  return {
    manifestVersion: manifest.version,
    manifestFingerprint,
    finalPools,
    changedPools,
    newPerks,
    stats,
    warnings: [
      "Generated pools remain unverified; pool_status describes extraction coverage only.",
      "Existing weapon and perk records are read-only in this sync. No weapon metadata, descriptions, verification statuses, or last_verified values are updated.",
      "Verified pools and manually curated non-generated pools are never overwritten, including when --force-generated is used.",
      "No catalogue records are deleted.",
      "Missing historical metadata and ambiguous perk identities remain unresolved for human review.",
    ],
  };
}

function parseJson(value, label) {
  try {
    return JSON.parse(value);
  } catch {
    throw new Error("The " + label + " response was not valid JSON.");
  }
}

async function requestGitHubOidcToken() {
  const requestUrl = process.env.ACTIONS_ID_TOKEN_REQUEST_URL;
  const requestToken = process.env.ACTIONS_ID_TOKEN_REQUEST_TOKEN;
  if (!requestUrl || !requestToken) {
    throw new Error("GitHub OIDC is unavailable. Run --write only from the protected GitHub Actions workflow.");
  }
  const url = new URL(requestUrl);
  url.searchParams.set("audience", OIDC_AUDIENCE);
  const response = await fetch(url, {
    headers: { Authorization: "Bearer " + requestToken, Accept: "application/json" },
    signal: AbortSignal.timeout(15000),
  });
  if (!response.ok) throw new Error("GitHub OIDC token request failed with HTTP " + response.status + ".");
  const body = await response.json();
  if (typeof body.value !== "string" || body.value.length < 100) {
    throw new Error("GitHub OIDC returned an invalid token payload.");
  }
  return body.value;
}

async function invokeSyncFunction(payload) {
  const config = getSupabaseReadConfiguration();
  const token = await requestGitHubOidcToken();
  const response = await fetch(config.url + "/functions/v1/" + EDGE_FUNCTION_NAME, {
    method: "POST",
    headers: {
      Authorization: "Bearer " + token,
      apikey: config.key,
      "Content-Type": "application/json",
      Accept: "application/json",
    },
    body: JSON.stringify(payload),
    signal: AbortSignal.timeout(90000),
  });
  const bodyText = await response.text();
  const body = bodyText ? parseJson(bodyText, "Edge Function") : {};
  if (!response.ok) {
    throw new Error("Sync gateway returned HTTP " + response.status + ": " +
      String(body.error ?? body.message ?? bodyText).slice(0, 1000));
  }
  return body;
}

async function uploadBatches(action, runId, records, manifestVersion, generatorVersion) {
  let uploaded = 0;
  for (let offset = 0; offset < records.length; offset += BATCH_SIZE) {
    const batch = records.slice(offset, offset + BATCH_SIZE);
    const result = await invokeSyncFunction({
      action,
      runId,
      manifestVersion,
      generatorVersion,
      records: batch,
    });
    uploaded += Number(result.accepted ?? 0);
    console.log(action + ": batch " + (Math.floor(offset / BATCH_SIZE) + 1) +
      ", accepted " + Number(result.accepted ?? 0) + "/" + batch.length +
      ", skipped " + Number(result.skipped ?? 0) + ".");
  }
  return uploaded;
}

function assertMainWorkflow() {
  if (process.env.GITHUB_REPOSITORY !== "rox4H/automated-dim-wishlists" ||
      process.env.GITHUB_REF !== "refs/heads/main") {
    throw new Error("Refusing production sync outside rox4H/automated-dim-wishlists on refs/heads/main.");
  }
  if (!process.env.ACTIONS_ID_TOKEN_REQUEST_URL || !process.env.ACTIONS_ID_TOKEN_REQUEST_TOKEN) {
    throw new Error("Production sync requires GitHub Actions OIDC credentials.");
  }
}

async function main() {
  const write = process.argv.includes("--write");
  const forceGenerated = process.argv.includes("--force-generated");
  if (process.argv.includes("--force")) {
    throw new Error("--force is intentionally unsupported. Use --force-generated; verified and manual pools remain immutable.");
  }
  if (write) assertMainWorkflow();

  const accessed = new Date().toISOString().slice(0, 10);
  console.log("Fetching the live Bungie Manifest and the read-only Supabase catalogue.");
  const [manifest, weapons, perks, pools] = await Promise.all([
    fetchManifestTables(),
    fetchCatalogueRows("weapons"),
    fetchCatalogueRows("perks"),
    fetchCatalogueRows("pools"),
  ]);

  let runId = null;
  let previousFingerprint = null;
  let completed = false;
  try {
    if (write) {
      const started = await invokeSyncFunction({
        action: "start",
        manifestVersion: manifest.version,
        generatorVersion: String(GENERATOR_VERSION),
        warnings: [],
      });
      if (typeof started.runId !== "string" || !started.runId) {
        throw new Error("Sync gateway did not return a run ID.");
      }
      runId = started.runId;
      previousFingerprint = typeof started.previousManifestFingerprint === "string"
        ? started.previousManifestFingerprint : null;
    }

    const plan = buildSyncPlan({
      weapons,
      perks,
      pools,
      manifest,
      previousManifestFingerprint: previousFingerprint,
      accessed,
      forceGenerated,
    });

    const report = {
      mode: write ? "write" : "dry-run",
      manifestVersion: plan.manifestVersion,
      manifestFingerprint: plan.manifestFingerprint,
      previousManifestFingerprint: previousFingerprint,
      runId,
      stats: plan.stats,
      changedPools: plan.changedPools.length,
      newPerks: plan.newPerks.length,
      warnings: plan.warnings,
    };
    console.log(JSON.stringify(report, null, 2));

    if (!write) return;

    const newPerksUploaded = await uploadBatches("upload-perks", runId, plan.newPerks, plan.manifestVersion);
    const poolsUploaded = await uploadBatches("upload-pools", runId, plan.changedPools, plan.manifestVersion, String(GENERATOR_VERSION));
    const finalStats = {
      ...plan.stats,
      manifest_fingerprint: plan.manifestFingerprint,
      pools_uploaded: poolsUploaded,
      perks_inserted: newPerksUploaded,
      changed_pools: plan.changedPools.length,
      new_perks: plan.newPerks.length,
    };
    await invokeSyncFunction({
      action: "finish",
      runId,
      status: "review_required",
      stats: finalStats,
      warnings: plan.warnings,
    });
    completed = true;
    console.log("Manifest sync run " + runId + " finished with review_required.");
  } catch (error) {
    if (write && runId && !completed) {
      try {
        await invokeSyncFunction({
          action: "finish",
          runId,
          status: "failed",
          stats: {},
          warnings: [],
          errorText: String(error?.message ?? error).slice(0, 4000),
        });
      } catch (finishError) {
        console.error("Could not mark failed sync run: " + String(finishError?.message ?? finishError));
      }
    }
    throw error;
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  main().catch((error) => {
    console.error(error);
    process.exitCode = 1;
  });
}
