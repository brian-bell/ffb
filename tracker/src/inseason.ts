// Closed in-season publish envelope (docs/specs/in-season-command-center.md).
// The Python CLI posts one envelope per report kind and week; the Worker
// validates the envelope plus the minimal report shape the /command page
// renders, then stores the body verbatim under inseason:v1:{season}:{kind}:{week}.
// The tracker never computes a report — it only stores and serves them.

export const INSEASON_KEY_PREFIX = "inseason:v1:";
export const INSEASON_SCHEMA_VERSION = 1;
export const INSEASON_KINDS = ["lineup", "digest", "retro", "ros"] as const;
export type InseasonKind = (typeof INSEASON_KINDS)[number];

export interface LineupContext {
  league_synced_at: string | null;
  projection_sources: string[];
  snapshot_generated_at: string | null;
}
export interface DigestContext {
  sources: string[];
}
export interface RetroContext {
  actuals_synced_at: string;
}
export interface RosContext {
  projection_sources: string[];
  playoff_weeks_requested: number[];
}

export interface LineupRow {
  name: string;
  position: string | null;
  team: string | null;
  slot: string | null;
  points: number | null;
  selected_position: string | null;
  injury?: { status: string; fetched_at?: string | null } | null;
}
export interface LineupReport {
  start: LineupRow[];
  sit: LineupRow[];
  undecidable: LineupRow[];
  missing_projections: LineupRow[];
  aligned: Array<{ slot: string; current: LineupRow | null; optimal: LineupRow | null }>;
  close_calls: Array<{ name: string; points: number; versus: string; slot: string; delta: number }>;
  current_total: number;
  optimal_total: number;
  delta: number;
  injury_as_of: string | null;
}

export interface Headline {
  headline: string;
  source: string | null;
  url: string | null;
  summary?: string | null;
}
export interface DigestPlayer {
  full_name: string | null;
  position: string | null;
  team: string | null;
  selected_position: string | null;
  injury?: { status: string; fetched_at?: string | null } | null;
  flag?: string | null;
  note?: string | null;
  headlines: Headline[];
}
export interface DigestReport {
  week: number | null;
  team_name: string | null;
  injury_as_of: string | null;
  news_as_of: string | null;
  roster: DigestPlayer[];
  watch: DigestPlayer[];
  other_headlines: Headline[];
  narrative: string | null;
  llm: { haiku?: boolean; sonnet?: boolean; error: string | null };
}

export interface RetroRow {
  native_id?: string;
  name: string;
  slot: string | null;
  projected: number | null;
  actual: number | null;
  selected_position?: string | null;
}
export interface RetroReport {
  recommended_total: number;
  started_total: number;
  delta: number;
  start_hits: RetroRow[];
  start_misses: RetroRow[];
  sit_hits: RetroRow[];
  sit_misses: RetroRow[];
  hindsight_total?: number;
  hindsight_started_total?: number;
  hindsight_delta?: number;
  hindsight_start?: RetroRow[];
  hindsight_sit?: RetroRow[];
  missing_actuals: Array<{ name: string; native_id?: string }>;
  source_accuracy: Array<{ source: string; n: number; mae: number; bias: number }>;
  matchup: { user_points: number; opponent_points: number; opponent_team_key?: string } | null;
}

export interface RosPlayer {
  rank: number;
  name: string;
  position: string | null;
  team: string | null;
  ros: number | null;
  bye: number | null;
  playoff: string;
  playoff_difficulty: number | null;
}
export interface RosReport {
  playoff_weeks: number[];
  players: RosPlayer[];
  teams: Array<{ team: string; games: number; avg_opp_def: number | null; difficulty: number | null; matchups: string }>;
  bye_plan: Array<{
    bye: number;
    players: Array<{ name: string; position: string | null; team: string | null; starter: boolean }>;
    thin_positions: string[];
  }>;
  usage_available: boolean;
}

