import { expect, test, type Page } from "@playwright/test";
import digestFixture from "../fixtures/inseason/digest.json" with { type: "json" };
import lineupFixture from "../fixtures/inseason/lineup.json" with { type: "json" };
import retroFixture from "../fixtures/inseason/retro.json" with { type: "json" };
import rosFixture from "../fixtures/inseason/ros.json" with { type: "json" };

// Sunday 11:40 ET pre-kickoff; the fixtures were "published" earlier that day
// (lineup, digest) and the previous Wednesday (retro, ros).
const NOW = "2026-09-20T15:40:00Z";
const DAY_MS = 86_400_000;

type View = Record<string, unknown> & {
  week: number;
  weeks: number[];
  server_now: string;
  league: { synced_at: string; current_week: number } | null;
  actuals_available: Record<string, boolean>;
  cards: Record<"lineup" | "digest" | "retro" | "ros", { envelope: Record<string, unknown> | null }>;
};

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function baseView(): View {
  const lineup = clone(lineupFixture) as Record<string, unknown>;
  const digest = clone(digestFixture) as Record<string, unknown>;
  const retro = clone(retroFixture) as Record<string, unknown>;
  const ros = clone(rosFixture) as Record<string, unknown>;
  lineup.week = 2;
  (lineup.context as Record<string, unknown>).league_synced_at = "2026-09-20T12:22:00Z";
  (lineup.report as Record<string, unknown>).missing_projections = [];
  (lineup.report as Record<string, unknown>).injury_as_of = "2026-09-20T13:02:00Z";
  digest.week = 2;
  (digest.report as Record<string, unknown>).injury_as_of = "2026-09-20T13:02:00Z";
  (digest.report as Record<string, unknown>).llm = { haiku: true, sonnet: true, error: null };
  (digest.report as Record<string, unknown>).narrative =
    "Derrick Henry practiced in full and is a clear start. <b>Not markup.</b> Flex Filler stays a bench option.";
  ros.week = 2;
  return {
    season: 2024,
    week: 2,
    server_now: NOW,
    league: { synced_at: "2026-09-20T12:22:00Z", current_week: 2 },
    actuals_available: { "1": true },
    weeks: [1, 2],
    cards: {
      lineup: { envelope: lineup },
      digest: { envelope: digest },
      retro: { envelope: retro },
      ros: { envelope: ros },
    },
  };
}

const YAHOO = "yahoo:470.l.928421";
const SLEEPER = "sleeper:1395854363380965376";

type Directory = { default_league: string; leagues: Array<Record<string, unknown>> };

function league(league_key: string, name: string, source: string, team_name: string | null = null): Record<string, unknown> {
  return { league_key, name, source, season: 2024, current_week: 2, num_teams: 10, synced_at: "2026-09-20T12:22:00Z", team_name };
}

const TWO_LEAGUES: Directory = {
  default_league: YAHOO,
  leagues: [league(YAHOO, "Money League", "yahoo", "Brian's Team"), league(SLEEPER, "Dynasty", "sleeper", "Bench Mob")],
};

async function serve(page: Page, view: View, requests: string[] = [], directory?: Directory): Promise<void> {
  await page.route("**/api/inseason*", (route) => {
    requests.push(new URL(route.request().url()).search);
    return route.fulfill({ json: view });
  });
  // Absent by default: the picker then stays hidden, which is what a
  // single-league deployment looks like.
  await page.route("**/api/leagues*", (route) =>
    directory ? route.fulfill({ json: directory }) : route.fulfill({ status: 404, json: { error: "not found" } }),
  );
}

async function open(
  page: Page,
  view: View,
  options: { width?: number; now?: string; requests?: string[]; directory?: Directory; path?: string } = {},
): Promise<void> {
  await page.setViewportSize({ width: options.width ?? 1440, height: 900 });
  await page.clock.setFixedTime(new Date(options.now ?? NOW));
  await page.addInitScript(() => localStorage.setItem("ffb.trackerKey", "test-secret-key"));
  await serve(page, view, options.requests, options.directory);
  await page.goto(options.path ?? "/command");
  await expect(page.locator("[data-grid] .card")).toHaveCount(4);
}

function state(page: Page, kind: string) {
  return page.locator(`[data-grid] .card.${kind}`).getAttribute("data-state");
}

