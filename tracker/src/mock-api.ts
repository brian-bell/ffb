import { BOARD_KEY, isValidBoard } from "./board";
import {
  MockDomainError,
  recordUserPick,
  startMock,
  type MockSetup,
} from "./mock-draft";
import {
  appendMockTransition,
  discardCurrentMock,
  hasActiveMock,
  insertMock,
  loadCurrentMock,
  unconfiguredMockState,
} from "./mock-store";
import { seededMarketStrategy } from "./mock-strategy";
import type { Board } from "./types";

export interface MockApiEnv {
  BOARD: KVNamespace;
  DB: D1Database;
}

function json(body: unknown, status = 200, headers: HeadersInit = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", ...headers },
  });
}

function error(code: string, message: string, status: number): Response {
  return json({ error: code, message }, status);
}

function methodNotAllowed(allow: string): Response {
  return json(
    { error: "method_not_allowed", message: "Method not allowed." },
    405,
    { Allow: allow },
  );
}

async function requestBody(request: Request): Promise<unknown | null> {
  try {
    return await request.json();
  } catch {
    return null;
  }
}

function validSetup(value: unknown): MockSetup | null {
  if (!value || typeof value !== "object") return null;
  const input = value as { user_slot?: unknown; seed?: unknown };
  if (
    !Number.isInteger(input.user_slot)
    || (input.user_slot as number) < 1
    || !Number.isInteger(input.seed)
    || (input.seed as number) < 0
    || (input.seed as number) > 0xffff_ffff
  ) {
    return null;
  }
  return {
    user_slot: input.user_slot as number,
    seed: input.seed as number,
  };
}

async function currentBoard(
  env: MockApiEnv,
): Promise<{ text: string; board: Board } | { response: Response }> {
  const text = await env.BOARD.get(BOARD_KEY);
  if (text === null) {
    return {
      response: error("no_board_published", "No board has been published.", 404),
    };
  }
  try {
    const board = JSON.parse(text) as unknown;
    if (!isValidBoard(board)) {
      return {
        response: error(
          "board_unreadable",
          "Board format is unreadable or unsupported.",
          503,
        ),
      };
    }
    if (
      !Number.isInteger(board.num_teams)
      || board.num_teams < 2
      || board.num_teams > 20
    ) {
      return {
        response: error(
          "board_unusable",
          "The published board must contain a 2–20 team league.",
          503,
        ),
      };
    }
    return { text, board };
  } catch {
    return {
      response: error(
        "board_unreadable",
        "Board format is unreadable or unsupported.",
        503,
      ),
    };
  }
}

async function fingerprint(text: string): Promise<string> {
  const bytes = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(text),
  );
  return [...new Uint8Array(bytes)]
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

async function createMock(request: Request, env: MockApiEnv): Promise<Response> {
  const setup = validSetup(await requestBody(request));
  if (!setup) {
    return error(
      "invalid_mock",
      "Provide a one-based draft slot and an unsigned 32-bit seed.",
      400,
    );
  }
  if (await hasActiveMock(env.DB)) {
    return error(
      "mock_active",
      "Discard the active mock before starting another.",
      409,
    );
  }
  const loadedBoard = await currentBoard(env);
  if ("response" in loadedBoard) return loadedBoard.response;

  try {
    const aggregate = startMock(
      loadedBoard.board,
      setup,
      seededMarketStrategy,
    );
    await insertMock(env.DB, aggregate, {
      id: crypto.randomUUID(),
      fingerprint: await fingerprint(loadedBoard.text),
      board_json: loadedBoard.text,
      board: loadedBoard.board,
    });
    const loaded = await loadCurrentMock(env.DB);
    if (!loaded) {
      return error("board_unusable", "The mock could not be created.", 503);
    }
    return json(
      { ...loaded.state, appended_picks: aggregate.picks },
      201,
    );
  } catch (caught) {
    if (caught instanceof MockDomainError) {
      const status = caught.code === "invalid_mock" ? 400 : 503;
      return error(caught.code, caught.message, status);
    }
    if (await hasActiveMock(env.DB)) {
      return error(
        "mock_active",
        "Discard the active mock before starting another.",
        409,
      );
    }
    return error("board_unusable", "The mock could not be created.", 503);
  }
}