interface EnvelopeBase {
  schema_version: 1;
  season: number;
  week: number;
  generated_at: string;
  team_name: string | null;
}
export type InseasonEnvelope =
  | (EnvelopeBase & { kind: "lineup"; context: LineupContext; report: LineupReport })
  | (EnvelopeBase & { kind: "digest"; context: DigestContext; report: DigestReport })
  | (EnvelopeBase & { kind: "retro"; context: RetroContext; report: RetroReport })
  | (EnvelopeBase & { kind: "ros"; context: RosContext; report: RosReport });

export type ParseEnvelopeResult =
  | { ok: true; envelope: InseasonEnvelope }
  | { ok: false; message: string };

export function isInseasonKind(value: unknown): value is InseasonKind {
  return typeof value === "string" && (INSEASON_KINDS as readonly string[]).includes(value);
}

export function inseasonPrefix(season: number, kind: InseasonKind): string {
  return `${INSEASON_KEY_PREFIX}${season}:${kind}:`;
}

export function inseasonKey(season: number, kind: InseasonKind, week: number): string {
  return `${inseasonPrefix(season, kind)}${week}`;
}

/** Week number encoded at the end of an inseason KV key, or null for a foreign key. */
export function weekFromKey(key: string, season: number, kind: InseasonKind): number | null {
  const prefix = inseasonPrefix(season, kind);
  if (!key.startsWith(prefix)) return null;
  const rest = key.slice(prefix.length);
  return /^[1-9]\d*$/.test(rest) ? Number(rest) : null;
}

// RFC 3339 UTC, as the producer contracts (parse_bundle, parse_actuals) accept
// it: `T` or space separator, optional fraction, and an explicit `Z`, `+00:00`,
// or `-00:00` designator. Naive or non-UTC offsets are rejected.
const UTC_TIMESTAMP = /^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2}(?:\.\d+)?)(Z|[+-]00:00)$/;

/** Epoch millis of a UTC timestamp in any accepted spelling, or NaN. */
export function utcMillis(text: string): number {
  const match = UTC_TIMESTAMP.exec(text);
  if (!match) return Number.NaN;
  return Date.parse(`${match[1]}T${match[2]}Z`);
}

/** Epoch millis of a validated `generated_at`, for ordering stored envelopes. */
export function generatedAtMillis(envelope: { generated_at: string }): number {
  return utcMillis(envelope.generated_at);
}

class Invalid extends Error {}

function fail(message: string): never {
  throw new Invalid(message);
}

function obj(value: unknown, label: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) fail(`${label} must be an object`);
  return value as Record<string, unknown>;
}

function list(value: unknown, label: string): unknown[] {
  if (!Array.isArray(value)) fail(`${label} must be a list`);
  return value;
}

function exactKeys(value: Record<string, unknown>, keys: readonly string[], label: string): void {
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  if (actual.length !== expected.length || actual.some((key, i) => key !== expected[i])) {
    fail(`${label} must have exactly keys ${expected.join(", ")}`);
  }
}

function requireKeys(value: Record<string, unknown>, keys: readonly string[], label: string): void {
  for (const key of keys) if (!(key in value)) fail(`${label}.${key} is required`);
}

function str(value: unknown, label: string): string {
  if (typeof value !== "string") fail(`${label} must be a string`);
  return value;
}

function nullableStr(value: unknown, label: string): string | null {
  if (value !== null && typeof value !== "string") fail(`${label} must be a string or null`);
  return value;
}

function num(value: unknown, label: string): number {
  if (typeof value !== "number" || !Number.isFinite(value)) fail(`${label} must be a finite number`);
  return value;
}

function nullableNum(value: unknown, label: string): number | null {
  if (value === null) return null;
  return num(value, label);
}

function positiveInt(value: unknown, label: string): number {
  if (typeof value !== "number" || !Number.isInteger(value) || value <= 0) {
    fail(`${label} must be a positive integer`);
  }
  return value;
}

function utcTimestamp(value: unknown, label: string): string {
  const text = str(value, label);
  if (Number.isNaN(utcMillis(text))) {
    fail(`${label} must be an RFC 3339 UTC timestamp (Z or +00:00)`);
  }
  return text;
}

