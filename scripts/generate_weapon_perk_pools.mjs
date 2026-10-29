#!/usr/bin/env node
/**
 * Generate version-specific weapon perk pools from Bungie's current JSON manifest.
 * This script never marks data verified: pool status is extraction coverage, not evidence review.
 *
 * Run: node --max-old-space-size=8192 scripts/generate_weapon_perk_pools.mjs --write
 */
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import {
  deleteCatalogueRecords,
  fetchCatalogueRows,
  requireSupabaseWriteKey,
  upsertCatalogueRecords,
} from "./supabase_catalogue.mjs";

const BUNGIE_ROOT = "https://www.bungie.net";
const MANIFEST_URL = BUNGIE_ROOT + "/Platform/Destiny2/Manifest/";
const CATALYST_EMPTY_CATEGORY = "v400.empty.exotic.masterwork";
export const GENERATOR_VERSION = 6;

const PERK_TYPE_MAP = new Map([
  ["arrow", "arrow"], ["barrel", "barrel"], ["battery", "battery"],
  ["blade", "blade"], ["bolt", "bolt"], ["bowstring", "bowstring"],
  ["guard", "guard"], ["grip", "grip"], ["handle", "handle"], ["haft", "haft"],
  ["intrinsic", "intrinsic"], ["launcher barrel", "launcher-barrel"],
  ["launcher-barrel", "launcher-barrel"], ["magazine", "magazine"],
  ["origin trait", "origin-trait"], ["rail", "rail"], ["scope", "scope"],
  ["sight", "sight"], ["stock", "stock"], ["tang", "tang"], ["trait", "trait"],
  ["weapon mod", "weapon-mod"],
]);
const ALLOWED_TYPES = new Set([
  "arrow", "barrel", "battery", "blade", "bolt", "bowstring", "guard", "grip",
  "handle", "haft", "intrinsic", "launcher-barrel", "magazine", "origin-trait",
  "other", "rail", "scope", "sight", "stock", "tang", "trait", "weapon-mod",
]);

