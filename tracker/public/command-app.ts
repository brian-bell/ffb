// /command client: one GET /api/inseason per week, four summary cards, one
// detail panel. Read-only. Every report string (LLM narrative, notes,
// headlines) is rendered through textContent — never innerHTML. Freshness
// badges come only from cardFreshness (../src/inseason-view).

import type { DigestPlayer, DigestReport, Headline, InseasonEnvelope, InseasonKind, LineupReport, LineupRow, RetroReport, RetroRow, RosReport } from "../src/inseason";
import { KIND_LABEL, ageMillis, cardFreshness, formatAge, millis, oldestSource, DAY_MS, type Freshness, type InseasonView } from "../src/inseason-view";
import { requestJson } from "../src/request-json";
import { makeStore } from "../src/state";

type Lineup = Extract<InseasonEnvelope, { kind: "lineup" }>;
type Digest = Extract<InseasonEnvelope, { kind: "digest" }>;
type Retro = Extract<InseasonEnvelope, { kind: "retro" }>;
type Ros = Extract<InseasonEnvelope, { kind: "ros" }>;

const KINDS: InseasonKind[] = ["lineup", "digest", "retro", "ros"];
const HORIZON: Record<InseasonKind, string> = { lineup: "this week", digest: "this week", retro: "last week", ros: "season" };

function localStorageOrNull(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}
const keyStore = makeStore(localStorageOrNull());

// ---- DOM refs ----
const $ = <T extends Element>(sel: string): T => {
  const node = document.querySelector<T>(sel);
  if (!node) throw new Error(`missing element: ${sel}`);
  return node;
};
const gridEl = $<HTMLElement>("[data-grid]");
const statusEl = $<HTMLElement>("[data-status]");
const weekEl = $<HTMLElement>("[data-week]");
const weekPrevEl = $<HTMLButtonElement>("[data-week-prev]");
const weekNextEl = $<HTMLButtonElement>("[data-week-next]");
const teamEl = $<HTMLElement>("[data-team]");
const oldestEl = $<HTMLElement>("[data-oldest]");
const oldestTextEl = $<HTMLElement>("[data-oldest-text]");
const scrimEl = $<HTMLElement>("[data-scrim]");
const panelEl = $<HTMLElement>("[data-panel]");
const panelTitleEl = $<HTMLElement>("[data-panel-title]");
const panelBadgeEl = $<HTMLElement>("[data-panel-badge]");
const panelBodyEl = $<HTMLElement>("[data-panel-body]");
const panelCloseEl = $<HTMLButtonElement>("[data-panel-close]");
const lockEl = $<HTMLElement>("[data-lock]");
const lockCloseEl = $<HTMLButtonElement>("[data-lock-close]");
const lockTitleEl = $<HTMLElement>("[data-lock-title]");
const keyInputEl = $<HTMLInputElement>("[data-key-input]");
const keyErrorEl = $<HTMLElement>("[data-key-error]");
const keySaveEl = $<HTMLButtonElement>("[data-key-save]");
const keyForgetEl = $<HTMLButtonElement>("[data-key-forget]");
const gearEl = $<HTMLButtonElement>("[data-gear]");

// ---- state ----
let view: InseasonView | null = null;
let lastFocus: HTMLElement | null = null;
let loading = false;

// ---- tiny DOM helpers (text only) ----
type Kid = Node | string | number | null | undefined | false;
function el(tag: string, attrs: Record<string, string | boolean | null | undefined> = {}, ...kids: Array<Kid | Kid[]>): HTMLElement {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value == null || value === false) continue;
    if (key === "class") node.className = String(value);
    else if (key === "text") node.textContent = String(value);
    else node.setAttribute(key, value === true ? "" : String(value));
  }
  for (const kid of kids.flat()) {
    if (kid == null || kid === false) continue;
    node.appendChild(typeof kid === "object" ? kid : document.createTextNode(String(kid)));
  }
  return node;
}

function fmtTime(iso: string | null | undefined): string {
  const at = millis(iso);
  if (at === null) return "—";
  return `${new Date(at).toLocaleString("en-US", { timeZone: "America/New_York", weekday: "short", hour: "numeric", minute: "2-digit" })} ET`;
}
const pts = (value: number | null | undefined): string => (value == null ? "—" : value.toFixed(1));
const signed = (value: number): string => `${value > 0 ? "+" : ""}${value.toFixed(1)}`;
const count = (n: number, singular: string, plural = `${singular}s`): string => `${n} ${n === 1 ? singular : plural}`;

