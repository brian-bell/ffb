// The league dimension the /command header lets you choose between.
//
// GET /api/leagues answers with this shape: one entry per LeagueBundle the
// Worker holds in KV. It is the only way the page can learn a league key —
// keys are provider ids, not anything a person would type — so the picker is
// built entirely from it.
//
// Pure: ordering and labelling are decided here so the Worker and the client
// agree, and so the rules are testable without KV.

export interface LeagueOption {
  /** Namespaced `<provider>:<the provider's own id>`, as stored in KV. */
  league_key: string;
  name: string;
  source: string;
  season: number | null;
  current_week: number | null;
  num_teams: number | null;
  synced_at: string | null;
  /** The bundle's `is_user_team` team, for the header when nothing is published. */
  team_name: string | null;
}

export interface LeagueDirectory {
  /** The league a request with no `?league=` is served. */
  default_league: string;
  leagues: LeagueOption[];
}

/**
 * Default league first, then by name, then by key.
 *
 * The default is pinned to the top because it is what every existing link and
 * every unparameterised request already resolves to; the rest are alphabetical
 * so the picker's order does not drift with KV list order. The key tiebreak
 * keeps two identically named leagues in a stable order.
 */
export function sortLeagues(leagues: LeagueOption[], defaultLeague: string): LeagueOption[] {
  return [...leagues].sort((a, b) => {
    if (a.league_key === b.league_key) return 0;
    if (a.league_key === defaultLeague) return -1;
    if (b.league_key === defaultLeague) return 1;
    const byName = a.name.localeCompare(b.name);
    return byName !== 0 ? byName : a.league_key.localeCompare(b.league_key);
  });
}

/**
 * Display label per league, disambiguated only when it has to be.
 *
 * Two leagues genuinely can share a name across providers ("Dynasty" on both
 * Yahoo and Sleeper), and a picker with two identical rows is unusable. The
 * provider is appended only to the names that collide, so the common
 * single-provider case stays clean.
 */
export function leagueLabels(leagues: LeagueOption[]): Map<string, string> {
  const seen = new Map<string, number>();
  for (const league of leagues) seen.set(league.name, (seen.get(league.name) ?? 0) + 1);
  const labels = new Map<string, string>();
  for (const league of leagues) {
    const collides = (seen.get(league.name) ?? 0) > 1;
    labels.set(league.league_key, collides ? `${league.name} · ${league.source}` : league.name);
  }
  return labels;
}
