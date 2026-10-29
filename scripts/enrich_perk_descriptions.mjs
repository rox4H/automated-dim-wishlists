#!/usr/bin/env node
/**
 * Enrich every canonical perk record from the current Bungie manifest.
 *
 * Existing non-empty descriptions are preserved. New descriptions are matched
 * by exact hash first, then by display name and compatible plug type. Catalogue
 * labels without defensible gameplay text are explicitly marked unresolved.
 *
 * Run: node --max-old-space-size=8192 scripts/enrich_perk_descriptions.mjs --write
 */
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import {
  fetchCatalogueRows,
  requireSupabaseWriteKey,
  upsertCatalogueRecords,
} from "./supabase_catalogue.mjs";

const BUNGIE_ROOT = "https://www.bungie.net";
const MANIFEST_URL = BUNGIE_ROOT + "/Platform/Destiny2/Manifest/";
export const GENERATOR_VERSION = 1;

function cleanDescription(value) {
  return String(value ?? "").replace(/\r\n/g, "\n").trim();
}

export function normalizeName(value) {
  return String(value ?? "").normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/&/g, "and")
    .replace(/[^a-z0-9]+/g, " ")
    .trim()
    .replace(/\s+/g, " ");
}

function itemName(definition) {
  return cleanDescription(definition?.displayProperties?.name);
}

function itemDescription(definition) {
  return cleanDescription(definition?.displayProperties?.description);
}

function definitionCategory(definition) {
  return [
    definition?.itemTypeDisplayName,
    definition?.plug?.plugCategoryIdentifier,
    definition?.plug?.plugCategoryHash,
  ].filter((value) => value !== undefined && value !== null).join(" ").toLowerCase();
}

function isPlausiblePerkDefinition(definition) {
  if (!itemName(definition) || !itemDescription(definition)) return false;
  if (definition?.plug && typeof definition.plug === "object") return true;
  const category = definitionCategory(definition);
  return /weapon perk|origin trait|intrinsic|barrel|magazine|battery|scope|sight|arrow|bowstring|guard|grip|stock|trait|haft|tang|blade|bolt|rail|handle|launcher/.test(category);
}

const TYPE_TERMS = {
  arrow: ["arrow"],
  barrel: ["barrel"],
  battery: ["battery"],
  blade: ["blade"],
  bolt: ["bolt"],
  bowstring: ["bowstring"],
  guard: ["guard"],
  grip: ["grip"],
  handle: ["handle"],
  haft: ["haft"],
  intrinsic: ["intrinsic"],
  "launcher-barrel": ["launcher barrel", "launcher-barrel"],
  magazine: ["magazine"],
  "origin-trait": ["origin trait"],
  rail: ["rail"],
  scope: ["scope"],
  sight: ["sight"],
  stock: ["stock"],
  tang: ["tang"],
  trait: ["trait", "weapon perks"],
  "weapon-mod": ["weapon mod"],
};

function typeScore(record, definition) {
  const wanted = record?.perk_type;
  if (!wanted || wanted === "other") return 0;
  const category = definitionCategory(definition);
  const terms = TYPE_TERMS[wanted] || [wanted.replace(/-/g, " ")];
  return terms.some((term) => category.includes(term)) ? 100 : -20;
}

export function buildDefinitionIndexes(definitions) {
  const byName = new Map();
  const byHash = definitions && typeof definitions === "object" ? definitions : {};
  for (const [hash, definition] of Object.entries(byHash)) {
    if (!isPlausiblePerkDefinition(definition)) continue;
    const key = normalizeName(itemName(definition));
    if (!key) continue;
    const candidates = byName.get(key) || [];
    candidates.push({ hash, definition, description: itemDescription(definition) });
    byName.set(key, candidates);
  }
  return { byHash, byName };
}

function chooseByName(record, candidates) {
  if (!Array.isArray(candidates) || candidates.length === 0) return null;
  const ranked = candidates.map((candidate) => ({
    ...candidate,
    score: typeScore(record, candidate.definition) +
      (candidate.definition?.plug ? 10 : 0),
  })).sort((a, b) => b.score - a.score || a.description.length - b.description.length);
  const topScore = ranked[0].score;
  const top = ranked.filter((candidate) => candidate.score === topScore);
  const counts = new Map();
  for (const candidate of top) {
    counts.set(candidate.description, (counts.get(candidate.description) || 0) + 1);
  }
  const count = Math.max(...counts.values());
  return top.find((candidate) => counts.get(candidate.description) === count) || top[0];
}

