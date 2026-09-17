// Closed LeagueBundle v2 validator + last-accepted KV mirror.
// v1 payloads (yahoo_player_id / yahoo_player_key) are upgraded on read; see
// upgradeV1Rosters.
// Faithful port of ffb.league.parse_bundle. The Worker never writes DuckDB;
// CLI later fetches this KV value and runs the Python path.

import {
  DEFAULT_LEAGUE_KEY,
  isDefaultLeague,
  leagueSlug,
  namespacedLeagueKey,
} from "./league-keys";

/** v1, single-league. Read-only during the dual-read window. */
export const LEAGUE_BUNDLE_KEY = "league:bundle:current";

/** v2: one key per league, `league:bundle:{league}:current`. */
export function leagueBundleKey(leagueKey: string = DEFAULT_LEAGUE_KEY): string {
  return `league:bundle:${leagueSlug(leagueKey)}:current`;
}

/** Every bundle key, v1 and v2, shares this prefix — the listing root. */
export const LEAGUE_BUNDLE_PREFIX = "league:bundle:";

/**
 * The league a bundle KV key names, or null when the key is not a bundle key.
 *
 * Listing `league:bundle:` returns the v1 key alongside the v2 ones, and the v1
 * key holds exactly the default league's bundle, so it maps there. The slug is
 * percent-encoded (league keys contain a colon), and a key whose slug does not
 * decode is not one we wrote.
 */
export function leagueKeyFromBundleKey(kvKey: string): string | null {
  if (kvKey === LEAGUE_BUNDLE_KEY) return DEFAULT_LEAGUE_KEY;
  if (!kvKey.startsWith(LEAGUE_BUNDLE_PREFIX) || !kvKey.endsWith(":current")) return null;
  const slug = kvKey.slice(LEAGUE_BUNDLE_PREFIX.length, -":current".length);
  if (slug === "" || slug.includes(":")) return null;
  try {
    const decoded = decodeURIComponent(slug);
    return decoded === "" ? null : decoded;
  } catch {
    return null;
  }
}

/** The namespaced league key a parsed bundle belongs to. */
export function bundleLeagueKey(bundle: LeagueBundle): string {
  return namespacedLeagueKey(bundle.source, bundle.league.league_key);
}

export interface LeagueBundleEnv {
  BOARD: KVNamespace;
}

export interface LeagueBundle {
  schema_version: 2;
  source: "fixture" | "yahoo" | "sleeper";
  synced_at: string;
  league: {
    league_id: string;
    league_key: string;
    name: string;
    season: number;
    current_week: number;
    num_teams: number;
  };
  settings: {
    roster_slots: Array<{ position: string; count: number; is_starting: boolean }>;
    scoring_rules: Array<{
      stat_key: string;
      points: number;
      provider_stat_id: string;
      provider_name: string;
    }>;
    unmapped_scoring_rules: Array<{
      points: number;
      provider_stat_id: string;
      provider_name: string;
    }>;
    provider_settings: Record<string, unknown>;
  };
  teams: Array<{
    team_id: string;
    team_key: string;
    name: string;
    managers: string[];
    is_user_team: boolean;
  }>;
  rosters: Array<{
    team_key: string;
    week: number;
    players: Array<{
      native_id: string;
      native_player_key: string;
      name: string;
      nfl_team: string | null;
      primary_position: string;
      eligible_positions: string[];
      selected_position: string;
    }>;
  }>;
}

export class LeagueBundleError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "LeagueBundleError";
  }
}

// Back-compat shim, mirroring ffb.league._upgrade_v1_players: schema v1 named
// the roster identity fields yahoo_player_id / yahoo_player_key even for
// non-Yahoo providers. v2 uses native_id / native_player_key. v1 bundles are
// still at rest in KV, so they are rewritten here and validated as v2. Remove
// once none remain.
function upgradeV1Rosters(rosters: unknown): unknown {
  if (!Array.isArray(rosters)) return rosters;
  return rosters.map((roster) => {
    if (typeof roster !== "object" || roster === null || Array.isArray(roster)) return roster;
    const source = roster as Record<string, unknown>;
    if (!Array.isArray(source.players)) return roster;
    const players = source.players.map((player) => {
      if (typeof player !== "object" || player === null || Array.isArray(player)) return player;
      const renamed: Record<string, unknown> = {};
      for (const [key, value] of Object.entries(player as Record<string, unknown>)) {
        const next =
          key === "yahoo_player_id"
            ? "native_id"
            : key === "yahoo_player_key"
              ? "native_player_key"
              : key;
        if (next in renamed) {
          throw new LeagueBundleError("bundle mixes schema-v1 and schema-v2 player identity fields");
        }
        renamed[next] = value;
      }
      return renamed;
    });
    return { ...source, players };
  });
}