test("desktop grid shows four fresh cards with the week, team, and oldest source", async ({ page }) => {
  await open(page, baseView());
  await expect(page.locator("[data-week]")).toHaveText("Week 2");
  await expect(page.locator("[data-team]")).toHaveText("Brian's Team");
  await expect(page.locator("[data-oldest-text]")).toHaveText("Oldest source: Retro, 4d");
  for (const kind of ["lineup", "digest", "retro", "ros"]) {
    expect(await state(page, kind)).toBe("fresh");
    await expect(page.locator(`[data-grid] .card.${kind} .badge`)).toContainText("fresh ·");
  }
  await expect(page.locator(".card.lineup [data-headline]")).toHaveText("+15.0");
  await expect(page.locator(".card.lineup")).toContainText("Derrick Henry");
  await expect(page.locator(".card.lineup .cfoot")).toContainText("Retro grades the Sun 10:05 AM ET snapshot");
  await expect(page.locator(".card.digest [data-headline]")).toHaveText("1");
  await expect(page.locator(".card.digest .excerpt")).toHaveText("Derrick Henry practiced in full and is a clear start.");
  await expect(page.locator(".card.retro [data-headline]")).toHaveText("-21.0");
  await expect(page.locator(".card.retro [data-hindsight]")).toHaveText("-33.3");
  await expect(page.locator(".card.retro")).toContainText("W 41.5–18.0");
  await expect(page.locator(".card.retro")).toContainText("Derrick Henry");
  await expect(page.locator(".card.retro")).toContainText("Malik Washington");
  await expect(page.locator(".card.retro")).toContainText("hindsight start · benched");
  await expect(page.locator(".card.retro")).toContainText("Rico Dowdle");
  await expect(page.locator(".card.ros [data-headline]")).toHaveText("15·16·17");
  await expect(page.locator(".card.ros")).toContainText("bye 5");

  const boxes = await page.evaluate(() => {
    const box = (selector: string) => document.querySelector(selector)!.getBoundingClientRect();
    return {
      lineup: box(".card.lineup"),
      news: box(".card.digest"),
      retro: box(".card.retro"),
      ros: box(".card.ros"),
      overflow: document.documentElement.scrollWidth > innerWidth,
    };
  });
  expect(boxes.overflow).toBe(false);
  expect(boxes.lineup.width).toBeGreaterThan(boxes.news.width * 1.8);
  expect(boxes.news.left).toBeGreaterThan(boxes.lineup.right - 1);
  expect(boxes.retro.top).toBeGreaterThanOrEqual(boxes.lineup.bottom);
  expect(boxes.ros.left).toBeGreaterThan(boxes.retro.right - 1);
  expect(Math.abs(boxes.ros.top - boxes.retro.top)).toBeLessThan(2);
  await page.screenshot({ path: "/tmp/ffb-command-1440.png", fullPage: true });
});

test("a sit/start swap that is also a close call renders as one close-call recommendation", async ({ page }) => {
  const view = baseView();
  const report = view.cards.lineup.envelope!.report as Record<string, unknown>;
  report.close_calls = [
    { name: "Slot Receiver", points: 17.8, versus: "Other Flex", slot: "W/R/T", delta: 0.2 },
    { name: "Flex Filler", points: 3.0, versus: "Derrick Henry", slot: "W/R/T", delta: 1.0 },
  ];
  await open(page, view);
  const rows = page.locator(".card.lineup .rows .row");
  await expect(rows).toHaveCount(2);
  await expect(rows.locator(".k")).toHaveText(["close call", "close call"]);
  await expect(rows.locator(".v")).toHaveText([
    "Start Derrick Henry vs. Flex Filler at W/R/T",
    "Slot Receiver vs Other Flex at W/R/T",
  ]);
  await expect(rows.locator(".n")).toHaveText(["−1.0", "−0.2"]);
  const lefts = await rows.locator(".v").evaluateAll((nodes) => nodes.map((node) => node.getBoundingClientRect().left));
  expect(lefts[1]).toBeCloseTo(lefts[0], 0);
});

