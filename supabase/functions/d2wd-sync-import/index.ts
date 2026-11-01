import { createClient } from "npm:@supabase/supabase-js@2";

const EXPECTED_AUDIENCE = "urn:rox4h:d2wd-supabase-sync";
const EXPECTED_ISSUER = "https://token.actions.githubusercontent.com";
const EXPECTED_REPOSITORY = "rox4H/automated-dim-wishlists";
const EXPECTED_REPOSITORY_ID = "1438586516";
const EXPECTED_REF = "refs/heads/main";
const EXPECTED_WORKFLOW_REF =
  "rox4H/automated-dim-wishlists/.github/workflows/sync-bungie-catalogue.yml@refs/heads/main";
const EXPECTED_ENVIRONMENT = "d2wd-catalogue-sync";
const MAX_BATCH_SIZE = 100;
const MAX_EVIDENCE_BATCH = 250;

let cachedJwks: { keys: JsonWebKey[]; fetchedAt: number } | null = null;
let cachedAdmin: ReturnType<typeof createClient> | null = null;

type JsonRecord = Record<string, unknown>;

function jsonResponse(status: number, body: JsonRecord): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": "no-store",
    },
  });
}

function decodeBase64Url(value: string): Uint8Array {
  const base64 = value.replace(/-/g, "+").replace(/_/g, "/");
  const padded = base64 + "=".repeat((4 - (base64.length % 4)) % 4);
  const binary = atob(padded);
  return Uint8Array.from(binary, (char) => char.charCodeAt(0));
}

function decodeJwtPart<T>(value: string): T {
  return JSON.parse(new TextDecoder().decode(decodeBase64Url(value))) as T;
}

async function getGitHubJwks(forceRefresh = false): Promise<JsonWebKey[]> {
  const now = Date.now();
  if (!forceRefresh && cachedJwks && now - cachedJwks.fetchedAt < 60 * 60 * 1000) {
    return cachedJwks.keys;
  }

  const response = await fetch("https://token.actions.githubusercontent.com/.well-known/jwks", {
    headers: { Accept: "application/json" },
    signal: AbortSignal.timeout(10000),
  });
  if (!response.ok) throw new Error("Unable to load GitHub OIDC signing keys.");
  const document = await response.json() as { keys?: JsonWebKey[] };
  if (!Array.isArray(document.keys) || document.keys.length === 0) {
    throw new Error("GitHub OIDC returned an empty JWKS.");
  }
  cachedJwks = { keys: document.keys, fetchedAt: now };
  return document.keys;
}

function validateGitHubClaims(claims: JsonRecord): void {
  const now = Math.floor(Date.now() / 1000);
  const audiences = Array.isArray(claims.aud) ? claims.aud : [claims.aud];

  if (claims.iss !== EXPECTED_ISSUER) throw new Error("OIDC issuer is not trusted.");
  if (!audiences.includes(EXPECTED_AUDIENCE)) throw new Error("OIDC audience is not trusted.");
  if (String(claims.repository) !== EXPECTED_REPOSITORY ||
      String(claims.repository_id) !== EXPECTED_REPOSITORY_ID) {
    throw new Error("OIDC repository identity is not trusted.");
  }
  if (claims.ref !== EXPECTED_REF || claims.workflow_ref !== EXPECTED_WORKFLOW_REF) {
    throw new Error("OIDC token did not originate from the protected main-branch sync workflow.");
  }
  if (claims.environment !== EXPECTED_ENVIRONMENT ||
      claims.sub !== "repo:rox4H/automated-dim-wishlists:environment:d2wd-catalogue-sync") {
    throw new Error("OIDC deployment environment is not trusted.");
  }
  if (!["schedule", "workflow_dispatch"].includes(String(claims.event_name))) {
    throw new Error("OIDC event type is not allowed.");
  }

  const exp = Number(claims.exp);
  const nbf = Number(claims.nbf ?? 0);
  const iat = Number(claims.iat);
  if (!Number.isFinite(exp) || exp <= now) throw new Error("OIDC token has expired.");
  if (!Number.isFinite(iat) || iat > now + 60 || now - iat > 12 * 60) {
    throw new Error("OIDC token issue time is invalid or too old.");
  }
  if (nbf > now + 60) throw new Error("OIDC token is not active yet.");
}

