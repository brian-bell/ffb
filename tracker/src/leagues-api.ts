// GET /api/leagues — the league directory behind the /command header picker.
//
// A league key is a provider id, so the page cannot invent one; without this
// route a second league is reachable only by hand-typing ?league= into the URL.
// KV only: it lists the LeagueBundle keys and reads the few header fields out
// of each, so it never parses a multi-megabyte roster it does not need.

import {
  LEAGUE_BUNDLE_KEY,
  LEAGUE_BUNDLE_PREFIX,
  leagueKeyFromBundleKey,
  type LeagueBundleEnv,
} from "./league-bundle";
import { DEFAULT_LEAGUE_KEY } from "./league-keys";
import { sortLeagues, type LeagueDirectory, type LeagueOption } from "./league-directory";

export type LeaguesApiEnv = LeagueBundleEnv;

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

interface BundleHead {
  source?: unknown;
  synced_at?: unknown;
  league?: {
    name?: unknown;
    season?: unknown;
    current_week?: unknown;
    num_teams?: unknown;
  };
  teams?: unknown;
}

const str = (value: unknown): string | null => (typeof value === "string" && value !== "" ? value : null);
const num = (value: unknown): number | null => (typeof value === "number" ? value : null);

/** The `is_user_team` team's name, so the header can name a team with no reports. */
function userTeamName(teams: unknown): string | null {
  if (!Array.isArray(teams)) return null;
  for (const team of teams) {
    if (team && typeof team === "object" && (team as { is_user_team?: unknown }).is_user_team === true) {
      return str((team as { name?: unknown }).name);
    }
  }
  return null;
}

/**
 * One directory entry from a stored bundle, or null when it is unreadable.
 *
 * A bundle that will not parse is skipped rather than failing the listing: one
 * corrupt value must not make every other league unpickable.
 */
function toOption(leagueKey: string, text: string): LeagueOption | null {
  let bundle: BundleHead;
  try {
    bundle = JSON.parse(text) as BundleHead;
  } catch {
    return null;
  }
  if (!bundle || typeof bundle !== "object") return null;
  const provider = leagueKey.includes(":") ? leagueKey.slice(0, leagueKey.indexOf(":")) : leagueKey;
  return {
    league_key: leagueKey,
    // A bundle always carries a league name; the key is a readable last resort
    // rather than a blank row in the picker.
    name: str(bundle.league?.name) ?? leagueKey,
    source: str(bundle.source) ?? provider,
    season: num(bundle.league?.season),
    current_week: num(bundle.league?.current_week),
    num_teams: num(bundle.league?.num_teams),
    synced_at: str(bundle.synced_at),
    team_name: userTeamName(bundle.teams),
  };
}

export async function handleLeaguesApi(request: Request, env: LeaguesApiEnv): Promise<Response> {
  if (request.method !== "GET") {
    return new Response(JSON.stringify({ error: "method_not_allowed", message: "Method not allowed." }), {
      status: 405,
      headers: { "content-type": "application/json", Allow: "GET" },
    });
  }

  // Keyed by league so the default league's v1 and v2 keys collapse to one
  // entry. KV lists the v1 key first (it sorts before every percent-encoded
  // slug), so it is held back and used only when that league has no v2 key —
  // the same preference as getLeagueBundleText.
  const byLeague = new Map<string, string>();
  let v1Key: string | null = null;
  let cursor: string | undefined;
  do {
    const page = await env.BOARD.list({ prefix: LEAGUE_BUNDLE_PREFIX, cursor });
    for (const { name } of page.keys) {
      if (name === LEAGUE_BUNDLE_KEY) {
        v1Key = name;
        continue;
      }
      const leagueKey = leagueKeyFromBundleKey(name);
      if (leagueKey !== null && !byLeague.has(leagueKey)) byLeague.set(leagueKey, name);
    }
    cursor = page.list_complete ? undefined : page.cursor;
  } while (cursor);
  if (v1Key !== null && !byLeague.has(DEFAULT_LEAGUE_KEY)) byLeague.set(DEFAULT_LEAGUE_KEY, v1Key);

  const leagues: LeagueOption[] = [];
  for (const [leagueKey, kvKey] of byLeague) {
    const text = await env.BOARD.get(kvKey);
    if (text === null) continue;
    const option = toOption(leagueKey, text);
    if (option !== null) leagues.push(option);
  }

  const directory: LeagueDirectory = {
    default_league: DEFAULT_LEAGUE_KEY,
    leagues: sortLeagues(leagues, DEFAULT_LEAGUE_KEY),
  };
  return json(directory);
}
