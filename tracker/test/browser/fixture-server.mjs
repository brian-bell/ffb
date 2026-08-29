import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { dirname, extname, join } from "node:path";
import { fileURLToPath } from "node:url";

const trackerRoot = join(dirname(fileURLToPath(import.meta.url)), "../..");
const publicRoot = join(trackerRoot, "public");
const fixture = JSON.parse(
  await readFile(join(trackerRoot, "test/fixtures/board.json"), "utf8"),
);
const positions = ["QB", "RB", "WR", "TE", "K", "DEF"];
const generatedPlayers = Array.from({ length: 240 }, (_, index) => {
  if (index < fixture.players.length) return fixture.players[index];
  const source = fixture.players[index % fixture.players.length];
  const pos = positions[index % positions.length];
  return {
    ...source,
    key: `browser-${index}`,
    name: `${pos} Prospect ${String(index).padStart(3, "0")}`,
    pos,
    rank: index + 1,
    pos_rank: Math.floor(index / positions.length) + 1,
    tier: Math.floor(index / 24) + 1,
    adp: index + 1.25,
    adp_rank: index + 1,
  };
});
const board = { ...fixture, players: generatedPlayers };
const teams = Array.from({ length: board.num_teams }, (_, draftSlot) => ({
  id: draftSlot + 1,
  name: draftSlot === 0 ? "Brian" : `CPU ${draftSlot + 1}`,
  draft_slot: draftSlot,
  is_user: draftSlot === 0,
}));

let state = null;

function nextUserPick(overallPick = 1) {
  return {
    overall_pick: overallPick,
    round: overallPick === 1 ? 1 : 2,
    round_pick: overallPick === 1 ? 1 : board.num_teams,
    team_id: 1,
    team_name: "Brian",
    is_user: true,
    direction: overallPick === 1 ? "forward" : "reverse",
  };
}

function unconfiguredState() {
  return { configured: false, picks: [], revision: 0 };
}

function configuredState(input, overrides = {}) {
  return {
    configured: true,
    board,
    mock: {
      id: "browser-mock",
      board_fingerprint: "browser-board",
      seed: Number(input.seed),
      strategy_version: "market-need-v1",
      user_slot: 1,
      team_count: board.num_teams,
      rounds: Object.values(board.roster_slots).reduce((sum, count) => sum + count, 0),
      variance_preset: input.variance_preset ?? "realistic",
    },
    teams,
    picks: [],
    next: nextUserPick(),
    complete: false,
    lifecycle: "active",
    can_undo: false,
    revision: 0,
    appended_picks: [],
    ...overrides,
  };
}

function pickSnapshot(player, overallPick, teamIndex, source) {
  const team = teams[teamIndex];
  return {
    overall_pick: overallPick,
    round: overallPick <= board.num_teams ? 1 : 2,
    round_pick: overallPick <= board.num_teams ? overallPick : overallPick - board.num_teams,
    draft_slot: team.draft_slot,
    team_name: team.name,
    player_key: player.key,
    player_name: player.name,
    player_pos: player.pos,
    player_team: player.team,
    source,
  };
}

function completedState() {
  const rounds = Object.values(board.roster_slots).reduce((sum, count) => sum + count, 0);
  const total = board.num_teams * rounds;
  const picks = Array.from({ length: total }, (_, index) => {
    const round = Math.floor(index / board.num_teams) + 1;
    const roundPick = (index % board.num_teams) + 1;
    const slot = round % 2 === 1 ? roundPick - 1 : board.num_teams - roundPick;
    const team = teams[slot];
    const player = board.players[index];
    return {
      overall_pick: index + 1,
      round,
      round_pick: roundPick,
      draft_slot: team.draft_slot,
      team_name: team.name,
      player_key: player.key,
      player_name: player.name,
      player_pos: player.pos,
      player_team: player.team,
      source: team.is_user ? "user" : "simulated",
    };
  });
  return configuredState(currentInput(), {
    picks,
    next: null,
    complete: true,
    lifecycle: "complete",
    can_undo: true,
    revision: total,
    appended_picks: [],
  });
}

async function requestBody(request) {
  const chunks = [];
  for await (const chunk of request) chunks.push(chunk);
  return chunks.length === 0 ? {} : JSON.parse(Buffer.concat(chunks).toString("utf8"));
}

