import test from "node:test";
import assert from "node:assert/strict";
import {
  classifySocketCategory,
  collectSocketPlugHashes,
  inferUnclassifiedSocketCategory,
  inferPerkType,
  isIgnorablePlug,
  isPrunableGeneratedPerkRecord,
  hasSocketSource,
  slugify,
  createUnknownPool,
  createPerkResolver,
  fingerprintManifestTables,
  shouldPreservePool,
  shouldRetainEmptySlot,
  buildPoolForWeapon,
  GENERATOR_VERSION,
} from "../scripts/generate_weapon_perk_pools.mjs";

test("season-variant IDs remain stable, lowercase slugs", () => {
  assert.equal(slugify("Avalanche (Season 19)"), "avalanche-season-19");
  assert.equal(slugify("Jötunn & Drang"), "jotunn-and-drang");
});

test("retains non-cosmetic candidates when a socket category is unknown", () => {
  assert.equal(inferUnclassifiedSocketCategory([
    { displayProperties: { name: "New Perk" }, itemTypeDisplayName: "Unfamiliar Plug" },
  ]), "perks");
  assert.equal(inferUnclassifiedSocketCategory([
    { displayProperties: { name: "Adaptive Frame" }, itemTypeDisplayName: "Intrinsic" },
  ]), "intrinsic");
  assert.equal(inferUnclassifiedSocketCategory([
    { displayProperties: { name: "Empty Weapon Perk" }, itemTypeDisplayName: "Unfamiliar Plug" },
  ]), null);
  assert.equal(inferUnclassifiedSocketCategory([], [404]), "perks");
});

test("classifies pool categories and explicitly excludes cosmetic, mod and tracker sockets", () => {
  assert.equal(classifySocketCategory("INTRINSIC TRAITS"), "intrinsic");
  assert.equal(classifySocketCategory("WEAPON PERKS"), "perks");
  assert.equal(classifySocketCategory("WEAPON COSMETICS"), "ignore");
  assert.equal(classifySocketCategory("WEAPON MODS"), "ignore");
  assert.equal(classifySocketCategory("WEAPON TRACKERS"), "ignore");
  assert.equal(classifySocketCategory("WEAPON MASTERWORKS"), "ignore");
  assert.equal(classifySocketCategory("WEAPON ORNAMENTS"), "ignore");
  assert.equal(classifySocketCategory("UNCLASSIFIED SOCKET"), null);
});

test("collects every socket source, deduplicates hashes and ignores empty hash values", () => {
  const result = collectSocketPlugHashes({
    singleInitialItemHash: 7,
    randomizedPlugSetHash: 100,
    reusablePlugSetHash: 200,
    reusablePlugItems: [{ plugItemHash: 3 }, { plugItemHash: 0 }, { plugItemHash: 7 }],
  }, {
    "100": { reusablePlugItems: [{ plugItemHash: 1 }, { plugItemHash: 2 }] },
    "200": { reusablePlugItems: [{ plugItemHash: 2 }, { plugItemHash: 4 }] },
  });
  assert.deepEqual(result.hashes, [1, 2, 4, 3, 7]);
  assert.deepEqual(result.missingPlugSets, []);
  assert.deepEqual(result.sourceHashes, [100, 200]);
  assert.equal(result.hasRandomizedSet, true);
  assert.equal(result.hasReusableSet, true);
  assert.equal(result.hasInlinePlugs, true);
});

test("missing plug sets are reported instead of silently treated as a complete pool", () => {
  const result = collectSocketPlugHashes({ randomizedPlugSetHash: 404, singleInitialItemHash: 123 }, {});
  assert.deepEqual(result.hashes, [123]);
  assert.deepEqual(result.missingPlugSets, [404]);
});

test("ignores unused sockets while retaining missing plug-set references as gaps", () => {
  const entry = { singleInitialItemHash: 0, randomizedPlugSetHash: 0, reusablePlugSetHash: 0 };
  const collected = collectSocketPlugHashes(entry, {});
  assert.equal(hasSocketSource(entry, collected), false);

  const emptySet = collectSocketPlugHashes({ randomizedPlugSetHash: 404 }, {
    "404": { reusablePlugItems: [] },
  });
  assert.equal(hasSocketSource({ randomizedPlugSetHash: 404 }, emptySet), false);

  const missingSet = collectSocketPlugHashes({ randomizedPlugSetHash: 405 }, {});
  assert.equal(hasSocketSource({ randomizedPlugSetHash: 405 }, missingSet), true);
});

test("filters empty slots, trackers and cosmetic plug definitions", () => {
  assert.equal(isIgnorablePlug({ displayProperties: { name: "Empty Weapon Perk" } }), true);
  assert.equal(isIgnorablePlug({ displayProperties: { name: "Kill Tracker" } }), true);
  assert.equal(isIgnorablePlug({ displayProperties: { name: "Default Shader" }, itemTypeDisplayName: "Shader" }), true);
  assert.equal(isIgnorablePlug({ displayProperties: { name: "Opening Shot" }, itemTypeDisplayName: "Trait", plug: { plugCategoryIdentifier: "trait" } }), false);
  assert.equal(isIgnorablePlug({ displayProperties: { name: "Ace of Spades Catalyst" }, itemTypeDisplayName: "Masterwork" }), true);
  assert.equal(isIgnorablePlug({ displayProperties: { name: "Ace of Spades Catalyst" }, itemTypeDisplayName: "Masterwork" }, { allowCatalyst: true }), false);
});

