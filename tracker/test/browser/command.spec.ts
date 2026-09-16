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

async function serve(page: Page, view: View, requests: string[] = []): Promise<void> {
  await page.route("**/api/inseason*", (route) => {
    requests.push(new URL(route.request().url()).search);
    return route.fulfill({ json: view });
  });
}

async function open(page: Page, view: View, options: { width?: number; now?: string; requests?: string[] } = {}): Promise<void> {
  await page.setViewportSize({ width: options.width ?? 1440, height: 900 });
  await page.clock.setFixedTime(new Date(options.now ?? NOW));
  await page.addInitScript(() => localStorage.setItem("ffb.trackerKey", "test-secret-key"));
  await serve(page, view, options.requests);
  await page.goto("/command");
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
  await expect(page.locator(".card.retro [data-hindsight]")).toHaveText("hindsight -22.0 vs started");
  await expect(page.locator(".card.retro")).toContainText("W 41.5–18.0");
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
  (degraded.cards.retro.envelope!.report as Record<string, unknown[]>).missing_actuals = [{ yahoo_player_id: "1", name: "Jake Bates" }];
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
  await expect(retro).toContainText("Start misses (1)");
  await expect(retro).toContainText("Hindsight lineup 47.5");
  await expect(retro).toContainText("Hindsight start (1)");
  await expect(retro).toContainText("Hindsight sit (1)");
  await expect(retro).toContainText("Derrick Henry");
  await expect(retro).toContainText("Slow Back");
});

test("a retro envelope without hindsight keys keeps the advice headline", async ({ page }) => {
  const legacy = baseView();
  const report = legacy.cards.retro.envelope!.report as Record<string, unknown>;
  delete report.hindsight_total;
  delete report.hindsight_delta;
  delete report.hindsight_start;
  delete report.hindsight_sit;
  await open(page, legacy);
  await expect(page.locator(".card.retro [data-headline]")).toHaveText("-21.0");
  await expect(page.locator(".card.retro [data-hindsight]")).toHaveCount(0);
  await page.locator(".card.retro").click();
  const panel = page.getByRole("dialog", { name: "Retro · week 1" });
  await expect(panel).toContainText("Start misses (1)");
  await expect(panel).not.toContainText("Hindsight lineup");
  await expect(panel).not.toContainText("Hindsight start");
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