function sendJson(response, status, value) {
  response.writeHead(status, {
    "cache-control": "no-store",
    "content-type": "application/json; charset=utf-8",
  });
  response.end(JSON.stringify(value));
}

function currentInput() {
  return {
    seed: state?.mock?.seed ?? 8042,
    variance_preset: state?.mock?.variance_preset ?? "realistic",
  };
}

const server = createServer(async (request, response) => {
  const url = new URL(request.url ?? "/", "http://127.0.0.1:4173");
  if (url.pathname.startsWith("/api/")) {
    const authorization = request.headers.authorization;
    if (authorization !== "Bearer test-secret-key" && authorization !== "Bearer test-recovery-key") {
      sendJson(response, 401, { message: "Unauthorized" });
      return;
    }
    if (request.method === "GET" && url.pathname === "/api/board") {
      sendJson(response, 200, board);
      return;
    }
    if (request.method === "GET" && url.pathname === "/api/mocks/current") {
      if (authorization === "Bearer test-recovery-key") {
        sendJson(response, 200, configuredState(currentInput(), {
          board: undefined,
          board_error: "Saved board snapshot is unreadable.",
          next: null,
          complete: true,
          lifecycle: "complete",
          can_undo: true,
        }));
        return;
      }
      sendJson(response, 200, state ?? unconfiguredState());
      return;
    }
    if (request.method === "POST" && url.pathname === "/api/test/complete") {
      state = completedState();
      sendJson(response, 200, state);
      return;
    }
    if (request.method === "POST" && url.pathname === "/api/mocks") {
      state = configuredState(await requestBody(request));
      sendJson(response, 201, state);
      return;
    }
    if (request.method === "POST" && url.pathname === "/api/mocks/current/picks") {
      const input = await requestBody(request);
      const selected = board.players.find((player) => player.key === input.player_key);
      const cpuPlayers = board.players.filter((player) => player.key !== input.player_key).slice(0, 3);
      const appended = [
        pickSnapshot(selected, 1, 0, "user"),
        ...cpuPlayers.map((player, index) => pickSnapshot(player, index + 2, index + 1, "simulated")),
      ];
      state = configuredState(currentInput(), {
        picks: appended,
        next: nextUserPick(24),
        can_undo: true,
        revision: (state?.revision ?? 0) + 1,
        appended_picks: appended,
      });
      sendJson(response, 200, state);
      return;
    }
    if (request.method === "POST" && url.pathname === "/api/mocks/current/pause") {
      state = { ...state, lifecycle: "paused", revision: state.revision + 1, appended_picks: [] };
      sendJson(response, 200, state);
      return;
    }
    if (request.method === "POST" && url.pathname === "/api/mocks/current/resume") {
      state = { ...state, lifecycle: "active", revision: state.revision + 1, appended_picks: [] };
      sendJson(response, 200, state);
      return;
    }
    if (request.method === "DELETE" && url.pathname === "/api/mocks/current/picks/latest") {
      state = configuredState(currentInput(), { revision: state.revision + 1 });
      sendJson(response, 200, state);
      return;
    }
    if (request.method === "POST" && url.pathname === "/api/mocks/current/reset") {
      state = configuredState(currentInput(), { revision: state.revision + 1 });
      sendJson(response, 200, state);
      return;
    }
    if (request.method === "DELETE" && url.pathname === "/api/mocks/current") {
      state = null;
      sendJson(response, 200, unconfiguredState());
      return;
    }
    sendJson(response, 404, { message: "Not found" });
    return;
  }

  const asset = url.pathname === "/mock" || url.pathname === "/mock/"
    ? "mock.html"
    : url.pathname.slice(1);
  if (!asset || asset.includes("..") || !["mock.html", "mock-app.js", "styles.css"].includes(asset)) {
    response.writeHead(404);
    response.end("Not found");
    return;
  }
  const contentTypes = {
    ".css": "text/css; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
  };
  response.writeHead(200, {
    "cache-control": "no-store",
    "content-type": contentTypes[extname(asset)],
  });
  response.end(await readFile(join(publicRoot, asset)));
});

server.listen(4173, "127.0.0.1");

for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, () => server.close(() => process.exit(0)));
}