function runCommand(kind: InseasonKind, season: number, week: number): string {
  switch (kind) {
    case "lineup": return `ffb lineup ${season} --week ${week} --publish`;
    case "digest": return `ffb digest ${season} --week ${week} --publish`;
    case "retro": return `ffb retro ${season} --week ${week - 1} --publish`;
    case "ros": return `ffb ros ${season} --publish`;
  }
}

function badge(freshness: Freshness, envelope: InseasonEnvelope | null, now: number): HTMLElement {
  const node = el("span", { class: `badge ${freshness.state}`, "data-state": freshness.state }, el("i", { "aria-hidden": "true" }));
  const age = envelope ? ageMillis(envelope.generated_at, now) : null;
  const label = age === null ? freshness.state : `${freshness.state} · ${formatAge(age)}`;
  node.appendChild(document.createTextNode(label));
  return node;
}

function row(key: string, keyClass: string, value: Kid, sub: string | null, right: string, rightClass = ""): HTMLElement {
  return el("li", { class: "row" },
    el("span", { class: `k ${keyClass}`.trim(), text: key }),
    el("span", { class: "v" }, value, sub ? el("small", { text: ` ${sub}` }) : null),
    el("span", { class: `n ${rightClass}`.trim(), text: right }),
  );
}

function cardShell(kind: InseasonKind, freshness: Freshness, envelope: InseasonEnvelope | null, now: number, ...body: HTMLElement[]): HTMLElement {
  const openable = envelope !== null;
  const card = el(openable ? "button" : "section", {
    type: openable ? "button" : null,
    class: `card ${kind} ${freshness.state}`,
    "data-kind": kind,
    "data-state": freshness.state,
    "aria-expanded": openable ? "false" : null,
    "aria-haspopup": openable ? "dialog" : null,
    "aria-label": openable ? null : `${KIND_LABEL[kind]}: ${freshness.state}`,
  });
  const head = el("div", { class: "chead" }, el("h2", { text: KIND_LABEL[kind] }), el("span", { class: "horizon", text: HORIZON[kind] }));
  head.appendChild(badge(freshness, envelope, now));
  card.appendChild(head);
  if (freshness.reason) {
    card.appendChild(el("div", { class: `reason ${freshness.state === "stale" || freshness.state === "degraded" ? "warn" : ""}`.trim(), "data-reason": "", text: freshness.reason }));
  }
  for (const part of body) card.appendChild(part);
  return card;
}

function emptyBody(kind: InseasonKind, freshness: Freshness, current: InseasonView): HTMLElement {
  const message = freshness.state === "waiting" ? "Upstream data does not exist yet." : "Nothing published for this week.";
  return el("div", { class: "empty" }, el("div", {}, message, freshness.state === "missing" ? el("code", { text: runCommand(kind, current.season, current.week) }) : null));
}

function lineupSub(player: LineupRow, showCurrent: boolean): string {
  const bits = [player.position ?? "—", player.slot ?? "—"];
  if (showCurrent && player.selected_position) bits.push(`now ${player.selected_position}`);
  if (player.injury?.status) bits.push(player.injury.status);
  return bits.join(" · ");
}

// ---- cards ----
function renderLineup(current: InseasonView, now: number): HTMLElement {
  const freshness = cardFreshness("lineup", current, now);
  const envelope = current.cards.lineup.envelope as Lineup | null;
  if (!envelope) return cardShell("lineup", freshness, null, now, emptyBody("lineup", freshness, current));
  const report: LineupReport = envelope.report;
  const rows = el("ul", { class: "rows" });
  for (const player of report.start) rows.appendChild(row("start", "start", player.name, lineupSub(player, true), pts(player.points), "good"));
  for (const player of report.sit) rows.appendChild(row("sit", "sit", player.name, lineupSub(player, false), pts(player.points), "bad"));
  for (const player of report.undecidable) rows.appendChild(row("?", "close", player.name, player.position ?? "", "no proj"));
  for (const call of report.close_calls.slice(0, 2)) rows.appendChild(row("close", "close", call.name, `vs ${call.versus} at ${call.slot}`, `−${call.delta.toFixed(1)}`));
  if (!rows.childElementCount) rows.appendChild(row("ok", "start", "Stored lineup matches the weekly optimum.", null, ""));
  const headline = el("div", { class: "head" },
    el("b", { class: report.delta > 0 ? "good" : "plain", "data-headline": "", text: report.delta > 0 ? `+${report.delta.toFixed(1)}` : "0.0" }),
    el("span", { text: `to optimal · ${pts(report.current_total)} now → ${pts(report.optimal_total)} optimal` }),
  );
  const snapshot = envelope.context.snapshot_generated_at;
  const foot = el("div", { class: "cfoot" },
    el("span", { text: snapshot ? `Retro grades the ${fmtTime(snapshot)} snapshot` : "No sit/start snapshot written" }),
    el("span", { class: "more", text: `${count(report.close_calls.length, "close call")} ›` }),
  );
  return cardShell("lineup", freshness, envelope, now, headline, rows, foot);
}