const MANUAL_DESCRIPTIONS = {
  "4-timer-refit": {
    description: "Rapid precision hits return two rounds to the magazine.",
    url: "https://www.light.gg/db/items/1266037486/4-timer-refit/",
  },
  "attrition-orbs-refit": {
    description: "Dealing sustained damage creates an Orb of Power.",
    url: "https://www.light.gg/db/items/3865920553/attrition-orbs-refit/",
  },
  "burning-ambition-refit": {
    description: "Sustained damage scorches targets; dealing damage to a scorched target applies additional scorch.",
    url: "https://www.light.gg/db/items/3865920555/burning-ambition-refit/",
  },
  "cascade-point-refit": {
    description: "Final blows or sustained precision hits with another weapon increase this weapon's rate of fire for a short duration.",
    url: "https://www.light.gg/db/items/2883096193/cascade-point-refit/",
  },
  "dark-forged-trigger": {
    description: "Removes hip-fire accuracy penalties while increasing rate of fire. At maximum stacks of Cranial Spike, hip-firing deals increased damage.",
    url: "https://www.light.gg/db/items/499501121/dark-forged-trigger/",
  },
  "destabilizing-rounds-refit": {
    description: "Defeating a target creates a Void burst; targets hit by the burst become volatile and grant Volatile Rounds for a short duration.",
    url: "https://www.light.gg/db/items/1931951169/destabilizing-rounds-refit/",
  },
  "disease-vector": {
    description: "Increases nanite damage. Enemies that die with nanites attached to them generate additional nanites.",
    url: "https://d2.destinygamewiki.com/wiki/Outbreak_Perfected_Catalyst",
  },
  "edge-of-action-catalyst": {
    description: "Defeating a Void-debuffed target grants an overshield.",
    url: "https://www.light.gg/db/search/?q=Edge%20of%20Action%20Catalyst",
  },
  "edge-of-concurrence-catalyst": {
    description: "Sprinting or becoming amplified temporarily improves reload speed and handling; the effect is stronger while amplified.",
    url: "https://www.light.gg/db/items/1731191722/edge-of-concurrence-catalyst/",
  },
  "edge-of-intent-catalyst": {
    description: "Reloading shortly after a final blow grants Cure to you and nearby allies.",
    url: "https://www.light.gg/db/items/2038196759/edge-of-intent-catalyst/",
  },
  "empty-catalyst-socket": {
    description: "Socket placeholder: no perk is selected. An Exotic catalyst can be inserted into this socket.",
    url: "https://www.bungie.net/en/Explore/Detail/DestinyInventoryItemDefinition/1649663920",
  },
  "enduring-snare-refit": {
    description: "Snareweaver traps last longer.",
    url: "https://d2.destinygamewiki.com/wiki/Wish-Keeper_Catalyst",
  },
  "feedback-refit": {
    description: "Blocking damage partially reloads this weapon from reserves.",
    url: "https://www.light.gg/db/items/3146691103/feedback-refit/",
  },
  "frenzy-refit": {
    description: "Rapid kills progressively increase reload speed for a short time.",
    url: "https://www.light.gg/db/search/?q=Frenzy%20Refit",
  },
  "hatchling-refit": {
    description: "Precision final blows or rapidly defeating targets with a non-precision weapon spawns a Threadling at the target's location.",
    url: "https://d2.destinygamewiki.com/wiki/Wish-Keeper_Catalyst",
  },
  "high-impact-reserves-refit": {
    description: "Rounds at the end of the magazine deal more damage.",
    url: "https://www.light.gg/db/items/4204405035/high-impact-reserves-refit/",
  },
  "immovable-refit": {
    description: "Projectile impacts grant increased weapon energy while you are stationary with your shield raised.",
    url: "https://d2.destinygamewiki.com/wiki/Vexcalibur_Catalyst",
  },
  "loose-change-refit": {
    description: "Applying an elemental debuff increases reload speed, aim assist, and movement while aiming down sights for a short duration.",
    url: "https://www.light.gg/db/search/?q=Loose%20Change%20Refit",
  },
  "multi-threaded-snare-refit": {
    description: "Snareweaver traps can suspend more targets.",
    url: "https://d2.destinygamewiki.com/wiki/Wish-Keeper_Catalyst",
  },
  "one-for-all-refit": {
    description: "Hitting three separate targets increases this weapon's damage for a short duration.",
    url: "https://www.light.gg/db/search/?q=One%20for%20All%20Refit",
  },
  "onslaught-refit": {
    description: "Final blows with this weapon increase its rate of fire.",
    url: "https://www.light.gg/db/items/1931951170/onslaught-refit/",
  },
  "osteo-striga-catalyst": {
    description: "Final blows with corruption return ammunition to the magazine.",
    url: "https://www.light.gg/db/items/229096166/osteo-striga-catalyst/",
  },
  "outlaw-refit": {
    description: "Precision final blows greatly decrease reload time.",
    url: "https://www.light.gg/db/search/?q=Outlaw%20Refit",
  },
  "pressurized-refit": {
    description: "This weapon becomes more accurate as the magazine gets lower.",
    url: "https://www.light.gg/db/search/?q=Pressurized%20Refit",
  },
  "rapid-hit-refit": {
    description: "Rapid precision hits temporarily increase stability and reload speed.",
    url: "https://www.light.gg/db/items/282266467/rapid-hit-refit/",
  },
  "relentless-strikes-refit": {
    description: "Landing three light-attack hits within a short time grants Sword ammunition.",
    url: "https://www.light.gg/db/items/3865920552/relentless-strikes-refit/",
  },
  "repulsor-brace-refit": {
    description: "Defeating a Void-debuffed target grants a Void overshield.",
    url: "https://www.light.gg/db/search/?q=Repulsor%20Brace%20Refit",
  },
  "robber-refit": {
    description: "Melee final blows or damage with a charged melee reloads this weapon's magazine from reserves.",
    url: "https://www.light.gg/db/items/3146691100/robber-refit/",
  },
  "stats-for-all-refit": {
    description: "Hitting three separate targets improves weapon handling, stability, reload, and range for a short duration.",
    url: "https://www.light.gg/db/search/?q=Stats%20for%20All%20Refit",
  },
  "subroutine-recursion": {
    description: "Quicksilver nanites restore more health as Nano-Assault stacks increase. Taking damage while Nano-Assault is active can generate nearby nanites.",
    url: "https://www.light.gg/db/items/3865920554/subroutine-recursion/",
  },
  "subsistence-refit": {
    description: "Defeating targets partially reloads this weapon's magazine from reserves.",
    url: "https://www.light.gg/db/search/?q=Subsistence%20Refit",
  },
  "target-lock-refit": {
    description: "Dealing sustained damage with this weapon increases its damage until the weapon is stowed or the target is defeated.",
    url: "https://www.light.gg/db/search/?q=Target%20Lock%20Refit",
  },
  "temporal-alignment-refit": {
    description: "Overcharging both Arc and Stasis modes grants a period of increased damage.",
    url: "https://d2.destinygamewiki.com/wiki/Temporal_Alignment_Refit",
  },
  "transcendent-zen-refit": {
    description: "Final blows grant bonus stats based on the weapon's damage-type alignment. Light elements grant stability and handling; Dark elements grant range and aim assist. While Transcendent, both bonuses apply.",
    url: "https://d2.destinygamewiki.com/wiki/Transcendent_Zen_Refit",
  },
  "vorpal-weapon-refit": {
    description: "Deals increased damage to bosses, vehicles, and Guardians with their Super active.",
    url: "https://d2.destinygamewiki.com/wiki/Wish-Keeper_Catalyst",
  },
  "whispered-breathing": {
    description: "Aiming this weapon without firing for a brief moment grants bonus range and precision damage while it remains zoomed in.",
    url: "https://www.light.gg/db/items/3732232161/whispered-breathing/",
  },
  "ranged-weapon": {
    description: "Catalogue label without a confirmed standalone gameplay effect. The exact perk plug definition remains unresolved.",
    url: "https://shardorkeep.com/db/perks",
    unresolved: true,
  },
};