test("minimum desktop widths collapse to two columns without horizontal overflow", async ({ page }) => {
  await open(page, baseView(), { width: 1100 });
  for (const width of [1100, 1024]) {
    await page.setViewportSize({ width, height: 900 });
    const boxes = await page.evaluate(() => {
      const box = (selector: string) => document.querySelector(selector)!.getBoundingClientRect();
      return { lineup: box(".card.lineup"), news: box(".card.digest"), retro: box(".card.retro"), ros: box(".card.ros"), overflow: document.documentElement.scrollWidth > innerWidth };
    });
    expect(boxes.overflow).toBe(false);
    expect(boxes.news.top).toBeGreaterThanOrEqual(boxes.lineup.bottom);
    expect(Math.abs(boxes.news.top - boxes.retro.top)).toBeLessThan(2);
    expect(boxes.ros.top).toBeGreaterThanOrEqual(boxes.retro.bottom);
    expect(boxes.ros.width).toBeGreaterThan(boxes.retro.width * 1.8);
    await page.screenshot({ path: `/tmp/ffb-command-${width}.png`, fullPage: true });
  }
});

test("stale, degraded, waiting, and missing states render with their reasons", async ({ page }) => {
  const rosterChanged = baseView();
  rosterChanged.league = { synced_at: "2026-09-20T15:10:00Z", current_week: 2 };
  await open(page, rosterChanged);
  expect(await state(page, "lineup")).toBe("stale");
  await expect(page.locator(".card.lineup [data-reason]")).toHaveText("Roster changed after this lineup was built");
  await expect(page.locator(".card.lineup .badge")).toContainText("stale ·");

  const degraded = baseView();
  (degraded.cards.lineup.envelope!.report as Record<string, unknown[]>).missing_projections = [
    { name: "Rookie Bench", position: "RB", team: "CHI", slot: "BN", points: null, selected_position: "BN" },
  ];
  (degraded.cards.digest.envelope!.report as Record<string, unknown>).llm = { haiku: false, sonnet: false, error: "LLM skipped (no ANTHROPIC_API_KEY / FFB_ANTHROPIC_API_KEY)." };
  (degraded.cards.digest.envelope!.report as Record<string, unknown>).narrative = null;
  (degraded.cards.retro.envelope!.report as Record<string, unknown[]>).missing_actuals = [{ native_id: "1", name: "Jake Bates" }];
  await serve(page, degraded);
  await page.reload();
  expect(await state(page, "lineup")).toBe("degraded");
  await expect(page.locator(".card.lineup [data-reason]")).toHaveText("1 player has no projection");
  expect(await state(page, "digest")).toBe("degraded");
  await expect(page.locator(".card.digest [data-reason]")).toHaveText("LLM skipped — headlines only");
  await expect(page.locator(".card.digest .excerpt")).toHaveText("No narrative. Headlines and injury designations only.");
  expect(await state(page, "retro")).toBe("degraded");
  await expect(page.locator(".card.retro [data-reason]")).toHaveText("1 player has no actuals");
  await page.screenshot({ path: "/tmp/ffb-command-degraded.png", fullPage: true });

  await serve(page, baseView());
  await page.clock.setFixedTime(new Date(Date.parse(NOW) + 6 * DAY_MS));
  await page.reload();
  expect(await state(page, "lineup")).toBe("stale");
  await expect(page.locator(".card.lineup [data-reason]")).toHaveText("Built more than 5 days ago");
  expect(await state(page, "digest")).toBe("stale");
  expect(await state(page, "ros")).toBe("stale");
  await expect(page.locator(".card.ros [data-reason]")).toHaveText("Built more than 8 days ago");
  expect(await state(page, "retro")).toBe("fresh");
  await expect(page.locator("[data-oldest]")).not.toHaveClass(/ok/);

  const weekOne = baseView();
  weekOne.week = 1;
  weekOne.weeks = [1];
  weekOne.league = { synced_at: "2026-09-20T12:22:00Z", current_week: 1 };
  weekOne.actuals_available = {};
  weekOne.cards.lineup.envelope = null;
  weekOne.cards.digest.envelope = null;
  weekOne.cards.retro.envelope = null;
  weekOne.cards.ros.envelope!.week = 1;
  await serve(page, weekOne);
  await page.clock.setFixedTime(new Date(NOW));
  await page.reload();
  await expect(page.locator("[data-week]")).toHaveText("Week 1");
  expect(await state(page, "lineup")).toBe("missing");
  await expect(page.locator(".card.lineup code")).toHaveText("ffb lineup 2024 --week 1 --publish");
  expect(await state(page, "digest")).toBe("missing");
  expect(await state(page, "retro")).toBe("waiting");
  await expect(page.locator(".card.retro [data-reason]")).toHaveText("No prior week to grade");
  await expect(page.locator(".card.lineup")).toHaveJSProperty("tagName", "SECTION");
  await expect(page.locator("[data-week-prev]")).toBeDisabled();
  await expect(page.locator("[data-week-next]")).toBeDisabled();
  await page.screenshot({ path: "/tmp/ffb-command-week1.png", fullPage: true });

  const waiting = baseView();
  waiting.week = 3;
  waiting.weeks = [1, 2, 3];
  waiting.league = { synced_at: "2026-09-30T10:40:00Z", current_week: 3 };
  waiting.actuals_available = { "2": false };
  waiting.cards.lineup.envelope = null;
  waiting.cards.digest.envelope = null;
  waiting.cards.retro.envelope = null;
  await serve(page, waiting);
  await page.reload();
  expect(await state(page, "retro")).toBe("waiting");
  await expect(page.locator(".card.retro [data-reason]")).toHaveText("Waiting for week 2 actuals");
  await expect(page.locator(".card.retro")).toContainText("Upstream data does not exist yet.");
  waiting.actuals_available = { "2": true };
  await serve(page, waiting);
  await page.reload();
  expect(await state(page, "retro")).toBe("missing");
  await expect(page.locator(".card.retro code")).toHaveText("ffb retro 2024 --week 2 --publish");
});

