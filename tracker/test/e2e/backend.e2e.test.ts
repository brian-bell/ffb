import { beforeEach, describe, expect, it } from "vitest";
import { env } from "cloudflare:test";
import { BOARD_KEY } from "../../src/board";
import { api } from "./api-client";

describe("generated backend contract", () => {
  beforeEach(async () => {
    await env.BOARD.put(BOARD_KEY, env.E2E_BOARD_JSON);
  });

  it("drives a draft through the real Worker API", async () => {
    const health = await api.health();
    expect(health.status).toBe(200);

    const boardResponse = await api.getBoard();
    expect(boardResponse.status).toBe(200);
    expect(boardResponse.body).toBe(env.E2E_BOARD_JSON);

    const board = boardResponse.json;
    expect(board).toBeDefined();
    if (!board) throw new Error("generated board did not decode");
    const selected = board.players[0];
    expect(selected).toBeDefined();

    const configured = await api.configureDraft({
      name: "E2E League",
      rounds: 2,
      teams: [
        { name: "A", is_user: true },
        { name: "B", is_user: false },
      ],
    });
    expect(configured.status).toBe(200);

    const recorded = await api.recordBoardPlayer(selected.key, 1);
    expect(recorded.status).toBe(201);

    const draftResponse = await api.getDraft();
    expect(draftResponse.status).toBe(200);
    expect(draftResponse.json).toMatchObject({
      revision: 1,
      picks: [
        {
          player_key: selected.key,
          player_name: selected.name,
          player_pos: selected.pos,
          player_team: selected.team,
        },
      ],
      next: { overall_pick: 2, team_name: "B" },
    });
  });

  it("persists a complete snake draft in derived team order", async () => {
    const board = (await api.getBoard()).json;
    expect(board?.players.length).toBeGreaterThanOrEqual(4);
    if (!board) throw new Error("generated board did not decode");
    const players = board.players.slice(0, 4);

    const configured = await api.configureDraft({
      name: "Snake E2E",
      rounds: 2,
      teams: [
        { name: "A", is_user: true },
        { name: "B", is_user: false },
      ],
    });
    expect(configured.status).toBe(200);

    const latestPicks = [];
    const revisions = [];
    for (const [index, player] of players.entries()) {
      const response = await api.recordBoardPlayer(player.key, index + 1);
      expect(response.status).toBe(201);
      latestPicks.push(response.json?.picks.at(-1));
      revisions.push(response.json?.revision);
    }

    expect(latestPicks).toMatchObject([
      { overall_pick: 1, round: 1, round_pick: 1, team_name: "A" },
      { overall_pick: 2, round: 1, round_pick: 2, team_name: "B" },
      { overall_pick: 3, round: 2, round_pick: 1, team_name: "B" },
      { overall_pick: 4, round: 2, round_pick: 2, team_name: "A" },
    ]);
    expect(revisions).toEqual([1, 2, 3, 4]);
    expect((await api.getDraft()).json).toMatchObject({
      revision: 4,
      complete: true,
      next: null,
    });
  });

  it("accepts exactly one of two concurrent stale writes", async () => {
    const board = (await api.getBoard()).json;
    expect(board?.players.length).toBeGreaterThanOrEqual(2);
    if (!board) throw new Error("generated board did not decode");
    await api.configureDraft({
      name: "Race E2E",
      rounds: 2,
      teams: [
        { name: "A", is_user: true },
        { name: "B", is_user: false },
      ],
    });

    const responses = await Promise.all([
      api.recordBoardPlayer(board.players[0].key, 1),
      api.recordBoardPlayer(board.players[1].key, 1),
    ]);

    expect(responses.map(({ status }) => status).sort()).toEqual([201, 409]);
    expect((await api.getDraft()).json).toMatchObject({
      revision: 1,
      picks: [{ overall_pick: 1 }],
      next: { overall_pick: 2 },
    });
  });

  it("preserves pick history while new picks use a republished board", async () => {
    const board = (await api.getBoard()).json;
    expect(board?.players.length).toBeGreaterThanOrEqual(2);
    if (!board) throw new Error("generated board did not decode");
    const originalA = board.players[0];
    const originalB = board.players[1];
    await api.configureDraft({
      name: "Republish E2E",
      rounds: 2,
      teams: [
        { name: "A", is_user: true },
        { name: "B", is_user: false },
      ],
    });
    expect((await api.recordBoardPlayer(originalA.key, 1)).status).toBe(201);

    const republished = structuredClone(board);
    republished.players[0].name = `${originalA.name} changed after pick`;
    republished.players[1].name = `${originalB.name} republished`;
    republished.players[1].team = "NEW";
    await env.BOARD.put(BOARD_KEY, JSON.stringify(republished));

    expect((await api.getDraft()).json?.picks[0]).toMatchObject({
      player_key: originalA.key,
      player_name: originalA.name,
      player_team: originalA.team,
    });

    const second = await api.recordBoardPlayer(originalB.key, 2);
    expect(second.status).toBe(201);
    expect(second.json?.picks[1]).toMatchObject({
      player_key: originalB.key,
      player_name: republished.players[1].name,
      player_team: "NEW",
    });
  });

  it("resets draft state without removing the generated board", async () => {
    const board = (await api.getBoard()).json;
    if (!board) throw new Error("generated board did not decode");
    await api.configureDraft({
      name: "Reset E2E",
      rounds: 1,
      teams: [
        { name: "A", is_user: true },
        { name: "B", is_user: false },
      ],
    });
    expect((await api.recordBoardPlayer(board.players[0].key, 1)).status).toBe(201);

    const reset = await api.resetDraft();
    expect(reset.status).toBe(200);
    expect(reset.json).toEqual({ configured: false, picks: [], revision: 0 });

    const servedBoard = await api.getBoard();
    expect(servedBoard.status).toBe(200);
    expect(servedBoard.body).toBe(env.E2E_BOARD_JSON);
  });

  it("does not mutate a draft when the current board is unreadable", async () => {
    const board = (await api.getBoard()).json;
    if (!board) throw new Error("generated board did not decode");
    await api.configureDraft({
      name: "Unreadable Board E2E",
      rounds: 1,
      teams: [
        { name: "A", is_user: true },
        { name: "B", is_user: false },
      ],
    });

    await env.BOARD.put(BOARD_KEY, "{not-json");
    const malformed = await api.recordBoardPlayer(board.players[0].key, 1);
    expect(malformed.status).toBe(503);
    expect(malformed.json).toMatchObject({ error: "board_unreadable" });

    await env.BOARD.put(BOARD_KEY, JSON.stringify({ ...board, version: 2 }));
    const unsupported = await api.recordBoardPlayer(board.players[0].key, 1);
    expect(unsupported.status).toBe(503);
    expect(unsupported.json).toMatchObject({ error: "board_unreadable" });

    expect((await api.getDraft()).json).toMatchObject({
      configured: true,
      revision: 0,
      picks: [],
      next: { overall_pick: 1 },
    });
  });
});
