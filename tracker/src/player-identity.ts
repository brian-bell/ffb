/** The minimal player snapshot shared by board rows and persisted draft picks. */
export interface PlayerIdentity {
  key: string;
  name: string;
  pos: string | null;
  team: string | null;
}

export function normalizedName(value: string): string {
  return value.toLocaleLowerCase().replace(/[^\p{L}\p{N}]+/gu, "");
}

export function normalizedPosition(pos: string | null): string | null {
  if (pos === null) return null;
  const normalizedPos = pos.trim().toLocaleUpperCase();
  if (!normalizedPos || normalizedPos === "UNKNOWN") return null;
  return normalizedPos === "DST" ? "DEF" : normalizedPos;
}

function normalizedTeam(team: string | null): string | null {
  const normalizedValue = team?.trim().toLocaleUpperCase() ?? "";
  return normalizedValue || null;
}

function isFallback(key: string): boolean {
  return key.includes(":");
}

function isManual(key: string): boolean {
  return key.startsWith("manual:");
}

/**
 * Availability identity is deliberately narrower than a name match.  Canonical
 * rows retain key identity; only fallback/manual rows are allowed to bridge to
 * a canonical board row via an exact normalized snapshot.
 */
export function playersEquivalent(a: PlayerIdentity, b: PlayerIdentity): boolean {
  if (a.key === b.key) return true;
  const aPos = normalizedPosition(a.pos);
  const bPos = normalizedPosition(b.pos);
  const aTeam = normalizedTeam(a.team);
  const bTeam = normalizedTeam(b.team);
  if (aPos === "DEF" && bPos === "DEF" && aTeam !== null && aTeam === bTeam) return true;
  if (!isFallback(a.key) && !isFallback(b.key)) return false;
  if (aPos !== bPos || aPos === null || normalizedName(a.name) !== normalizedName(b.name)) return false;
  if (aTeam !== null && aTeam === bTeam) return true;
  return aTeam === null && bTeam === null && isManual(a.key) && isManual(b.key);
}

export function isAvailable(player: PlayerIdentity, picked: readonly PlayerIdentity[]): boolean {
  return !picked.some((snapshot) => playersEquivalent(player, snapshot));
}