test("the detail panel opens and closes from the keyboard and renders report text safely", async ({ page }) => {
  await open(page, baseView());
  const lineup = page.locator(".card.lineup");
  await lineup.focus();
  await page.keyboard.press("Enter");
  const panel = page.getByRole("dialog", { name: "Lineup · week 2" });
  await expect(panel).toBeVisible();
  await expect(lineup).toHaveAttribute("aria-expanded", "true");
  await expect(page.getByRole("button", { name: "Close panel" })).toBeFocused();
  await expect(panel).toContainText("Slots");
  await expect(panel.locator("table").first().locator("tbody tr")).toHaveCount(4);
  await expect(panel).toContainText("Retro will grade the sit/start snapshot written Sun 10:05 AM ET");
  await page.keyboard.press("Escape");
  await expect(panel).toBeHidden();
  await expect(lineup).toBeFocused();
  await expect(lineup).toHaveAttribute("aria-expanded", "false");

  await page.locator(".card.digest").click();
  const digest = page.getByRole("dialog", { name: "News · week 2" });
  await expect(digest).toBeVisible();
  await expect(digest).toContainText("<b>Not markup.</b>");
  expect(await digest.locator("b", { hasText: "Not markup." }).count()).toBe(0);
  const secure = digest.locator("a", { hasText: "Derrick Henry questionable for Week 1" });
  await expect(secure).toHaveAttribute("rel", "noopener noreferrer");
  await expect(secure).toHaveAttribute("target", "_blank");
  await expect(digest.locator("li", { hasText: "Unknown specialist signs" }).locator("a")).toHaveCount(0);
  await page.locator("[data-scrim]").click({ position: { x: 10, y: 10 } });
  await expect(digest).toBeHidden();

  await page.locator(".card.ros").click();
  const ros = page.getByRole("dialog", { name: "Rest of season · week 2" });
  await expect(ros).toContainText("Playoff schedule strength");
  await ros.getByRole("button", { name: "WR", exact: true }).click();
  await expect(ros.locator("table").first()).toContainText("Ja'Marr Chase");
  await expect(ros.locator("table").first()).not.toContainText("Derrick Henry");
  await page.keyboard.press("Escape");

  await page.locator(".card.retro").click();
  const retro = page.getByRole("dialog", { name: "Retro · week 1" });
  await expect(retro).toContainText("A hit means the started lineup followed the advice; a miss means it did not.");
  await expect(retro).toContainText("Hindsight is the best actuals lineup from the snapshotted roster.");
  await expect(retro).toContainText("Start misses (1)");
  await expect(retro).toContainText("Hindsight start (2)");
  const startMissDelta = retro.locator("h3", { hasText: "Start misses (1)" }).locator("+ .tablewrap td.bad");
  const hindsightStartDelta = retro.locator("h3", { hasText: "Hindsight start (2)" }).locator("+ .tablewrap td.bad");
  await expect(startMissDelta).toHaveCount(1);
  await expect(hindsightStartDelta).toHaveCount(2);
  await expect(retro.locator("h3", { hasText: "Hindsight start (2)" }).locator("+ .tablewrap td.good")).toHaveCount(0);
});

