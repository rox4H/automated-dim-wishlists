#!/usr/bin/env node
/**
 * Build the weapon / perk / perk-pool catalogue from a committed Bungie manifest snapshot.
 *
 * This replaces the Supabase read path without changing the record shape that the
 * validators and the DIM exporter consume. Inputs:
 *
 *   catalogue/curated-overlay.json    Human-curated records exported once from Supabase
 *                                     (weapon versions, perks, human-verified pools).
 *   catalogue/manifest-snapshot.json  A slim, verbatim subset of Bungie's manifest: only the
 *                                     definitions reachable from the weapons in the overlay.
 *
 * Policy (mirrors generate_weapon_perk_pools.mjs):
 *   - Weapon and perk records come from the overlay; curated fields are never rewritten.
 *   - A human-verified pool is preserved as-is. The pool the manifest would produce is
 *     compared with it and any difference is reported, never applied silently.
 *   - Every other pool is generated from the snapshot with buildPoolForWeapon().
 *   - Perks that generation has to seed are emitted only when a final pool references them.
 *
 * Usage:
 *   node scripts/build_manifest_catalogue.mjs fetch --overlay FILE --snapshot FILE [--manifest-dir DIR] [--captured YYYY-MM-DD]
 *   node scripts/build_manifest_catalogue.mjs build --overlay FILE --snapshot FILE --out DIR
 *
 * `fetch` downloads the full manifest (several minutes; use `node --max-old-space-size=8192`)
 * unless --manifest-dir points at pre-downloaded JSON tables. `build` is offline and deterministic.
 */
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import {
  buildPoolForWeapon,
  collectSocketPlugHashes,
  createPerkResolver,
  fetchManifestTables,
  shouldPreservePool,
} from "./generate_weapon_perk_pools.mjs";

export const SNAPSHOT_FORMAT = 1;
export const OVERLAY_FORMAT = 1;
export const MANIFEST_SOURCE_URL = "https://www.bungie.net/Platform/Destiny2/Manifest/";
const MANIFEST_TABLES = [
  "DestinyInventoryItemDefinition",
  "DestinyPlugSetDefinition",
  "DestinySocketCategoryDefinition",
  "DestinySocketTypeDefinition",
];
const ENTRY_HASH_FIELDS = [
  "socketTypeHash",
  "singleInitialItemHash",
  "randomizedPlugSetHash",
  "reusablePlugSetHash",
];

function sortedByHash(map) {
  return Object.fromEntries([...map.entries()].sort((a, b) => Number(a[0]) - Number(b[0])));
}

function slimSocketEntry(entry) {
  const slim = {};
  for (const field of ENTRY_HASH_FIELDS) if (entry?.[field] !== undefined) slim[field] = entry[field];
  if (Array.isArray(entry?.reusablePlugItems)) {
    slim.reusablePlugItems = entry.reusablePlugItems.map((plug) => ({ plugItemHash: plug?.plugItemHash }));
  }
  return slim;
}

function slimPlug(hash, definition) {
  const properties = definition.displayProperties ?? {};
  const slim = {
    hash: definition.hash ?? hash,
    displayProperties: { name: properties.name ?? "", description: properties.description ?? "" },
  };
  if (definition.itemTypeDisplayName !== undefined) slim.itemTypeDisplayName = definition.itemTypeDisplayName;
  if (definition.plug?.plugCategoryIdentifier !== undefined) {
    slim.plug = { plugCategoryIdentifier: definition.plug.plugCategoryIdentifier };
  }
  return slim;
}

