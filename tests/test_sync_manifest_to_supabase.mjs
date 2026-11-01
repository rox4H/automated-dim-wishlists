import test from "node:test";
import assert from "node:assert/strict";
import {
  buildSyncPlan,
  isGeneratedPool,
} from "../scripts/sync_manifest_to_supabase.mjs";
import { fingerprintManifestTables, GENERATOR_VERSION } from "../scripts/generate_weapon_perk_pools.mjs";

function createFixture() {
  const weapon = {
    id: "sample-weapon_s1",
    name: "Sample Weapon",
    bungie_hash: 100,
    season: "Season 1",
    season_number: 1,
    ammo_type: "Primary",
    weapon_slot: "Kinetic",
    frame: null,
  };
  const perks = [{
    record: {
      id: "opening-shot",
      name: "Opening Shot",
      bungie_hash: null,
      perk_type: "trait",
      description: null,
      verification_status: "unverified",
      last_verified: null,
      notes: ["Legacy name-only row; exact Bungie hash not yet established."],
    },
  }];
  const manifest = {
    version: "fixture-manifest",
    tables: {
      DestinyInventoryItemDefinition: {
        "100": {
          hash: 100,
          displayProperties: { name: "Sample Weapon" },
          sockets: {
            socketEntries: [
              { singleInitialItemHash: 101 },
              { reusablePlugItems: [{ plugItemHash: 201 }, { plugItemHash: 202 }] },
            ],
            socketCategories: [
              { socketCategoryHash: 10, socketIndexes: [0] },
              { socketCategoryHash: 20, socketIndexes: [1] },
            ],
          },
        },
        "101": {
          hash: 101,
          displayProperties: { name: "Adaptive Frame", description: "" },
          itemTypeDisplayName: "Intrinsic",
          plug: { plugCategoryIdentifier: "intrinsic" },
        },
        "201": {
          hash: 201,
          displayProperties: { name: "Opening Shot", description: "Manifest Opening Shot." },
          itemTypeDisplayName: "Trait",
          plug: { plugCategoryIdentifier: "trait" },
        },
        "202": {
          hash: 202,
          displayProperties: { name: "Rampage", description: "Manifest Rampage." },
          itemTypeDisplayName: "Trait",
          plug: { plugCategoryIdentifier: "trait" },
        },
      },
      DestinyPlugSetDefinition: {},
      DestinySocketCategoryDefinition: {
        "10": { displayProperties: { name: "INTRINSIC" } },
        "20": { displayProperties: { name: "WEAPON PERKS" } },
      },
      DestinySocketTypeDefinition: {},
    },
  };
  return { weapon, perks, manifest };
}

test("planner resolves perks by exact plug hash and does not reuse name-only legacy rows", () => {
  const { weapon, perks, manifest } = createFixture();
  const plan = buildSyncPlan({
    weapons: [{ record: weapon }],
    perks,
    pools: [],
    manifest,
    previousManifestFingerprint: null,
    accessed: "2026-11-01",
  });

  assert.equal(plan.stats.complete, 1);
  assert.equal(plan.stats.unknown, 0);
  assert.equal(plan.changedPools.length, 1);
  assert.ok(isGeneratedPool(plan.changedPools[0]));
  assert.equal(plan.changedPools[0].verification_status, "unverified");

  const ids = new Set(plan.changedPools[0].slots.flatMap((slot) => slot.perk_ids));
  assert.ok(ids.has("opening-shot-201"), "same-name perk with no hash must not be reused");
  assert.ok(ids.has("rampage"));
  assert.ok(ids.has("adaptive-frame"));
  assert.equal(plan.newPerks.length, 3);
  assert.equal(plan.newPerks.find((perk) => perk.id === "opening-shot-201").bungie_hash, 201);
});