function flagged(players: DigestPlayer[]): DigestPlayer[] {
  return players.filter((player) => player.flag || player.injury?.status);
}

function firstSentence(text: string): string {
  const match = /^[\s\S]*?[.!?](?=\s|$)/.exec(text.trim());
  return match ? match[0] : text.trim();
}

function renderDigest(current: InseasonView, now: number): HTMLElement {
  const freshness = cardFreshness("digest", current, now);
  const envelope = current.cards.digest.envelope as Digest | null;
  if (!envelope) return cardShell("digest", freshness, null, now, emptyBody("digest", freshness, current));
  const report: DigestReport = envelope.report;
  const hits = flagged(report.roster);
  const headline = el("div", { class: "head" },
    el("b", { class: hits.length ? "plain" : "good", "data-headline": "", text: String(hits.length) }),
    el("span", { text: `roster ${count(hits.length, "player").replace(/^\d+ /, "")} flagged` }),
  );
  const rows = el("ul", { class: "rows" });
  for (const player of hits.slice(0, 4)) {
    rows.appendChild(row(player.flag ?? player.injury?.status ?? "", player.flag ? "flag" : "", player.full_name ?? "—", `${player.position ?? "—"} · ${player.team ?? "—"}`, player.flag ? player.injury?.status ?? "" : ""));
  }
  const excerpt = report.narrative
    ? el("div", { class: "excerpt", text: firstSentence(report.narrative) })
    : el("div", { class: "excerpt", text: "No narrative. Headlines and injury designations only." });
  const foot = el("div", { class: "cfoot" },
    el("span", { text: `news ${fmtTime(report.news_as_of)} · injuries ${fmtTime(report.injury_as_of)}` }),
    el("span", { class: "more", text: `${report.watch.length} on watch ›` }),
  );
  return cardShell("digest", freshness, envelope, now, headline, rows, excerpt, foot);
}

function matchupResult(matchup: RetroReport["matchup"]): string {
  if (!matchup) return "no matchup";
  const result = matchup.user_points > matchup.opponent_points ? "W" : matchup.user_points < matchup.opponent_points ? "L" : "T";
  return `${result} ${pts(matchup.user_points)}–${pts(matchup.opponent_points)}`;
}

function delta(rowValue: RetroRow): string {
  return rowValue.actual == null || rowValue.projected == null ? "—" : signed(rowValue.actual - rowValue.projected);
}

function hasHindsight(report: RetroReport): report is RetroReport & {
  hindsight_total: number;
  hindsight_delta: number;
  hindsight_start: RetroRow[];
  hindsight_sit: RetroRow[];
} {
  return (
    typeof report.hindsight_total === "number" &&
    typeof report.hindsight_delta === "number" &&
    Array.isArray(report.hindsight_start) &&
    Array.isArray(report.hindsight_sit)
  );
}

function renderRetro(current: InseasonView, now: number): HTMLElement {
  const freshness = cardFreshness("retro", current, now);
  const envelope = current.cards.retro.envelope as Retro | null;
  if (!envelope) return cardShell("retro", freshness, null, now, emptyBody("retro", freshness, current));
  const report: RetroReport = envelope.report;
  const headline = el("div", { class: "head" },
    el("b", { class: report.delta > 0 ? "bad" : "good", "data-headline": "", text: signed(-report.delta) }),
    el("span", { text: `started vs advice · ${matchupResult(report.matchup)}` }),
  );
  const hindsightLine = hasHindsight(report)
    ? el("div", { class: "head-sub", "data-hindsight": "", text: `hindsight ${signed(-report.hindsight_delta)} vs started` })
    : null;
  const rows = el("ul", { class: "rows" });
  for (const player of report.start_misses) rows.appendChild(row("miss", "miss", player.name, "advised start · benched", delta(player)));
  for (const player of report.sit_misses) rows.appendChild(row("miss", "miss", player.name, "advised sit · started", delta(player)));
  for (const player of report.start_hits.slice(0, 2)) rows.appendChild(row("hit", "hit", player.name, "started as advised", pts(player.actual), "good"));
  for (const player of report.sit_hits.slice(0, 2)) rows.appendChild(row("hit", "hit", player.name, "benched as advised", pts(player.actual), "good"));
  if (!rows.childElementCount) rows.appendChild(row("ok", "hit", "No sit/start swaps in the advice snapshot.", null, ""));
  const best = [...report.source_accuracy].sort((a, b) => a.mae - b.mae)[0];
  const foot = el("div", { class: "cfoot" },
    el("span", { text: `week ${envelope.week} · best source ${best ? `${best.source} MAE ${best.mae.toFixed(2)}` : "—"}` }),
    el("span", { class: "more", text: "all grades ›" }),
  );
  return cardShell("retro", freshness, envelope, now, headline, ...(hindsightLine ? [hindsightLine] : []), rows, foot);
}