test("old retro reports without hindsight keys still render advice only", async ({ page }) => {
  const view = baseView();
  const report = (view.cards.retro.envelope!.report as Record<string, unknown>);
  delete report.hindsight_total;
  delete report.hindsight_started_total;
  delete report.hindsight_delta;
  delete report.hindsight_start;
  delete report.hindsight_sit;
  await open(page, view);
  await expect(page.locator(".card.retro [data-headline]")).toHaveText("-21.0");
  await expect(page.locator(".card.retro [data-hindsight]")).toHaveCount(0);
  await expect(page.locator(".card.retro")).toContainText("Derrick Henry");
  await expect(page.locator(".card.retro")).not.toContainText("Malik Washington");
  await expect(page.locator(".card.retro")).not.toContainText("hindsight start");
  await page.locator(".card.retro").click();
  const retro = page.getByRole("dialog", { name: "Retro · week 1" });
  await expect(retro).toContainText("Hindsight is the best actuals lineup from the snapshotted roster.");
  await expect(retro).not.toContainText("Hindsight start");
});

test("the week picker requests the neighbouring week and the API key gate works", async ({ page }) => {
  const requests: string[] = [];
  await open(page, baseView(), { requests });
  expect(requests).toEqual([""]);
  await expect(page).toHaveURL(/\?week=2$/);
  await page.locator("[data-week-prev]").click();
  await expect.poll(() => requests.length).toBe(2);
  expect(requests[1]).toBe("?season=2024&week=1");
  await expect(page).toHaveURL(/week=2$/);

  await page.context().clearCookies();
  await page.addInitScript(() => localStorage.removeItem("ffb.trackerKey"));
  await page.route("**/api/inseason*", (route) => {
    const auth = route.request().headers()["authorization"];
    return auth === "Bearer test-secret-key" ? route.fulfill({ json: baseView() }) : route.fulfill({ status: 401, json: { error: "unauthorized" } });
  });
  await page.goto("/command");
  await expect(page.getByRole("dialog", { name: "Unlock the command center" })).toBeVisible();
  await page.getByRole("textbox", { name: "API key" }).fill("wrong");
  await page.getByRole("button", { name: "Unlock" }).click();
  await expect(page.locator("[data-key-error]")).toHaveText("Invalid API key. Check it and try again.");
  await page.getByRole("textbox", { name: "API key" }).fill("test-secret-key");
  await page.keyboard.press("Enter");
  await expect(page.getByRole("dialog", { name: "Unlock the command center" })).toBeHidden();
  await expect(page.locator("[data-grid] .card")).toHaveCount(4);
  expect(await page.evaluate(() => localStorage.getItem("ffb.trackerKey"))).toBe("test-secret-key");
});

// ---- league picker ----

test("hides the picker when the deployment has one league", async ({ page }) => {
  await open(page, baseView(), { directory: { default_league: YAHOO, leagues: [league(YAHOO, "Money League", "yahoo")] } });
  await expect(page.locator("[data-leaguepick]")).toBeHidden();
});

test("hides the picker when the league directory is unavailable", async ({ page }) => {
  await open(page, baseView());
  await expect(page.locator("[data-leaguepick]")).toBeHidden();
  // The dashboard itself still loaded — a missing directory is not fatal.
  await expect(page.locator("[data-status]")).toBeHidden();
  await expect(page.locator("[data-week]")).toHaveText("Week 2");
});

test("lists both leagues, default first, with the default selected", async ({ page }) => {
  await open(page, baseView(), { directory: TWO_LEAGUES });
  const select = page.locator("[data-league-select]");
  await expect(page.locator("[data-leaguepick]")).toBeVisible();
  await expect(select.locator("option")).toHaveText([
    "Money League",
    "Dynasty",
  ]);
  await expect(select).toHaveValue(YAHOO);
});

test("switching leagues reloads against that league and forgets the old week", async ({ page }) => {
  const requests: string[] = [];
  await open(page, baseView(), { directory: TWO_LEAGUES, requests, path: "/command?week=2" });
  await expect(page.locator("[data-league-select]")).toHaveValue(YAHOO);
  requests.length = 0;

  await page.locator("[data-league-select]").selectOption(SLEEPER);
  await expect.poll(() => requests.length).toBeGreaterThan(0);

  const params = new URLSearchParams(requests[0]!);
  expect(params.get("league")).toBe(SLEEPER);
  // Season and week are per-league, so neither is carried across.
  expect(params.get("week")).toBeNull();
  expect(params.get("season")).toBeNull();

  // The URL names the new league and drops the old league's week.
  await expect.poll(() => new URL(page.url()).searchParams.get("league")).toBe(SLEEPER);
  await expect(page.locator("[data-league-select]")).toHaveValue(SLEEPER);
});

