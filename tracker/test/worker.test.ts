import { describe, it, expect, beforeAll, beforeEach } from "vitest";
import { SELF, env } from "cloudflare:test";
import { BOARD_KEY } from "../src/board";
import { buildPlayerPool } from "../src/player-pool";
import { mockSuggestions } from "../src/mock-ui";
import fixtureJson from "./fixtures/board.json";
import type { Board } from "../src/types";

const KEY = "test-secret-key"; // matches vitest.config.ts miniflare binding
const fixtureText = JSON.stringify(fixtureJson);
const mockFixture = {
  ...fixtureJson,
  players: [
    ...fixtureJson.players,
    ...Array.from({ length: 12 * 15 - fixtureJson.players.length }, (_, index) => ({
      ...fixtureJson.players[0],
      key: `mock-extra-${index}`,
      name: `Mock Extra ${index}`,
      pos: ["QB", "RB", "WR", "TE", "K", "DEF"][index % 6],
      team: `T${index}`,
      rank: fixtureJson.players.length + index + 1,
      pos_rank: index + 10,
      adp: 140 + index,
      adp_rank: fixtureJson.players.length + index + 1,
    })),
  ],
} as Board;
const mockFixtureText = JSON.stringify(mockFixture);
const workerYahooRoster = {
  QB: 1, WR: 1, RB: 1, TE: 1, "W/T": 1, "W/R/T": 2, DEF: 1, BN: 8,
};
const workerYahooPositions = [
  ...Array(25).fill("QB"),
  ...Array(45).fill("RB"),
  ...Array(45).fill("WR"),
  ...Array(25).fill("TE"),
  ...Array(20).fill("DEF"),
] as string[];
const workerYahooBoard = {
  ...mockFixture,
  num_teams: 10,
  roster_slots: workerYahooRoster,
  scoring: "Yahoo half PPR",
  players: workerYahooPositions.map((pos, index) => ({
    ...mockFixture.players[index % mockFixture.players.length]!,
    key: `worker-yahoo-${index}`,
    name: `Worker Yahoo ${pos} ${index}`,
    pos,
    team: `WY${index}`,
    rank: index + 1,
    pos_rank: index + 1,
    adp: index + 1,
    adp_rank: index + 1,
  })),
} as Board;

function bearer(key: string): HeadersInit {
  return { Authorization: `Bearer ${key}` };
}

describe("Worker /api/health", () => {
  it("is a public 200 liveness ping", async () => {
    const res = await SELF.fetch("https://x/api/health");
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ ok: true });
  });
});

describe("Worker /api/board auth", () => {
  beforeAll(async () => {
    await env.BOARD.put(BOARD_KEY, fixtureText);
  });

  it("rejects a missing key (401)", async () => {
    expect((await SELF.fetch("https://x/api/board")).status).toBe(401);
  });

  it("rejects a wrong key (401)", async () => {
    const res = await SELF.fetch("https://x/api/board", { headers: bearer("nope") });
    expect(res.status).toBe(401);
  });

  it("serves the KV board verbatim to a valid key (200)", async () => {
    const res = await SELF.fetch("https://x/api/board", { headers: bearer(KEY) });
    expect(res.status).toBe(200);
    expect(res.headers.get("content-type")).toContain("application/json");
    expect(await res.text()).toBe(fixtureText);
  });
});

describe("Worker /api/board when nothing is published", () => {
  it("returns 404 so the frontend can prompt a publish", async () => {
    await env.BOARD.delete(BOARD_KEY);
    const res = await SELF.fetch("https://x/api/board", { headers: bearer(KEY) });
    expect(res.status).toBe(404);
  });
});