/** Keep only the manifest definitions that buildPoolForWeapon() can reach from these weapons. */
export function slimManifest(manifest, weaponHashes) {
  const items = manifest.tables?.DestinyInventoryItemDefinition ?? {};
  const plugSets = manifest.tables?.DestinyPlugSetDefinition ?? {};
  const categories = manifest.tables?.DestinySocketCategoryDefinition ?? {};
  const types = manifest.tables?.DestinySocketTypeDefinition ?? {};
  const outItems = new Map();
  const outPlugSets = new Map();
  const outCategories = new Map();
  const outTypes = new Map();
  const nameOnly = (definition) => ({ displayProperties: { name: definition?.displayProperties?.name ?? "" } });

  for (const weaponHash of [...new Set(weaponHashes)].sort((a, b) => a - b)) {
    const definition = items[String(weaponHash)];
    if (!definition) continue;
    const slim = { hash: definition.hash ?? weaponHash, displayProperties: { name: definition.displayProperties?.name ?? "" } };
    if (definition.sockets) {
      const entries = definition.sockets.socketEntries;
      slim.sockets = {
        socketEntries: Array.isArray(entries) ? entries.map(slimSocketEntry) : entries,
        socketCategories: (definition.sockets.socketCategories ?? []).map((category) => ({
          socketCategoryHash: category.socketCategoryHash,
          socketIndexes: category.socketIndexes ?? [],
        })),
      };
      for (const category of slim.sockets.socketCategories) {
        const found = categories[String(category.socketCategoryHash)];
        if (found) outCategories.set(String(category.socketCategoryHash), nameOnly(found));
      }
      for (const entry of Array.isArray(entries) ? entries : []) {
        const type = types[String(entry?.socketTypeHash)];
        if (type) outTypes.set(String(entry.socketTypeHash), nameOnly(type));
        const collected = collectSocketPlugHashes(entry, plugSets);
        for (const setHash of collected.sourceHashes) {
          const set = plugSets[String(setHash)];
          if (Array.isArray(set?.reusablePlugItems)) {
            outPlugSets.set(String(setHash), {
              reusablePlugItems: set.reusablePlugItems.map((plug) => ({ plugItemHash: plug?.plugItemHash })),
            });
          }
        }
        for (const plugHash of collected.hashes) {
          const plug = items[String(plugHash)];
          if (plug) outItems.set(String(plugHash), slimPlug(plugHash, plug));
        }
      }
    }
    outItems.set(String(weaponHash), slim);
  }
  return {
    version: manifest.version,
    tables: {
      DestinyInventoryItemDefinition: sortedByHash(outItems),
      DestinyPlugSetDefinition: sortedByHash(outPlugSets),
      DestinySocketCategoryDefinition: sortedByHash(outCategories),
      DestinySocketTypeDefinition: sortedByHash(outTypes),
    },
  };
}

function poolPerkIds(pool) {
  const ids = new Set();
  for (const slot of pool?.slots ?? []) for (const id of slot?.perk_ids ?? []) ids.add(id);
  return ids;
}

function poolDrift(preserved, fresh) {
  const before = poolPerkIds(preserved);
  const after = poolPerkIds(fresh);
  const added = [...after].filter((id) => !before.has(id)).sort();
  const removed = [...before].filter((id) => !after.has(id)).sort();
  if (!added.length && !removed.length) return null;
  return { added, removed, manifest_pool_status: fresh.pool_status };
}

const byId = (key) => (a, b) => (a[key] < b[key] ? -1 : a[key] > b[key] ? 1 : 0);

export function buildCatalogue({ overlay, snapshot }) {
  const manifest = { version: snapshot.manifest_version, tables: snapshot.tables };
  const items = manifest.tables.DestinyInventoryItemDefinition ?? {};
  const accessed = snapshot.captured ?? null;
  const weapons = Object.values(overlay.weapons ?? {}).sort(byId("id"));
  const perkRecords = Object.values(overlay.perks ?? {}).sort(byId("id"));
  const verifiedPools = overlay.verified_pools ?? {};
  const generatedPerks = new Map();
  const resolver = createPerkResolver(perkRecords.map((record) => ({ record })), items, manifest.version, generatedPerks);

  const pools = new Map();
  const report = {
    manifest_version: manifest.version,
    captured: accessed,
    weapons: weapons.length,
    pools: { preserved_verified: 0, generated_complete: 0, generated_partial: 0, generated_unknown: 0 },
    verified_pool_drift: [],
    weapons_missing_from_manifest: [],
    weapon_name_mismatches: [],
    seeded_perks: [],
  };

  for (const weapon of weapons) {
    const definition = Number.isSafeInteger(weapon.bungie_hash) ? items[String(weapon.bungie_hash)] : null;
    if (!definition) report.weapons_missing_from_manifest.push(weapon.id);
    else if (String(definition.displayProperties?.name ?? "").trim() !== String(weapon.name ?? "").trim()) {
      report.weapon_name_mismatches.push({ weapon_id: weapon.id, catalogue: weapon.name, manifest: definition.displayProperties?.name ?? "" });
    }
    const fresh = buildPoolForWeapon(weapon, manifest, resolver, { accessed });
    const existing = verifiedPools[weapon.id] ?? null;
    if (shouldPreservePool(existing)) {
      pools.set(weapon.id, existing);
      report.pools.preserved_verified++;
      const drift = poolDrift(existing, fresh);
      if (drift) report.verified_pool_drift.push({ weapon_id: weapon.id, ...drift });
    } else {
      pools.set(weapon.id, fresh);
      report.pools["generated_" + fresh.pool_status]++;
    }
  }

  const referenced = new Set();
  for (const pool of pools.values()) for (const id of poolPerkIds(pool)) referenced.add(id);
  const seeded = [...generatedPerks.values()].filter((record) => referenced.has(record.id));
  report.seeded_perks = seeded.map((record) => record.id).sort();
  return {
    weapons,
    perks: [...perkRecords, ...seeded].sort(byId("id")),
    pools: [...pools.values()].sort(byId("weapon_id")),
    report,
  };
}