function rosteredPlayers(report: RosReport): RosReport["players"] {
  const names = new Set(report.bye_plan.flatMap((group) => group.players.map((player) => `${player.name}|${player.position ?? ""}`)));
  return report.players.filter((player) => names.has(`${player.name}|${player.position ?? ""}`));
}

function renderRos(current: InseasonView, now: number): HTMLElement {
  const freshness = cardFreshness("ros", current, now);
  const envelope = current.cards.ros.envelope as Ros | null;
  if (!envelope) return cardShell("ros", freshness, null, now, emptyBody("ros", freshness, current));
  const report: RosReport = envelope.report;
  const roster = rosteredPlayers(report);
  const hard = roster.filter((player) => player.playoff_difficulty != null && player.playoff_difficulty <= 8).length;
  const headline = el("div", { class: "head" },
    el("b", { class: "plain", "data-headline": "", text: report.playoff_weeks.join("·") || "—" }),
    el("span", { text: roster.length ? `playoff weeks · ${hard} of ${roster.length} rostered face a top-8 slate` : "playoff weeks · no roster stored" }),
  );
  const rows = el("ul", { class: "rows" });
  const next = report.bye_plan.find((group) => group.bye >= current.week);
  if (next) {
    rows.appendChild(row(`bye ${next.bye}`, "bye", next.players.map((player) => player.name).join(", "), next.thin_positions.length ? `thin: ${next.thin_positions.join(", ")}` : null, `${next.players.length} out`));
  }
  const byDifficulty = [...roster].sort((a, b) => (a.playoff_difficulty ?? 99) - (b.playoff_difficulty ?? 99)).slice(0, 3);
  for (const player of byDifficulty) {
    rows.appendChild(row(player.playoff_difficulty == null ? "—" : `#${player.playoff_difficulty}`, "", player.name, `${player.position ?? "—"} · ${player.playoff || "no slate"}`, `ROS ${pts(player.ros)}`));
  }
  if (!rows.childElementCount) rows.appendChild(row("—", "", "No rostered players with a known bye.", null, ""));
  const foot = el("div", { class: "cfoot" },
    el("span", { text: report.usage_available ? "usage trends on" : "usage_available: false" }),
    el("span", { class: "more", text: `${count(report.bye_plan.length, "bye week")} ›` }),
  );
  return cardShell("ros", freshness, envelope, now, headline, rows, foot);
}

// ---- panel bodies ----
function provenance(pairs: Array<[string, string]>): HTMLElement {
  const node = el("div", { class: "prov" });
  for (const [key, value] of pairs) node.appendChild(el("span", {}, `${key} `, el("b", { text: value })));
  return node;
}

type Cell = string | { text?: string; cls?: string; node?: Node } | null;
function table(cols: Array<{ h: string; num?: boolean }>, rows: Cell[][]): HTMLElement {
  const head = el("tr");
  for (const col of cols) head.appendChild(el("th", { class: col.num ? "num" : null, text: col.h }));
  const body = el("tbody");
  for (const cells of rows) {
    const tr = el("tr");
    cols.forEach((col, index) => {
      const value = cells[index] ?? null;
      const classes = [col.num ? "num" : "", typeof value === "object" && value?.cls ? value.cls : ""].filter(Boolean).join(" ");
      const td = el("td", { class: classes || null });
      if (typeof value === "object" && value?.node) td.appendChild(value.node);
      else td.textContent = value == null ? "—" : typeof value === "string" ? value : value.text ?? "—";
      tr.appendChild(td);
    });
    body.appendChild(tr);
  }
  return el("div", { class: "tablewrap" }, el("table", {}, el("thead", {}, head), body));
}

