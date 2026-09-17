// The league dimension of every KV key. Mirrors config.namespaced_league_key on
// the Python side: "<provider>:<the provider's own league id>".
//
// A league key contains a colon, and KV keys are colon-delimited, so the key is
// percent-encoded before it goes into one. Without that,
// inseason:v2:2026:yahoo:470.l.928421:lineup:3 has two readings.

/** The league that owned every v1 key, and the default when a caller names none. */
export const DEFAULT_LEAGUE_KEY = "yahoo:470.l.928421";

/** Percent-encode a league key so it occupies exactly one KV key segment. */
export function leagueSlug(leagueKey: string): string {
  return encodeURIComponent(leagueKey);
}

/** The namespaced league key for a bundle's source and its own raw league key. */
export function namespacedLeagueKey(source: string, leagueKey: string): string {
  const provider = source === "yahoo" || source === "fixture" ? "yahoo" : source;
  const key = leagueKey ?? "";
  if (!provider) return key;
  return key.startsWith(`${provider}:`) ? key : `${provider}:${key}`;
}

/**
 * Resolve a `?league=` query parameter.
 *
 * Absent means the default league, which is what the v1 keys held, so existing
 * callers keep reading their own data. An empty or blank value is a caller
 * mistake rather than an omission, so it is rejected instead of defaulted.
 */
export function leagueFromParam(value: string | null): string | null {
  if (value === null) return DEFAULT_LEAGUE_KEY;
  const trimmed = value.trim();
  return trimmed === "" ? null : trimmed;
}

/** True when v1 (single-league) keys may serve this league's reads. */
export function isDefaultLeague(leagueKey: string): boolean {
  return leagueKey === DEFAULT_LEAGUE_KEY;
}