function reconcileStaleDescriptionNotes(record) {
  const notes = Array.isArray(record.notes) ? record.notes : [];
  record.notes = notes.map((note) => String(note)
    .replace(/The canonical hash and description remain unset until historical(?:\/enhanced)? variants are resolved/gi,
      "The canonical hash remains unset until historical/enhanced variants are resolved; the populated display-name description is representative and unverified")
    .replace(/the description is unset until historical(?:\/enhanced)? variants are resolved/gi,
      "the description is populated from a candidate definition but remains unverified")
    .replace(/description remains unset until historical(?:\/enhanced)? variants are resolved/gi,
      "description is populated from a candidate definition but remains unverified")
    .replace(/Listed in the community weapon-perk index, but no exact-name match was found in the available manifest-derived lookup\. Keep the hash unset until the definition and historical\/enhanced variant are identified\./gi,
      "Listed in the community weapon-perk index; the canonical hash remains unresolved. A current manifest display-name description is populated, but hash identity and historical/enhanced behaviour remain unverified."));
}

function addNote(record, note) {
  const notes = Array.isArray(record.notes) ? record.notes : [];
  if (!notes.includes(note)) notes.push(note);
  record.notes = notes;
}

function addDescriptionSourceNote(record, result, manifestVersion) {
  const versionText = "Bungie manifest snapshot " + manifestVersion;
  if (result.method === "exact-hash") {
    addNote(record, "Description populated from " + versionText + " by exact hash " + result.hash + "; historical version and weapon-specific availability remain unverified.");
  } else if (result.method === "display-name") {
    addNote(record, "Description matched by display name in " + versionText + "; the record has not been hash-resolved or checked for historical/version-specific behaviour.");
  } else if (result.method === "base-perk") {
    addNote(record, "Description inherited from base perk " + result.baseName + " using " + versionText + "; the Refit-specific change is not independently confirmed.");
  } else if (result.method === "manual") {
    addNote(record, "Description paraphrased from " + result.url + "; exact item identity and historical/version-specific behaviour remain unverified.");
  } else if (result.method === "unresolved" && result.url) {
    addNote(record, "No standalone gameplay effect has been confirmed for this catalogue label. See " + result.url + "; keep this record unverified.");
  } else {
    addNote(record, "No matching non-empty description was found in " + versionText + "; this is an explicit review-needed placeholder, not a verified gameplay effect.");
  }
}