describe("Worker draft state", () => {
  beforeEach(async () => {
    await env.DB.batch([
      env.DB.prepare("DELETE FROM picks WHERE draft_id = 1"),
      env.DB.prepare("DELETE FROM teams WHERE draft_id = 1"),
      env.DB.prepare("DELETE FROM drafts WHERE id = 1"),
    ]);
    await env.BOARD.put(BOARD_KEY, fixtureText);
  });

  it("returns an authenticated unconfigured draft state", async () => {
    const res = await SELF.fetch("https://x/api/draft", { headers: bearer(KEY) });
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ configured: false, picks: [], revision: 0 });
  });

  it("configures, records, and undoes a canonical board player", async () => {
    const config = await SELF.fetch("https://x/api/draft", {
      method: "PUT",
      headers: { ...bearer(KEY), "content-type": "application/json" },
      body: JSON.stringify({
        name: "Home League",
        rounds: 2,
        teams: [
          { name: "Brian", is_user: true },
          { name: "Other", is_user: false },
        ],
      }),
    });
    expect(config.status).toBe(200);
    const configured = (await config.json()) as { next: { overall_pick: number } };
    expect(configured.next.overall_pick).toBe(1);

    const pick = await SELF.fetch("https://x/api/picks", {
      method: "POST",
      headers: { ...bearer(KEY), "content-type": "application/json" },
      body: JSON.stringify({ player_key: fixtureJson.players[0].key, expected_overall_pick: 1 }),
    });
    expect(pick.status).toBe(201);
    expect(await pick.json()).toMatchObject({ picks: [{ player_key: fixtureJson.players[0].key }], next: { overall_pick: 2, team_name: "Other" } });

    const undo = await SELF.fetch("https://x/api/picks/latest", {
      method: "DELETE",
      headers: { ...bearer(KEY), "content-type": "application/json" },
      body: JSON.stringify({ expected_overall_pick: 1 }),
    });
    expect(undo.status).toBe(200);
    expect(await undo.json()).toMatchObject({ picks: [], next: { overall_pick: 1 } });
  });

  it("uses stable conflict and validation errors instead of mutating stale state", async () => {
    const invalid = await SELF.fetch("https://x/api/draft", {
      method: "PUT",
      headers: { ...bearer(KEY), "content-type": "application/json" },
      body: JSON.stringify({ rounds: 16, teams: [{ name: "Only team", is_user: true }] }),
    });
    expect(invalid.status).toBe(400);
    expect(await invalid.json()).toMatchObject({ error: "invalid_draft" });

    await SELF.fetch("https://x/api/draft", {
      method: "PUT",
      headers: { ...bearer(KEY), "content-type": "application/json" },
      body: JSON.stringify({ rounds: 1, teams: [{ name: "Brian", is_user: true }, { name: "Other", is_user: false }] }),
    });
    const unknown = await SELF.fetch("https://x/api/picks", {
      method: "POST",
      headers: { ...bearer(KEY), "content-type": "application/json" },
      body: JSON.stringify({ player_key: "not-on-board", expected_overall_pick: 1 }),
    });
    expect(unknown.status).toBe(422);
    expect(await unknown.json()).toMatchObject({ error: "unknown_player" });

    const staleUndo = await SELF.fetch("https://x/api/picks/latest", {
      method: "DELETE",
      headers: { ...bearer(KEY), "content-type": "application/json" },
      body: JSON.stringify({ expected_overall_pick: 1 }),
    });
    expect(staleUndo.status).toBe(409);
    expect(await staleUndo.json()).toMatchObject({ error: "no_picks" });
  });

  it("records a validated manual snapshot and rejects its equivalent canonical board row", async () => {
    await SELF.fetch("https://x/api/draft", {
      method: "PUT",
      headers: { ...bearer(KEY), "content-type": "application/json" },
      body: JSON.stringify({ rounds: 1, teams: [{ name: "Brian", is_user: true }, { name: "Other", is_user: false }] }),
    });
    const manual = await SELF.fetch("https://x/api/picks", {
      method: "POST",
      headers: { ...bearer(KEY), "content-type": "application/json" },
      body: JSON.stringify({ expected_overall_pick: 1, manual_player: { name: "  Mystery DST  ", pos: "DST", team: " nyj " } }),
    });
    expect(manual.status).toBe(201);
    expect(await manual.json()).toMatchObject({ picks: [{ player_key: expect.stringMatching(/^manual:/), player_name: "Mystery DST", player_pos: "DEF", player_team: "NYJ" }] });

    const duplicate = await SELF.fetch("https://x/api/picks", {
      method: "POST",
      headers: { ...bearer(KEY), "content-type": "application/json" },
      body: JSON.stringify({ expected_overall_pick: 2, player_key: fixtureJson.players.find((player) => player.pos === "DEF")?.key }),
    });
    expect(duplicate.status).toBe(409);
    expect(await duplicate.json()).toMatchObject({ error: "player_already_picked" });
  });

  it("keeps ambiguous canonical board rows recordable after an equivalent manual pick", async () => {
    const original = fixtureJson.players[0]!;
    const ambiguousBoard = {
      ...fixtureJson,
      players: [
        { ...original, key: "canonical:one", name: "Ambiguous Player" },
        { ...original, key: "canonical:two", name: "Ambiguous Player" },
      ],
    };
    await env.BOARD.put(BOARD_KEY, JSON.stringify(ambiguousBoard));
    await SELF.fetch("https://x/api/draft", {
      method: "PUT",
      headers: { ...bearer(KEY), "content-type": "application/json" },
      body: JSON.stringify({ rounds: 1, teams: [{ name: "Brian", is_user: true }, { name: "Other", is_user: false }] }),
    });
    const manual = await SELF.fetch("https://x/api/picks", {
      method: "POST",
      headers: { ...bearer(KEY), "content-type": "application/json" },
      body: JSON.stringify({
        expected_overall_pick: 1,
        manual_player: { name: "Ambiguous Player", pos: original.pos, team: original.team },
      }),
    });
    expect(manual.status).toBe(201);

    const canonical = await SELF.fetch("https://x/api/picks", {
      method: "POST",
      headers: { ...bearer(KEY), "content-type": "application/json" },
      body: JSON.stringify({ player_key: "canonical:one", expected_overall_pick: 2 }),
    });

    expect(canonical.status).toBe(201);
  });

  it("requires a teamless manual entry to use an already-listed board row", async () => {
    await SELF.fetch("https://x/api/draft", {
      method: "PUT",
      headers: { ...bearer(KEY), "content-type": "application/json" },
      body: JSON.stringify({ rounds: 1, teams: [{ name: "Brian", is_user: true }, { name: "Other", is_user: false }] }),
    });
    const response = await SELF.fetch("https://x/api/picks", {
      method: "POST",
      headers: { ...bearer(KEY), "content-type": "application/json" },
      body: JSON.stringify({ expected_overall_pick: 1, manual_player: { name: fixtureJson.players[0].name, pos: fixtureJson.players[0].pos, team: null } }),
    });
    expect(response.status).toBe(422);
    expect(await response.json()).toMatchObject({ error: "manual_player_matches_board" });
  });

  it("does not let an unknown-position manual entry duplicate a listed player", async () => {
    await SELF.fetch("https://x/api/draft", {
      method: "PUT",
      headers: { ...bearer(KEY), "content-type": "application/json" },
      body: JSON.stringify({ rounds: 1, teams: [{ name: "Brian", is_user: true }, { name: "Other", is_user: false }] }),
    });
    const response = await SELF.fetch("https://x/api/picks", {
      method: "POST",
      headers: { ...bearer(KEY), "content-type": "application/json" },
      body: JSON.stringify({ expected_overall_pick: 1, manual_player: { name: fixtureJson.players[0].name, pos: "Unknown", team: fixtureJson.players[0].team } }),
    });
    expect(response.status).toBe(422);
    expect(await response.json()).toMatchObject({ error: "manual_player_matches_board" });
  });

  it("rejects an unconfigured draft before attempting to read the board", async () => {
    await env.BOARD.delete(BOARD_KEY);
    const response = await SELF.fetch("https://x/api/picks", {
      method: "POST",
      headers: { ...bearer(KEY), "content-type": "application/json" },
      body: JSON.stringify({ player_key: "any", expected_overall_pick: 1 }),
    });
    expect(response.status).toBe(409);
    expect(await response.json()).toMatchObject({ error: "draft_unconfigured" });
  });
});