function safeLink(headline: Headline): HTMLElement {
  const ok = typeof headline.url === "string" && headline.url.startsWith("https:");
  const item = el("li");
  if (ok) item.appendChild(el("a", { href: headline.url as string, target: "_blank", rel: "noopener noreferrer", text: headline.headline }));
  else item.appendChild(el("span", { text: headline.headline }));
  if (headline.source) item.appendChild(document.createTextNode(` · ${headline.source}`));
  return item;
}

function panelLineup(envelope: Lineup): HTMLElement[] {
  const report = envelope.report;
  const body: HTMLElement[] = [];
  body.push(provenance([
    ["built", fmtTime(envelope.generated_at)],
    ["league synced", fmtTime(envelope.context.league_synced_at)],
    ["injuries", fmtTime(report.injury_as_of)],
    ["sources", envelope.context.projection_sources.join(", ") || "—"],
  ]));
  body.push(el("h3", { text: "Slots" }));
  body.push(table(
    [{ h: "Slot" }, { h: "Now" }, { h: "Pts", num: true }, { h: "Optimal" }, { h: "Pts", num: true }],
    report.aligned.map((pair) => {
      const diff = (pair.current?.name ?? null) !== (pair.optimal?.name ?? null);
      return [
        pair.slot,
        { text: pair.current?.name ?? "—", cls: diff ? "bad" : "" },
        pts(pair.current?.points),
        { text: pair.optimal?.name ?? "—", cls: diff ? "good" : "" },
        pts(pair.optimal?.points),
      ];
    }),
  ));
  body.push(el("p", { class: "note", text: `Current ${pts(report.current_total)} · optimal ${pts(report.optimal_total)} · delta ${signed(report.delta)}` }));
  body.push(el("h3", { text: "Close calls" }));
  body.push(report.close_calls.length
    ? table([{ h: "Bench" }, { h: "Pts", num: true }, { h: "Versus" }, { h: "Slot" }, { h: "Gap", num: true }], report.close_calls.map((call) => [call.name, pts(call.points), call.versus, call.slot, call.delta.toFixed(1)]))
    : el("p", { class: "note", text: "None within the close-call window." }));
  body.push(el("h3", { text: "Missing projections" }));
  const missing = [...report.missing_projections, ...report.undecidable];
  body.push(missing.length
    ? el("div", { class: "chips" }, missing.map((player) => el("span", { class: "chip warn", text: `${player.name} · ${player.position ?? "—"}` })))
    : el("p", { class: "note", text: "Every roster player has a weekly projection." }));
  body.push(el("h3", { text: "Snapshot" }));
  body.push(el("p", { class: "note", text: envelope.context.snapshot_generated_at
    ? `Retro will grade the sit/start snapshot written ${fmtTime(envelope.context.snapshot_generated_at)}. ffb lineup does not replace a locked snapshot without --force.`
    : "No snapshot was written for this run (post-hoc week without --force)." }));
  return body;
}

function people(list: DigestPlayer[]): HTMLElement {
  const wrap = el("div");
  if (!list.length) wrap.appendChild(el("p", { class: "note", text: "None." }));
  for (const player of list) {
    wrap.appendChild(el("p", {},
      el("b", { text: player.full_name ?? "—" }),
      ` ${player.position ?? "—"} · ${player.team ?? "—"}`,
      player.selected_position ? ` · ${player.selected_position}` : "",
      player.injury?.status ? ` · ${player.injury.status}` : "",
      player.flag ? el("span", { class: "chip warn inline", text: player.flag }) : null,
    ));
    if (player.note) wrap.appendChild(el("p", { class: "note", text: player.note }));
    if (player.headlines?.length) wrap.appendChild(el("ul", { class: "hl" }, player.headlines.map(safeLink)));
  }
  return wrap;
}

function panelDigest(envelope: Digest): HTMLElement[] {
  const report = envelope.report;
  const body: HTMLElement[] = [];
  body.push(provenance([
    ["built", fmtTime(envelope.generated_at)],
    ["news", fmtTime(report.news_as_of)],
    ["injuries", fmtTime(report.injury_as_of)],
    ["sources", envelope.context.sources.join(", ") || "—"],
    ["llm", report.llm.error ? "skipped" : "haiku + sonnet"],
  ]));
  if (report.llm.error) body.push(el("p", { class: "note", text: report.llm.error }));
  body.push(el("h3", { text: "Narrative" }));
  body.push(el("p", { text: report.narrative || "No narrative (LLM skipped)." }));
  body.push(el("h3", { text: "Roster" }), people(report.roster));
  body.push(el("h3", { text: "Watch list" }), people(report.watch));
  body.push(el("h3", { text: "Other headlines" }));
  body.push(report.other_headlines.length ? el("ul", { class: "hl" }, report.other_headlines.map(safeLink)) : el("p", { class: "note", text: "None." }));
  return body;
}

