import {
  actualsKey,
  actualsLeagueKey,
  actualsReadKeys,
  parseActuals,
  type WeeklyActualsBundle,
} from "./actuals";
import { leagueFromParam } from "./league-keys";

export interface ActualsApiEnv {
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
  return json({ error: "method_not_allowed", message: "Method not allowed." }, 405, {
    Allow: allow,
  });
}

export async function handleActualsApi(
  request: Request,
  env: ActualsApiEnv,
  url: URL,
): Promise<Response> {
  if (request.method === "POST") return postActuals(request, env, url);
  if (request.method === "GET") return getActuals(env, url);
  return methodNotAllowed("GET, POST");
}

async function postActuals(request: Request, env: ActualsApiEnv, url: URL): Promise<Response> {
  let payload: unknown;
  try {
    payload = await request.json();
  } catch {
    return error("invalid_actuals", "Provide a WeeklyActualsBundle JSON object.", 400);
  }
  const parsed = parseActuals(payload);
  if (!parsed.ok) return error("invalid_actuals", parsed.message, 400);
  const { bundle } = parsed;
  const leagueKey = actualsLeagueKey(bundle);
  const requested = url.searchParams.get("league");
  if (requested !== null) {
    const named = leagueFromParam(requested);
    if (named === null) {
      return error("invalid_request", "league must be a nonempty league key", 400);
    }
    if (named !== leagueKey) {
      return error(
        "invalid_actuals",
        `league ${named} does not match bundle league ${leagueKey}`,
        400,
      );
    }
  }
  const key = actualsKey(bundle.league.season, bundle.league.week, leagueKey);
  await env.BOARD.put(key, JSON.stringify(bundle));
  return json({
    ok: true,
    season: bundle.league.season,
    week: bundle.league.week,
    players: bundle.players.length,
    matchups: bundle.matchups.length,
    key,
  });
}

async function getActuals(env: ActualsApiEnv, url: URL): Promise<Response> {
  const season = parsePositiveInt(url.searchParams.get("season"));
  const week = parsePositiveInt(url.searchParams.get("week"));
  if (season === null || week === null) {
    return error("invalid_request", "Provide positive integer season and week query parameters.", 400);
  }
  const leagueKey = leagueFromParam(url.searchParams.get("league"));
  if (leagueKey === null) {
    return error("invalid_request", "league must be a nonempty league key", 400);
  }
  const text = await readActualsText(env, season, week, leagueKey);
  if (text === null) return error("not_found", "No weekly actuals stored for that week.", 404);
  try {
    return json(JSON.parse(text) as WeeklyActualsBundle);
  } catch {
    return error("actuals_unreadable", "Stored weekly actuals are unreadable.", 503);
  }
}

async function readActualsText(
  env: ActualsApiEnv,
  season: number,
  week: number,
  leagueKey: string,
): Promise<string | null> {
  for (const key of actualsReadKeys(season, week, leagueKey)) {
    const text = await env.BOARD.get(key);
    if (text !== null) return text;
  }
  return null;
}

function parsePositiveInt(value: string | null): number | null {
  if (value === null || !/^[1-9]\d*$/.test(value)) return null;
  return Number(value);
}
