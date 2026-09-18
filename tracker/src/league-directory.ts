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
 * Display label per league, disambiguated only as far as it has to be.
 *
 * Two leagues genuinely can share a name across providers ("Dynasty" on both
 * Yahoo and Sleeper), and a picker with two identical rows is unusable, so the
 * provider is appended — but only to the names that collide, leaving the common
 * case clean. Two leagues on the *same* provider can share a name too, and then
 * the provider qualifier is identical on both rows; the key is the only field
 * guaranteed to differ, so it is the last resort. Every label is unique.
 */
export function leagueLabels(leagues: LeagueOption[]): Map<string, string> {
  const tally = (of: (league: LeagueOption) => string): Map<string, number> => {
    const seen = new Map<string, number>();
    for (const league of leagues) {
      const value = of(league);
      seen.set(value, (seen.get(value) ?? 0) + 1);
    }
    return seen;
  };
  const qualified = (league: LeagueOption): string => `${league.name} · ${league.source}`;
  const byName = tally((league) => league.name);
  const byQualified = tally(qualified);

  const labels = new Map<string, string>();
  for (const league of leagues) {
    if ((byName.get(league.name) ?? 0) < 2) labels.set(league.league_key, league.name);
    else if ((byQualified.get(qualified(league)) ?? 0) < 2) labels.set(league.league_key, qualified(league));
    else labels.set(league.league_key, `${league.name} · ${league.league_key}`);
  }
  return labels;
}