export function parseBundle(payload: unknown, season?: number): LeagueBundle {
  const data = mapping(payload, "bundle");
  exactKeys(
    data,
    ["schema_version", "source", "synced_at", "league", "settings", "teams", "rosters"],
    "bundle",
  );
  if (data.schema_version === 1) {
    data.rosters = upgradeV1Rosters(data.rosters);
    data.schema_version = 2;
  }
  if (data.schema_version !== 2) {
    throw new LeagueBundleError("bundle.schema_version must be 2");
  }
  if (data.source !== "fixture" && data.source !== "yahoo" && data.source !== "sleeper") {
    throw new LeagueBundleError("bundle.source must be fixture, yahoo, or sleeper");
  }
  utcTimestamp(data.synced_at, "bundle.synced_at");

  const league = mapping(data.league, "league");
  exactKeys(
    league,
    ["league_id", "league_key", "name", "season", "current_week", "num_teams"],
    "league",
  );
  for (const key of ["league_id", "league_key", "name"] as const) {
    asString(league[key], `league.${key}`);
  }
  positiveInt(league.season, "league.season");
  if (season !== undefined && league.season !== season) {
    throw new LeagueBundleError(
      `requested season ${season} does not match bundle season ${league.season}`,
    );
  }
  positiveInt(league.current_week, "league.current_week");
  positiveInt(league.num_teams, "league.num_teams");

  const settings = mapping(data.settings, "settings");
  exactKeys(
    settings,
    ["roster_slots", "scoring_rules", "unmapped_scoring_rules", "provider_settings"],
    "settings",
  );
  const rosterSlots = asList(settings.roster_slots, "settings.roster_slots");
  const scoringRules = asList(settings.scoring_rules, "settings.scoring_rules");
  const unmappedRules = asList(settings.unmapped_scoring_rules, "settings.unmapped_scoring_rules");
  mapping(settings.provider_settings, "settings.provider_settings");
  validateSlots(rosterSlots);
  validateRules(scoringRules, unmappedRules);

  const teams = asList(data.teams, "teams");
  if (teams.length !== league.num_teams) {
    throw new LeagueBundleError("league.num_teams must equal number of teams");
  }
  const keys = new Set<string>();
  const ids = new Set<string>();
  for (const [i, teamValue] of teams.entries()) {
    const team = mapping(teamValue, `teams[${i}]`);
    exactKeys(team, ["team_id", "team_key", "name", "managers", "is_user_team"], `teams[${i}]`);
    for (const field of ["team_id", "team_key", "name"] as const) {
      asString(team[field], `teams[${i}].${field}`);
    }
    asStrings(team.managers, `teams[${i}].managers`);
    if (typeof team.is_user_team !== "boolean") {
      throw new LeagueBundleError(`teams[${i}].is_user_team must be boolean`);
    }
    if (ids.has(team.team_id as string) || keys.has(team.team_key as string)) {
      throw new LeagueBundleError("team IDs and keys must be unique");
    }
    ids.add(team.team_id as string);
    keys.add(team.team_key as string);
  }

  const rosters = asList(data.rosters, "rosters");
  if (rosters.length !== keys.size) {
    throw new LeagueBundleError("every team must have exactly one current-week roster");
  }
  const rosterKeys = new Set<string>();
  const playerIds = new Set<string>();
  for (const [i, rosterValue] of rosters.entries()) {
    const roster = mapping(rosterValue, `rosters[${i}]`);
    exactKeys(roster, ["team_key", "week", "players"], `rosters[${i}]`);
    asString(roster.team_key, `rosters[${i}].team_key`);
    if (roster.week !== league.current_week) {
      throw new LeagueBundleError("every roster week must equal league.current_week");
    }
    if (rosterKeys.has(roster.team_key as string)) {
      throw new LeagueBundleError("every team must have exactly one current-week roster");
    }
    rosterKeys.add(roster.team_key as string);
    for (const [j, playerValue] of asList(roster.players, `rosters[${i}].players`).entries()) {
      const player = mapping(playerValue, `rosters[${i}].players[${j}]`);
      exactKeys(
        player,
        [
          "native_id",
          "native_player_key",
          "name",
          "nfl_team",
          "primary_position",
          "eligible_positions",
          "selected_position",
        ],
        `rosters[${i}].players[${j}]`,
      );
      for (const field of [
        "native_id",
        "native_player_key",
        "name",
        "primary_position",
        "selected_position",
      ] as const) {
        asString(player[field], `rosters[${i}].players[${j}].${field}`);
      }
      if (player.nfl_team !== null) {
        asString(player.nfl_team, `rosters[${i}].players[${j}].nfl_team`);
      }
      asStrings(player.eligible_positions, `rosters[${i}].players[${j}].eligible_positions`);
      if (playerIds.has(player.native_id as string)) {
        throw new LeagueBundleError("native player IDs must be unique across league rosters");
      }
      playerIds.add(player.native_id as string);
    }
  }
  if (!sameSet(rosterKeys, keys)) {
    throw new LeagueBundleError("roster team keys must equal the team-key set");
  }
  return data as unknown as LeagueBundle;
}