function nullableUtcTimestamp(value: unknown, label: string): string | null {
  return value === null ? null : utcTimestamp(value, label);
}

function stringList(value: unknown, label: string): string[] {
  return list(value, label).map((item, i) => str(item, `${label}[${i}]`));
}

function intList(value: unknown, label: string): number[] {
  return list(value, label).map((item, i) => positiveInt(item, `${label}[${i}]`));
}

function bool(value: unknown, label: string): boolean {
  if (typeof value !== "boolean") fail(`${label} must be a boolean`);
  return value;
}

function injury(value: unknown, label: string): void {
  if (value === undefined || value === null) return;
  const row = obj(value, label);
  str(row.status, `${label}.status`);
}

function lineupRow(value: unknown, label: string): void {
  const row = obj(value, label);
  str(row.name, `${label}.name`);
  nullableStr(row.position ?? null, `${label}.position`);
  nullableStr(row.team ?? null, `${label}.team`);
  nullableStr(row.slot ?? null, `${label}.slot`);
  nullableNum(row.points ?? null, `${label}.points`);
  nullableStr(row.selected_position ?? null, `${label}.selected_position`);
  injury(row.injury, `${label}.injury`);
}

function lineupReport(value: unknown): void {
  const report = obj(value, "report");
  requireKeys(
    report,
    ["start", "sit", "undecidable", "missing_projections", "aligned", "close_calls", "current_total", "optimal_total", "delta", "injury_as_of"],
    "report",
  );
  for (const field of ["start", "sit", "undecidable", "missing_projections"] as const) {
    list(report[field], `report.${field}`).forEach((row, i) => lineupRow(row, `report.${field}[${i}]`));
  }
  list(report.aligned, "report.aligned").forEach((raw, i) => {
    const pair = obj(raw, `report.aligned[${i}]`);
    str(pair.slot, `report.aligned[${i}].slot`);
    if (pair.current !== null) lineupRow(pair.current, `report.aligned[${i}].current`);
    if (pair.optimal !== null) lineupRow(pair.optimal, `report.aligned[${i}].optimal`);
  });
  list(report.close_calls, "report.close_calls").forEach((raw, i) => {
    const call = obj(raw, `report.close_calls[${i}]`);
    str(call.name, `report.close_calls[${i}].name`);
    num(call.points, `report.close_calls[${i}].points`);
    str(call.versus, `report.close_calls[${i}].versus`);
    str(call.slot, `report.close_calls[${i}].slot`);
    num(call.delta, `report.close_calls[${i}].delta`);
  });
  num(report.current_total, "report.current_total");
  num(report.optimal_total, "report.optimal_total");
  num(report.delta, "report.delta");
  nullableUtcTimestamp(report.injury_as_of, "report.injury_as_of");
}

function headline(value: unknown, label: string): void {
  const row = obj(value, label);
  str(row.headline, `${label}.headline`);
  nullableStr(row.source ?? null, `${label}.source`);
  nullableStr(row.url ?? null, `${label}.url`);
}

function digestPlayer(value: unknown, label: string): void {
  const row = obj(value, label);
  nullableStr(row.full_name ?? null, `${label}.full_name`);
  nullableStr(row.position ?? null, `${label}.position`);
  nullableStr(row.team ?? null, `${label}.team`);
  nullableStr(row.selected_position ?? null, `${label}.selected_position`);
  nullableStr(row.flag ?? null, `${label}.flag`);
  nullableStr(row.note ?? null, `${label}.note`);
  injury(row.injury, `${label}.injury`);
  list(row.headlines ?? [], `${label}.headlines`).forEach((item, i) => headline(item, `${label}.headlines[${i}]`));
}

