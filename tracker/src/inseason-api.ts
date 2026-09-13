// POST /api/inseason/{kind} stores one closed publish envelope in KV;
// GET /api/inseason?season=&week= composes the /command dashboard view in a
// single response. KV only — never DuckDB, D1, or the draft tables. The error
// vocabulary mirrors the LeagueBundle ingest route.

import { actualsKey } from "./actuals";
import { getBoardText } from "./board";
import {
  generatedAtMillis,
  inseasonKey,
  inseasonPrefix,
  isInseasonKind,
  parseEnvelope,
  weekFromKey,
  INSEASON_KINDS,
  type InseasonEnvelope,
  type InseasonKind,
} from "./inseason";
import type { InseasonCard, InseasonView } from "./inseason-view";
import { MAX_BUNDLE_BYTES } from "./league-api";
import { getLeagueBundleText } from "./league-bundle";

export interface InseasonApiEnv {
  BOARD: KVNamespace;
}

function json(body: unknown, status = 200, headers: HeadersInit = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", ...headers },
  });
}

function error(code: string, message: string, status: number): Response {
  return json({ error: code, message }, status);
}

function methodNotAllowed(allow: string): Response {
  return json({ error: "method_not_allowed", message: "Method not allowed." }, 405, { Allow: allow });
}

export async function handleInseasonApi(
  request: Request,
  env: InseasonApiEnv,
  url: URL,
  pathname: string,
): Promise<Response> {
  if (pathname === "/api/inseason") {
    if (request.method !== "GET") return methodNotAllowed("GET");
    return getDashboard(env, url);
  }
  const kind = pathname.slice("/api/inseason/".length);
  if (!isInseasonKind(kind) || kind.includes("/")) {
    return json({ error: "not found" }, 404);
  }
  if (request.method !== "POST") return methodNotAllowed("POST");
  return postReport(request, env, kind);
}

async function postReport(request: Request, env: InseasonApiEnv, kind: InseasonKind): Promise<Response> {
  const declared = Number(request.headers.get("content-length"));
  if (Number.isFinite(declared) && declared > MAX_BUNDLE_BYTES) return payloadTooLarge();
  const text = await request.text();
  if (new TextEncoder().encode(text).byteLength > MAX_BUNDLE_BYTES) return payloadTooLarge();

  let payload: unknown;
  try {
    payload = JSON.parse(text);
  } catch {
    return error("invalid_json", "body must be JSON", 400);
  }
  const parsed = parseEnvelope(payload, kind);
  if (!parsed.ok) return error("invalid_report", parsed.message, 400);
  const { envelope } = parsed;

  const boardSeason = await publishedBoardSeason(env);
  if (boardSeason !== null && boardSeason !== envelope.season) {
    return error(
      "season_mismatch",
      `report season ${envelope.season} does not match published board season ${boardSeason}`,
      409,
    );
  }

  const key = inseasonKey(envelope.season, kind, envelope.week);
  const stored = await readEnvelope(env, key);
  // Equal generated_at is an idempotent re-POST after a network error; only a
  // strictly newer stored document wins.
  if (stored && generatedAtMillis(stored) > generatedAtMillis(envelope)) {
    return error(
      "stale_report",
      `stored ${kind} for week ${envelope.week} was generated at ${stored.generated_at}, after ${envelope.generated_at}`,
      409,
    );
  }
  await env.BOARD.put(key, text);
  return json({
    kind,
    season: envelope.season,
    week: envelope.week,
    generated_at: envelope.generated_at,
  });
}

function payloadTooLarge(): Response {
  return error("payload_too_large", `body must be at most ${MAX_BUNDLE_BYTES} bytes`, 413);
}

async function publishedBoardSeason(env: InseasonApiEnv): Promise<number | null> {
  const text = await getBoardText(env);
  if (text === null) return null;
  try {
    const season = (JSON.parse(text) as { season?: unknown }).season;
    return typeof season === "number" ? season : null;
  } catch {
    return null;
  }
}

async function readEnvelope(env: InseasonApiEnv, key: string): Promise<InseasonEnvelope | null> {
  const text = await env.BOARD.get(key);
  if (text === null) return null;
  try {
    const parsed = parseEnvelope(JSON.parse(text));
    return parsed.ok ? parsed.envelope : null;
  } catch {
    return null; // unreadable stored value: treat as absent so a republish can repair it
  }
}