test("maps common weapon perk labels to the canonical schema enum", () => {
  assert.equal(inferPerkType({ itemTypeDisplayName: "Origin Trait" }), "origin-trait");
  assert.equal(inferPerkType({ itemTypeDisplayName: "Launcher Barrel" }), "launcher-barrel");
  assert.equal(inferPerkType({ itemTypeDisplayName: "Trait" }), "trait");
  assert.equal(inferPerkType({ itemTypeDisplayName: "Unfamiliar Component" }), "other");
});

test("only prunes unverified automatic perk seeds without user edits", () => {
  const generated = {
    verification_status: "unverified",
    last_verified: null,
    notes: [
      "Seeded automatically from the Bungie manifest snapshot 123 while resolving a weapon-version perk pool.",
      "Verify this exact definition, display name and perk type before marking the record verified.",
    ],
  };
  assert.equal(isPrunableGeneratedPerkRecord(generated), true);
  assert.equal(isPrunableGeneratedPerkRecord({ ...generated, verification_status: "verified" }), false);
  assert.equal(isPrunableGeneratedPerkRecord({ ...generated, notes: [...generated.notes, "User edit"] }), false);
});

test("keeps empty slots only when extraction has unresolved data", () => {
  assert.equal(shouldRetainEmptySlot([], [], 0), false);
  assert.equal(shouldRetainEmptySlot([], [], 2), true);
  assert.equal(shouldRetainEmptySlot([], [123], 0), true);
  assert.equal(shouldRetainEmptySlot(["opening-shot"], [], 0), false);
});

test("rebuilds stale generated pools but preserves manual and verified pools", () => {
  const staleGenerated = {
    pool_status: "partial",
    verification_status: "unverified",
    notes: ["Generated from Bungie's live manifest snapshot old-manifest."],
  };
  const currentGenerated = {
    pool_status: "partial",
    verification_status: "unverified",
    notes: [
      "Generated from Bungie's live manifest snapshot current-manifest.",
      "Pool extraction generator version: " + GENERATOR_VERSION + ".",
    ],
  };
  const manual = {
    pool_status: "partial",
    verification_status: "unverified",
    notes: ["Reviewed manually; preserve this curated pool."],
  };
  const verifiedGenerated = {
    pool_status: "complete",
    verification_status: "verified",
    notes: ["Generated from Bungie's live manifest snapshot older-version."],
  };
  assert.equal(shouldPreservePool(staleGenerated), false);
  assert.equal(shouldPreservePool(currentGenerated), true);
  assert.equal(shouldPreservePool(manual), true);
  assert.equal(shouldPreservePool(verifiedGenerated), true);
  assert.equal(shouldPreservePool(currentGenerated, true), false);
  assert.equal(shouldPreservePool(verifiedGenerated, true), true, "force must never bypass human verification");
  assert.equal(shouldPreservePool(manual, true), true, "force must never overwrite a manual pool");
  assert.equal(shouldPreservePool({ pool_status: "unknown", notes: [] }), false);

  const fingerprinted = {
    pool_status: "complete",
    verification_status: "unverified",
    notes: [
      "Generated from Bungie's live manifest snapshot current-manifest for this exact weapon item hash.",
      "Pool extraction generator version: " + GENERATOR_VERSION + ".",
      "Manifest component fingerprint (SHA-256): abc123.",
    ],
  };
  assert.equal(shouldPreservePool(fingerprinted, false, "current-manifest", "abc123"), true);
  assert.equal(shouldPreservePool(fingerprinted, false, "current-manifest", "different"), false);
  assert.equal(shouldPreservePool(fingerprinted, false, "new-manifest", "abc123"), false);
});

test("strict perk resolution never reuses a same-name row without the exact Bungie hash", () => {
  const existing = {
    id: "opening-shot",
    name: "Opening Shot",
    bungie_hash: null,
    perk_type: "trait",
    description: "Existing catalogue text",
    verification_status: "unverified",
    last_verified: null,
    notes: [],
  };
  const definitions = {
    "201": {
      displayProperties: { name: "Opening Shot", description: "Manifest description" },
      itemTypeDisplayName: "Trait",
      plug: { plugCategoryIdentifier: "trait" },
    },
    "202": {
      displayProperties: { name: "Opening Shot", description: "Another exact definition" },
      itemTypeDisplayName: "Trait",
      plug: { plugCategoryIdentifier: "trait" },
    },
    "203": {
      displayProperties: { name: "Snapshot Sights", description: "Snapshot description" },
      itemTypeDisplayName: "Trait",
      plug: { plugCategoryIdentifier: "trait" },
    },
  };
  const generated = new Map();
  const resolver = createPerkResolver(
    [{ record: existing }],
    definitions,
    "test-manifest",
    generated,
    { strictHashOnly: true },
  );

  assert.equal(resolver.resolve(201), "opening-shot-201");
  assert.equal(resolver.resolve(202), "opening-shot-202");
  assert.equal(resolver.resolve(203), "snapshot-sights");
  assert.equal(generated.size, 3);
  assert.equal(generated.get("opening-shot-201").bungie_hash, 201);
  assert.equal(generated.get("opening-shot-202").bungie_hash, 202);
});