function digestReport(value: unknown): void {
  const report = obj(value, "report");
  requireKeys(report, ["roster", "watch", "other_headlines", "narrative", "llm", "injury_as_of", "news_as_of"], "report");
  list(report.roster, "report.roster").forEach((row, i) => digestPlayer(row, `report.roster[${i}]`));
  list(report.watch, "report.watch").forEach((row, i) => digestPlayer(row, `report.watch[${i}]`));
  list(report.other_headlines, "report.other_headlines").forEach((row, i) => headline(row, `report.other_headlines[${i}]`));
  nullableStr(report.narrative, "report.narrative");
  const llm = obj(report.llm, "report.llm");
  nullableStr(llm.error ?? null, "report.llm.error");
  nullableStr(report.injury_as_of, "report.injury_as_of");
  nullableStr(report.news_as_of, "report.news_as_of");
}

function retroRow(value: unknown, label: string): void {
  const row = obj(value, label);
  str(row.name, `${label}.name`);
  nullableStr(row.slot ?? null, `${label}.slot`);
  nullableNum(row.projected ?? null, `${label}.projected`);
  nullableNum(row.actual ?? null, `${label}.actual`);
}

const RETRO_HINDSIGHT_KEYS = [
  "hindsight_total",
  "hindsight_started_total",
  "hindsight_delta",
  "hindsight_start",
  "hindsight_sit",
] as const;

function retroReport(value: unknown): void {
  const report = obj(value, "report");
  requireKeys(
    report,
    [
      "recommended_total",
      "started_total",
      "delta",
      "start_hits",
      "start_misses",
      "sit_hits",
      "sit_misses",
      "missing_actuals",
      "source_accuracy",
      "matchup",
    ],
    "report",
  );
  num(report.recommended_total, "report.recommended_total");
  num(report.started_total, "report.started_total");
  num(report.delta, "report.delta");
  for (const field of ["start_hits", "start_misses", "sit_hits", "sit_misses"] as const) {
    list(report[field], `report.${field}`).forEach((row, i) => retroRow(row, `report.${field}[${i}]`));
  }
  const hindsightPresent = RETRO_HINDSIGHT_KEYS.filter((key) => key in report);
  if (hindsightPresent.length > 0) {
    requireKeys(report, RETRO_HINDSIGHT_KEYS, "report");
    num(report.hindsight_total, "report.hindsight_total");
    num(report.hindsight_started_total, "report.hindsight_started_total");
    num(report.hindsight_delta, "report.hindsight_delta");
    for (const field of ["hindsight_start", "hindsight_sit"] as const) {
      list(report[field], `report.${field}`).forEach((row, i) => retroRow(row, `report.${field}[${i}]`));
    }
  }
  list(report.missing_actuals, "report.missing_actuals").forEach((raw, i) => {
    str(obj(raw, `report.missing_actuals[${i}]`).name, `report.missing_actuals[${i}].name`);
  });
  list(report.source_accuracy, "report.source_accuracy").forEach((raw, i) => {
    const row = obj(raw, `report.source_accuracy[${i}]`);
    str(row.source, `report.source_accuracy[${i}].source`);
    num(row.n, `report.source_accuracy[${i}].n`);
    num(row.mae, `report.source_accuracy[${i}].mae`);
    num(row.bias, `report.source_accuracy[${i}].bias`);
  });
  if (report.matchup !== null) {
    const matchup = obj(report.matchup, "report.matchup");
    num(matchup.user_points, "report.matchup.user_points");
    num(matchup.opponent_points, "report.matchup.opponent_points");
  }
}

