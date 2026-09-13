// Closed WeeklyActualsBundle v1. Sibling of the LeagueBundle ingest path —
// extra keys fail, and live scores never belong in git.

export const ACTUALS_KEY_PREFIX = "actuals:v1:";
export const ACTUALS_SCHEMA_VERSION = 1;

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
  yahoo_player_id: string;
  yahoo_player_key: string;
  name: string;
  team_key: string;
  selected_position: string;
  points: number;
}

export interface WeeklyActualsBundle {
  schema_version: number;
  source: "fixture" | "yahoo";
  synced_at: string;
  league: ActualsLeague;
  matchups: ActualsMatchup[];
  players: ActualsPlayer[];
}

export type ParseActualsResult =
  | { ok: true; bundle: WeeklyActualsBundle }
  | { ok: false; message: string };

export function actualsKey(season: number, week: number): string {
  return `${ACTUALS_KEY_PREFIX}${season}:${week}`;
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
  if (data.schema_version !== ACTUALS_SCHEMA_VERSION) {
    throw new Error("bundle.schema_version must be 1");
  }
  if (data.source !== "fixture" && data.source !== "yahoo") {
    throw new Error("bundle.source must be fixture or yahoo");
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
      ["yahoo_player_id", "yahoo_player_key", "name", "team_key", "selected_position", "points"],
      `players[${index}]`,
    );
    const yahooPlayerId = nonemptyString(player.yahoo_player_id, `players[${index}].yahoo_player_id`);
    if (ids.has(yahooPlayerId)) throw new Error("Yahoo player IDs must be unique across actuals");
    ids.add(yahooPlayerId);
    const teamKey = nonemptyString(player.team_key, `players[${index}].team_key`);
    if (!teamKeys.has(teamKey)) throw new Error("player team_key must appear on the scoreboard");
    return {
      yahoo_player_id: yahooPlayerId,
      yahoo_player_key: nonemptyString(player.yahoo_player_key, `players[${index}].yahoo_player_key`),
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