function refreshManifestMatchedManualDescription(record, indexes, manifestVersion) {
  const notes = Array.isArray(record.notes) ? record.notes : [];
  const manualIndex = notes.findIndex((note) =>
    String(note).startsWith("Description paraphrased from https://"),
  );
  if (manualIndex < 0) return null;
  const manifest = chooseManifestSource(record, indexes);
  if (!manifest) return null;

  record.description = cleanDescription(manifest.description);
  record.notes = notes.filter((_, index) => index !== manualIndex);
  reconcileStaleDescriptionNotes(record);
  addDescriptionSourceNote(record, manifest, manifestVersion);
  record.verification_status = "unverified";
  record.last_verified = null;
  return manifest.method;
}


function chooseManifestSource(record, indexes) {
  const storedHash = record.bungie_hash;
  if (Number.isSafeInteger(storedHash) && storedHash > 0) {
    const definition = indexes.byHash[String(storedHash)];
    if (definition && normalizeName(itemName(definition)) === normalizeName(record.name)) {
      const description = itemDescription(definition);
      if (description) return { description, method: "exact-hash", hash: String(storedHash) };
    }
  }

  const direct = chooseByName(record, indexes.byName.get(normalizeName(record.name)));
  if (direct?.description) {
    return {
      description: direct.description,
      method: "display-name",
      hash: direct.hash,
      candidateCount: (indexes.byName.get(normalizeName(record.name)) || []).length,
    };
  }

  const baseName = record.name.replace(/\s+refit$/i, "").trim();
  if (baseName && baseName !== record.name) {
    const base = chooseByName(record, indexes.byName.get(normalizeName(baseName)));
    if (base?.description) {
      return {
        description: base.description,
        method: "base-perk",
        hash: base.hash,
        baseName,
      };
    }
  }
  return null;
}

