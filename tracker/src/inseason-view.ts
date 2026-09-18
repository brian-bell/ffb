// Dashboard read contract and the pure freshness rules for /command.
// `cardFreshness` is the only place the page infers a badge from; it is a
// function of the GET /api/inseason response and the client clock, first
// matching rule wins (docs/specs/in-season-command-center.md, "Freshness").

import type { InseasonEnvelope, InseasonKind, LineupReport, LineupRow } from "./inseason";

export interface InseasonCard {
  envelope: InseasonEnvelope | null;
}

export interface InseasonView {
  season: number;
  week: number;
  server_now: string;
  league: { synced_at: string; current_week: number } | null;
  actuals_available: Record<string, boolean>;
  weeks: number[];
  cards: Record<InseasonKind, InseasonCard>;
}

export type FreshnessState = "fresh" | "stale" | "degraded" | "waiting" | "missing";

export interface Freshness {
  state: FreshnessState;
  reason: string;
}

export const DAY_MS = 86_400_000;
export const LINEUP_MAX_AGE_MS = 5 * DAY_MS;
export const DIGEST_MAX_AGE_MS = 5 * DAY_MS;
export const ROS_MAX_AGE_MS = 8 * DAY_MS;

export const KIND_LABEL: Record<InseasonKind, string> = {
  lineup: "Lineup",
  digest: "News",
  retro: "Retro",
  ros: "Rest of season",
};

export function millis(iso: string | null | undefined): number | null {
  if (!iso) return null;
  const value = Date.parse(iso);
  return Number.isNaN(value) ? null : value;
}

/** Age of an ISO timestamp relative to `now`, or null when unparsable. */
export function ageMillis(iso: string | null | undefined, now: number): number | null {
  const at = millis(iso);
  return at === null ? null : now - at;
}

function later(left: string | null | undefined, right: string | null | undefined): boolean {
  const a = millis(left);
  const b = millis(right);
  return a !== null && b !== null && a > b;
}

function plural(count: number, singular: string, pluralWord: string): string {
  return `${count} ${count === 1 ? singular : pluralWord}`;
}

const FRESH: Freshness = { state: "fresh", reason: "" };

export function cardFreshness(kind: InseasonKind, view: InseasonView, now: number): Freshness {
  const week = view.week;
  const envelope = view.cards[kind]?.envelope ?? null;
  const age = envelope ? ageMillis(envelope.generated_at, now) : null;

  if (kind === "lineup") {
    if (!envelope || envelope.kind !== "lineup") return { state: "missing", reason: `Not published for week ${week}` };
    if (view.league && later(view.league.synced_at, envelope.context.league_synced_at)) {
      return { state: "stale", reason: "Roster changed after this lineup was built" };
    }
    const digest = view.cards.digest?.envelope ?? null;
    const digestInjury = digest && digest.kind === "digest" ? digest.report.injury_as_of : null;
    if (envelope.report.injury_as_of && digestInjury && later(digestInjury, envelope.report.injury_as_of)) {
      return { state: "stale", reason: "Newer injury report available" };
    }
    if (age !== null && age > LINEUP_MAX_AGE_MS) return { state: "stale", reason: "Built more than 5 days ago" };
    const noProjection = envelope.report.missing_projections.length + envelope.report.undecidable.length;
    if (noProjection > 0) {
      return { state: "degraded", reason: `${plural(noProjection, "player has", "players have")} no projection` };
    }
    return FRESH;
  }

  if (kind === "digest") {
    if (!envelope || envelope.kind !== "digest") return { state: "missing", reason: `Not published for week ${week}` };
    if (age !== null && age > DIGEST_MAX_AGE_MS) return { state: "stale", reason: "Headlines are more than 5 days old" };
    if (envelope.report.llm?.error) return { state: "degraded", reason: "LLM skipped — headlines only" };
    return FRESH;
  }

  if (kind === "retro") {
    if (week <= 1) return { state: "waiting", reason: "No prior week to grade" };
    const hasActuals = view.actuals_available[String(week - 1)] === true;
    if (!envelope || envelope.kind !== "retro") {
      if (!hasActuals) return { state: "waiting", reason: `Waiting for week ${week - 1} actuals` };
      return { state: "missing", reason: "Actuals are in; retro not published" };
    }
    const missing = envelope.report.missing_actuals.length;
    if (missing > 0) return { state: "degraded", reason: `${plural(missing, "player has", "players have")} no actuals` };
    return FRESH;
  }

  if (!envelope || envelope.kind !== "ros") return { state: "missing", reason: "Not published" };
  if (age !== null && age > ROS_MAX_AGE_MS) return { state: "stale", reason: "Built more than 8 days ago" };
  return FRESH;
}

export interface OldestSource {
  kind: InseasonKind;
  ageMs: number;
}

/** The published card whose `generated_at` is oldest, for the header strip. */
export function oldestSource(view: InseasonView, now: number): OldestSource | null {
  let oldest: OldestSource | null = null;
  for (const kind of Object.keys(view.cards) as InseasonKind[]) {
    const envelope = view.cards[kind]?.envelope;
    if (!envelope) continue;
    const ageMs = ageMillis(envelope.generated_at, now);
    if (ageMs === null) continue;
    if (oldest === null || ageMs > oldest.ageMs) oldest = { kind, ageMs };
  }
  return oldest;
}

export const LINEUP_CARD_CLOSE_CALLS = 2;

/** A close call on the card; `swap` marks one that also stands in for a sit/start pair. */
export type CardCloseCall = LineupReport["close_calls"][number] & { swap: boolean };

/**
 * Rows for the compact lineup card. A sit/start swap whose sitting player is a
 * close call against the incoming starter is shown once, as that close row;
 * folded close rows always show, and the rest fill up to the card's cap.
 */
export function lineupCardRows(report: LineupReport): { start: LineupRow[]; sit: LineupRow[]; close: CardCloseCall[] } {
  const starting = new Set(report.start.map((row) => row.name));
  const sitting = new Set(report.sit.map((row) => row.name));
  const folded = new Set(report.close_calls.filter((call) => sitting.has(call.name) && starting.has(call.versus)));
  let room = Math.max(0, LINEUP_CARD_CLOSE_CALLS - folded.size);
  const close = report.close_calls.filter((call) => {
    if (folded.has(call)) return true;
    if (room === 0) return false;
    room -= 1;
    return true;
  }).map((call) => ({ ...call, swap: folded.has(call) }));
  const foldedIn = new Set([...folded].map((call) => call.versus));
  const foldedOut = new Set([...folded].map((call) => call.name));
  return {
    start: report.start.filter((row) => !foldedIn.has(row.name)),
    sit: report.sit.filter((row) => !foldedOut.has(row.name)),
    close,
  };
}

/** Compact human age: 12m, 5h, 3d. */
export function formatAge(ms: number): string {
  const minutes = Math.max(0, Math.round(ms / 60_000));
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.round(minutes / 60);
  if (hours < 36) return `${hours}h`;
  return `${Math.round(hours / 24)}d`;
}