async function verifyGitHubOidcToken(token: string): Promise<JsonRecord> {
  const parts = token.split(".");
  if (parts.length !== 3) throw new Error("Malformed GitHub OIDC token.");
  const header = decodeJwtPart<{ alg?: string; kid?: string; typ?: string }>(parts[0]);
  if (header.alg !== "RS256" || typeof header.kid !== "string") {
    throw new Error("Unsupported GitHub OIDC signing algorithm.");
  }

  const keys = await getGitHubJwks();
  let jwk = keys.find((key) => (key as JsonRecord).kid === header.kid &&
    (key as JsonRecord).alg === "RS256" && (key as JsonRecord).kty === "RSA");
  if (!jwk) {
    const refreshed = await getGitHubJwks(true);
    jwk = refreshed.find((key) => (key as JsonRecord).kid === header.kid &&
      (key as JsonRecord).alg === "RS256" && (key as JsonRecord).kty === "RSA");
  }
  if (!jwk) throw new Error("GitHub OIDC signing key was not found.");

  const publicKey = await crypto.subtle.importKey(
    "jwk",
    jwk,
    { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" },
    false,
    ["verify"],
  );
  const signingInput = new TextEncoder().encode(parts[0] + "." + parts[1]);
  const signature = decodeBase64Url(parts[2]);
  const valid = await crypto.subtle.verify(
    "RSASSA-PKCS1-v1_5",
    publicKey,
    signature,
    signingInput,
  );
  if (!valid) throw new Error("GitHub OIDC signature verification failed.");

  const claims = decodeJwtPart<JsonRecord>(parts[1]);
  validateGitHubClaims(claims);
  return claims;
}

function getAdminClient(): ReturnType<typeof createClient> {
  if (cachedAdmin) return cachedAdmin;

  const url = Deno.env.get("SUPABASE_URL");
  const secretDictionary = JSON.parse(Deno.env.get("SUPABASE_SECRET_KEYS") ?? "{}") as Record<string, string>;
  const key = secretDictionary.default || Deno.env.get("SUPABASE_SERVICE_ROLE_KEY");
  if (!url || !key) throw new Error("Supabase server-side secret configuration is unavailable.");

  cachedAdmin = createClient(url, key, {
    auth: { autoRefreshToken: false, persistSession: false },
    global: { headers: { "X-D2WD-Sync-Gateway": "github-oidc" } },
  });
  return cachedAdmin;
}

function requireString(value: unknown, label: string, maxLength = 512): string {
  if (typeof value !== "string" || value.trim().length === 0 || value.length > maxLength) {
    throw new Error("Invalid " + label + ".");
  }
  return value;
}

function requireRunId(value: unknown): string {
  const runId = requireString(value, "run ID", 64);
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(runId)) {
    throw new Error("Invalid run ID.");
  }
  return runId;
}

function requireBatch(value: unknown): JsonRecord[] {
  if (!Array.isArray(value) || value.length === 0 || value.length > MAX_BATCH_SIZE) {
    throw new Error("records must contain between 1 and " + MAX_BATCH_SIZE + " entries.");
  }
  if (value.some((item) => !item || typeof item !== "object" || Array.isArray(item))) {
    throw new Error("Every record must be a JSON object.");
  }
  return value as JsonRecord[];
}

function countPoolPerks(pool: JsonRecord): number {
  const slots = Array.isArray(pool.slots) ? pool.slots as JsonRecord[] : [];
  return slots.reduce((total, slot) =>
    total + (Array.isArray(slot.perk_ids) ? slot.perk_ids.length : 0), 0);
}

function poolSummary(pool: JsonRecord): JsonRecord {
  const slots = Array.isArray(pool.slots) ? pool.slots as JsonRecord[] : [];
  return {
    pool_status: pool.pool_status ?? null,
    verification_status: pool.verification_status ?? null,
    weapon_hash: pool.weapon_hash ?? null,
    slot_count: slots.length,
    perk_count: countPoolPerks(pool),
  };
}