function chooseSource(record, indexes) {
  const manifest = chooseManifestSource(record, indexes);
  if (manifest) return manifest;
  const manual = MANUAL_DESCRIPTIONS[record.id];
  if (manual) return { ...manual, method: manual.unresolved ? "unresolved" : "manual" };
  return {
    method: "unresolved",
    description: "Description unavailable: no matching gameplay perk text was resolved from the current Bungie manifest. Verify this catalogue entry before treating it as a confirmed gameplay effect.",
  };
}


export function enrichPerkRecord(record, indexes, manifestVersion) {
  if (cleanDescription(record.description)) {
    return { record, method: "preserved" };
  }

  const result = chooseSource(record, indexes);
  record.description = cleanDescription(result.description);
  reconcileStaleDescriptionNotes(record);
  addDescriptionSourceNote(record, result, manifestVersion);

  record.verification_status = "unverified";
  record.last_verified = null;
  return { record, method: result.method };
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

export async function fetchCurrentManifest() {
  const response = await fetchWithRetry(MANIFEST_URL);
  const json = await response.json();
  const manifest = json?.Response;
  const path = manifest?.jsonWorldComponentContentPaths?.en?.DestinyInventoryItemDefinition;
  if (!manifest?.version || !path) {
    throw new Error("Bungie manifest response is missing DestinyInventoryItemDefinition.");
  }
  const definitionsResponse = await fetchWithRetry(BUNGIE_ROOT + path);
  const definitions = await definitionsResponse.json();
  return { version: manifest.version, definitions };
}

async function loadPerkRecords() {
  return fetchCatalogueRows("perks");
}

export async function enrichAllRecords(records, definitions, manifestVersion) {
  const indexes = buildDefinitionIndexes(definitions);
  const enriched = records.map(({ path, record }) => {
    let method = "preserved";
    if (cleanDescription(record.description)) {
      reconcileStaleDescriptionNotes(record);
      method = refreshManifestMatchedManualDescription(record, indexes, manifestVersion) || "preserved";
    } else {
      method = enrichPerkRecord(record, indexes, manifestVersion).method;
    }
    return { path, record, method };
  });

  // Report final provenance categories, rather than only the changes in this run.
  const summary = {
    total: enriched.length, preserved: 0, exactHash: 0, displayName: 0,
    manual: 0, basePerk: 0, unresolved: 0, missingAfter: 0,
  };
  for (const { record } of enriched) {
    const notes = Array.isArray(record.notes) ? record.notes.map(String) : [];
    if (notes.some((note) => note.includes("by exact hash "))) summary.exactHash++;
    else if (notes.some((note) => note.includes("matched by display name in Bungie manifest"))) summary.displayName++;
    else if (notes.some((note) => note.includes("inherited from base perk "))) summary.basePerk++;
    else if (notes.some((note) => note.startsWith("Description paraphrased from https://"))) summary.manual++;
    else if (notes.some((note) => note.includes("No standalone gameplay effect has been confirmed") || note.includes("review-needed placeholder"))) summary.unresolved++;
    else summary.preserved++;
    if (!cleanDescription(record.description)) summary.missingAfter++;
  }
  return { records: enriched.map(({ path, record }) => ({ path, record })), summary };
}


async function main() {
  const write = process.argv.includes("--write");
  if (write) requireSupabaseWriteKey();
  const { version, definitions } = await fetchCurrentManifest();
  console.log("Loaded Bungie manifest " + version + " with " + Object.keys(definitions).length + " item definitions.");
  const loaded = await loadPerkRecords();
  const { records, summary } = await enrichAllRecords(loaded, definitions, version);
  if (write) {
    await upsertCatalogueRecords("perks", records.map(({ record }) => record));
  }

  console.log(JSON.stringify({ manifestVersion: version, write, ...summary }, null, 2));
  if (summary.missingAfter > 0) {
    throw new Error(summary.missingAfter + " perk records still have an empty description.");
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch((error) => {
    console.error(error);
    process.exitCode = 1;
  });
}
