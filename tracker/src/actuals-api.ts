import { actualsKey, parseActuals, type WeeklyActualsBundle } from "./actuals";

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
  if (request.method === "POST") return postActuals(request, env);
  if (request.method === "GET") return getActuals(env, url);
  return methodNotAllowed("GET, POST");
}

async function postActuals(request: Request, env: ActualsApiEnv): Promise<Response> {
  let payload: unknown;
  try {
    payload = await request.json();
  } catch {
    return error("invalid_actuals", "Provide a WeeklyActualsBundle JSON object.", 400);
  }
  const parsed = parseActuals(payload);
  if (!parsed.ok) return error("invalid_actuals", parsed.message, 400);
  const { bundle } = parsed;
  await env.BOARD.put(actualsKey(bundle.league.season, bundle.league.week), JSON.stringify(bundle));
  return json({
    ok: true,
    season: bundle.league.season,
    week: bundle.league.week,
    players: bundle.players.length,
    matchups: bundle.matchups.length,
    key: actualsKey(bundle.league.season, bundle.league.week),
  });
}

async function getActuals(env: ActualsApiEnv, url: URL): Promise<Response> {
  const season = parsePositiveInt(url.searchParams.get("season"));
  const week = parsePositiveInt(url.searchParams.get("week"));
  if (season === null || week === null) {
    return error("invalid_request", "Provide positive integer season and week query parameters.", 400);
  }
  const text = await env.BOARD.get(actualsKey(season, week));
  if (text === null) return error("not_found", "No weekly actuals stored for that week.", 404);
  try {
    return json(JSON.parse(text) as WeeklyActualsBundle);
  } catch {
    return error("actuals_unreadable", "Stored weekly actuals are unreadable.", 503);
  }
}

function parsePositiveInt(value: string | null): number | null {
  if (value === null || !/^[1-9]\d*$/.test(value)) return null;
  return Number(value);
}