function writeJson(path, value) {
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, JSON.stringify(value, null, 2) + "\n");
}

export function writeCatalogue(outDir, catalogue) {
  for (const record of catalogue.weapons) writeJson(join(outDir, "data", "weapons", record.id + ".json"), record);
  for (const record of catalogue.perks) writeJson(join(outDir, "data", "perks", record.id + ".json"), record);
  for (const record of catalogue.pools) writeJson(join(outDir, "data", "weapon-perk-pools", record.weapon_id + ".json"), record);
  writeJson(join(outDir, "manifest-catalogue-report.json"), catalogue.report);
}

function readJson(path, label) {
  try {
    return JSON.parse(readFileSync(path, "utf8"));
  } catch (error) {
    throw new Error("Could not read " + label + " at " + path + ": " + error.message);
  }
}

export function readOverlay(path) {
  const overlay = readJson(path, "curated overlay");
  if (overlay?.format !== OVERLAY_FORMAT || typeof overlay.weapons !== "object" || typeof overlay.perks !== "object") {
    throw new Error("Curated overlay " + path + " is not a format " + OVERLAY_FORMAT + " file. Re-run scripts/export_catalogue_overlay.py.");
  }
  return overlay;
}

export function readSnapshot(path) {
  const snapshot = readJson(path, "manifest snapshot");
  if (snapshot?.format !== SNAPSHOT_FORMAT || !snapshot.manifest_version || typeof snapshot.tables !== "object") {
    throw new Error("Manifest snapshot " + path + " is not a format " + SNAPSHOT_FORMAT + " file. Re-run the fetch command.");
  }
  return snapshot;
}

function loadManifestDirectory(directory) {
  const meta = readJson(join(directory, "manifest.json"), "manifest metadata");
  const tables = {};
  for (const name of MANIFEST_TABLES) {
    try {
      tables[name] = JSON.parse(readFileSync(join(directory, name + ".json"), "utf8"));
    } catch (error) {
      if (error.code !== "ENOENT") throw error;
      tables[name] = {};
    }
  }
  return { version: meta.version, tables };
}

function parseArguments(argv) {
  const [command, ...rest] = argv;
  const options = {};
  for (let index = 0; index < rest.length; index++) {
    if (!rest[index].startsWith("--")) throw new Error("Unexpected argument: " + rest[index]);
    const next = rest[index + 1];
    options[rest[index].slice(2)] = next !== undefined && !next.startsWith("--") ? rest[++index] : true;
  }
  return { command, options };
}

function required(options, name) {
  if (typeof options[name] !== "string") throw new Error("Missing required option --" + name);
  return resolve(options[name]);
}

async function main() {
  const { command, options } = parseArguments(process.argv.slice(2));
  if (command === "fetch") {
    const overlay = readOverlay(required(options, "overlay"));
    const snapshotPath = required(options, "snapshot");
    const manifest = typeof options["manifest-dir"] === "string"
      ? loadManifestDirectory(resolve(options["manifest-dir"]))
      : await fetchManifestTables();
    const hashes = Object.values(overlay.weapons).map((weapon) => weapon.bungie_hash).filter(Number.isSafeInteger);
    const slim = slimManifest(manifest, hashes);
    writeJson(snapshotPath, {
      format: SNAPSHOT_FORMAT,
      manifest_version: slim.version,
      captured: typeof options.captured === "string" ? options.captured : new Date().toISOString().slice(0, 10),
      source: MANIFEST_SOURCE_URL,
      tables: slim.tables,
    });
    console.log("Wrote manifest snapshot " + slim.version + " with " +
      Object.keys(slim.tables.DestinyInventoryItemDefinition).length + " item definitions.");
  } else if (command === "build") {
    const catalogue = buildCatalogue({
      overlay: readOverlay(required(options, "overlay")),
      snapshot: readSnapshot(required(options, "snapshot")),
    });
    writeCatalogue(required(options, "out"), catalogue);
    console.log(JSON.stringify(catalogue.report));
  } else {
    throw new Error("Usage: build_manifest_catalogue.mjs <fetch|build> --overlay FILE --snapshot FILE [--out DIR | --manifest-dir DIR]");
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  main().catch((error) => { console.error(error.message); process.exitCode = 1; });
}