test("strict perk resolution accepts an exact hash match but rejects a conflicting name", () => {
  const exact = {
    id: "opening-shot",
    name: "Opening Shot",
    bungie_hash: 201,
    perk_type: "trait",
  };
  const conflicting = {
    id: "wrong-record",
    name: "Different Name",
    bungie_hash: 202,
    perk_type: "trait",
  };
  const definitions = {
    "201": { displayProperties: { name: "Opening Shot" }, itemTypeDisplayName: "Trait" },
    "202": { displayProperties: { name: "Manifest Name" }, itemTypeDisplayName: "Trait" },
  };
  const generated = new Map();
  const resolver = createPerkResolver(
    [{ record: exact }, { record: conflicting }],
    definitions,
    "test-manifest",
    generated,
    { strictHashOnly: true },
  );

  assert.equal(resolver.resolve(201), "opening-shot");
  assert.equal(resolver.resolve(202), null);
  assert.equal(generated.size, 0);
});

test("manifest fingerprints are deterministic and react to definition content changes", () => {
  const first = {
    DestinyInventoryItemDefinition: {
      "2": { displayProperties: { name: "B" } },
      "1": { displayProperties: { name: "A" } },
    },
    DestinyPlugSetDefinition: { "7": { reusablePlugItems: [{ plugItemHash: 1 }] } },
    DestinySocketCategoryDefinition: {},
    DestinySocketTypeDefinition: {},
  };
  const reordered = {
    DestinyInventoryItemDefinition: {
      "1": { displayProperties: { name: "A" } },
      "2": { displayProperties: { name: "B" } },
    },
    DestinyPlugSetDefinition: { "7": { reusablePlugItems: [{ plugItemHash: 1 }] } },
    DestinySocketCategoryDefinition: {},
    DestinySocketTypeDefinition: {},
  };
  const changed = structuredClone(reordered);
  changed.DestinyInventoryItemDefinition["1"].displayProperties.name = "Changed";
  assert.equal(fingerprintManifestTables(first), fingerprintManifestTables(reordered));
  assert.notEqual(fingerprintManifestTables(first), fingerprintManifestTables(changed));
});

test("unknown pool placeholder explicitly states that unknown does not mean no perks", () => {
  const pool = createUnknownPool({ id: "sample_s5", name: "Sample", bungie_hash: null, season: "Season 5" },
    "Definition unavailable", "test-manifest", "2026-10-27");
  assert.equal(pool.weapon_id, "sample_s5");
  assert.equal(pool.pool_status, "unknown");
  assert.equal(pool.verification_status, "unverified");
  assert.deepEqual(pool.slots[0].perk_ids, []);
  assert.match(pool.notes.join(" "), /does not mean the weapon has no perks/i);
});

test("keeps multiple perk alternatives together inside their original socket slot", () => {
  const weapon = {
    id: "sample-weapon_s5",
    name: "Sample Weapon",
    bungie_hash: 100,
    season: "Season 5: Black Armory / Season of the Forge",
  };
  const manifest = {
    version: "test-manifest",
    tables: {
      DestinyInventoryItemDefinition: {
        "100": {
          displayProperties: { name: "Sample Weapon" },
          sockets: {
            socketEntries: [{
              singleInitialItemHash: 0,
              reusablePlugItems: [{ plugItemHash: 201 }, { plugItemHash: 202 }],
            }],
            socketCategories: [{ socketCategoryHash: 10, socketIndexes: [0] }],
          },
        },
        "201": {
          displayProperties: { name: "Opening Shot", description: "" },
          itemTypeDisplayName: "Trait",
          plug: { plugCategoryIdentifier: "trait" },
        },
        "202": {
          displayProperties: { name: "Rampage", description: "" },
          itemTypeDisplayName: "Trait",
          plug: { plugCategoryIdentifier: "trait" },
        },
      },
      DestinyPlugSetDefinition: {},
      DestinySocketCategoryDefinition: {
        "10": { displayProperties: { name: "WEAPON PERKS" } },
      },
      DestinySocketTypeDefinition: {},
    },
  };
  const perkResolver = {
    resolve(hash) {
      return ({ 201: "opening-shot", 202: "rampage" })[hash] ?? null;
    },
  };

  const pool = buildPoolForWeapon(weapon, manifest, perkResolver, { accessed: "2026-10-27" });

  assert.equal(pool.pool_status, "complete");
  assert.equal(pool.slots.length, 1, "one manifest socket must remain one slot");
  assert.equal(pool.slots[0].selection_mode, "choose-one");
  assert.deepEqual(pool.slots[0].perk_ids, ["opening-shot", "rampage"]);
});
