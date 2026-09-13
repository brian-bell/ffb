import { getBoardText } from "./board";
import {
  getLeagueBundleText,
  leagueBundleSummary,
  parseBundle,
  putLeagueBundle,
  syncedAtMillis,
  type LeagueBundle,
  type LeagueBundleEnv,
} from "./league-bundle";

function json(body: unknown, status = 200, headers: HeadersInit = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", ...headers },
  });
}

// KV values cap at 25 MiB and a real bundle is well under 1 MiB; bound the body
// so an oversized payload gets a structured 413 instead of an unhandled put error.
export const MAX_BUNDLE_BYTES = 10 * 1024 * 1024;

function payloadTooLarge(): Response {
  return json(
    { error: "payload_too_large", message: `body must be at most ${MAX_BUNDLE_BYTES} bytes` },
    413,
  );
}

function methodNotAllowed(allow: string): Response {
  return json({ error: "method_not_allowed", message: "Method not allowed." }, 405, {
    Allow: allow,
  });
}

export async function handleLeagueApi(
  request: Request,
  env: LeagueBundleEnv,
): Promise<Response> {
  if (request.method === "GET") {
    const text = await getLeagueBundleText(env);
    if (text === null) {
      return json({ error: "no league bundle" }, 404);
    }
    return new Response(text, { headers: { "content-type": "application/json" } });
  }

  if (request.method !== "POST") {
    return methodNotAllowed("GET, POST");
  }

  const declared = Number(request.headers.get("content-length"));
  if (Number.isFinite(declared) && declared > MAX_BUNDLE_BYTES) {
    return payloadTooLarge();
  }
  const text = await request.text();
  if (new TextEncoder().encode(text).byteLength > MAX_BUNDLE_BYTES) {
    return payloadTooLarge();
  }

  let payload: unknown;
  try {
    payload = JSON.parse(text);
  } catch {
    return json({ error: "invalid_json", message: "body must be JSON" }, 400);
  }

  let bundle;
  try {
    bundle = parseBundle(payload);
  } catch (caught) {
    const message = caught instanceof Error ? caught.message : "invalid league bundle";
    return json({ error: "invalid_bundle", message }, 400);
  }

  const conflict = await rejectStaleOrWrongSeason(env, bundle);
  if (conflict) return conflict;

  await putLeagueBundle(env, bundle);
  return json(leagueBundleSummary(bundle));
}

// A replayed or queued older payload must not overwrite a newer bundle, and a
// bundle for another season must not sit beside the published board. Equal
// synced_at is an idempotent re-post. The season pin only applies once a valid
// board has been published; the stored bundle is trusted (it passed parseBundle).
async function rejectStaleOrWrongSeason(
  env: LeagueBundleEnv,
  bundle: LeagueBundle,
): Promise<Response | null> {
  const boardText = await getBoardText(env);
  if (boardText !== null) {
    let boardSeason: unknown;
    try {
      boardSeason = (JSON.parse(boardText) as { season?: unknown }).season;
    } catch {
      boardSeason = undefined;
    }
    if (typeof boardSeason === "number" && boardSeason !== bundle.league.season) {
      return json(
        {
          error: "season_mismatch",
          message: `bundle season ${bundle.league.season} does not match published board season ${boardSeason}`,
        },
        409,
      );
    }
  }

  const storedText = await getLeagueBundleText(env);
  if (storedText === null) return null;
  let stored: LeagueBundle;
  try {
    stored = parseBundle(JSON.parse(storedText));
  } catch {
    return null; // unreadable stored value: allow replacement
  }
  if (syncedAtMillis(bundle) < syncedAtMillis(stored)) {
    return json(
      {
        error: "stale_bundle",
        message: `bundle.synced_at ${bundle.synced_at} is older than stored ${stored.synced_at}`,
      },
      409,
    );
  }
  return null;
}
