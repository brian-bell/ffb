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

/**
 * Consume an unread request body so returning early raises no runtime error.
 *
 * Cancelling the stream is not enough: workerd still has the socket read
 * outstanding and raises "Can't read from request stream after response has
 * been sent" once the 413 goes out. Reading it to the cap and discarding costs
 * nothing extra — `readBounded` already stops at `MAX_BUNDLE_BYTES` — and
 * leaves the connection in a state the runtime can close cleanly.
 */
async function discard(request: Request): Promise<void> {
  try {
    await readBounded(request);
  } catch {
    // The client hung up first; nothing left to release.
  }
}

/**
 * Read the body as text, or return null once it exceeds `MAX_BUNDLE_BYTES`.
 *
 * `content-length` is absent on a chunked upload, so it cannot be the only
 * guard: `request.text()` would buffer up to the 100 MB Workers request limit
 * as a UTF-16 string, and encoding that back to bytes to measure it doubles the
 * cost again, exceeding the 128 MB isolate limit long before the check runs.
 * Counting raw bytes as they arrive bounds the work and never holds a second
 * copy of the body.
 */
async function readBounded(request: Request): Promise<string | null> {
  if (request.body === null) return "";
  const reader = request.body.getReader();
  const decoder = new TextDecoder();
  let seen = 0;
  let text = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    seen += value.byteLength;
    if (seen > MAX_BUNDLE_BYTES) {
      await reader.cancel();
      return null;
    }
    text += decoder.decode(value, { stream: true });
  }
  return text + decoder.decode();
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
    // Abandoning an unread stream raises an uncaught "Can't read from request
    // stream after response has been sent", which would log an error for every
    // legitimate 413 and can reset a client that is still uploading.
    await discard(request);
    return payloadTooLarge();
  }
  const text = await readBounded(request);
  if (text === null) {
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
//
// This guard is best-effort, not a transaction. Both checks are a non-atomic
// read-modify-write over KV, whose reads are edge-cached and not read-your-
// writes across colos, so two things remain possible:
//   - a correct bundle POSTed just after a new-season board is published can
//     read the previous board and be rejected `season_mismatch` until the write
//     propagates;
//   - two POSTs racing within that window can both read the pre-write bundle,
//     letting the older `synced_at` land last.
// Both need a compare-and-set that KV cannot express; closing them means moving
// the pin and the stored `synced_at` into D1 or a Durable Object. The CLI is the
// only producer and posts serially, so the exposure is a retry or a manual
// re-run, not normal operation. Keep docs/tracker.md honest about this.
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
