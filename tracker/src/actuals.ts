// Closed WeeklyActualsBundle v2. Sibling of the LeagueBundle ingest path —
// extra keys fail, and live scores never belong in git.
// v1 payloads (yahoo_player_id / yahoo_player_key) are upgraded on read; see
// upgradeV1Players.
//
// KV keys gained a league segment the same way in-season reports did. Writes
// go to `actuals:v2:{season}:{league}:{week}`. Reads of a Yahoo league still
// accept the unpartitioned `actuals:v1:{season}:{week}` key when the v2 key is
// empty: before the rekey only fixture/yahoo bundles were accepted, so every
// v1 blob is Yahoo's, whatever that season's game key. The v1 prefix
// namespaces that legacy key, not the payload schema.

import { DEFAULT_LEAGUE_KEY, leagueSlug, namespacedLeagueKey } from "./league-keys";

export const ACTUALS_KEY_PREFIX = "actuals:v1:";
export const ACTUALS_KEY_PREFIX_V2 = "actuals:v2:";
export const ACTUALS_SCHEMA_VERSION = 2;

export interface ActualsLeague {
  league_id: string;
  league_key: string;
  name: string;
  season: number;
  week: number;
  num_teams: number;
}

export interface ActualsMatchupTeam {
  team_key: string;
  points: number;
}

export interface ActualsMatchup {
  matchup_id: string;
  week: number;
  teams: ActualsMatchupTeam[];
}

export interface ActualsPlayer {
  native_id: string;
  native_player_key: string;
  name: string;
  team_key: string;
  selected_position: string;
  points: number;
}

export interface WeeklyActualsBundle {
  schema_version: number;
  source: "fixture" | "yahoo" | "sleeper";
  synced_at: string;
  league: ActualsLeague;
  matchups: ActualsMatchup[];
  players: ActualsPlayer[];
}

export type ParseActualsResult =
  | { ok: true; bundle: WeeklyActualsBundle }
  | { ok: false; message: string };

/** Unpartitioned key. Holds Yahoo MCFFL actuals written before the rekey. */
export function actualsKeyV1(season: number, week: number): string {
  return `${ACTUALS_KEY_PREFIX}${season}:${week}`;
}

/** League-scoped key. `league` is percent-encoded so its colon stays one segment. */
export function actualsKey(
  season: number,
  week: number,
  leagueKey: string = DEFAULT_LEAGUE_KEY,
): string {
  return `${ACTUALS_KEY_PREFIX_V2}${season}:${leagueSlug(leagueKey)}:${week}`;
}

/** The namespaced league a parsed bundle is stored under. */
export function actualsLeagueKey(bundle: Pick<WeeklyActualsBundle, "source" | "league">): string {
  return namespacedLeagueKey(bundle.source, bundle.league.league_key);
}

/**
 * Keys to try, in order. The v2 key wins. The v1 key is a read fallback for
 * Yahoo leagues only — a Sleeper league must never be served Yahoo's blob.
 */
export function actualsReadKeys(
  season: number,
  week: number,
  leagueKey: string = DEFAULT_LEAGUE_KEY,
): string[] {
  const keys = [actualsKey(season, week, leagueKey)];
  if (leagueKey.startsWith("yahoo:")) keys.push(actualsKeyV1(season, week));
  return keys;
}

/** First stored actuals blob for this league, in `actualsReadKeys` order. */
export async function readActuals(
  kv: KVNamespace,
  season: number,
  week: number,
  leagueKey: string,
): Promise<string | null> {
  for (const key of actualsReadKeys(season, week, leagueKey)) {
    const text = await kv.get(key);
    if (text !== null) return text;
  }
  return null;
}

export function parseActuals(
  payload: unknown,
  opts: { season?: number } = {},
): ParseActualsResult {
  try {
    const bundle = parseBundle(payload, opts.season);
    return { ok: true, bundle };
  } catch (caught) {
    const message = caught instanceof Error ? caught.message : "invalid actuals";
    return { ok: false, message };
  }
}