function isGeneratedPool(pool: JsonRecord): boolean {
  const notes = Array.isArray(pool.notes) ? pool.notes : [];
  return notes.some((note) => typeof note === "string" &&
    note.startsWith("Generated from Bungie's live manifest snapshot "));
}

function validateGeneratedPool(
  pool: JsonRecord,
  manifestVersion: string,
  generatorVersion: string,
): void {
  if (pool.verification_status !== "unverified" || pool.last_verified !== null) {
    throw new Error("Automated sync may only upload unverified pools with last_verified=null.");
  }
  if (!["complete", "partial", "unknown"].includes(String(pool.pool_status))) {
    throw new Error("Invalid pool_status.");
  }
  if (typeof pool.weapon_id !== "string" || !pool.weapon_id || pool.weapon_id.length > 180) {
    throw new Error("Invalid pool weapon_id.");
  }
  if (!Number.isSafeInteger(pool.weapon_hash) || Number(pool.weapon_hash) <= 0) {
    throw new Error("A generated pool must have an exact positive weapon hash.");
  }
  const notes = Array.isArray(pool.notes) ? pool.notes : [];
  if (!notes.includes("Generated from Bungie's live manifest snapshot " +
      manifestVersion + " for this exact weapon item hash.")) {
    throw new Error("Pool source note does not match the current Manifest version.");
  }
  if (!notes.includes("Pool extraction generator version: " + generatorVersion + ".")) {
    throw new Error("Pool generator version note does not match the request.");
  }
  if (!Array.isArray(pool.slots) || pool.slots.length === 0) {
    throw new Error("Generated pool must contain at least one socket slot.");
  }
}

function validateGeneratedPerk(perk: JsonRecord): void {
  if (typeof perk.id !== "string" || !perk.id || perk.id.length > 180 ||
      typeof perk.name !== "string" || !perk.name ||
      !Number.isSafeInteger(perk.bungie_hash) || Number(perk.bungie_hash) <= 0 ||
      perk.verification_status !== "unverified" || perk.last_verified !== null) {
    throw new Error("Generated perk record failed validation.");
  }
  const notes = Array.isArray(perk.notes) ? perk.notes : [];
  if (!notes.some((note) => typeof note === "string" &&
      note.startsWith("Seeded automatically from the Bungie manifest snapshot "))) {
    throw new Error("Generated perk does not contain its automatic-seed provenance.");
  }
  if (!notes.some((note) => typeof note === "string" &&
      note.includes("exact plug hash"))) {
    throw new Error("Generated perk does not declare exact-hash resolution.");
  }
}

async function startRun(admin: ReturnType<typeof createClient>, body: JsonRecord) {
  const manifestVersion = requireString(body.manifestVersion, "manifest version", 160);
  const generatorVersion = requireString(body.generatorVersion, "generator version", 40);
  const warnings = Array.isArray(body.warnings) ? body.warnings.slice(0, 20) : [];
  const previous = await admin.rpc("d2wd_get_manifest_sync_state");
  if (previous.error) throw new Error("Could not read previous sync state: " + previous.error.message);

  const started = await admin.rpc("d2wd_start_manifest_sync", {
    p_manifest_version: manifestVersion,
    p_generator_version: generatorVersion,
    p_run_mode: "github_actions",
    p_warnings: warnings,
  });
  if (started.error) throw new Error("Could not start Manifest sync run: " + started.error.message);
  if (typeof started.data !== "string") throw new Error("Manifest sync RPC did not return a run UUID.");

  return {
    ok: true,
    runId: started.data,
    previousManifestFingerprint: previous.data?.previousManifestFingerprint ?? null,
  };
}