function panelRetro(envelope: Retro): HTMLElement[] {
  const report = envelope.report;
  const body: HTMLElement[] = [];
  body.push(provenance([["graded", fmtTime(envelope.generated_at)], ["actuals synced", fmtTime(envelope.context.actuals_synced_at)], ["week", String(envelope.week)]]));
  body.push(el("p", { class: "note", text: "A hit means the started lineup followed the advice; a miss means it did not." }));
  body.push(el("p", { text: `Advised lineup ${pts(report.recommended_total)} · started ${pts(report.started_total)} · left on bench ${signed(report.delta)} · ${matchupResult(report.matchup)}` }));
  if (hasHindsight(report)) {
    body.push(el("p", { class: "note", "data-hindsight-panel": "", text: `Hindsight lineup ${pts(report.hindsight_total)} · started ${pts(report.started_total)} · left on bench ${signed(report.hindsight_delta)}` }));
  }
  const grades = (title: string, list: RetroRow[], cls: string): void => {
    body.push(el("h3", { text: `${title} (${list.length})` }));
    body.push(list.length
      ? table([{ h: "Player" }, { h: "Slot" }, { h: "Proj", num: true }, { h: "Actual", num: true }, { h: "Δ", num: true }], list.map((player) => [player.name, player.slot ?? "—", pts(player.projected), pts(player.actual), { text: delta(player), cls }]))
      : el("p", { class: "note", text: "None." }));
  };
  grades("Start hits", report.start_hits, "good");
  grades("Start misses", report.start_misses, "bad");
  grades("Sit hits", report.sit_hits, "good");
  grades("Sit misses", report.sit_misses, "bad");
  if (hasHindsight(report)) {
    grades("Hindsight start", report.hindsight_start, "good");
    grades("Hindsight sit", report.hindsight_sit, "bad");
  }
  body.push(el("h3", { text: "Source accuracy" }));
  body.push(report.source_accuracy.length
    ? table([{ h: "Source" }, { h: "n", num: true }, { h: "MAE", num: true }, { h: "Bias", num: true }], report.source_accuracy.map((source) => [source.source, String(source.n), source.mae.toFixed(2), signed(source.bias)]))
    : el("p", { class: "note", text: "No per-source projections in the snapshot." }));
  body.push(el("h3", { text: "Missing actuals" }));
  body.push(report.missing_actuals.length
    ? el("div", { class: "chips" }, report.missing_actuals.map((player) => el("span", { class: "chip warn", text: player.name })))
    : el("p", { class: "note", text: "Every snapshotted player has actuals." }));
  return body;
}