async function recordMockPick(
  request: Request,
  env: MockApiEnv,
): Promise<Response> {
  const input = await requestBody(request);
  if (!input || typeof input !== "object") {
    return error(
      "invalid_request",
      "Provide a player key and displayed mock revision.",
      400,
    );
  }
  const value = input as {
    player_key?: unknown;
    expected_revision?: unknown;
  };
  const playerKey =
    typeof value.player_key === "string" && value.player_key.trim()
      ? value.player_key.trim()
      : null;
  if (
    playerKey === null
    || !Number.isInteger(value.expected_revision)
    || (value.expected_revision as number) < 0
  ) {
    return error(
      "invalid_request",
      "Provide a player key and displayed mock revision.",
      400,
    );
  }

  const loaded = await loadCurrentMock(env.DB);
  if (!loaded) {
    return error(
      "mock_unconfigured",
      "Start a mock before recording a pick.",
      409,
    );
  }
  if (loaded.aggregate.complete) {
    return error("mock_complete", "The mock draft is complete.", 409);
  }
  if (loaded.aggregate.strategy_version !== seededMarketStrategy.version) {
    return error(
      "board_unusable",
      "This mock uses an unsupported opponent strategy.",
      503,
    );
  }
  const expectedRevision = value.expected_revision as number;
  if (loaded.aggregate.revision !== expectedRevision) {
    return error(
      "stale_mock",
      "The mock changed in another tab; reload before recording.",
      409,
    );
  }

  let board: Board;
  try {
    const parsed = JSON.parse(loaded.board_json) as unknown;
    if (!isValidBoard(parsed)) throw new Error("invalid board");
    board = parsed;
  } catch {
    return error(
      "board_unreadable",
      "The mock's board snapshot is unreadable.",
      503,
    );
  }

  try {
    const transition = recordUserPick(
      loaded.aggregate,
      board,
      playerKey,
      seededMarketStrategy,
    );
    const result = await appendMockTransition(
      env.DB,
      loaded.state.mock!.id,
      expectedRevision,
      transition,
    );
    if (result !== "ok") {
      return error(
        "stale_mock",
        "The mock changed in another tab; reload before recording.",
        409,
      );
    }
    const updated = await loadCurrentMock(env.DB);
    if (!updated) {
      return error("mock_unconfigured", "The mock no longer exists.", 409);
    }
    return json(
      { ...updated.state, appended_picks: transition.appended_picks },
      201,
    );
  } catch (caught) {
    if (caught instanceof MockDomainError) {
      if (caught.code === "unknown_player") {
        return error(caught.code, caught.message, 422);
      }
      if (
        caught.code === "player_unavailable"
        || caught.code === "mock_complete"
        || caught.code === "not_user_turn"
      ) {
        return error(caught.code, caught.message, 409);
      }
      return error(caught.code, caught.message, 503);
    }
    return error("board_unusable", "The mock could not advance.", 503);
  }
}

export async function handleMockApi(
  request: Request,
  env: MockApiEnv,
  pathname: string,
): Promise<Response> {
  if (pathname === "/api/mocks") {
    return request.method === "POST"
      ? createMock(request, env)
      : methodNotAllowed("POST");
  }
  if (pathname === "/api/mocks/current") {
    if (request.method === "GET") {
      const current = await loadCurrentMock(env.DB);
      return json(current?.state ?? unconfiguredMockState());
    }
    if (request.method === "DELETE") {
      await discardCurrentMock(env.DB);
      return json(unconfiguredMockState());
    }
    return methodNotAllowed("GET, DELETE");
  }
  if (pathname === "/api/mocks/current/picks") {
    return request.method === "POST"
      ? recordMockPick(request, env)
      : methodNotAllowed("POST");
  }
  return error("not_found", "Not found.", 404);
}
