import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  buildCatalogue,
  readOverlay,
  slimManifest,
} from "../scripts/build_manifest_catalogue.mjs";

const fixtures = new URL("./fixtures/manifest_catalogue/", import.meta.url);
const readJson = (relative) => JSON.parse(readFileSync(new URL(relative, fixtures), "utf8"));

function loadManifest() {
  return {
    version: readJson("manifest/manifest.json").version,
    tables: {
      DestinyInventoryItemDefinition: readJson("manifest/DestinyInventoryItemDefinition.json"),
      DestinyPlugSetDefinition: readJson("manifest/DestinyPlugSetDefinition.json"),
      DestinySocketCategoryDefinition: readJson("manifest/DestinySocketCategoryDefinition.json"),
      DestinySocketTypeDefinition: readJson("manifest/DestinySocketTypeDefinition.json"),
    },
  };
}

function loadCatalogue() {
  const overlay = readOverlay(new URL("curated-overlay.json", fixtures).pathname);
  const hashes = Object.values(overlay.weapons).map((weapon) => weapon.bungie_hash);
  const slim = slimManifest(loadManifest(), hashes);
  const snapshot = { format: 1, manifest_version: slim.version, captured: "2026-10-31", tables: slim.tables };
  return buildCatalogue({ overlay, snapshot });
}

test("slimManifest keeps only definitions reachable from the requested weapons", () => {
  const slim = slimManifest(loadManifest(), [1001, 1002]);
  const items = slim.tables.DestinyInventoryItemDefinition;
  assert.equal(slim.version, "fixture-1");
  assert.ok(items["1001"] && items["1002"] && items["5301"] && items["5403"]);
  assert.equal(items["7777"], undefined, "unrelated definitions must be dropped");
  assert.deepEqual(Object.keys(slim.tables.DestinyPlugSetDefinition), ["9001", "9002", "9003", "9004", "9006"]);
  assert.deepEqual(Object.keys(slim.tables.DestinySocketCategoryDefinition), ["1", "2", "3"]);
  assert.deepEqual(Object.keys(slim.tables.DestinySocketTypeDefinition), ["201", "202", "203", "204", "205", "206"]);
  assert.deepEqual(items["1001"].sockets.socketEntries[1], {
    socketTypeHash: 202, singleInitialItemHash: 5101, reusablePlugSetHash: 9001,
  });
});

test("slimManifest tolerates weapon hashes that are absent from the manifest", () => {
  const slim = slimManifest(loadManifest(), [424242]);
  assert.deepEqual(slim.tables.DestinyInventoryItemDefinition, {});
});

test("generates an extraction-complete pool whose slots map onto the roll columns", () => {
  const pool = loadCatalogue().pools.find((record) => record.weapon_id === "test-rifle_s1");
  assert.equal(pool.pool_status, "complete");
  assert.equal(pool.verification_status, "unverified");
  assert.deepEqual(pool.slots.map((slot) => slot.id), ["intrinsic", "barrel", "magazine", "trait-1", "trait-2"]);
  assert.deepEqual(pool.slots[3].perk_ids, ["rampage", "kill-clip", "surplus"]);
  assert.equal(pool.sources[0].accessed, "2026-10-31");
});

test("preserves a human-verified pool unchanged and reports drift against the manifest", () => {
  const catalogue = loadCatalogue();
  const overlay = readOverlay(new URL("curated-overlay.json", fixtures).pathname);
  const pool = catalogue.pools.find((record) => record.weapon_id === "verified-rifle_s1");
  assert.deepEqual(pool, overlay.verified_pools["verified-rifle_s1"]);
  assert.deepEqual(catalogue.report.verified_pool_drift, [{
    weapon_id: "verified-rifle_s1",
    added: ["subsistence", "surplus"],
    removed: ["frenzy"],
    manifest_pool_status: "complete",
  }]);
});

test("a weapon missing from the manifest gets an unknown pool and is reported", () => {
  const catalogue = loadCatalogue();
  const pool = catalogue.pools.find((record) => record.weapon_id === "ghost-rifle_s1");
  assert.equal(pool.pool_status, "unknown");
  assert.equal(pool.verification_status, "unverified");
  assert.deepEqual(catalogue.report.weapons_missing_from_manifest, ["ghost-rifle_s1"]);
});

test("curated perks are untouched and only referenced seeded perks are emitted", () => {
  const catalogue = loadCatalogue();
  const overlay = readOverlay(new URL("curated-overlay.json", fixtures).pathname);
  for (const record of Object.values(overlay.perks)) {
    assert.deepEqual(catalogue.perks.find((perk) => perk.id === record.id), record);
  }
  const seeded = catalogue.perks.filter((perk) => !overlay.perks[perk.id]);
  assert.deepEqual(seeded.map((perk) => perk.id), ["surplus"]);
  assert.equal(seeded[0].verification_status, "unverified");
  assert.equal(seeded[0].bungie_hash, 5303);
  assert.equal(catalogue.perks.some((perk) => perk.id === "subsistence"), false);
});

test("building twice from the same inputs is deterministic", () => {
  assert.deepEqual(loadCatalogue(), loadCatalogue());
});