function parseBundle(payload: unknown, season?: number): WeeklyActualsBundle {
  const data = mapping(payload, "bundle");
  exactKeys(data, ["schema_version", "source", "synced_at", "league", "matchups", "players"], "bundle");
  if (data.schema_version === 1) {
    data.players = upgradeV1Players(data.players);
    data.schema_version = ACTUALS_SCHEMA_VERSION;
  }
  if (data.schema_version !== ACTUALS_SCHEMA_VERSION) {
    throw new Error("bundle.schema_version must be 2");
  }
  if (data.source !== "fixture" && data.source !== "yahoo" && data.source !== "sleeper") {
    throw new Error("bundle.source must be fixture, yahoo, or sleeper");
  }
  utcTimestamp(data.synced_at, "bundle.synced_at");

  const leagueObj = mapping(data.league, "league");
  exactKeys(leagueObj, ["league_id", "league_key", "name", "season", "week", "num_teams"], "league");
  const league: ActualsLeague = {
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
  if (league.num_teams % 2 !== 0) {
    throw new Error("league.num_teams must be even so matchups can pair every team");
  }

  const matchups = parseMatchups(list(data.matchups, "matchups"), league.week, league.num_teams);
  const teamKeys = new Set(matchups.flatMap((matchup) => matchup.teams.map((team) => team.team_key)));
  const players = parsePlayers(list(data.players, "players"), teamKeys);
  return {
    schema_version: ACTUALS_SCHEMA_VERSION,
    source: data.source,
    synced_at: data.synced_at as string,
    league,
    matchups,
    players,
  };
}

// Back-compat shim, mirroring ffb.actuals._upgrade_v1_players: schema v1 named
// the identity fields yahoo_player_id / yahoo_player_key even for non-Yahoo
// providers. v2 uses native_id / native_player_key. v1 payloads are still at
// rest in KV, so they are rewritten here and validated as v2. Remove once none
// remain.
function upgradeV1Players(players: unknown): unknown {
  if (!Array.isArray(players)) return players;
  return players.map((player) => {
    if (typeof player !== "object" || player === null || Array.isArray(player)) return player;
    const source = player as Record<string, unknown>;
    const renamed: Record<string, unknown> = {};
    for (const [key, value] of Object.entries(source)) {
      const next =
        key === "yahoo_player_id" ? "native_id" : key === "yahoo_player_key" ? "native_player_key" : key;
      if (next in renamed) {
        throw new Error("actuals mix schema-v1 and schema-v2 player identity fields");
      }
      renamed[next] = value;
    }
    return renamed;
  });
}

function parseMatchups(matchups: unknown[], week: number, numTeams: number): ActualsMatchup[] {
  const expected = numTeams / 2;
  if (matchups.length !== expected) {
    throw new Error(`matchups must cover every team (${expected} pairings)`);
  }
  const ids = new Set<string>();
  const teamKeys = new Set<string>();
  return matchups.map((value, index) => {
    const matchup = mapping(value, `matchups[${index}]`);
    exactKeys(matchup, ["matchup_id", "week", "teams"], `matchups[${index}]`);
    const matchupId = nonemptyString(matchup.matchup_id, `matchups[${index}].matchup_id`);
    if (ids.has(matchupId)) throw new Error("matchup_id values must be unique nonempty strings");
    ids.add(matchupId);
    if (matchup.week !== week) throw new Error("every matchup week must equal league.week");
    const teamsRaw = list(matchup.teams, `matchups[${index}].teams`);
    if (teamsRaw.length !== 2) throw new Error("every matchup must have exactly two teams");
    const teams = teamsRaw.map((teamValue, teamIndex) => {
      const team = mapping(teamValue, `matchups[${index}].teams[${teamIndex}]`);
      exactKeys(team, ["team_key", "points"], `matchups[${index}].teams[${teamIndex}]`);
      const teamKey = nonemptyString(team.team_key, `matchups[${index}].teams[${teamIndex}].team_key`);
      if (teamKeys.has(teamKey)) throw new Error("matchup team_key values must be unique nonempty strings");
      teamKeys.add(teamKey);
      return { team_key: teamKey, points: finite(team.points, `matchups[${index}].teams[${teamIndex}].points`) };
    });
    return { matchup_id: matchupId, week, teams };
  });
}

function parsePlayers(players: unknown[], teamKeys: Set<string>): ActualsPlayer[] {
  const ids = new Set<string>();
  return players.map((value, index) => {
    const player = mapping(value, `players[${index}]`);
    exactKeys(
      player,
      ["native_id", "native_player_key", "name", "team_key", "selected_position", "points"],
      `players[${index}]`,
    );
    const nativeId = nonemptyString(player.native_id, `players[${index}].native_id`);
    if (ids.has(nativeId)) throw new Error("native player IDs must be unique across actuals");
    ids.add(nativeId);
    const teamKey = nonemptyString(player.team_key, `players[${index}].team_key`);
    if (!teamKeys.has(teamKey)) throw new Error("player team_key must appear on the scoreboard");
    return {
      native_id: nativeId,
      native_player_key: nonemptyString(player.native_player_key, `players[${index}].native_player_key`),
      name: nonemptyString(player.name, `players[${index}].name`),
      team_key: teamKey,
      selected_position: nonemptyString(player.selected_position, `players[${index}].selected_position`),
      points: finite(player.points, `players[${index}].points`),
    };
  });
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

function finite(value: unknown, name: string): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new Error(`${name} must be a finite number`);
  }
  return value;
}

function utcTimestamp(value: unknown, name: string): void {
  if (typeof value !== "string") throw new Error(`${name} must be an RFC 3339 UTC timestamp`);
  const parsed = Date.parse(value);
  if (Number.isNaN(parsed)) throw new Error(`${name} must be an RFC 3339 UTC timestamp`);
  if (!/(Z|[+]00:00)$/.test(value)) throw new Error(`${name} must be UTC`);
}
