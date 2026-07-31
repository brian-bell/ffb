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
  if (!normalizedValue) return null;
  const aliases: Record<string, string> = {
    SF: "SFO",
    KC: "KCC",
    GB: "GBP",
    NE: "NEP",
    NO: "NOS",
    TB: "TBB",
    LV: "LVR",
    JAX: "JAC",
  };
  return aliases[normalizedValue] ?? normalizedValue;
}

function isFallback(key: string): boolean {
  return ["sleeper:", "espn:", "ffc:", "yahoo:", "manual:"].some((prefix) =>
    key.startsWith(prefix)
  );
}

function isManual(key: string): boolean {
  return key.startsWith("manual:");
}

/** Preserve first occurrence while applying the same equivalence classes as availability. */
export function distinctPlayerIdentities<T extends PlayerIdentity>(identities: readonly T[]): T[] {
  const distinct: T[] = [];
  const exact = new Set<string>();
  const defenses = new Set<string>();
  const fallbackBridges = new Set<string>();
  const canonicalBridges = new Set<string>();
  const manualWithoutPosition = new Set<string>();
  const manualWithoutTeam = new Set<string>();

  for (const identity of identities) {
    if (exact.has(identity.key)) continue;
    const pos = normalizedPosition(identity.pos);
    const team = normalizedTeam(identity.team);
    const name = normalizedName(identity.name);
    if (pos === "DEF" && team !== null) {
      if (defenses.has(team)) continue;
      defenses.add(team);
    } else if (pos === null) {
      const key = signature(name, team);
      if (isManual(identity.key) && manualWithoutPosition.has(key)) continue;
      if (isManual(identity.key)) manualWithoutPosition.add(key);
    } else if (team === null) {
      const key = signature(pos, name);
      if (isManual(identity.key) && manualWithoutTeam.has(key)) continue;
      if (isManual(identity.key)) manualWithoutTeam.add(key);
    } else {
      const bridge = signature(pos, name, team);
      if (isFallback(identity.key)) {
        if (fallbackBridges.has(bridge) || canonicalBridges.has(bridge)) continue;
        fallbackBridges.add(bridge);
      } else {
        if (fallbackBridges.has(bridge)) continue;
        canonicalBridges.add(bridge);
      }
    }
    exact.add(identity.key);
    distinct.push(identity);
  }
  return distinct;
}

export interface PlayerIdentityIndex<T extends PlayerIdentity> {
  match(player: PlayerIdentity): T | undefined;
}

function signature(...parts: Array<string | null>): string {
  return parts.map((part) => part ?? "").join("\u0000");
}

/**
 * Index persisted identities once so board availability does not need to scan
 * every pick for every player. The signatures mirror playersEquivalent:
 * canonical keys stay exact, defenses bridge by team, and only fallback/manual
 * identities may bridge by normalized snapshots.
 */
export function indexPlayerIdentities<T extends PlayerIdentity>(
  identities: readonly T[],
): PlayerIdentityIndex<T> {
  const exact = new Map<string, T>();
  const defenses = new Map<string, T>();
  const bridgeFromFallback = new Map<string, T>();
  const bridgeToFallback = new Map<string, T>();
  const manualWithoutTeam = new Map<string, T>();
  const manualWithoutPosition = new Map<string, T>();

  for (const identity of identities) {
    exact.set(identity.key, identity);
    const pos = normalizedPosition(identity.pos);
    const team = normalizedTeam(identity.team);
    if (pos === "DEF" && team !== null) defenses.set(team, identity);

    const name = normalizedName(identity.name);
    if (pos === null) {
      if (isManual(identity.key)) {
        manualWithoutPosition.set(signature(name, team), identity);
      }
      continue;
    }
    if (team === null) {
      if (isManual(identity.key)) {
        manualWithoutTeam.set(signature(pos, name), identity);
      }
      continue;
    }
    const bridge = signature(pos, name, team);
    if (isFallback(identity.key)) bridgeFromFallback.set(bridge, identity);
    else bridgeToFallback.set(bridge, identity);
  }

  return {
    match(player) {
      const exactMatch = exact.get(player.key);
      if (exactMatch) return exactMatch;

      const pos = normalizedPosition(player.pos);
      const team = normalizedTeam(player.team);
      if (pos === "DEF" && team !== null) {
        const defenseMatch = defenses.get(team);
        if (defenseMatch) return defenseMatch;
      }

      const name = normalizedName(player.name);
      if (pos === null) {
        return isManual(player.key)
          ? manualWithoutPosition.get(signature(name, team))
          : undefined;
      }
      if (team === null) {
        return isManual(player.key)
          ? manualWithoutTeam.get(signature(pos, name))
          : undefined;
      }
      const bridge = signature(pos, name, team);
      return bridgeFromFallback.get(bridge)
        ?? (isFallback(player.key) ? bridgeToFallback.get(bridge) : undefined);
    },
  };
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
  if (aPos !== bPos || normalizedName(a.name) !== normalizedName(b.name)) return false;
  if (aPos === null) return isManual(a.key) && isManual(b.key) && aTeam === bTeam;
  if (aTeam !== null && aTeam === bTeam) return true;
  return aTeam === null && bTeam === null && isManual(a.key) && isManual(b.key);
}

export function isAvailable(player: PlayerIdentity, picked: readonly PlayerIdentity[]): boolean {
  return !picked.some((snapshot) => playersEquivalent(player, snapshot));
}
