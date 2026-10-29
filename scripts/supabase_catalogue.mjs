/** Shared Supabase REST adapter for local catalogue maintenance scripts.
 *
 * Read operations use the public publishable key and read-only RLS policies.
 * Write operations require SUPABASE_SECRET_KEY in the local shell environment;
 * never add that key to source control or GitHub Actions.
 */
const DEFAULT_SUPABASE_URL = "https://zpxeusonadqchhsspkxe.supabase.co";
const DEFAULT_SUPABASE_PUBLISHABLE_KEY = "sb_publishable_RBECWzuCIWIZXM1za1DULA_KmPmtsO1";
const PAGE_SIZE = 1000;

const TABLES = Object.freeze({
  weapons: { table: "d2wd_weapons", key: "id", directory: "data/weapons" },
  perks: { table: "d2wd_perks", key: "id", directory: "data/perks" },
  pools: { table: "d2wd_weapon_perk_pools", key: "weapon_id", directory: "data/weapon-perk-pools" },
});

export function requireSupabaseWriteKey() {
  const key = process.env.SUPABASE_SECRET_KEY || "";
  if (!key || key.startsWith("sb_publishable_")) {
    throw new Error(
      "Catalogue writes require SUPABASE_SECRET_KEY in your local shell. " +
      "Use a Supabase secret key with write privileges; do not put it in Git or GitHub Actions."
    );
  }
  if (!key.startsWith("sb_secret_") && !key.startsWith("eyJ")) {
    throw new Error("SUPABASE_SECRET_KEY must be a Supabase secret key or legacy service-role JWT.");
  }
  return key;
}

function configuration() {
  const url = (process.env.SUPABASE_URL || DEFAULT_SUPABASE_URL).replace(/\/+$/, "");
  const key = process.env.SUPABASE_PUBLISHABLE_KEY || DEFAULT_SUPABASE_PUBLISHABLE_KEY;
  if (!url.startsWith("https://")) throw new Error("SUPABASE_URL must be an HTTPS URL.");
  if (!key) throw new Error("Set SUPABASE_PUBLISHABLE_KEY to read the canonical catalogue.");
  return { url, key };
}

function legacyBearer(key, headers) {
  if (key.startsWith("eyJ")) headers.Authorization = "Bearer " + key;
}

async function readJsonResponse(response, context) {
  const body = await response.text();
  if (!response.ok) {
    throw new Error("Supabase REST " + context + " failed with HTTP " + response.status + ": " + body.slice(0, 800));
  }
  try {
    return body ? JSON.parse(body) : [];
  } catch {
    throw new Error("Supabase REST " + context + " returned invalid JSON.");
  }
}

export async function fetchCatalogueRows(kind) {
  const config = configuration();
  const metadata = TABLES[kind];
  if (!metadata) throw new Error("Unsupported catalogue kind: " + kind);
  const rows = [];
  for (let offset = 0; ; offset += PAGE_SIZE) {
    const url = new URL(config.url + "/rest/v1/" + metadata.table);
    url.searchParams.set("select", metadata.key + ",source_file,data");
    url.searchParams.set("order", metadata.key + ".asc");
    const headers = {
      apikey: config.key,
      Accept: "application/json",
      Range: offset + "-" + (offset + PAGE_SIZE - 1),
      "Range-Unit": "items",
    };
    legacyBearer(config.key, headers);
    const response = await fetch(url, { method: "GET", headers, signal: AbortSignal.timeout(60000) });
    const page = await readJsonResponse(response, metadata.table);
    if (!Array.isArray(page) || page.some((row) => !row || typeof row !== "object" || Array.isArray(row))) {
      throw new Error("Supabase table " + metadata.table + " did not return a row array.");
    }
    for (const row of page) {
      const record = row.data;
      if (!record || typeof record !== "object" || Array.isArray(record) || record[metadata.key] !== row[metadata.key]) {
        throw new Error("Supabase table " + metadata.table + " returned a row whose key disagrees with its JSON data.");
      }
      rows.push({ path: metadata.directory + "/" + row[metadata.key] + ".json", record });
    }
    if (page.length < PAGE_SIZE) break;
  }
  return rows;
}

function requireWriteKey() {
  const key = requireSupabaseWriteKey();
  const url = (process.env.SUPABASE_URL || DEFAULT_SUPABASE_URL).replace(/\/+$/, "");
  return { url, key };
}

export async function upsertCatalogueRecords(kind, records, { batchSize = 100 } = {}) {
  if (!Array.isArray(records) || records.length === 0) return;
  const config = requireWriteKey();
  const metadata = TABLES[kind];
  if (!metadata) throw new Error("Unsupported catalogue kind: " + kind);
  for (let offset = 0; offset < records.length; offset += batchSize) {
    const rows = records.slice(offset, offset + batchSize).map((record) => {
      const key = record?.[metadata.key];
      if (typeof key !== "string" || !key || key.includes("/") || key.includes("\\")) {
        throw new Error("Refusing to upsert an invalid " + metadata.key + " in " + kind + ".");
      }
      return {
        [metadata.key]: key,
        source_file: metadata.directory + "/" + key + ".json",
        data: record,
      };
    });
    const url = new URL(config.url + "/rest/v1/" + metadata.table);
    url.searchParams.set("on_conflict", metadata.key);
    const headers = {
      apikey: config.key,
      Accept: "application/json",
      "Content-Type": "application/json",
      Prefer: "resolution=merge-duplicates,return=minimal",
    };
    legacyBearer(config.key, headers);
    const response = await fetch(url, {
      method: "POST",
      headers,
      body: JSON.stringify(rows),
      signal: AbortSignal.timeout(60000),
    });
    await readJsonResponse(response, "upsert " + metadata.table);
  }
}

export async function deleteCatalogueRecords(kind, recordIds, { batchSize = 100 } = {}) {
  if (!Array.isArray(recordIds) || recordIds.length === 0) return;
  const config = requireWriteKey();
  const metadata = TABLES[kind];
  if (!metadata) throw new Error("Unsupported catalogue kind: " + kind);
  for (let offset = 0; offset < recordIds.length; offset += batchSize) {
    const batch = recordIds.slice(offset, offset + batchSize);
    if (batch.some((id) => typeof id !== "string" || !id || /[,()\\/]/.test(id))) {
      throw new Error("Refusing to delete malformed catalogue IDs.");
    }
    const url = new URL(config.url + "/rest/v1/" + metadata.table);
    url.searchParams.set(metadata.key, "in.(" + batch.join(",") + ")");
    const headers = {
      apikey: config.key,
      Accept: "application/json",
      Prefer: "return=minimal",
    };
    legacyBearer(config.key, headers);
    const response = await fetch(url, {
      method: "DELETE",
      headers,
      signal: AbortSignal.timeout(60000),
    });
    await readJsonResponse(response, "delete " + metadata.table);
  }
}
