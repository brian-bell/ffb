// Closed FreeAgentsBundle v1. Sibling of WeeklyActualsBundle: the Grok bot
// reads Yahoo's unrostered pool from the live UI and posts it here; the CLI
// pulls it, resolves identities, and ranks (ffb waivers). Identity only — no
// points. The Worker keeps the latest pool per season and league.

import { DEFAULT_LEAGUE_KEY, leagueSlug, namespacedLeagueKey } from "./league-keys";

export const FREE_AGENTS_KEY_PREFIX = "freeagents:v1:";
export const FREE_AGENTS_SCHEMA_VERSION = 1;

export interface FreeAgentsLeague {
  league_id: string;
  league_key: string;
  name: string;
  season: number;
  week: number;
  num_teams: number;
}

export interface FreeAgentPlayer {
  native_id: string;
  native_player_key: string;
  name: string;
  nfl_team: string | null;
  primary_position: string;
  eligible_positions: string[];
}

export interface FreeAgentsBundle {
  schema_version: number;
  source: "fixture" | "yahoo";
  synced_at: string;
  league: FreeAgentsLeague;
  players: FreeAgentPlayer[];
}

export type ParseFreeAgentsResult =
  | { ok: true; bundle: FreeAgentsBundle }
  | { ok: false; message: string };

/** League-scoped key. `league` is percent-encoded so its colon stays one segment. */
export function freeAgentsKey(season: number, leagueKey: string = DEFAULT_LEAGUE_KEY): string {
  return `${FREE_AGENTS_KEY_PREFIX}${season}:${leagueSlug(leagueKey)}`;
}

/** The namespaced league a parsed bundle is stored under. */
export function freeAgentsLeagueKey(bundle: Pick<FreeAgentsBundle, "source" | "league">): string {
  return namespacedLeagueKey(bundle.source, bundle.league.league_key);
}

export function parseFreeAgents(
  payload: unknown,
  opts: { season?: number } = {},
): ParseFreeAgentsResult {
  try {
    return { ok: true, bundle: parseBundle(payload, opts.season) };
  } catch (caught) {
    const message = caught instanceof Error ? caught.message : "invalid free agents";
    return { ok: false, message };
  }
}

function parseBundle(payload: unknown, season?: number): FreeAgentsBundle {
  const data = mapping(payload, "bundle");
  exactKeys(data, ["schema_version", "source", "synced_at", "league", "players"], "bundle");
  if (data.schema_version !== FREE_AGENTS_SCHEMA_VERSION) {
    throw new Error("bundle.schema_version must be 1");
  }
  if (data.source !== "fixture" && data.source !== "yahoo") {
    throw new Error("bundle.source must be fixture or yahoo");
  }
  utcTimestamp(data.synced_at, "bundle.synced_at");

  const leagueObj = mapping(data.league, "league");
  exactKeys(leagueObj, ["league_id", "league_key", "name", "season", "week", "num_teams"], "league");
  const league: FreeAgentsLeague = {
    league_id: nonemptyString(leagueObj.league_id, "league.league_id"),
    league_key: nonemptyString(leagueObj.league_key, "league.league_key"),
    name: nonemptyString(leagueObj.name, "league.name"),
    season: positiveInt(leagueObj.season, "league.season"),
    week: positiveInt(leagueObj.week, "league.week"),
    num_teams: positiveInt(leagueObj.num_teams, "league.num_teams"),
  };
  if (season !== undefined && league.season !== season) {
    throw new Error(`requested season ${season} does not match bundle season ${league.season}`);
  }

  const playersRaw = list(data.players, "players");
  if (playersRaw.length === 0) {
    throw new Error("players must not be empty; an empty pool is a failed scrape");
  }
  const ids = new Set<string>();
  const players = playersRaw.map((value, index): FreeAgentPlayer => {
    const player = mapping(value, `players[${index}]`);
    exactKeys(
      player,
      ["native_id", "native_player_key", "name", "nfl_team", "primary_position", "eligible_positions"],
      `players[${index}]`,
    );
    const nativeId = nonemptyString(player.native_id, `players[${index}].native_id`);
    if (ids.has(nativeId)) throw new Error("native player IDs must be unique across free agents");
    ids.add(nativeId);
    return {
      native_id: nativeId,
      native_player_key: nonemptyString(player.native_player_key, `players[${index}].native_player_key`),
      name: nonemptyString(player.name, `players[${index}].name`),
      nfl_team:
        player.nfl_team === null ? null : nonemptyString(player.nfl_team, `players[${index}].nfl_team`),
      primary_position: nonemptyString(player.primary_position, `players[${index}].primary_position`),
      eligible_positions: list(player.eligible_positions, `players[${index}].eligible_positions`).map(
        (position, j) => nonemptyString(position, `players[${index}].eligible_positions[${j}]`),
      ),
    };
  });
  return {
    schema_version: FREE_AGENTS_SCHEMA_VERSION,
    source: data.source,
    synced_at: data.synced_at as string,
    league,
    players,
  };
}

function mapping(value: unknown, name: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`${name} must be an object`);
  }
  return value as Record<string, unknown>;
}

function list(value: unknown, name: string): unknown[] {
  if (!Array.isArray(value)) throw new Error(`${name} must be a list`);
  return value;
}

function exactKeys(value: Record<string, unknown>, expected: string[], name: string): void {
  const keys = Object.keys(value);
  if (keys.length !== expected.length || expected.some((key) => !(key in value))) {
    throw new Error(`${name} has unknown or missing fields`);
  }
}

function nonemptyString(value: unknown, name: string): string {
  if (typeof value !== "string" || !value) throw new Error(`${name} must be a string`);
  return value;
}

function positiveInt(value: unknown, name: string): number {
  if (typeof value !== "number" || !Number.isInteger(value) || value <= 0) {
    throw new Error(`${name} must be a positive integer`);
  }
  return value;
}

function utcTimestamp(value: unknown, name: string): void {
  if (typeof value !== "string") throw new Error(`${name} must be an RFC 3339 UTC timestamp`);
  if (Number.isNaN(Date.parse(value))) throw new Error(`${name} must be an RFC 3339 UTC timestamp`);
  if (!/(Z|[+]00:00)$/.test(value)) throw new Error(`${name} must be UTC`);
}