async function listWeeks(env: InseasonApiEnv, season: number, kind: InseasonKind): Promise<number[]> {
  const prefix = inseasonPrefix(season, kind);
  const weeks = new Set<number>();
  let cursor: string | undefined;
  do {
    const page = await env.BOARD.list({ prefix, cursor });
    for (const { name } of page.keys) {
      const week = weekFromKey(name, season, kind);
      if (week !== null) weeks.add(week);
    }
    cursor = page.list_complete ? undefined : page.cursor;
  } while (cursor);
  return [...weeks].sort((a, b) => a - b);
}

interface LeagueSummary {
  season: number | null;
  synced_at: string | null;
  current_week: number | null;
}

async function leagueSummary(env: InseasonApiEnv): Promise<LeagueSummary> {
  const text = await getLeagueBundleText(env);
  if (text === null) return { season: null, synced_at: null, current_week: null };
  try {
    const bundle = JSON.parse(text) as { synced_at?: unknown; league?: { season?: unknown; current_week?: unknown } };
    const season = typeof bundle.league?.season === "number" ? bundle.league.season : null;
    const week = typeof bundle.league?.current_week === "number" ? bundle.league.current_week : null;
    const syncedAt = typeof bundle.synced_at === "string" ? bundle.synced_at : null;
    return { season, synced_at: syncedAt, current_week: week };
  } catch {
    return { season: null, synced_at: null, current_week: null };
  }
}

function parsePositiveInt(value: string | null): number | null {
  if (value === null || !/^[1-9]\d*$/.test(value)) return null;
  return Number(value);
}

async function getDashboard(env: InseasonApiEnv, url: URL): Promise<Response> {
  const seasonParam = url.searchParams.get("season");
  const weekParam = url.searchParams.get("week");
  if (seasonParam !== null && parsePositiveInt(seasonParam) === null) {
    return error("invalid_request", "season must be a positive integer", 400);
  }
  if (weekParam !== null && parsePositiveInt(weekParam) === null) {
    return error("invalid_request", "week must be a positive integer", 400);
  }

  const league = await leagueSummary(env);
  const season = parsePositiveInt(seasonParam) ?? league.season ?? (await publishedBoardSeason(env));
  if (season === null) {
    return error("invalid_request", "season is required until a league bundle or board is published", 400);
  }

  const [lineupWeeks, digestWeeks, rosWeeks] = await Promise.all([
    listWeeks(env, season, "lineup"),
    listWeeks(env, season, "digest"),
    listWeeks(env, season, "ros"),
  ]);
  const weeks = [...new Set([...lineupWeeks, ...digestWeeks])].sort((a, b) => a - b);
  const week =
    parsePositiveInt(weekParam) ??
    (league.season === season ? league.current_week : null) ??
    (weeks.length ? weeks[weeks.length - 1]! : 1);

  const rosWeek = rosWeeks.filter((candidate) => candidate <= week).pop() ?? null;
  const [lineup, digest, retro, ros, actualsText] = await Promise.all([
    readEnvelope(env, inseasonKey(season, "lineup", week)),
    readEnvelope(env, inseasonKey(season, "digest", week)),
    week > 1 ? readEnvelope(env, inseasonKey(season, "retro", week - 1)) : Promise.resolve(null),
    rosWeek === null ? Promise.resolve(null) : readEnvelope(env, inseasonKey(season, "ros", rosWeek)),
    week > 1 ? env.BOARD.get(actualsKey(season, week - 1)) : Promise.resolve(null),
  ]);

  const cards = Object.fromEntries(INSEASON_KINDS.map((kind) => [kind, { envelope: null }])) as Record<InseasonKind, InseasonCard>;
  cards.lineup = { envelope: lineup };
  cards.digest = { envelope: digest };
  cards.retro = { envelope: retro };
  cards.ros = { envelope: ros };

  const view: InseasonView = {
    season,
    week,
    server_now: new Date().toISOString().replace(/\.\d{3}Z$/, "Z"),
    league:
      league.season === season && league.synced_at !== null && league.current_week !== null
        ? { synced_at: league.synced_at, current_week: league.current_week }
        : null,
    actuals_available: week > 1 ? { [String(week - 1)]: actualsText !== null } : {},
    weeks,
    cards,
  };
  return json(view);
}