describe("Worker mock draft state", () => {
  beforeEach(async () => {
    await env.DB.batch([
      env.DB.prepare("DELETE FROM mock_checkpoints"),
      env.DB.prepare("DELETE FROM mock_picks"),
      env.DB.prepare("DELETE FROM mock_teams"),
      env.DB.prepare("DELETE FROM mock_drafts"),
      env.DB.prepare("DELETE FROM mock_boards"),
      env.DB.prepare("DELETE FROM picks WHERE draft_id = 1"),
      env.DB.prepare("DELETE FROM teams WHERE draft_id = 1"),
      env.DB.prepare("DELETE FROM drafts WHERE id = 1"),
    ]);
    await env.BOARD.put(BOARD_KEY, mockFixtureText);
  });

  it("requires auth and creates a seeded mock from the published league shape", async () => {
    expect(
      (await SELF.fetch("https://x/api/mocks/current")).status,
    ).toBe(401);

    const absent = await SELF.fetch("https://x/api/mocks/current", {
      headers: bearer(KEY),
    });
    expect(absent.status).toBe(200);
    expect(await absent.json()).toEqual({
      configured: false,
      picks: [],
      revision: 0,
    });

    const created = await SELF.fetch("https://x/api/mocks", {
      method: "POST",
      headers: { ...bearer(KEY), "content-type": "application/json" },
      body: JSON.stringify({ user_slot: 4, seed: 8042 }),
    });
    expect(created.status).toBe(201);
    expect(await created.json()).toMatchObject({
      configured: true,
      mock: {
        seed: 8042,
        strategy_version: "market-need-v1",
        variance_preset: "realistic",
        user_slot: 4,
        team_count: 12,
        rounds: 15,
      },
      picks: [
        { overall_pick: 1, draft_slot: 0, source: "simulated" },
        { overall_pick: 2, draft_slot: 1, source: "simulated" },
        { overall_pick: 3, draft_slot: 2, source: "simulated" },
      ],
      next: {
        overall_pick: 4,
        round: 1,
        round_pick: 4,
        team_name: "Brian",
        is_user: true,
      },
      lifecycle: "active",
      can_undo: false,
      revision: 3,
    });
  });

  it("pauses across reload, resumes, undoes a whole decision, resets, and discards", async () => {
    const created = await SELF.fetch("https://x/api/mocks", {
      method: "POST",
      headers: { ...bearer(KEY), "content-type": "application/json" },
      body: JSON.stringify({ user_slot: 4, seed: 8042 }),
    });
    const initial = await created.json() as import("../src/mock-draft").MockState;
    const target = (revision: number) => ({
      mock_id: initial.mock!.id,
      expected_revision: revision,
    });

    const paused = await SELF.fetch("https://x/api/mocks/current/pause", {
      method: "POST",
      headers: { ...bearer(KEY), "content-type": "application/json" },
      body: JSON.stringify(target(initial.revision)),
    });
    expect(paused.status).toBe(200);
    expect(await paused.json()).toMatchObject({
      lifecycle: "paused",
      revision: initial.revision + 1,
      picks: initial.picks,
      next: initial.next,
    });
    const reloaded = await SELF.fetch("https://x/api/mocks/current", {
      headers: bearer(KEY),
    });
    expect(await reloaded.json()).toMatchObject({
      lifecycle: "paused",
      revision: initial.revision + 1,
      picks: initial.picks,
      next: initial.next,
    });

    const pickedKeys = new Set(initial.picks.map((pick) => pick.player_key));
    const selected = mockFixture.players.find((player) => !pickedKeys.has(player.key))!;
    const blockedPick = await SELF.fetch("https://x/api/mocks/current/picks", {
      method: "POST",
      headers: { ...bearer(KEY), "content-type": "application/json" },
      body: JSON.stringify({
        ...target(initial.revision + 1),
        player_key: selected.key,
      }),
    });
    expect(blockedPick.status).toBe(409);
    expect(await blockedPick.json()).toMatchObject({ error: "mock_paused" });

    const resumed = await SELF.fetch("https://x/api/mocks/current/resume", {
      method: "POST",
      headers: { ...bearer(KEY), "content-type": "application/json" },
      body: JSON.stringify(target(initial.revision + 1)),
    });
    const resumedState = await resumed.json() as import("../src/mock-draft").MockState;
    expect(resumedState).toMatchObject({ lifecycle: "active", revision: initial.revision + 2 });

    const advanced = await SELF.fetch("https://x/api/mocks/current/picks", {
      method: "POST",
      headers: { ...bearer(KEY), "content-type": "application/json" },
      body: JSON.stringify({
        ...target(resumedState.revision),
        player_key: selected.key,
      }),
    });
    const advancedState = await advanced.json() as import("../src/mock-draft").MockState;
    const firstSequence = advancedState.appended_picks!.map((pick) => pick.player_key);
    expect(advancedState.can_undo).toBe(true);

    const undone = await SELF.fetch("https://x/api/mocks/current/picks/latest", {
      method: "DELETE",
      headers: { ...bearer(KEY), "content-type": "application/json" },
      body: JSON.stringify(target(advancedState.revision)),
    });
    const undoneState = await undone.json() as import("../src/mock-draft").MockState;
    expect(undoneState).toMatchObject({
      lifecycle: "active",
      can_undo: false,
      revision: advancedState.revision + 1,
      picks: initial.picks,
      next: initial.next,
    });

    const replayed = await SELF.fetch("https://x/api/mocks/current/picks", {
      method: "POST",
      headers: { ...bearer(KEY), "content-type": "application/json" },
      body: JSON.stringify({
        ...target(undoneState.revision),
        player_key: selected.key,
      }),
    });
    const replayedState = await replayed.json() as import("../src/mock-draft").MockState;
    expect(replayedState.appended_picks!.map((pick) => pick.player_key)).toEqual(firstSequence);

    const reset = await SELF.fetch("https://x/api/mocks/current/reset", {
      method: "POST",
      headers: { ...bearer(KEY), "content-type": "application/json" },
      body: JSON.stringify(target(replayedState.revision)),
    });
    const resetState = await reset.json() as import("../src/mock-draft").MockState;
    expect(resetState).toMatchObject({
      lifecycle: "active",
      can_undo: false,
      revision: replayedState.revision + 1,
      picks: initial.picks,
      next: initial.next,
      mock: initial.mock,
    });

    const discarded = await SELF.fetch("https://x/api/mocks/current", {
      method: "DELETE",
      headers: { ...bearer(KEY), "content-type": "application/json" },
      body: JSON.stringify(target(resetState.revision)),
    });
    expect(discarded.status).toBe(200);
    expect(await discarded.json()).toEqual({ configured: false, picks: [], revision: 0 });
  });

  it.each([
    ["/api/mocks/current/pause", "GET", "POST"],
    ["/api/mocks/current/resume", "DELETE", "POST"],
    ["/api/mocks/current/reset", "GET", "POST"],
    ["/api/mocks/current/picks/latest", "POST", "DELETE"],
  ])("returns an accurate Allow header for %s", async (path, method, allow) => {
    const response = await SELF.fetch(`https://x${path}`, {
      method,
      headers: bearer(KEY),
    });
    expect(response.status).toBe(405);
    expect(response.headers.get("allow")).toBe(allow);
  });

  it("persists an explicit variance preset and rejects unknown presets", async () => {
    const invalid = await SELF.fetch("https://x/api/mocks", {
      method: "POST",
      headers: { ...bearer(KEY), "content-type": "application/json" },
      body: JSON.stringify({ user_slot: 4, seed: 8042, variance_preset: "chaos" }),
    });
    expect(invalid.status).toBe(400);
    expect(await invalid.json()).toMatchObject({ error: "invalid_mock" });

    const created = await SELF.fetch("https://x/api/mocks", {
      method: "POST",
      headers: { ...bearer(KEY), "content-type": "application/json" },
      body: JSON.stringify({ user_slot: 4, seed: 0, variance_preset: "wild" }),
    });
    expect(created.status).toBe(201);
    expect(await created.json()).toMatchObject({
      mock: { seed: 0, strategy_version: "market-need-v1", variance_preset: "wild" },
    });
  });

  it.each(["constructor", "toString", "__proto__"])(
    "rejects inherited property name %s as a variance preset",
    async (variancePreset) => {
      const invalid = await SELF.fetch("https://x/api/mocks", {
        method: "POST",
        headers: { ...bearer(KEY), "content-type": "application/json" },
        body: JSON.stringify({ user_slot: 4, seed: 8042, variance_preset: variancePreset }),
      });

      expect(invalid.status).toBe(400);
      expect(await invalid.json()).toMatchObject({ error: "invalid_mock" });
    },
  );

  it("continues an active seeded-market-v0 mock through the version registry", async () => {
    const created = await SELF.fetch("https://x/api/mocks", {
      method: "POST",
      headers: { ...bearer(KEY), "content-type": "application/json" },
      body: JSON.stringify({ user_slot: 1, seed: 9 }),
    });
    const state = (await created.json()) as { mock: { id: string }; revision: number };
    await env.DB.prepare(
      "UPDATE mock_drafts SET strategy_version = 'seeded-market-v0', rng_state = seed WHERE id = ?",
    ).bind(state.mock.id).run();

    const advanced = await SELF.fetch("https://x/api/mocks/current/picks", {
      method: "POST",
      headers: { ...bearer(KEY), "content-type": "application/json" },
      body: JSON.stringify({
        mock_id: state.mock.id,
        player_key: mockFixture.players[0]!.key,
        expected_revision: state.revision,
      }),
    });
    expect(advanced.status).toBe(201);
    const advancedState = await advanced.json() as import("../src/mock-draft").MockState;
    expect(advancedState).toMatchObject({
      mock: { strategy_version: "seeded-market-v0", variance_preset: "realistic" },
    });

    const reset = await SELF.fetch("https://x/api/mocks/current/reset", {
      method: "POST",
      headers: { ...bearer(KEY), "content-type": "application/json" },
      body: JSON.stringify({
        mock_id: state.mock.id,
        expected_revision: advancedState.revision,
      }),
    });
    expect(reset.status).toBe(200);
    expect(await reset.json()).toMatchObject({
      mock: { strategy_version: "seeded-market-v0" },
      lifecycle: "active",
      picks: [],
      next: { overall_pick: 1, team_name: "Brian" },
    });
  });

  it("maps an impossible user roster choice to 409 without changing revision or RNG", async () => {
    const positions = ["DEF", "QB", "QB", "DEF", "QB", "QB"] as const;
    const scarceBoard = {
      ...mockFixture,
      num_teams: 2,
      roster_slots: { DEF: 1, BN: 2 },
      players: positions.map((pos, index) => ({
        ...mockFixture.players[index]!,
        key: `scarce-${index}`,
        name: `Scarce ${pos} ${index}`,
        pos,
        team: `SC${index}`,
        rank: index + 1,
        pos_rank: index + 1,
        adp: index + 1,
        adp_rank: index + 1,
      })),
    };
    await env.BOARD.put(BOARD_KEY, JSON.stringify(scarceBoard));
    const created = await SELF.fetch("https://x/api/mocks", {
      method: "POST",
      headers: { ...bearer(KEY), "content-type": "application/json" },
      body: JSON.stringify({ user_slot: 1, seed: 1, variance_preset: "calm" }),
    });
    const initial = (await created.json()) as {
      mock: { id: string };
      revision: number;
    };
    await env.DB.batch([
      ...[
        [1, 1, 1, 0, "scarce-0", "Scarce DEF 0", "DEF", "SC0", "user"],
        [2, 1, 2, 1, "scarce-1", "Scarce QB 1", "QB", "SC1", "simulated"],
        [3, 2, 1, 1, "scarce-2", "Scarce QB 2", "QB", "SC2", "simulated"],
      ].map((values) => env.DB.prepare(
        `INSERT INTO mock_picks
          (mock_id, overall_pick, round, round_pick, draft_slot, player_key,
           player_name, player_pos, player_team, source, picked_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      ).bind(initial.mock.id, ...values, new Date(0).toISOString())),
      env.DB.prepare("UPDATE mock_drafts SET revision = 3 WHERE id = ?")
        .bind(initial.mock.id),
    ]);
    const before = { revision: 3, mock: initial.mock };
    const storedBefore = await env.DB.prepare(
      "SELECT revision, rng_state FROM mock_drafts WHERE id = ?",
    ).bind(before.mock.id).first<{ revision: number; rng_state: number }>();

    const rejected = await SELF.fetch("https://x/api/mocks/current/picks", {
      method: "POST",
      headers: { ...bearer(KEY), "content-type": "application/json" },
      body: JSON.stringify({
        mock_id: before.mock.id,
        player_key: "scarce-3",
        expected_revision: before.revision,
      }),
    });
    expect(rejected.status).toBe(409);
    expect(await rejected.json()).toMatchObject({ error: "illegal_roster_pick" });
    expect(await env.DB.prepare(
      "SELECT revision, rng_state FROM mock_drafts WHERE id = ?",
    ).bind(before.mock.id).first()).toEqual(storedBefore);
  });

  it("persists and reloads a complete 160-pick v1 Yahoo mock without touching live state", async () => {
    await env.BOARD.put(BOARD_KEY, JSON.stringify(workerYahooBoard));
    await SELF.fetch("https://x/api/draft", {
      method: "PUT",
      headers: { ...bearer(KEY), "content-type": "application/json" },
      body: JSON.stringify({
        name: "Live During Mock",
        rounds: 1,
        teams: [
          { name: "Brian", is_user: true },
          { name: "Other", is_user: false },
        ],
      }),
    });
    const liveBefore = await (
      await SELF.fetch("https://x/api/draft", { headers: bearer(KEY) })
    ).text();

    let state = await (
      await SELF.fetch("https://x/api/mocks", {
        method: "POST",
        headers: { ...bearer(KEY), "content-type": "application/json" },
        body: JSON.stringify({ user_slot: 5, seed: 0, variance_preset: "wild" }),
      })
    ).json() as import("../src/mock-draft").MockState;
    while (!state.complete) {
      const pool = buildPlayerPool(workerYahooBoard.players, state.picks);
      const selected = mockSuggestions({
        board: workerYahooBoard,
        pool,
        picks: state.picks,
        lifecycle: state.lifecycle!,
        next: state.next!,
        user_slot: state.mock!.user_slot,
      })[0];
      expect(selected).toBeDefined();
      const beforeRevision = state.revision;
      const response = await SELF.fetch("https://x/api/mocks/current/picks", {
        method: "POST",
        headers: { ...bearer(KEY), "content-type": "application/json" },
        body: JSON.stringify({
          mock_id: state.mock!.id,
          player_key: selected!.key,
          expected_revision: state.revision,
        }),
      });
      expect(response.status).toBe(201);
      state = await response.json() as import("../src/mock-draft").MockState;
      expect(state.revision - beforeRevision).toBe(state.appended_picks!.length);
      const refreshedPool = buildPlayerPool(workerYahooBoard.players, state.picks);
      const refreshedSuggestions = mockSuggestions({
        board: workerYahooBoard,
        pool: refreshedPool,
        picks: state.picks,
        lifecycle: state.lifecycle!,
        next: state.next ?? null,
        user_slot: state.mock!.user_slot,
      });
      expect(refreshedSuggestions.every((player) => refreshedPool.available.includes(player))).toBe(true);
    }

    expect(state).toMatchObject({
      complete: true,
      revision: 160,
      next: null,
      mock: {
        seed: 0,
        strategy_version: "market-need-v1",
        variance_preset: "wild",
        team_count: 10,
        rounds: 16,
      },
    });
    expect(state.picks).toHaveLength(160);
    expect(new Set(state.picks.map((pick) => pick.player_key)).size).toBe(160);
    const stored = await env.DB.prepare(
      "SELECT status, revision, rng_state, variance_preset FROM mock_drafts WHERE id = ?",
    ).bind(state.mock!.id).first<Record<string, unknown>>();
    expect(stored).toMatchObject({
      status: "complete",
      revision: 160,
      variance_preset: "wild",
    });
    expect(stored?.rng_state).not.toBe(0);

    const reloaded = await SELF.fetch("https://x/api/mocks/current", { headers: bearer(KEY) });
    expect(await reloaded.json()).toMatchObject({
      complete: true,
      revision: 160,
      next: null,
      mock: { id: state.mock!.id, variance_preset: "wild" },
    });
    expect(await (
      await SELF.fetch("https://x/api/draft", { headers: bearer(KEY) })
    ).text()).toBe(liveBefore);
  }, 30_000);

  it("atomically records Brian's pick plus CPU turns and rejects a stale replay", async () => {
    const created = await SELF.fetch("https://x/api/mocks", {
      method: "POST",
      headers: { ...bearer(KEY), "content-type": "application/json" },
      body: JSON.stringify({ user_slot: 4, seed: 8042 }),
    });
    const initial = (await created.json()) as {
      mock: { id: string };
      picks: Array<{ player_key: string }>;
      revision: number;
    };
    const pickedKeys = new Set(initial.picks.map((pick) => pick.player_key));
    const selected = mockFixture.players.find((player) => !pickedKeys.has(player.key))!;

    const advanced = await SELF.fetch("https://x/api/mocks/current/picks", {
      method: "POST",
      headers: { ...bearer(KEY), "content-type": "application/json" },
      body: JSON.stringify({
        mock_id: initial.mock.id,
        player_key: selected.key,
        expected_revision: initial.revision,
      }),
    });
    expect(advanced.status).toBe(201);
    const advancedState = (await advanced.json()) as {
      revision: number;
      appended_picks: Array<Record<string, unknown>>;
      next: Record<string, unknown>;
    };
    expect(advancedState).toMatchObject({
      revision: 20,
      next: {
        overall_pick: 21,
        round: 2,
        round_pick: 9,
        team_name: "Brian",
        is_user: true,
      },
    });
    expect(advancedState.appended_picks).toHaveLength(17);
    expect(advancedState.appended_picks[0]).toMatchObject({
      overall_pick: 4,
      draft_slot: 3,
      player_key: selected.key,
      source: "user",
    });
    expect(advancedState.appended_picks.at(-1)).toMatchObject({
      overall_pick: 20,
      source: "simulated",
    });

    const stale = await SELF.fetch("https://x/api/mocks/current/picks", {
      method: "POST",
      headers: { ...bearer(KEY), "content-type": "application/json" },
      body: JSON.stringify({
        mock_id: initial.mock.id,
        player_key: mockFixture.players.at(-1)!.key,
        expected_revision: initial.revision,
      }),
    });
    expect(stale.status).toBe(409);
    expect(await stale.json()).toMatchObject({ error: "stale_mock" });
  });

  it("preserves the exact live draft response through create, advance, and discard", async () => {
    await SELF.fetch("https://x/api/draft", {
      method: "PUT",
      headers: { ...bearer(KEY), "content-type": "application/json" },
      body: JSON.stringify({
        name: "Live League",
        rounds: 2,
        teams: [
          { name: "Brian", is_user: true },
          { name: "Other", is_user: false },
        ],
      }),
    });
    await SELF.fetch("https://x/api/picks", {
      method: "POST",
      headers: { ...bearer(KEY), "content-type": "application/json" },
      body: JSON.stringify({
        player_key: mockFixture.players[0]!.key,
        expected_overall_pick: 1,
      }),
    });
    const liveBefore = await (
      await SELF.fetch("https://x/api/draft", { headers: bearer(KEY) })
    ).text();

    const created = await SELF.fetch("https://x/api/mocks", {
      method: "POST",
      headers: { ...bearer(KEY), "content-type": "application/json" },
      body: JSON.stringify({ user_slot: 1, seed: 8042 }),
    });
    expect(created.status).toBe(201);
    expect(
      await (
        await SELF.fetch("https://x/api/draft", { headers: bearer(KEY) })
      ).text(),
    ).toBe(liveBefore);

    const createdState = (await created.json()) as {
      mock: { id: string };
      revision: number;
      picks: Array<{ player_key: string }>;
    };
    const mockPicked = new Set(createdState.picks.map((pick) => pick.player_key));
    const selected = mockFixture.players.find(
      (player) => !mockPicked.has(player.key),
    )!;
    const advanced = await SELF.fetch("https://x/api/mocks/current/picks", {
      method: "POST",
      headers: { ...bearer(KEY), "content-type": "application/json" },
      body: JSON.stringify({
        mock_id: createdState.mock.id,
        player_key: selected.key,
        expected_revision: createdState.revision,
      }),
    });
    expect(advanced.status).toBe(201);
    const advancedState = (await advanced.clone().json()) as { revision: number };
    expect(
      await (
        await SELF.fetch("https://x/api/draft", { headers: bearer(KEY) })
      ).text(),
    ).toBe(liveBefore);

    const discarded = await SELF.fetch("https://x/api/mocks/current", {
      method: "DELETE",
      headers: { ...bearer(KEY), "content-type": "application/json" },
      body: JSON.stringify({
        mock_id: createdState.mock.id,
        expected_revision: advancedState.revision,
      }),
    });
    expect(discarded.status).toBe(200);
    expect(await discarded.json()).toEqual({
      configured: false,
      picks: [],
      revision: 0,
    });
    expect(
      await (
        await SELF.fetch("https://x/api/draft", { headers: bearer(KEY) })
      ).text(),
    ).toBe(liveBefore);
  });

  it("returns stable player errors and replays the same CPU sequence after discard", async () => {
    const created = await SELF.fetch("https://x/api/mocks", {
      method: "POST",
      headers: { ...bearer(KEY), "content-type": "application/json" },
      body: JSON.stringify({ user_slot: 4, seed: 8042 }),
    });
    const initial = (await created.json()) as {
      mock: { id: string };
      picks: Array<{ player_key: string }>;
      revision: number;
    };
    const initialKeys = initial.picks.map((pick) => pick.player_key);

    const conflict = await SELF.fetch("https://x/api/mocks", {
      method: "POST",
      headers: { ...bearer(KEY), "content-type": "application/json" },
      body: JSON.stringify({ user_slot: 4, seed: 8042 }),
    });
    expect(conflict.status).toBe(409);
    expect(await conflict.json()).toMatchObject({ error: "mock_active" });

    const unavailable = await SELF.fetch("https://x/api/mocks/current/picks", {
      method: "POST",
      headers: { ...bearer(KEY), "content-type": "application/json" },
      body: JSON.stringify({
        mock_id: initial.mock.id,
        player_key: initialKeys[0],
        expected_revision: initial.revision,
      }),
    });
    expect(unavailable.status).toBe(409);
    expect(await unavailable.json()).toMatchObject({
      error: "player_unavailable",
    });

    const unknown = await SELF.fetch("https://x/api/mocks/current/picks", {
      method: "POST",
      headers: { ...bearer(KEY), "content-type": "application/json" },
      body: JSON.stringify({
        mock_id: initial.mock.id,
        player_key: "not-on-snapshot",
        expected_revision: initial.revision,
      }),
    });
    expect(unknown.status).toBe(422);
    expect(await unknown.json()).toMatchObject({ error: "unknown_player" });

    await SELF.fetch("https://x/api/mocks/current", {
      method: "DELETE",
      headers: { ...bearer(KEY), "content-type": "application/json" },
      body: JSON.stringify({
        mock_id: initial.mock.id,
        expected_revision: initial.revision,
      }),
    });
    const replay = await SELF.fetch("https://x/api/mocks", {
      method: "POST",
      headers: { ...bearer(KEY), "content-type": "application/json" },
      body: JSON.stringify({ user_slot: 4, seed: 8042 }),
    });
    const replayState = (await replay.json()) as {
      picks: Array<{ player_key: string }>;
    };
    expect(replayState.picks.map((pick) => pick.player_key)).toEqual(initialKeys);
  });

  it("accepts exactly one concurrent transition for a displayed mock revision", async () => {
    const created = await SELF.fetch("https://x/api/mocks", {
      method: "POST",
      headers: { ...bearer(KEY), "content-type": "application/json" },
      body: JSON.stringify({ user_slot: 4, seed: 8042 }),
    });
    const initial = (await created.json()) as {
      mock: { id: string };
      picks: Array<{ player_key: string }>;
      revision: number;
    };
    const picked = new Set(initial.picks.map((pick) => pick.player_key));
    const candidates = mockFixture.players
      .filter((player) => !picked.has(player.key))
      .slice(0, 2);

    const responses = await Promise.all(
      candidates.map((player) =>
        SELF.fetch("https://x/api/mocks/current/picks", {
          method: "POST",
          headers: { ...bearer(KEY), "content-type": "application/json" },
          body: JSON.stringify({
            mock_id: initial.mock.id,
            player_key: player.key,
            expected_revision: initial.revision,
          }),
        }),
      ),
    );

    expect(responses.map((response) => response.status).sort()).toEqual([
      201,
      409,
    ]);
    const current = await SELF.fetch("https://x/api/mocks/current", {
      headers: bearer(KEY),
    });
    expect(await current.json()).toMatchObject({
      revision: 20,
      next: { overall_pick: 21, team_name: "Brian" },
    });
  });

  it("rejects a stale discard without deleting the newer current mock", async () => {
    const first = await SELF.fetch("https://x/api/mocks", {
      method: "POST",
      headers: { ...bearer(KEY), "content-type": "application/json" },
      body: JSON.stringify({ user_slot: 1, seed: 1 }),
    });
    const firstState = (await first.json()) as { mock: { id: string }; revision: number };
    const firstDiscard = await SELF.fetch("https://x/api/mocks/current", {
      method: "DELETE",
      headers: { ...bearer(KEY), "content-type": "application/json" },
      body: JSON.stringify({
        mock_id: firstState.mock.id,
        expected_revision: firstState.revision,
      }),
    });
    expect(firstDiscard.status).toBe(200);

    const second = await SELF.fetch("https://x/api/mocks", {
      method: "POST",
      headers: { ...bearer(KEY), "content-type": "application/json" },
      body: JSON.stringify({ user_slot: 1, seed: 2 }),
    });
    const secondState = (await second.json()) as { mock: { id: string } };

    const staleDiscard = await SELF.fetch("https://x/api/mocks/current", {
      method: "DELETE",
      headers: { ...bearer(KEY), "content-type": "application/json" },
      body: JSON.stringify({ mock_id: firstState.mock.id, expected_revision: 0 }),
    });
    expect(staleDiscard.status).toBe(409);
    expect(await staleDiscard.json()).toMatchObject({ error: "stale_mock" });

    const current = await SELF.fetch("https://x/api/mocks/current", {
      headers: bearer(KEY),
    });
    expect(await current.json()).toMatchObject({
      configured: true,
      mock: { id: secondState.mock.id },
    });
  });

  it("rejects a stale pick for a replaced mock with the same revision", async () => {
    const first = await SELF.fetch("https://x/api/mocks", {
      method: "POST",
      headers: { ...bearer(KEY), "content-type": "application/json" },
      body: JSON.stringify({ user_slot: 1, seed: 1 }),
    });
    const firstState = (await first.json()) as { mock: { id: string }; revision: number };
    await SELF.fetch("https://x/api/mocks/current", {
      method: "DELETE",
      headers: { ...bearer(KEY), "content-type": "application/json" },
      body: JSON.stringify({
        mock_id: firstState.mock.id,
        expected_revision: firstState.revision,
      }),
    });

    const second = await SELF.fetch("https://x/api/mocks", {
      method: "POST",
      headers: { ...bearer(KEY), "content-type": "application/json" },
      body: JSON.stringify({ user_slot: 1, seed: 2 }),
    });
    const secondState = (await second.json()) as { mock: { id: string } };

    const stalePick = await SELF.fetch("https://x/api/mocks/current/picks", {
      method: "POST",
      headers: { ...bearer(KEY), "content-type": "application/json" },
      body: JSON.stringify({
        mock_id: firstState.mock.id,
        player_key: mockFixture.players[0]!.key,
        expected_revision: 0,
      }),
    });
    expect(stalePick.status).toBe(409);
    expect(await stalePick.json()).toMatchObject({ error: "stale_mock" });

    const current = await SELF.fetch("https://x/api/mocks/current", {
      headers: bearer(KEY),
    });
    expect(await current.json()).toMatchObject({
      mock: { id: secondState.mock.id },
      revision: 0,
      picks: [],
    });
  });

  it("keeps an active mock discardable when its saved board becomes incompatible", async () => {
    const created = await SELF.fetch("https://x/api/mocks", {
      method: "POST",
      headers: { ...bearer(KEY), "content-type": "application/json" },
      body: JSON.stringify({ user_slot: 1, seed: 8042 }),
    });
    const createdState = (await created.json()) as {
      mock: { id: string; board_fingerprint: string };
      revision: number;
    };
    await env.DB.prepare("UPDATE mock_boards SET board_json = ? WHERE fingerprint = ?")
      .bind(
        JSON.stringify({ ...mockFixture, version: mockFixture.version + 1 }),
        createdState.mock.board_fingerprint,
      )
      .run();

    const current = await SELF.fetch("https://x/api/mocks/current", {
      headers: bearer(KEY),
    });
    expect(current.status).toBe(200);
    expect(await current.json()).toMatchObject({
      configured: true,
      mock: { id: createdState.mock.id },
      board_error: "The mock's board snapshot is unreadable or unsupported.",
    });

    const discarded = await SELF.fetch("https://x/api/mocks/current", {
      method: "DELETE",
      headers: { ...bearer(KEY), "content-type": "application/json" },
      body: JSON.stringify({
        mock_id: createdState.mock.id,
        expected_revision: createdState.revision,
      }),
    });
    expect(discarded.status).toBe(200);
    expect(await discarded.json()).toEqual({ configured: false, picks: [], revision: 0 });
  });

  it("continues from the immutable board snapshot after a board republish", async () => {
    const original = mockFixture.players[0]!;
    const created = await SELF.fetch("https://x/api/mocks", {
      method: "POST",
      headers: { ...bearer(KEY), "content-type": "application/json" },
      body: JSON.stringify({ user_slot: 1, seed: 8042 }),
    });
    expect(created.status).toBe(201);
    const createdState = (await created.clone().json()) as { mock: { id: string } };
    const republished = structuredClone(mockFixture);
    republished.players[0]!.name = `${original.name} republished`;
    await env.BOARD.put(BOARD_KEY, JSON.stringify(republished));

    const advanced = await SELF.fetch("https://x/api/mocks/current/picks", {
      method: "POST",
      headers: { ...bearer(KEY), "content-type": "application/json" },
      body: JSON.stringify({
        mock_id: createdState.mock.id,
        player_key: original.key,
        expected_revision: 0,
      }),
    });
    expect(advanced.status).toBe(201);
    const advancedState = (await advanced.json()) as {
      picks: Array<Record<string, unknown>>;
    };
    expect(advancedState.picks[0]).toMatchObject({
      overall_pick: 1,
      player_key: original.key,
      player_name: original.name,
      source: "user",
    });

    await env.BOARD.delete(BOARD_KEY);
    const resumed = await SELF.fetch("https://x/api/mocks/current", {
      headers: bearer(KEY),
    });
    expect(resumed.status).toBe(200);
    const resumedState = (await resumed.json()) as { board: Board };
    expect(resumedState.board.generated_at).toBe(mockFixture.generated_at);
    expect(resumedState.board.players[0]).toMatchObject({
      key: original.key,
      name: original.name,
    });
  });
});

describe("Worker static shell", () => {
  it("serves the HTML shell publicly at /", async () => {
    const res = await SELF.fetch("https://x/");
    expect(res.status).toBe(200);
    const body = await res.text();
    expect(body).toContain("DRAFT");
    expect(body).toContain('data-list');
  });

  it("404s an unknown /api path", async () => {
    const res = await SELF.fetch("https://x/api/nope", { headers: bearer(KEY) });
    expect(res.status).toBe(404);
  });

  it("serves the distinct mock shell publicly and never falls through for its API", async () => {
    const shell = await SELF.fetch("https://x/mock");
    expect(shell.status).toBe(200);
    const body = await shell.text();
    expect(body).toContain("DRAFTMOCK");
    expect(body).toContain("ISOLATED MODE");
    expect(body).toContain("Mock picks only");
    expect(body).toContain('value="realistic" selected');
    expect(body).toContain("data-status-variance");
    expect(body).toContain("data-status-lifecycle");
    expect(body).toContain("data-lifecycle-toggle");
    expect(body).toContain("data-undo");
    expect(body).toContain("data-reset");
    expect(body).toContain("rewinds Brian’s latest choice");
    expect(body).toContain('href="styles.css"');
    expect(body).toContain('src="mock-app.js"');

    const api = await SELF.fetch("https://x/api/mocks/not-a-route", {
      headers: bearer(KEY),
    });
    expect(api.status).toBe(404);
    expect(api.headers.get("content-type")).toContain("application/json");
  });
});