export function leagueBundleSummary(bundle: LeagueBundle): {
  ok: true;
  season: number;
  current_week: number;
  teams: number;
  players: number;
  source: "fixture" | "yahoo" | "sleeper";
  synced_at: string;
} {
  return {
    ok: true,
    season: bundle.league.season,
    current_week: bundle.league.current_week,
    teams: bundle.teams.length,
    players: bundle.rosters.reduce((count, roster) => count + roster.players.length, 0),
    source: bundle.source,
    synced_at: bundle.synced_at,
  };
}

/**
 * Read one league's last accepted bundle, preferring v2 and falling back to v1.
 *
 * The v1 key holds the default league's pre-rekey bundle, so only that league
 * may fall back; another league finding nothing at v2 has nothing.
 */
export async function getLeagueBundleText(
  env: LeagueBundleEnv,
  leagueKey: string = DEFAULT_LEAGUE_KEY,
): Promise<string | null> {
  const current = await env.BOARD.get(leagueBundleKey(leagueKey));
  if (current !== null || !isDefaultLeague(leagueKey)) return current;
  return env.BOARD.get(LEAGUE_BUNDLE_KEY);
}

/** Writes go to the bundle's own v2 key; v1 is never written again. */
export async function putLeagueBundle(env: LeagueBundleEnv, bundle: LeagueBundle): Promise<void> {
  await env.BOARD.put(leagueBundleKey(bundleLeagueKey(bundle)), JSON.stringify(bundle));
}

function validateSlots(slots: unknown[]): void {
  const positions = new Set<string>();
  for (const [i, value] of slots.entries()) {
    const slot = mapping(value, `roster_slots[${i}]`);
    exactKeys(slot, ["position", "count", "is_starting"], `roster_slots[${i}]`);
    const position = asString(slot.position, `roster_slots[${i}].position`);
    if (!position || positions.has(position)) {
      throw new LeagueBundleError("roster slot positions must be unique nonempty strings");
    }
    positions.add(position);
    if (!Number.isInteger(slot.count) || (slot.count as number) < 0) {
      throw new LeagueBundleError("roster slot counts must be nonnegative integers");
    }
    if (typeof slot.is_starting !== "boolean") {
      throw new LeagueBundleError("roster slot is_starting must be boolean");
    }
  }
}