test("verified and manually authored pools are immutable, even with force-generated", () => {
  const { weapon, perks, manifest } = createFixture();
  const verified = {
    weapon_id: weapon.id,
    weapon_hash: weapon.bungie_hash,
    version_label: weapon.season,
    pool_status: "complete",
    verification_status: "verified",
    last_verified: "2026-10-20",
    slots: [{ id: "manual", label: "Manual", selection_mode: "fixed", perk_ids: ["opening-shot"] }],
    notes: ["Human review completed."],
  };
  const manual = {
    ...verified,
    verification_status: "unverified",
    last_verified: null,
    notes: ["Maintainer-curated pool; preserve this record."],
  };

  const verifiedPlan = buildSyncPlan({
    weapons: [{ record: weapon }], perks, pools: [{ record: verified }], manifest,
    previousManifestFingerprint: "different", forceGenerated: true,
  });
  assert.deepEqual(verifiedPlan.changedPools, []);
  assert.equal(verifiedPlan.stats.poolsPreservedVerified, 1);
  assert.equal(verifiedPlan.finalPools[0], verified);

  const manualPlan = buildSyncPlan({
    weapons: [{ record: weapon }], perks, pools: [{ record: manual }], manifest,
    previousManifestFingerprint: "different", forceGenerated: true,
  });
  assert.deepEqual(manualPlan.changedPools, []);
  assert.equal(manualPlan.stats.poolsPreservedManual, 1);
  assert.equal(manualPlan.finalPools[0], manual);
});

test("same Manifest version with changed definition content rebuilds generated pools", () => {
  const { weapon, perks, manifest } = createFixture();
  const first = buildSyncPlan({
    weapons: [{ record: weapon }], perks, pools: [], manifest,
    previousManifestFingerprint: null, accessed: "2026-11-01",
  });
  const oldFingerprint = first.manifestFingerprint;

  const changedManifest = structuredClone(manifest);
  changedManifest.tables.DestinyInventoryItemDefinition["203"] = {
    hash: 203,
    displayProperties: { name: "Subsistence", description: "Manifest Subsistence." },
    itemTypeDisplayName: "Trait",
    plug: { plugCategoryIdentifier: "trait" },
  };
  changedManifest.tables.DestinyInventoryItemDefinition["100"].sockets.socketEntries[1]
    .reusablePlugItems.push({ plugItemHash: 203 });

  assert.equal(changedManifest.version, manifest.version, "the test intentionally keeps the version label unchanged");
  assert.notEqual(fingerprintManifestTables(changedManifest.tables), oldFingerprint);

  const second = buildSyncPlan({
    weapons: [{ record: weapon }],
    perks,
    pools: [{ record: first.finalPools[0] }],
    manifest: changedManifest,
    previousManifestFingerprint: oldFingerprint,
    accessed: "2026-11-02",
  });

  assert.equal(second.stats.poolsRebuilt, 1);
  assert.equal(second.changedPools.length, 1);
  const perkIds = new Set(second.changedPools[0].slots.flatMap((slot) => slot.perk_ids));
  assert.ok(perkIds.has("subsistence"));
});

test("same content fingerprint preserves current generated pools without rewriting", () => {
  const { weapon, perks, manifest } = createFixture();
  const first = buildSyncPlan({
    weapons: [{ record: weapon }], perks, pools: [], manifest,
    previousManifestFingerprint: null, accessed: "2026-11-01",
  });
  const second = buildSyncPlan({
    weapons: [{ record: weapon }],
    perks: [...perks, ...first.newPerks.map((record) => ({ record }))],
    pools: [{ record: first.finalPools[0] }],
    manifest,
    previousManifestFingerprint: first.manifestFingerprint,
    accessed: "2026-11-02",
  });

  assert.equal(second.stats.poolsPreservedCurrent, 1);
  assert.equal(second.stats.poolsRebuilt, 0);
  assert.deepEqual(second.changedPools, []);
  assert.equal(second.manifestFingerprint, first.manifestFingerprint);
});