async function uploadPerks(admin: ReturnType<typeof createClient>, body: JsonRecord) {
  const runId = requireRunId(body.runId);
  const records = requireBatch(body.records);
  const manifestVersion = requireString(body.manifestVersion, "manifest version", 160);
  for (const record of records) validateGeneratedPerk(record);

  const ids = records.map((record) => String(record.id));
  const { data: existing, error: lookupError } = await admin
    .from("d2wd_perks")
    .select("id,data")
    .in("id", ids);
  if (lookupError) throw new Error("Could not check existing perk IDs: " + lookupError.message);
  const existingById = new Map((existing ?? []).map((row: JsonRecord) => [row.id, row.data]));
  const insertRows: JsonRecord[] = [];
  const evidence: JsonRecord[] = [];
  let skipped = 0;

  for (const perk of records) {
    const current = existingById.get(perk.id) as JsonRecord | undefined;
    if (current) {
      if (current.bungie_hash !== perk.bungie_hash || current.name !== perk.name) {
        throw new Error("Refusing to overwrite an existing perk ID with a different exact hash or name: " + perk.id);
      }
      skipped++;
      continue;
    }
    insertRows.push({
      id: perk.id,
      source_file: "data/perks/" + perk.id + ".json",
      data: perk,
    });
    evidence.push({
      entity_type: "perk",
      entity_id: perk.id,
      field_name: "bungie_hash",
      old_value: null,
      new_value: perk.bungie_hash,
      source_type: "bungie_manifest",
      source_ref: "DestinyInventoryItemDefinition plug hash " + perk.bungie_hash,
      source_version: manifestVersion,
      confidence: "exact",
      notes: "New perk record seeded from an exact Bungie plug item hash. Identity is hash-bound; human verification remains pending.",
    });
  }

  if (insertRows.length) {
    const { error } = await admin.from("d2wd_perks").insert(insertRows);
    if (error) throw new Error("Could not insert exact-hash perk records: " + error.message);
  }
  let evidenceWritten = 0;
  if (evidence.length) {
    const result = await admin.rpc("d2wd_record_sync_evidence", {
      p_run_id: runId,
      p_entries: evidence.slice(0, MAX_EVIDENCE_BATCH),
    });
    if (result.error) throw new Error("Could not write perk evidence: " + result.error.message);
    evidenceWritten = Number(result.data ?? 0);
  }

  return { ok: true, accepted: insertRows.length, skipped, evidenceWritten };
}

async function uploadPools(admin: ReturnType<typeof createClient>, body: JsonRecord) {
  const runId = requireRunId(body.runId);
  const records = requireBatch(body.records);
  const manifestVersion = requireString(body.manifestVersion, "manifest version", 160);
  const generatorVersion = requireString(body.generatorVersion, "generator version", 40);
  for (const record of records) validateGeneratedPool(record, manifestVersion, generatorVersion);

  const ids = records.map((record) => String(record.weapon_id));
  const [{ data: weapons, error: weaponError }, { data: existingPools, error: poolError }] = await Promise.all([
    admin.from("d2wd_weapons").select("id,data").in("id", ids),
    admin.from("d2wd_weapon_perk_pools").select("weapon_id,data").in("weapon_id", ids),
  ]);
  if (weaponError) throw new Error("Could not verify weapon identities: " + weaponError.message);
  if (poolError) throw new Error("Could not check existing pools: " + poolError.message);

  const weaponsById = new Map((weapons ?? []).map((row: JsonRecord) => [row.id, row.data]));
  const poolsById = new Map((existingPools ?? []).map((row: JsonRecord) => [row.weapon_id, row.data]));
  const rows: JsonRecord[] = [];
  const evidence: JsonRecord[] = [];
  let skipped = 0;

  for (let index = 0; index < records.length; index++) {
    const pool = records[index];
    const weapon = weaponsById.get(pool.weapon_id) as JsonRecord | undefined;
    if (!weapon || weapon.bungie_hash !== pool.weapon_hash) {
      throw new Error("Pool weapon hash does not match the exact weapon record: " + String(pool.weapon_id));
    }

    const current = poolsById.get(pool.weapon_id) as JsonRecord | undefined;
    if (current?.verification_status === "verified") {
      skipped++;
      continue;
    }
    if (current && current.pool_status !== "unknown" && !isGeneratedPool(current)) {
      skipped++;
      continue;
    }

    rows.push({
      weapon_id: pool.weapon_id,
      source_file: "data/weapon-perk-pools/" + pool.weapon_id + ".json",
      data: pool,
    });
    evidence.push({
      entity_type: "pool",
      entity_id: String(pool.weapon_id),
      field_name: "pool_payload",
      old_value: current ? poolSummary(current) : null,
      new_value: poolSummary(pool),
      source_type: "bungie_manifest",
      source_ref: "DestinyInventoryItemDefinition weapon hash " + pool.weapon_hash,
      source_version: manifestVersion,
      confidence: "exact",
      notes: "Pool sockets were extracted from the exact weapon item definition and referenced plug sets. pool_status describes extraction completeness only; verification_status remains unverified.",
    });
  }

  if (rows.length) {
    const { error } = await admin.from("d2wd_weapon_perk_pools").upsert(rows, {
      onConflict: "weapon_id",
      ignoreDuplicates: false,
    });
    if (error) throw new Error("Could not upsert generated pools: " + error.message);
  }
  let evidenceWritten = 0;
  if (evidence.length) {
    for (let offset = 0; offset < evidence.length; offset += MAX_EVIDENCE_BATCH) {
      const result = await admin.rpc("d2wd_record_sync_evidence", {
        p_run_id: runId,
        p_entries: evidence.slice(offset, offset + MAX_EVIDENCE_BATCH),
      });
      if (result.error) throw new Error("Could not write pool evidence: " + result.error.message);
      evidenceWritten += Number(result.data ?? 0);
    }
  }

  return { ok: true, accepted: rows.length, skipped, evidenceWritten };
}