function validateRules(mapped: unknown[], unmapped: unknown[]): void {
  const statKeys = new Set<string>();
  const providerIds = new Set<string>();
  for (const [i, value] of mapped.entries()) {
    const rule = mapping(value, `scoring_rules[${i}]`);
    exactKeys(
      rule,
      ["stat_key", "points", "provider_stat_id", "provider_name"],
      `scoring_rules[${i}]`,
    );
    statKeys.add(asString(rule.stat_key, `scoring_rules[${i}].stat_key`));
    providerIds.add(asString(rule.provider_stat_id, `scoring_rules[${i}].provider_stat_id`));
    asString(rule.provider_name, `scoring_rules[${i}].provider_name`);
    finite(rule.points, `scoring_rules[${i}].points`);
  }
  if (statKeys.size !== mapped.length || providerIds.size !== mapped.length) {
    throw new LeagueBundleError("scoring rule keys and provider stat IDs must be unique");
  }
  for (const [i, value] of unmapped.entries()) {
    const rule = mapping(value, `unmapped_scoring_rules[${i}]`);
    exactKeys(
      rule,
      ["points", "provider_stat_id", "provider_name"],
      `unmapped_scoring_rules[${i}]`,
    );
    const providerId = asString(
      rule.provider_stat_id,
      `unmapped_scoring_rules[${i}].provider_stat_id`,
    );
    if (providerIds.has(providerId)) {
      throw new LeagueBundleError("provider stat IDs must be unique across scoring rules");
    }
    providerIds.add(providerId);
    asString(rule.provider_name, `unmapped_scoring_rules[${i}].provider_name`);
    finite(rule.points, `unmapped_scoring_rules[${i}].points`);
  }
}

function mapping(value: unknown, name: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new LeagueBundleError(`${name} must be an object`);
  }
  return value as Record<string, unknown>;
}

function asList(value: unknown, name: string): unknown[] {
  if (!Array.isArray(value)) {
    throw new LeagueBundleError(`${name} must be a list`);
  }
  return value;
}

function asString(value: unknown, name: string): string {
  if (typeof value !== "string") {
    throw new LeagueBundleError(`${name} must be a string`);
  }
  return value;
}

function asStrings(value: unknown, name: string): string[] {
  return asList(value, name).map((item) => asString(item, name));
}

function positiveInt(value: unknown, name: string): void {
  if (!Number.isInteger(value) || (value as number) <= 0) {
    throw new LeagueBundleError(`${name} must be a positive integer`);
  }
}

function finite(value: unknown, name: string): void {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new LeagueBundleError(`${name} must be a finite number`);
  }
}

function exactKeys(value: Record<string, unknown>, expected: string[], name: string): void {
  const actual = Object.keys(value);
  if (actual.length !== expected.length || expected.some((key) => !Object.hasOwn(value, key))) {
    throw new LeagueBundleError(`${name} has unknown or missing fields`);
  }
}

function utcTimestamp(value: unknown, name: string): void {
  const text = asString(value, name);
  const match = /^(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?)(Z|[+-]\d{2}:\d{2})?$/.exec(text);
  if (!match) {
    throw new LeagueBundleError(`${name} must be an RFC 3339 UTC timestamp`);
  }
  const [, datetime, offset] = match;
  // Require an explicit UTC designator. Do not invent `Z` for naive values;
  // Python parse_bundle rejects those as non-UTC.
  if (offset !== "Z" && offset !== "+00:00" && offset !== "-00:00") {
    throw new LeagueBundleError(`${name} must be UTC`);
  }
  // `new Date` rolls invalid dates over (Feb 30 -> Mar 2); Python parse_bundle
  // rejects them, so round-trip the fields and require an exact match.
  const parsed = new Date(`${datetime.replace(" ", "T")}${offset}`);
  const [datePart, timePart] = datetime.split(/[T ]/);
  const [year, month, day] = datePart.split("-").map(Number);
  const [hour, minute, second] = timePart.split(":").map(Number);
  if (
    Number.isNaN(parsed.getTime()) ||
    parsed.getUTCFullYear() !== year ||
    parsed.getUTCMonth() + 1 !== month ||
    parsed.getUTCDate() !== day ||
    parsed.getUTCHours() !== hour ||
    parsed.getUTCMinutes() !== minute ||
    parsed.getUTCSeconds() !== Math.floor(second)
  ) {
    throw new LeagueBundleError(`${name} must be an RFC 3339 UTC timestamp`);
  }
}

/** Epoch millis of a validated UTC `synced_at`, for ordering stored bundles. */
export function syncedAtMillis(bundle: LeagueBundle): number {
  return Date.parse(bundle.synced_at.replace(" ", "T"));
}

function sameSet(left: Set<string>, right: Set<string>): boolean {
  if (left.size !== right.size) return false;
  for (const value of left) {
    if (!right.has(value)) return false;
  }
  return true;
}