function panelRos(envelope: Ros): HTMLElement[] {
  const report = envelope.report;
  const body: HTMLElement[] = [];
  body.push(provenance([
    ["built", fmtTime(envelope.generated_at)],
    ["playoffs", envelope.context.playoff_weeks_requested.join(", ")],
    ["sources", envelope.context.projection_sources.join(", ") || "—"],
    ["usage", report.usage_available ? "on" : "unavailable"],
  ]));
  body.push(el("h3", { text: "Rest-of-season consensus" }));
  const rostered = new Set(rosteredPlayers(report).map((player) => `${player.name}|${player.position ?? ""}`));
  const filters = el("div", { class: "filters", role: "group", "aria-label": "Position filter" });
  const holder = el("div");
  let position = "ALL";
  const draw = (): void => {
    holder.replaceChildren(table(
      [{ h: "#", num: true }, { h: "Player" }, { h: "Pos" }, { h: "Bye", num: true }, { h: "ROS", num: true }, { h: "Playoffs" }, { h: "Diff", num: true }],
      report.players
        .filter((player) => position === "ALL" || player.position === position)
        .sort((a, b) => a.rank - b.rank)
        .map((player) => [
          String(player.rank),
          { node: el("span", {}, player.name, rostered.has(`${player.name}|${player.position ?? ""}`) ? el("span", { class: "chip inline", text: "yours" }) : null) },
          player.position ?? "—",
          player.bye == null ? "—" : String(player.bye),
          pts(player.ros),
          player.playoff || "—",
          player.playoff_difficulty == null ? "—" : String(player.playoff_difficulty),
        ]),
    ));
    for (const button of filters.children) button.setAttribute("aria-pressed", String(button.textContent === position));
  };
  for (const option of ["ALL", "QB", "RB", "WR", "TE", "K", "DEF"]) {
    const button = el("button", { type: "button", text: option });
    button.addEventListener("click", () => { position = option; draw(); });
    filters.appendChild(button);
  }
  draw();
  body.push(filters, holder);
  body.push(el("h3", { text: "Playoff schedule strength" }));
  body.push(el("p", { class: "note", text: "Difficulty 1 is the hardest slate (highest average opponent DEF projection)." }));
  body.push(report.teams.length
    ? table([{ h: "Diff", num: true }, { h: "Team" }, { h: "Games", num: true }, { h: "Avg opp DEF", num: true }, { h: "Matchups" }], report.teams.map((team) => [team.difficulty == null ? "—" : String(team.difficulty), team.team, String(team.games), team.avg_opp_def == null ? "—" : team.avg_opp_def.toFixed(1), team.matchups || "—"]))
    : el("p", { class: "note", text: "No schedule games stored for the playoff weeks." }));
  body.push(el("h3", { text: "Bye plan" }));
  body.push(report.bye_plan.length
    ? table([{ h: "Bye", num: true }, { h: "Players" }, { h: "Thin" }], report.bye_plan.map((group) => [String(group.bye), group.players.map((player) => `${player.name}${player.starter ? "" : " (bench)"}`).join(", "), group.thin_positions.length ? { text: group.thin_positions.join(", "), cls: "warn" } : "—"]))
    : el("p", { class: "note", text: "No roster stored, so no bye plan." }));
  if (!report.usage_available) body.push(el("p", { class: "note", text: "usage_available: false — usage trends are not ingested; stash/buy-low flags omitted." }));
  return body;
}

function panelBody(kind: InseasonKind, envelope: InseasonEnvelope): HTMLElement[] {
  switch (kind) {
    case "lineup": return panelLineup(envelope as Lineup);
    case "digest": return panelDigest(envelope as Digest);
    case "retro": return panelRetro(envelope as Retro);
    case "ros": return panelRos(envelope as Ros);
  }
}

// ---- page ----
function setStatus(message: string, error = false): void {
  statusEl.hidden = !message;
  statusEl.textContent = message;
  statusEl.classList.toggle("error", error);
}

function renderAll(): void {
  if (!view) return;
  const current = view;
  const now = Date.now();
  weekEl.textContent = `Week ${current.week}`;
  const known = current.weeks.length ? current.weeks : [current.week];
  weekPrevEl.disabled = current.week <= Math.min(...known, current.week);
  weekNextEl.disabled = current.week >= Math.max(...known, current.week);
  const team = KINDS.map((kind) => current.cards[kind].envelope?.team_name).find((name): name is string => Boolean(name));
  teamEl.textContent = team ?? "—";
  const renderers: Record<InseasonKind, (v: InseasonView, n: number) => HTMLElement> = { lineup: renderLineup, digest: renderDigest, retro: renderRetro, ros: renderRos };
  gridEl.replaceChildren(...KINDS.map((kind) => renderers[kind](current, now)));
  for (const card of gridEl.querySelectorAll<HTMLButtonElement>("button.card")) {
    card.addEventListener("click", () => openPanel(card.dataset.kind as InseasonKind, card));
  }
  const oldest = oldestSource(current, now);
  oldestEl.classList.remove("ok", "none");
  if (oldest) {
    oldestTextEl.textContent = `Oldest source: ${KIND_LABEL[oldest.kind]}, ${formatAge(oldest.ageMs)}`;
    oldestEl.classList.toggle("ok", oldest.ageMs < 2 * DAY_MS);
  } else {
    oldestTextEl.textContent = "Nothing published";
    oldestEl.classList.add("none");
  }
  document.title = `FFB Command · Week ${current.week}`;
}

function openPanel(kind: InseasonKind, trigger: HTMLElement): void {
  if (!view) return;
  const envelope = view.cards[kind].envelope;
  if (!envelope) return;
  const now = Date.now();
  lastFocus = trigger;
  trigger.setAttribute("aria-expanded", "true");
  panelTitleEl.textContent = `${KIND_LABEL[kind]} · week ${envelope.week}`;
  panelBadgeEl.replaceChildren(badge(cardFreshness(kind, view, now), envelope, now));
  panelBodyEl.replaceChildren(...panelBody(kind, envelope));
  panelBodyEl.scrollTop = 0;
  scrimEl.hidden = false;
  panelEl.hidden = false;
  panelCloseEl.focus();
}