test("a ?league= deep link preselects that league and sends it upstream", async ({ page }) => {
  const requests: string[] = [];
  await open(page, baseView(), { directory: TWO_LEAGUES, requests, path: `/command?league=${encodeURIComponent(SLEEPER)}` });
  await expect(page.locator("[data-league-select]")).toHaveValue(SLEEPER);
  expect(new URLSearchParams(requests[0]!).get("league")).toBe(SLEEPER);
});

test("keeps a league the directory does not list rather than swapping it", async ({ page }) => {
  const unknown = "sleeper:999";
  await open(page, baseView(), { directory: TWO_LEAGUES, path: `/command?league=${encodeURIComponent(unknown)}` });
  const select = page.locator("[data-league-select]");
  await expect(select).toHaveValue(unknown);
  await expect(select.locator("option")).toHaveText([
    "Money League",
    "Dynasty",
    "sleeper:999 (not published)",
  ]);
});

test("names the team from the league bundle when nothing is published", async ({ page }) => {
  const empty = baseView();
  for (const kind of ["lineup", "digest", "retro", "ros"] as const) empty.cards[kind] = { envelope: null };
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.clock.setFixedTime(new Date(NOW));
  await page.addInitScript(() => localStorage.setItem("ffb.trackerKey", "test-secret-key"));
  await serve(page, empty, [], TWO_LEAGUES);
  await page.goto(`/command?league=${encodeURIComponent(SLEEPER)}`);
  await expect(page.locator("[data-league-select]")).toHaveValue(SLEEPER);
  await expect(page.locator("[data-team]")).toHaveText("Bench Mob");
});

test("a league switch supersedes a week request still in flight", async ({ page }) => {
  // The week request is answered slowly and the league request immediately, so
  // the stale week response lands last. It must not repaint the page: its cards
  // belong to the league the user just left, under the new league's name.
  const slow = baseView();
  slow.week = 3;
  slow.weeks = [1, 2, 3];
  const fast = baseView();
  fast.week = 5;
  fast.weeks = [5];
  for (const kind of ["lineup", "digest", "retro", "ros"] as const) {
    const envelope = fast.cards[kind].envelope;
    if (envelope) envelope.team_name = "Bench Mob";
  }

  await page.setViewportSize({ width: 1440, height: 900 });
  await page.clock.setFixedTime(new Date(NOW));
  await page.addInitScript(() => localStorage.setItem("ffb.trackerKey", "test-secret-key"));
  await page.route("**/api/leagues*", (route) => route.fulfill({ json: TWO_LEAGUES }));
  // Week 2 of 1–3, so the "next week" button is enabled to start the race.
  const initial = baseView();
  initial.weeks = [1, 2, 3];
  let first = true;
  await page.route("**/api/inseason*", async (route) => {
    const league = new URL(route.request().url()).searchParams.get("league");
    if (first) {
      first = false;
      return route.fulfill({ json: initial });
    }
    if (league === SLEEPER) return route.fulfill({ json: fast });
    await new Promise((resolve) => setTimeout(resolve, 1200));
    return route.fulfill({ json: slow });
  });
  await page.goto("/command");
  await expect(page.locator("[data-grid] .card")).toHaveCount(4);
  await expect(page.locator("[data-league-select]")).toHaveValue(YAHOO);

  await page.locator("[data-week-next]").click();
  await page.locator("[data-league-select]").selectOption(SLEEPER);

  await expect(page.locator("[data-week]")).toHaveText("Week 5");
  await expect(page.locator("[data-team]")).toHaveText("Bench Mob");
  // Well past the slow response; the superseded answer stays discarded.
  await page.waitForTimeout(1600);
  await expect(page.locator("[data-week]")).toHaveText("Week 5");
  await expect(page.locator("[data-team]")).toHaveText("Bench Mob");
  await expect(page.locator("[data-league-select]")).toHaveValue(SLEEPER);
});

test("the picker survives the narrow viewport without overflowing", async ({ page }) => {
  await open(page, baseView(), { width: 420, directory: TWO_LEAGUES });
  await expect(page.locator("[data-leaguepick]")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
});
