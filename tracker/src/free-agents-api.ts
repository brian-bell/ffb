import {
  freeAgentsKey,
  freeAgentsLeagueKey,
  parseFreeAgents,
  type FreeAgentsBundle,
} from "./free-agents";
import { isDefaultLeague, leagueFromParam } from "./league-keys";

export interface FreeAgentsApiEnv {
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

export async function handleFreeAgentsApi(
  request: Request,
  env: FreeAgentsApiEnv,
  url: URL,
): Promise<Response> {
  if (request.method === "POST") return postFreeAgents(request, env, url);
  if (request.method === "GET") return getFreeAgents(env, url);
  return json({ error: "method_not_allowed", message: "Method not allowed." }, 405, {
    Allow: "GET, POST",
  });
}

async function postFreeAgents(request: Request, env: FreeAgentsApiEnv, url: URL): Promise<Response> {
  let payload: unknown;
  try {
    payload = await request.json();
  } catch {
    return error("invalid_free_agents", "Provide a FreeAgentsBundle JSON object.", 400);
  }
  const parsed = parseFreeAgents(payload);
  if (!parsed.ok) return error("invalid_free_agents", parsed.message, 400);
  const { bundle } = parsed;
  const leagueKey = freeAgentsLeagueKey(bundle);
  const requested = url.searchParams.get("league");
  if (requested === null && bundle.source === "yahoo" && !isDefaultLeague(leagueKey)) {
    // A plain GET reads the default league, so silently storing a live Yahoo
    // pool elsewhere (a stale game key, say) would strand it.
    return error(
      "invalid_free_agents",
      `bundle league ${leagueKey} is not the default league; pass ?league= to store it`,
      400,
    );
  }
  if (requested !== null) {
    const named = leagueFromParam(requested);
    if (named === null) return error("invalid_request", "league must be a nonempty league key", 400);
    if (named !== leagueKey) {
      return error(
        "invalid_free_agents",
        `league ${named} does not match bundle league ${leagueKey}`,
        400,
      );
    }
  }
  const key = freeAgentsKey(bundle.league.season, leagueKey);
  const existing = await env.BOARD.get(key);
  if (existing !== null && isNewer(existing, bundle)) {
    return error("stale_free_agents", "A newer free-agent pool is already stored.", 409);
  }
  await env.BOARD.put(key, JSON.stringify(bundle));
  return json({
    ok: true,
    season: bundle.league.season,
    week: bundle.league.week,
    players: bundle.players.length,
    synced_at: bundle.synced_at,
    key,
  });
}

/** True when the stored pool was scraped after the incoming one. Equal is accepted (idempotent). */
function isNewer(stored: string, incoming: FreeAgentsBundle): boolean {
  try {
    const synced = Date.parse((JSON.parse(stored) as { synced_at?: string }).synced_at ?? "");
    return !Number.isNaN(synced) && synced > Date.parse(incoming.synced_at);
  } catch {
    return false;
  }
}

async function getFreeAgents(env: FreeAgentsApiEnv, url: URL): Promise<Response> {
  const seasonParam = url.searchParams.get("season");
  if (seasonParam === null || !/^[1-9]\d*$/.test(seasonParam)) {
    return error("invalid_request", "Provide a positive integer season query parameter.", 400);
  }
  const leagueKey = leagueFromParam(url.searchParams.get("league"));
  if (leagueKey === null) return error("invalid_request", "league must be a nonempty league key", 400);
  const text = await env.BOARD.get(freeAgentsKey(Number(seasonParam), leagueKey));
  if (text === null) return error("not_found", "No free-agent pool stored for that season.", 404);
  try {
    return json(JSON.parse(text) as FreeAgentsBundle);
  } catch {
    return error("free_agents_unreadable", "Stored free-agent pool is unreadable.", 503);
  }
}