function rosReport(value: unknown): void {
  const report = obj(value, "report");
  requireKeys(report, ["playoff_weeks", "players", "teams", "bye_plan", "usage_available"], "report");
  intList(report.playoff_weeks, "report.playoff_weeks");
  list(report.players, "report.players").forEach((raw, i) => {
    const row = obj(raw, `report.players[${i}]`);
    positiveInt(row.rank, `report.players[${i}].rank`);
    str(row.name, `report.players[${i}].name`);
    nullableStr(row.position ?? null, `report.players[${i}].position`);
    nullableStr(row.team ?? null, `report.players[${i}].team`);
    nullableNum(row.ros ?? null, `report.players[${i}].ros`);
    nullableNum(row.bye ?? null, `report.players[${i}].bye`);
    str(row.playoff ?? "", `report.players[${i}].playoff`);
    nullableNum(row.playoff_difficulty ?? null, `report.players[${i}].playoff_difficulty`);
  });
  list(report.teams, "report.teams").forEach((raw, i) => {
    const row = obj(raw, `report.teams[${i}]`);
    str(row.team, `report.teams[${i}].team`);
    num(row.games, `report.teams[${i}].games`);
    nullableNum(row.avg_opp_def ?? null, `report.teams[${i}].avg_opp_def`);
    nullableNum(row.difficulty ?? null, `report.teams[${i}].difficulty`);
    str(row.matchups ?? "", `report.teams[${i}].matchups`);
  });
  list(report.bye_plan, "report.bye_plan").forEach((raw, i) => {
    const group = obj(raw, `report.bye_plan[${i}]`);
    positiveInt(group.bye, `report.bye_plan[${i}].bye`);
    list(group.players, `report.bye_plan[${i}].players`).forEach((item, j) => {
      const player = obj(item, `report.bye_plan[${i}].players[${j}]`);
      str(player.name, `report.bye_plan[${i}].players[${j}].name`);
      bool(player.starter, `report.bye_plan[${i}].players[${j}].starter`);
    });
    stringList(group.thin_positions, `report.bye_plan[${i}].thin_positions`);
  });
  bool(report.usage_available, "report.usage_available");
}

function context(kind: InseasonKind, value: unknown): void {
  const ctx = obj(value, "context");
  switch (kind) {
    case "lineup":
      exactKeys(ctx, ["league_synced_at", "projection_sources", "snapshot_generated_at"], "context");
      nullableUtcTimestamp(ctx.league_synced_at, "context.league_synced_at");
      stringList(ctx.projection_sources, "context.projection_sources");
      nullableUtcTimestamp(ctx.snapshot_generated_at, "context.snapshot_generated_at");
      return;
    case "digest":
      exactKeys(ctx, ["sources"], "context");
      stringList(ctx.sources, "context.sources");
      return;
    case "retro":
      exactKeys(ctx, ["actuals_synced_at"], "context");
      utcTimestamp(ctx.actuals_synced_at, "context.actuals_synced_at");
      return;
    case "ros":
      exactKeys(ctx, ["projection_sources", "playoff_weeks_requested"], "context");
      stringList(ctx.projection_sources, "context.projection_sources");
      intList(ctx.playoff_weeks_requested, "context.playoff_weeks_requested");
      return;
  }
}

const REPORT_CHECKS: Record<InseasonKind, (value: unknown) => void> = {
  lineup: lineupReport,
  digest: digestReport,
  retro: retroReport,
  ros: rosReport,
};

/**
 * Validate one publish envelope. `expectedKind` is the route's kind; a
 * mismatch is an `invalid_report`, never a silent redirect to another key.
 */
export function parseEnvelope(payload: unknown, expectedKind?: InseasonKind): ParseEnvelopeResult {
  try {
    const data = obj(payload, "envelope");
    exactKeys(data, ["schema_version", "kind", "season", "week", "generated_at", "team_name", "context", "report"], "envelope");
    if (data.schema_version !== INSEASON_SCHEMA_VERSION) fail("envelope.schema_version must be 1");
    if (!isInseasonKind(data.kind)) fail("envelope.kind must be lineup, digest, retro, or ros");
    if (expectedKind !== undefined && data.kind !== expectedKind) {
      fail(`envelope.kind ${data.kind} does not match route kind ${expectedKind}`);
    }
    positiveInt(data.season, "envelope.season");
    positiveInt(data.week, "envelope.week");
    utcTimestamp(data.generated_at, "envelope.generated_at");
    nullableStr(data.team_name, "envelope.team_name");
    context(data.kind, data.context);
    REPORT_CHECKS[data.kind](data.report);
    return { ok: true, envelope: data as unknown as InseasonEnvelope };
  } catch (caught) {
    if (caught instanceof Invalid) return { ok: false, message: caught.message };
    throw caught;
  }
}