function closePanel(): void {
  if (panelEl.hidden) return;
  scrimEl.hidden = true;
  panelEl.hidden = true;
  if (lastFocus) {
    lastFocus.setAttribute("aria-expanded", "false");
    lastFocus.focus();
  }
}

function trapFocus(event: KeyboardEvent): void {
  if (panelEl.hidden || event.key !== "Tab") return;
  const focusable = panelEl.querySelectorAll<HTMLElement>("button, a[href], input, [tabindex]:not([tabindex='-1'])");
  if (!focusable.length) return;
  const first = focusable[0]!;
  const last = focusable[focusable.length - 1]!;
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
}

// ---- key gate ----
function setLocked(locked: boolean, message = "", settings = false): void {
  lockEl.hidden = !locked;
  keyErrorEl.hidden = !message;
  keyErrorEl.textContent = message;
  lockCloseEl.hidden = !settings;
  keyForgetEl.hidden = !settings;
  lockTitleEl.textContent = settings ? "Command center settings" : "Unlock the command center";
  if (locked) setTimeout(() => keyInputEl.focus(), 0);
}

function requestedWeek(): number | null {
  const value = new URL(window.location.href).searchParams.get("week");
  return value && /^[1-9]\d*$/.test(value) ? Number(value) : null;
}

function rememberWeek(week: number): void {
  const url = new URL(window.location.href);
  url.searchParams.set("week", String(week));
  window.history.replaceState(null, "", url);
}

async function load(week: number | null, key = keyStore.get()): Promise<boolean> {
  if (!key) {
    setLocked(true);
    return false;
  }
  if (loading) return false;
  loading = true;
  setStatus("Loading…");
  const params = new URLSearchParams();
  if (view) params.set("season", String(view.season));
  if (week !== null) params.set("week", String(week));
  const query = params.toString();
  const result = await requestJson<InseasonView & { error?: string; message?: string }>(fetch, `/api/inseason${query ? `?${query}` : ""}`, { headers: { Authorization: `Bearer ${key}` } });
  loading = false;
  if (result.transportError) {
    setStatus(result.transportError, true);
    return false;
  }
  const response = result.response!;
  if (response.status === 401) {
    keyStore.del();
    view = null;
    gridEl.replaceChildren();
    setStatus("");
    setLocked(true, "Invalid API key. Check it and try again.");
    return false;
  }
  if (!response.ok || !result.value || !result.value.cards) {
    setStatus(result.value?.message ?? `The tracker returned ${response.status}.`, true);
    return false;
  }
  keyStore.set(key);
  view = result.value;
  setStatus("");
  setLocked(false);
  rememberWeek(view.week);
  renderAll();
  return true;
}

async function submitKey(): Promise<void> {
  const key = keyInputEl.value.trim();
  if (!key) {
    setLocked(true, "Enter your API key to continue.");
    return;
  }
  keySaveEl.disabled = true;
  const ok = await load(view?.week ?? requestedWeek(), key);
  keySaveEl.disabled = false;
  if (ok) keyInputEl.value = "";
}

keySaveEl.addEventListener("click", () => { void submitKey(); });
keyInputEl.addEventListener("keydown", (event) => { if (event.key === "Enter") void submitKey(); });
keyForgetEl.addEventListener("click", () => {
  keyStore.del();
  view = null;
  gridEl.replaceChildren();
  setLocked(true);
});
lockCloseEl.addEventListener("click", () => setLocked(false));
gearEl.addEventListener("click", () => {
  keyInputEl.value = "";
  setLocked(true, "", true);
});
panelCloseEl.addEventListener("click", closePanel);
scrimEl.addEventListener("click", closePanel);
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    if (!panelEl.hidden) closePanel();
    else if (!lockEl.hidden && !lockCloseEl.hidden) setLocked(false);
  }
  trapFocus(event);
});
weekPrevEl.addEventListener("click", () => { if (view) { closePanel(); void load(view.week - 1); } });
weekNextEl.addEventListener("click", () => { if (view) { closePanel(); void load(view.week + 1); } });
window.addEventListener("focus", () => { if (view && panelEl.hidden) renderAll(); });

void load(requestedWeek());