export function slugify(value) {
  return String(value ?? "").normalize("NFKD").replace(/[\u0300-\u036f]/g, "")
    .toLowerCase().replace(/&/g, "and").replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

export function classifySocketCategory(value) {
  const name = String(value ?? "").toUpperCase().replace(/[_-]+/g, " ");
  if (name.includes("INTRINSIC TRAITS")) return "intrinsic";
  if (name.includes("WEAPON PERKS")) return "perks";
  if (/WEAPON (COSMETIC|MOD|TRACKER|MASTERWORK|ORNAMENT|MEMENTO)/.test(name)) return "ignore";
  return null;
}

/** Collect candidates from the default plug, inline plugs, and referenced plug sets. */
export function collectSocketPlugHashes(entry, plugSets) {
  const hashes = [];
  const seen = new Set();
  const missingPlugSets = [];
  const sourceHashes = [];
  let hasRandomizedSet = false;
  let hasReusableSet = false;
  let hasInlinePlugs = false;
  const add = (value) => {
    const hash = Number(value);
    if (!Number.isSafeInteger(hash) || hash <= 0 || seen.has(hash)) return;
    seen.add(hash);
    hashes.push(hash);
  };
  const resolveSet = (field) => {
    const setHash = Number(entry?.[field] ?? 0);
    if (!Number.isSafeInteger(setHash) || setHash <= 0) return;
    sourceHashes.push(setHash);
    if (field === "randomizedPlugSetHash") hasRandomizedSet = true;
    if (field === "reusablePlugSetHash") hasReusableSet = true;
    const set = plugSets?.[String(setHash)];
    if (!Array.isArray(set?.reusablePlugItems)) {
      missingPlugSets.push(setHash);
      return;
    }
    for (const plug of set.reusablePlugItems) add(plug?.plugItemHash);
  };
  resolveSet("randomizedPlugSetHash");
  resolveSet("reusablePlugSetHash");
  if (Array.isArray(entry?.reusablePlugItems) && entry.reusablePlugItems.length) {
    hasInlinePlugs = true;
    for (const plug of entry.reusablePlugItems) add(plug?.plugItemHash);
  }
  add(entry?.singleInitialItemHash);
  return {
    hashes, missingPlugSets, sourceHashes: [...new Set(sourceHashes)],
    hasRandomizedSet, hasReusableSet, hasInlinePlugs,
  };
}

export function hasSocketSource(entry, collected) {
  // A category can list reserved sockets whose plug set exists but contains no
  // candidate plugs. Treat those as unused sockets, not incomplete perk columns.
  // A missing referenced plug set remains an explicit extraction gap.
  return collected.hashes.length > 0 || collected.missingPlugSets.length > 0;
}

export function isIgnorablePlug(definition, options = {}) {
  const name = String(definition?.displayProperties?.name ?? "").trim();
  const typeName = String(definition?.itemTypeDisplayName ?? "").trim();
  const category = String(definition?.plug?.plugCategoryIdentifier ?? "").trim();
  if (!name) return true;
  if (/^empty\b/i.test(name) || /tracker$/i.test(name)) return true;
  const normalizedType = typeName.toLowerCase();
  const normalizedCategory = category.toLowerCase();
  if (/shader|ornament|projection|ghost|memento|cosmetic|tracker/.test(normalizedType)) return true;
  if (/shader|ornament|projection|ghost|memento|cosmetic|tracker/.test(normalizedCategory)) return true;
  if (/masterwork/i.test(normalizedType) && !options.allowCatalyst) return true;
  if (/masterwork/i.test(normalizedCategory) && !options.allowCatalyst) return true;
  if (/\bmod\b|weapon.?mod/i.test(normalizedType)) return true;
  if (/catalyst/i.test(name) && !options.allowCatalyst) return true;
  if (/^v\d+\.plugs\.weapons\.masterworks?\.stat\./i.test(category)) return true;
  return false;
}

export function inferPerkType(definition, override = null) {
  if (override && ALLOWED_TYPES.has(override)) return override;
  const name = String(definition?.itemTypeDisplayName ?? "").trim().toLowerCase();
  const category = String(definition?.plug?.plugCategoryIdentifier ?? "").trim().toLowerCase();
  if (/origin[ ._-]?trait/.test(name) || /origin[ ._-]?trait/.test(category)) return "origin-trait";
  if (/intrinsic/.test(name) || /intrinsics/.test(category)) return "intrinsic";
  if (PERK_TYPE_MAP.has(name)) return PERK_TYPE_MAP.get(name);
  for (const [label, type] of PERK_TYPE_MAP.entries()) {
    if (label.length > 5 && name.includes(label)) return type;
  }
  if (/barrel|scopes|launcher-barrel/.test(category)) return "barrel";
  if (/magazine|magazines|battery/.test(category)) return "magazine";
  if (/trait|perks/.test(category)) return "trait";
  return "other";
}

export function shouldPreservePool(existing, force = false) {
  if (!existing || force || existing.pool_status === "unknown") return false;
  // A verified pool is human-reviewed and must not be overwritten by manifest generation.
  if (existing.verification_status === "verified") return true;
  const notes = Array.isArray(existing.notes) ? existing.notes : [];
  const generatedByThisScript = notes.some((note) =>
    String(note).startsWith("Generated from Bungie's live manifest snapshot "),
  );
  const hasCurrentVersion = notes.includes("Pool extraction generator version: " + GENERATOR_VERSION + ".");
  // Preserve manual/unmarked records; regenerate generated unverified pools when the
  // extractor version changes so algorithm fixes reach previously generated files.
  return !generatedByThisScript || hasCurrentVersion;
}

export function inferUnclassifiedSocketCategory(candidateDefinitions, missingPlugSets = []) {
  const usable = candidateDefinitions.filter((definition) => !isIgnorablePlug(definition));
  const knownRollTypes = usable.some((plug) => {
    const type = String(plug.itemTypeDisplayName ?? "").toLowerCase();
    return ["barrel", "scope", "sight", "magazine", "battery", "blade", "bolt",
      "bowstring", "guard", "grip", "handle", "haft", "launcher barrel", "rail",
      "stock", "tang", "trait", "origin trait", "intrinsic"].includes(type);
  });
  if (knownRollTypes) {
    return usable.some((plug) => /intrinsic/i.test(String(plug.itemTypeDisplayName ?? "")))
      ? "intrinsic" : "perks";
  }
  // Do not silently drop a potentially valid, non-cosmetic plug just because its
  // socket category name is new or missing from the manifest snapshot.
  return usable.length > 0 || missingPlugSets.length > 0 ? "perks" : null;
}

export function isPrunableGeneratedPerkRecord(record) {
  const notes = Array.isArray(record?.notes) ? record.notes : [];
  return record?.verification_status === "unverified" &&
    record?.last_verified === null &&
    notes.length === 2 &&
    String(notes[0]).startsWith("Seeded automatically from the Bungie manifest snapshot ") &&
    notes[1] === "Verify this exact definition, display name and perk type before marking the record verified.";
}

export function shouldRetainEmptySlot(perkIds, missingPlugSets = [], unresolvedCandidateCount = 0) {
  return perkIds.length === 0 && (missingPlugSets.length > 0 || unresolvedCandidateCount > 0);
}

export function createUnknownPool(weapon, reason, manifestVersion = null, accessed = null) {
  const hash = Number.isSafeInteger(weapon?.bungie_hash) ? weapon.bungie_hash : null;
  const notes = [
    "Unknown means the perk pool could not be resolved from the available manifest data; it does not mean the weapon has no perks.",
    String(reason),
  ];
  if (manifestVersion) notes.push("Manifest snapshot checked: " + manifestVersion + ".");
  return {
    weapon_id: weapon.id, weapon_hash: hash, version_label: weapon.season ?? null,
    pool_status: "unknown", verification_status: "unverified", last_verified: null,
    slots: [{
      id: "unresearched", label: "Perk pool not resolved", selection_mode: "unknown",
      perk_ids: [], notes: [String(reason)],
    }],
    notes,
    sources: hash !== null && manifestVersion ? [{
      url: MANIFEST_URL,
      claim: "Manifest snapshot " + manifestVersion + " was checked for exact DestinyInventoryItemDefinition hash " + hash + ", but a usable weapon perk socket set was not resolved.",
      accessed: accessed ?? new Date().toISOString().slice(0, 10),
    }] : [],
  };
}

async function fetchWithRetry(url, retries = 5) {
  let lastError;
  for (let attempt = 1; attempt <= retries; attempt++) {
    try {
      const response = await fetch(url, { signal: AbortSignal.timeout(300000) });
      if (response.ok) return response;
      const error = new Error("HTTP " + response.status + " fetching " + url);
      if (response.status < 500 || attempt === retries) throw error;
      lastError = error;
    } catch (error) {
      lastError = error;
      if (attempt === retries) break;
    }
    const delayMs = Math.min(30000, 1500 * 2 ** (attempt - 1));
    console.log("Fetch attempt " + attempt + " failed; retrying in " + delayMs + " ms.");
    await new Promise((resolvePromise) => setTimeout(resolvePromise, delayMs));
  }
  throw lastError ?? new Error("Fetch failed for " + url);
}

async function fetchManifestTables() {
  const response = await fetchWithRetry(MANIFEST_URL);
  const json = await response.json();
  const manifest = json?.Response;
  const paths = manifest?.jsonWorldComponentContentPaths?.en;
  if (!manifest?.version || !paths?.DestinyInventoryItemDefinition ||
      !paths?.DestinyPlugSetDefinition || !paths?.DestinySocketCategoryDefinition) {
    throw new Error("Bungie manifest response is missing required component paths.");
  }
  const tableNames = [
    "DestinyInventoryItemDefinition", "DestinyPlugSetDefinition",
    "DestinySocketCategoryDefinition", "DestinySocketTypeDefinition",
  ];
  const loaded = await Promise.all(tableNames.map(async (name) => {
    const path = paths[name];
    if (!path) return [name, {}];
    const tableResponse = await fetchWithRetry(BUNGIE_ROOT + path);
    return [name, await tableResponse.json()];
  }));
  return { version: manifest.version, tables: Object.fromEntries(loaded) };
}

function makeNameLookup(records) {
  const byId = new Map();
  const byHash = new Map();
  const bySlug = new Map();
  for (const { record } of records) {
    byId.set(record.id, record);
    if (Number.isSafeInteger(record.bungie_hash)) {
      const entries = byHash.get(record.bungie_hash) ?? [];
      entries.push(record);
      byHash.set(record.bungie_hash, entries);
    }
    const nameSlug = slugify(record.name);
    const entries = bySlug.get(nameSlug) ?? [];
    entries.push(record);
    bySlug.set(nameSlug, entries);
  }
  return { byId, byHash, bySlug };
}

function createPerkResolver(perkRecords, manifestItems, manifestVersion, generatedPerks) {
  const lookup = makeNameLookup(perkRecords);
  const resolvedHashes = new Map();
  function resolve(hash, options = {}) {
    const numericHash = Number(hash);
    if (!Number.isSafeInteger(numericHash) || numericHash <= 0) return null;
    if (resolvedHashes.has(numericHash)) return resolvedHashes.get(numericHash);
    const definition = manifestItems[String(numericHash)];
    const name = String(definition?.displayProperties?.name ?? "").trim();
    if (!name || isIgnorablePlug(definition, { allowCatalyst: options.allowCatalyst === true })) {
      resolvedHashes.set(numericHash, null);
      return null;
    }

    const exactHashRecords = lookup.byHash.get(numericHash) ?? [];
    if (exactHashRecords.length === 1) {
      resolvedHashes.set(numericHash, exactHashRecords[0].id);
      return exactHashRecords[0].id;
    }
    if (exactHashRecords.length > 1) {
      const exactName = exactHashRecords.filter((record) => record.name === name);
      if (exactName.length === 1) {
        resolvedHashes.set(numericHash, exactName[0].id);
        return exactName[0].id;
      }
    }

    const normalizedName = slugify(name);
    const existingBySlugId = lookup.byId.get(normalizedName);
    if (existingBySlugId && slugify(existingBySlugId.name) === normalizedName) {
      resolvedHashes.set(numericHash, existingBySlugId.id);
      return existingBySlugId.id;
    }

    const sameNameRecords = (lookup.bySlug.get(normalizedName) ?? [])
      .filter((record) => record.name === name || slugify(record.name) === normalizedName);
    if (sameNameRecords.length === 1) {
      resolvedHashes.set(numericHash, sameNameRecords[0].id);
      return sameNameRecords[0].id;
    }

    let id = normalizedName || ("perk-" + numericHash);
    if (lookup.byId.has(id) && slugify(lookup.byId.get(id).name) !== normalizedName) id = id + "-" + numericHash;
    if (lookup.byId.has(id)) {
      const existing = lookup.byId.get(id);
      if (existing.name === name || slugify(existing.name) === normalizedName) {
        resolvedHashes.set(numericHash, existing.id);
        return existing.id;
      }
      id = id + "-" + numericHash;
    }

    const record = {
      id, name, bungie_hash: numericHash,
      perk_type: inferPerkType(definition, options.forceType ?? null),
      description: String(definition?.displayProperties?.description ?? "").trim() || null,
      verification_status: "unverified", last_verified: null,
      notes: [
        "Seeded automatically from the Bungie manifest snapshot " + manifestVersion + " while resolving a weapon-version perk pool.",
        "Verify this exact definition, display name and perk type before marking the record verified.",
      ],
    };
    lookup.byId.set(id, record);
    const slugEntries = lookup.bySlug.get(normalizedName) ?? [];
    slugEntries.push(record);
    lookup.bySlug.set(normalizedName, slugEntries);
    const hashEntries = lookup.byHash.get(numericHash) ?? [];
    hashEntries.push(record);
    lookup.byHash.set(numericHash, hashEntries);
    generatedPerks.set(id, record);
    resolvedHashes.set(numericHash, id);
    return id;
  }
  return { resolve, lookup };
}

function socketCategoryName(socketCategoryDefinitions, hash) {
  return String(socketCategoryDefinitions?.[String(hash)]?.displayProperties?.name ?? "").trim();
}
function socketTypeName(socketTypeDefinitions, hash) {
  return String(socketTypeDefinitions?.[String(hash)]?.displayProperties?.name ?? "").trim();
}
function isCatalystSocket(entry, manifestItems) {
  const defaultPlug = manifestItems[String(entry?.singleInitialItemHash ?? 0)];
  return defaultPlug?.plug?.plugCategoryIdentifier === CATALYST_EMPTY_CATEGORY ||
    /^Empty Catalyst Socket$/i.test(String(defaultPlug?.displayProperties?.name ?? "").trim());
}
function getSlotLabel(candidateDefinitions, category, socketTypeFallback) {
  if (category === "intrinsic") return "Intrinsic";
  const categories = candidateDefinitions.map((definition) => String(definition?.plug?.plugCategoryIdentifier ?? ""));
  if (categories.some((value) => /origin[._ -]?trait/i.test(value))) return "Origin Trait";
  const labels = [...new Set(candidateDefinitions
    .map((definition) => String(definition?.itemTypeDisplayName ?? "").trim())
    .filter(Boolean))];
  if (labels.length === 1) return labels[0];
  if (labels.length > 1) return labels.join(" / ");
  return socketTypeFallback || "Perk";
}

export function buildPoolForWeapon(weapon, manifest, perkResolver, options = {}) {
  const accessed = options.accessed;
  const manifestVersion = manifest.version;
  const items = manifest.tables.DestinyInventoryItemDefinition;
  const plugSets = manifest.tables.DestinyPlugSetDefinition;
  const categoryDefinitions = manifest.tables.DestinySocketCategoryDefinition;
  const typeDefinitions = manifest.tables.DestinySocketTypeDefinition;
  const hash = Number.isSafeInteger(weapon.bungie_hash) ? weapon.bungie_hash : null;
  if (hash === null) return createUnknownPool(weapon, "The weapon record has no Bungie item hash, so an exact definition cannot be selected.", manifestVersion, accessed);
  const definition = items[String(hash)];
  if (!definition) return createUnknownPool(weapon, "The exact Bungie item hash is not present in the current manifest snapshot.", manifestVersion, accessed);
  const entries = definition?.sockets?.socketEntries;
  if (!Array.isArray(entries) || entries.length === 0) {
    return createUnknownPool(weapon, "The exact item definition has no resolved socket entries in the current manifest snapshot.", manifestVersion, accessed);
  }

  const categoryByIndex = new Map();
  for (const category of definition?.sockets?.socketCategories ?? []) {
    const name = socketCategoryName(categoryDefinitions, category.socketCategoryHash);
    for (const index of category.socketIndexes ?? []) categoryByIndex.set(index, name);
  }
  const catalystIndexes = new Set();
  for (let i = 0; i < entries.length; i++) if (isCatalystSocket(entries[i], items)) catalystIndexes.add(i);

  const preparedSlots = [];
  let incomplete = false;
  let relevantSocketCount = 0;
  let unknownCategoryCount = 0;

  for (let i = 0; i < entries.length; i++) {
    const entry = entries[i];
    const categoryName = categoryByIndex.get(i) ?? "";
    const categoryClassification = classifySocketCategory(categoryName);
    if (categoryClassification === "ignore") continue;
    let category = categoryClassification;
    const collected = collectSocketPlugHashes(entry, plugSets);

    // Some definitions have category indexes for unused sockets. If the socket
    // has no default, inline plugs, or plug-set references, it is not a missing
    // perk pool and should not downgrade an otherwise fully extracted weapon.
    if (!hasSocketSource(entry, collected)) continue;

    if (catalystIndexes.has(i)) {
      const candidates = [];
      const seen = new Set();
      let unresolvedCatalystCandidates = 0;
      for (const candidateHash of collected.hashes) {
        const plug = items[String(candidateHash)];
        if (!plug) { incomplete = true; unresolvedCatalystCandidates++; continue; }
        if (isIgnorablePlug(plug, { allowCatalyst: true })) continue;
        const id = perkResolver.resolve(candidateHash, { allowCatalyst: true, forceType: "other" });
        if (!id) { incomplete = true; unresolvedCatalystCandidates++; continue; }
        if (!seen.has(id)) { candidates.push(id); seen.add(id); }
      }
      if (collected.missingPlugSets.length) incomplete = true;
      if (candidates.length) {
        preparedSlots.push({
          index: i, category: "catalyst", baseLabel: "Exotic Catalyst",
          slotIdBase: "catalyst", perkIds: candidates,
          isChoice: collected.hasRandomizedSet || collected.hasReusableSet || candidates.length > 1,
          notes: ["Catalyst socket for this exact weapon definition; kept separate from standard random-roll columns."],
        });
      } else if (shouldRetainEmptySlot([], collected.missingPlugSets, unresolvedCatalystCandidates)) {
        incomplete = true;
        preparedSlots.push({
          index: i, category: "catalyst", baseLabel: "Exotic Catalyst",
          slotIdBase: "catalyst", perkIds: [], isChoice: false,
          notes: ["Catalyst socket candidates could not be fully resolved from the manifest; manual review is required."],
        });
      }
      continue;
    }

    if (!category) {
      const possible = collected.hashes.map((candidateHash) => items[String(candidateHash)]).filter(Boolean);
      category = inferUnclassifiedSocketCategory(possible, collected.missingPlugSets);
      if (!category) continue;
      incomplete = true;
      unknownCategoryCount++;
    }

    relevantSocketCount++;
    if (collected.missingPlugSets.length) incomplete = true;
    const candidateDefinitions = [];
    const perkIds = [];
    const seenIds = new Set();
    let unresolvedCandidateCount = 0;

    for (const candidateHash of collected.hashes) {
      const plug = items[String(candidateHash)];
      if (!plug) { incomplete = true; unresolvedCandidateCount++; continue; }
      if (isIgnorablePlug(plug)) continue;
      const name = String(plug?.displayProperties?.name ?? "").trim();
      if (!name) { incomplete = true; unresolvedCandidateCount++; continue; }
      candidateDefinitions.push(plug);
      const id = perkResolver.resolve(candidateHash);
      if (!id) { incomplete = true; unresolvedCandidateCount++; continue; }
      if (!seenIds.has(id)) { perkIds.push(id); seenIds.add(id); }
    }

    if (perkIds.length === 0) {
      if (!shouldRetainEmptySlot(perkIds, collected.missingPlugSets, unresolvedCandidateCount)) continue;
      incomplete = true;
    }
    const typeFallback = socketTypeName(typeDefinitions, entry?.socketTypeHash);
    const baseLabel = getSlotLabel(candidateDefinitions, category, typeFallback);
    preparedSlots.push({
      index: i, category, baseLabel,
      slotIdBase: category === "intrinsic" ? "intrinsic" : slugify(baseLabel) || ("socket-" + (i + 1)),
      perkIds,
      isChoice: collected.hasRandomizedSet || collected.hasReusableSet || collected.hasInlinePlugs || perkIds.length > 1,
      notes: [
        "Manifest socket index " + i + "; category " + (categoryName || "unclassified") + ".",
        "Candidate hashes include the socket default, inline reusable plugs, and referenced reusable/randomized plug sets; duplicate canonical perk IDs are removed.",
      ].concat(
        collected.missingPlugSets.length ? ["Unable to resolve referenced plug-set hash(es): " + collected.missingPlugSets.join(", ") + "."] : [],
        unresolvedCandidateCount ? [String(unresolvedCandidateCount) + " candidate plug(s) could not be resolved; this slot needs further review."] : [],
        categoryName ? [] : ["The socket category was inferred from plug item types; verify this classification."],
      ),
    });
  }

  if (preparedSlots.length === 0) {
    return createUnknownPool(weapon, "No intrinsic, weapon-perk, or catalyst sockets could be classified in the exact item definition.", manifestVersion, accessed);
  }
  const baseLabelCounts = new Map();
  for (const slot of preparedSlots) baseLabelCounts.set(slot.baseLabel, (baseLabelCounts.get(slot.baseLabel) ?? 0) + 1);
  const baseLabelOrdinal = new Map();
  const idCounts = new Map();
  const slots = preparedSlots.map((slot) => {
    const ordinal = (baseLabelOrdinal.get(slot.baseLabel) ?? 0) + 1;
    baseLabelOrdinal.set(slot.baseLabel, ordinal);
    let label = slot.baseLabel;
    if (baseLabelCounts.get(slot.baseLabel) > 1) label = slot.baseLabel + " " + ordinal;
    let slotId = slot.slotIdBase;
    if (slot.category === "perks" && baseLabelCounts.get(slot.baseLabel) > 1) slotId = (slugify(slot.baseLabel) || slotId) + "-" + ordinal;
    const idOrdinal = (idCounts.get(slotId) ?? 0) + 1;
    idCounts.set(slotId, idOrdinal);
    if (idOrdinal > 1) slotId += "-" + idOrdinal;
    return {
      id: slotId, label,
      selection_mode: slot.perkIds.length === 0 ? "unknown" : (slot.isChoice ? "choose-one" : "fixed"),
      perk_ids: slot.perkIds,
      notes: slot.notes,
    };
  });

  const usableSlots = slots.filter((slot) => slot.perk_ids.length > 0).length;
  const hasEmptySlot = slots.some((slot) => slot.perk_ids.length === 0);
  if (usableSlots === 0 || (relevantSocketCount === 0 && catalystIndexes.size === 0)) {
    return createUnknownPool(weapon, "Socket entries were present, but no usable intrinsic/perk/catalyst candidates were resolved.", manifestVersion, accessed);
  }

  const poolStatus = incomplete || hasEmptySlot || unknownCategoryCount > 0 ? "partial" : "complete";
  const sourceClaim = "Socket candidate plugs extracted from Bungie's DestinyInventoryItemDefinition hash " +
    hash + " and its referenced DestinyPlugSetDefinition entries in manifest snapshot " + manifestVersion +
    ". The extraction is not an independent audit of historical availability or roll legality.";
  return {
    weapon_id: weapon.id, weapon_hash: hash, version_label: weapon.season ?? null,
    pool_status: poolStatus, verification_status: "unverified", last_verified: null,
    slots,
    notes: [
      "Generated from Bungie's live manifest snapshot " + manifestVersion + " for this exact weapon item hash.",
      "Pool extraction generator version: " + GENERATOR_VERSION + ".",
      "Pool completeness describes extraction coverage of resolved manifest sockets, not human verification or proof that the item was obtainable in every season shown in this repository.",
      "Intrinsic, weapon-perk, origin-trait and identified exotic-catalyst sockets are included. Cosmetic, tracker, universal weapon-mod and stat-masterwork sockets are excluded.",
      "Candidate entries from default, inline reusable, reusable-set and randomized-set references are merged and deduplicated by canonical perk ID. A human review is still required before setting verification_status to verified.",
    ].concat(unknownCategoryCount ? [String(unknownCategoryCount) + " socket(s) were classified from plug item type because the socket category name was unavailable."] : []),
    sources: [{ url: MANIFEST_URL, claim: sourceClaim, accessed }],
  };
}

async function main() {
  if (!process.argv.includes("--write")) {
    throw new Error("Refusing to write without explicit --write. Run: node --max-old-space-size=8192 scripts/generate_weapon_perk_pools.mjs --write");
  }
  requireSupabaseWriteKey();
  const accessed = new Date().toISOString().slice(0, 10);
  console.log("Downloading Bungie manifest tables. This may take several minutes.");
  const manifest = await fetchManifestTables();
  const items = manifest.tables.DestinyInventoryItemDefinition;
  const plugSets = manifest.tables.DestinyPlugSetDefinition;
  if (!items || Object.keys(items).length === 0 || !plugSets) throw new Error("Required manifest tables are empty.");

  const [weapons, perkRecords, existingPools] = await Promise.all([
    fetchCatalogueRows("weapons"),
    fetchCatalogueRows("perks"),
    fetchCatalogueRows("pools"),
  ]);
  const existingPoolsById = new Map(existingPools.map(({ record }) => [record.weapon_id, record]));
  const generatedPerks = new Map();
  const perkResolver = createPerkResolver(perkRecords, items, manifest.version, generatedPerks);
  const poolRecords = new Map();
  const changedPools = [];
  const stats = { weapons: weapons.length, matchedDefinitions: 0, complete: 0, partial: 0, unknown: 0, newPerks: 0, prunedPerks: 0, preserved: 0, created: 0, updated: 0 };
  const force = process.argv.includes("--force");

  for (let index = 0; index < weapons.length; index++) {
    const weapon = weapons[index].record;
    const existing = existingPoolsById.get(weapon.id) ?? null;
    if (shouldPreservePool(existing, force)) {
      poolRecords.set(weapon.id, existing);
      stats.preserved++;
      continue;
    }
    if (Number.isSafeInteger(weapon.bungie_hash) && items[String(weapon.bungie_hash)]) stats.matchedDefinitions++;
    const pool = buildPoolForWeapon(weapon, manifest, perkResolver, { accessed });
    poolRecords.set(weapon.id, pool);
    changedPools.push(pool);
    if (pool.pool_status === "complete") stats.complete++;
    else if (pool.pool_status === "partial") stats.partial++;
    else stats.unknown++;
    if (existing) stats.updated++; else stats.created++;
    if ((index + 1) % 250 === 0) console.log("Processed " + (index + 1) + "/" + weapons.length + " weapon versions.");
  }

  const referencedPerkIds = new Set();
  for (const pool of poolRecords.values()) {
    for (const slot of pool.slots ?? []) {
      for (const id of slot.perk_ids ?? []) referencedPerkIds.add(id);
    }
  }
  const prunedPerkIds = [];
  for (const { record } of perkRecords) {
    if (isPrunableGeneratedPerkRecord(record) &&
        !referencedPerkIds.has(record.id) &&
        !generatedPerks.has(record.id)) {
      prunedPerkIds.push(record.id);
      stats.prunedPerks++;
    }
  }
  stats.newPerks = generatedPerks.size;
  console.log("Writing " + changedPools.length + " changed perk pools and " + generatedPerks.size + " generated perks to Supabase.");
  await upsertCatalogueRecords("pools", changedPools);
  await upsertCatalogueRecords("perks", [...generatedPerks.values()]);
  await deleteCatalogueRecords("perks", prunedPerkIds);
  const missingPoolIds = weapons.map((item) => item.record.id).filter((id) => !poolRecords.has(id));
  if (missingPoolIds.length) throw new Error("Internal coverage failure: " + missingPoolIds.length + " weapon versions have no pool record.");

  console.log(JSON.stringify({
    manifestVersion: manifest.version,
    weapons: stats.weapons,
    matchedDefinitions: stats.matchedDefinitions,
    poolRecords: poolRecords.size,
    complete: [...poolRecords.values()].filter((pool) => pool.pool_status === "complete").length,
    partial: [...poolRecords.values()].filter((pool) => pool.pool_status === "partial").length,
    unknown: [...poolRecords.values()].filter((pool) => pool.pool_status === "unknown").length,
    newPerkRecords: stats.newPerks,
    prunedPerkRecords: stats.prunedPerks,
    preservedNonUnknownPools: stats.preserved,
    createdPoolFiles: stats.created,
    updatedPoolFiles: stats.updated,
  }, null, 2));
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  main().catch((error) => { console.error(error); process.exitCode = 1; });
}
