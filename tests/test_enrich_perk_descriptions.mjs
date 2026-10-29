import test from "node:test";
import assert from "node:assert/strict";
import {
  buildDefinitionIndexes,
  enrichPerkRecord,
  enrichAllRecords,
} from "../scripts/enrich_perk_descriptions.mjs";

const definition = (name, description, type = "Trait", category = "v3.plugs.weapons.perks") => ({
  displayProperties: { name, description },
  itemTypeDisplayName: type,
  plug: { plugCategoryIdentifier: category },
});

const record = (overrides = {}) => ({
  id: "sample-perk",
  name: "Sample Perk",
  bungie_hash: null,
  perk_type: "trait",
  description: null,
  verification_status: "unverified",
  last_verified: null,
  notes: [],
  ...overrides,
});

test("exact hash and matching display name are preferred", () => {
  const definitions = {
    "123": definition("Sample Perk", "Exact hash text."),
    "456": definition("Sample Perk", "Alternative text."),
  };
  const result = enrichPerkRecord(record({ bungie_hash: 123 }), buildDefinitionIndexes(definitions), "test");
  assert.equal(result.method, "exact-hash");
  assert.equal(result.record.description, "Exact hash text.");
  assert.match(result.record.notes.join(" "), /exact hash 123/);
});

test("does not trust a hash whose definition has a different display name", () => {
  const definitions = {
    "123": definition("Different Item", "Wrong definition text."),
    "456": definition("Sample Perk", "Correct name text."),
  };
  const result = enrichPerkRecord(record({ bungie_hash: 123 }), buildDefinitionIndexes(definitions), "test");
  assert.equal(result.method, "display-name");
  assert.equal(result.record.description, "Correct name text.");
});

test("name matching respects the requested plug type when possible", () => {
  const definitions = {
    "123": definition("Sample Perk", "Wrong socket text.", "Intrinsic", "intrinsics"),
    "456": definition("Sample Perk", "Weapon-trait text.", "Trait", "v3.plugs.weapons.perks"),
  };
  const result = enrichPerkRecord(record(), buildDefinitionIndexes(definitions), "test");
  assert.equal(result.record.description, "Weapon-trait text.");
  assert.equal(result.method, "display-name");
});

test("preserves a curated non-empty description without replacing it", () => {
  const curated = record({ description: "Curated description." });
  const result = enrichPerkRecord(curated, buildDefinitionIndexes({}), "test");
  assert.equal(result.method, "preserved");
  assert.equal(result.record.description, "Curated description.");
  assert.equal(result.record.notes.length, 0);
});

test("known socket entries get an explicit non-gameplay description", () => {
  const result = enrichPerkRecord(record({
    id: "empty-catalyst-socket",
    name: "Empty Catalyst Socket",
    perk_type: "other",
  }), buildDefinitionIndexes({}), "test");
  assert.equal(result.method, "manual");
  assert.match(result.record.description, /Socket placeholder/);
  assert.match(result.record.notes.join(" "), /light\.gg|bungie\.net/);
});

test("refreshes stale notes that claim a newly populated description is still unset", () => {
  const stale = record({
    notes: ["Multiple manifest-derived definitions share this display name. The canonical hash and description remain unset until historical/enhanced variants are resolved; verify exact weapon/version availability before marking this record verified."],
  });
  const result = enrichPerkRecord(stale, buildDefinitionIndexes({
    "123": definition("Sample Perk", "New display-name text."),
  }), "test");
  assert.equal(result.record.description, "New display-name text.");
  assert.doesNotMatch(result.record.notes.join(" "), /description remain unset/);
  assert.match(result.record.notes.join(" "), /representative and unverified/);
});

test("refreshes old notes that incorrectly say a display-name match is missing", async () => {
  const existing = record({
    description: "Already populated.",
    notes: ["Listed in the community weapon-perk index, but no exact-name match was found in the available manifest-derived lookup. Keep the hash unset until the definition and historical/enhanced variant are identified."],
  });
  const { records } = await enrichAllRecords([{ path: "sample-perk.json", record: existing }], {}, "snapshot-x");
  assert.doesNotMatch(records[0].record.notes.join(" "), /no exact-name match was found/);
  assert.match(records[0].record.notes.join(" "), /current manifest display-name description is populated/);
});

test("official manifest descriptions replace weaker manual fallbacks when a current match exists", async () => {
  const existing = record({
    description: "Manual fallback text.",
    notes: ["Description paraphrased from https://example.com/old-source; exact item identity and historical/version-specific behaviour remain unverified."],
  });
  const defs = { "101": definition("Sample Perk", "Current manifest text.") };
  const { records, summary } = await enrichAllRecords([{ path: "sample-perk.json", record: existing }], defs, "snapshot-x");
  assert.equal(records[0].record.description, "Current manifest text.");
  assert.equal(summary.displayName, 1);
  assert.equal(summary.manual, 0);
  assert.doesNotMatch(records[0].record.notes.join(" "), /old-source/);
});

test("unmatched entries receive an explicitly unresolved placeholder", () => {
  const result = enrichPerkRecord(record(), buildDefinitionIndexes({}), "snapshot-x");
  assert.equal(result.method, "unresolved");
  assert.match(result.record.description, /Description unavailable/);
  assert.match(result.record.notes.join(" "), /review-needed placeholder/);
  assert.equal(result.record.verification_status, "unverified");
  assert.equal(result.record.last_verified, null);
});

test("enrichAllRecords reports every path and keeps the description field populated", async () => {
  const rows = [
    { path: "a.json", record: record({ id: "a", name: "A" }) },
    { path: "b.json", record: record({ id: "b", name: "B", description: "Existing." }) },
  ];
  const defs = { "101": definition("A", "A description.") };
  const { records, summary } = await enrichAllRecords(rows, defs, "snapshot-x");
  assert.equal(summary.total, 2);
  assert.equal(summary.exactHash, 0);
  assert.equal(summary.displayName, 1);
  assert.equal(summary.preserved, 1);
  assert.equal(summary.missingAfter, 0);
  assert.equal(records[0].record.description, "A description.");
  assert.equal(records[1].record.description, "Existing.");
});