async function finishRun(admin: ReturnType<typeof createClient>, body: JsonRecord) {
  const runId = requireRunId(body.runId);
  const status = requireString(body.status, "run status", 40);
  const stats = body.stats && typeof body.stats === "object" && !Array.isArray(body.stats)
    ? body.stats : {};
  const warnings = Array.isArray(body.warnings) ? body.warnings : [];
  const errorText = typeof body.errorText === "string" ? body.errorText.slice(0, 4000) : null;
  const result = await admin.rpc("d2wd_finish_manifest_sync", {
    p_run_id: runId,
    p_status: status,
    p_stats: stats,
    p_warnings: warnings,
    p_error_text: errorText,
  });
  if (result.error) throw new Error("Could not finish Manifest sync run: " + result.error.message);
  return { ok: true, runId, status };
}

Deno.serve(async (request: Request) => {
  if (request.method !== "POST") return jsonResponse(405, { error: "Method not allowed." });

  const authorization = request.headers.get("Authorization") ?? "";
  if (!authorization.startsWith("Bearer ")) {
    return jsonResponse(401, { error: "A GitHub Actions OIDC bearer token is required." });
  }
  try {
    await verifyGitHubOidcToken(authorization.slice(7).trim());
  } catch (error) {
    console.warn("D2WD sync gateway rejected an OIDC token:", error);
    return jsonResponse(403, { error: "GitHub Actions OIDC authentication failed." });
  }

  try {
    const body = await request.json() as JsonRecord;
    if (!body || typeof body !== "object" || Array.isArray(body)) {
      return jsonResponse(400, { error: "Request body must be a JSON object." });
    }
    const admin = getAdminClient();
    const action = requireString(body.action, "action", 40);

    if (action === "start") return jsonResponse(200, await startRun(admin, body));
    if (action === "upload-perks") return jsonResponse(200, await uploadPerks(admin, body));
    if (action === "upload-pools") return jsonResponse(200, await uploadPools(admin, body));
    if (action === "finish") return jsonResponse(200, await finishRun(admin, body));

    return jsonResponse(400, { error: "Unsupported sync action." });
  } catch (error) {
    console.error("D2WD sync gateway operation failed:", error);
    return jsonResponse(500, {
      error: error instanceof Error ? error.message : "Sync request failed.",
    });
  }
});
