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
  loadMock,
  resetCurrentMock,
  setMockPaused,
  type LoadedMock,
  undoLatestMockDecision,
  unconfiguredMockState,
} from "./mock-store";
import {
  isVariancePreset,
  marketNeedStrategy,
  strategyForVersion,
} from "./mock-strategy";
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
  const input = value as {
    user_slot?: unknown;
    seed?: unknown;
    variance_preset?: unknown;
  };
  if (
    !Number.isInteger(input.user_slot)
    || (input.user_slot as number) < 1
    || !Number.isInteger(input.seed)
    || (input.seed as number) < 0
    || (input.seed as number) > 0xffff_ffff
    || (input.variance_preset !== undefined && !isVariancePreset(input.variance_preset))
  ) {
    return null;
  }
  return {
    user_slot: input.user_slot as number,
    seed: input.seed as number,
    variance_preset: input.variance_preset === undefined
      ? "realistic"
      : input.variance_preset as MockSetup["variance_preset"],
  };
}

interface MockTarget {
  mock_id: string;
  expected_revision: number;
}

function validTarget(value: unknown): MockTarget | null {
  if (!value || typeof value !== "object") return null;
  const input = value as { mock_id?: unknown; expected_revision?: unknown };
  if (
    typeof input.mock_id !== "string"
    || !input.mock_id.trim()
    || !Number.isInteger(input.expected_revision)
    || (input.expected_revision as number) < 0
  ) {
    return null;
  }
  return {
    mock_id: input.mock_id.trim(),
    expected_revision: input.expected_revision as number,
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

function savedBoard(loaded: LoadedMock): Board | null {
  try {
    const parsed = JSON.parse(loaded.board_json) as unknown;
    return isValidBoard(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

function stateWithBoard(loaded: LoadedMock, board: Board) {
  return { ...loaded.state, board };
}

function stateWithBoardError(loaded: LoadedMock) {
  return {
    ...loaded.state,
    board_error: "The mock's board snapshot is unreadable or unsupported.",
  };
}

function loadedStateResponse(loaded: LoadedMock, status = 200): Response {
  const board = savedBoard(loaded);
  return board
    ? json(stateWithBoard(loaded, board), status)
    : json(stateWithBoardError(loaded), status);
}

async function currentTarget(
  env: MockApiEnv,
  target: MockTarget,
): Promise<LoadedMock | Response> {
  const loaded = await loadCurrentMock(env.DB);
  if (!loaded) {
    return error("mock_unconfigured", "Start a mock before changing it.", 409);
  }
  if (
    loaded.state.mock?.id !== target.mock_id
    || loaded.aggregate.revision !== target.expected_revision
  ) {
    return error(
      "stale_mock",
      "The mock changed in another tab; reload before changing it.",
      409,
    );
  }
  return loaded;
}

async function reloadedState(env: MockApiEnv, mockId: string): Promise<Response> {
  const loaded = await loadMock(env.DB, mockId);
  return loaded
    ? loadedStateResponse(loaded)
    : error("mock_unconfigured", "The mock no longer exists.", 409);
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
      marketNeedStrategy,
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
      { ...stateWithBoard(loaded, loadedBoard.board), appended_picks: aggregate.picks },
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
      "Provide a player key, displayed mock ID, and displayed mock revision.",
      400,
    );
  }
  const value = input as {
    mock_id?: unknown;
    player_key?: unknown;
    expected_revision?: unknown;
  };
  const target = validTarget(value);
  const playerKey =
    typeof value.player_key === "string" && value.player_key.trim()
      ? value.player_key.trim()
      : null;
  if (
    target === null
    || playerKey === null
    || !Number.isInteger(value.expected_revision)
    || (value.expected_revision as number) < 0
  ) {
    return error(
      "invalid_request",
      "Provide a player key, displayed mock ID, and displayed mock revision.",
      400,
    );
  }

  const targeted = await currentTarget(env, target);
  if (targeted instanceof Response) return targeted;
  const loaded = targeted;
  if (loaded.state.lifecycle === "paused") {
    return error("mock_paused", "Resume the mock before recording a pick.", 409);
  }
  if (loaded.aggregate.complete) {
    return error("mock_complete", "The mock draft is complete.", 409);
  }
  const strategy = strategyForVersion(loaded.aggregate.strategy_version);
  if (!strategy) {
    return error(
      "board_unusable",
      "This mock uses an unsupported opponent strategy.",
      503,
    );
  }
  const expectedRevision = target.expected_revision;

  const board = savedBoard(loaded);
  if (!board) {
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
      strategy,
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
    const updated = await loadMock(env.DB, loaded.state.mock!.id);
    if (!updated) {
      return error("mock_unconfigured", "The mock no longer exists.", 409);
    }
    return json(
      { ...stateWithBoard(updated, board), appended_picks: transition.appended_picks },
      201,
    );
  } catch (caught) {
    if (caught instanceof MockDomainError) {
      if (caught.code === "unknown_player") {
        return error(caught.code, caught.message, 422);
      }
      if (
        caught.code === "player_unavailable"
        || caught.code === "illegal_roster_pick"
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

async function discardMock(request: Request, env: MockApiEnv): Promise<Response> {
  const target = validTarget(await requestBody(request));
  if (!target) {
    return error(
      "invalid_request",
      "Provide the displayed mock ID and revision.",
      400,
    );
  }
  const targeted = await currentTarget(env, target);
  if (targeted instanceof Response) return targeted;
  const result = await discardCurrentMock(
    env.DB,
    target.mock_id,
    target.expected_revision,
  );
  if (result !== "ok") {
    return error(
      "stale_mock",
      "The current mock changed in another tab; reload before discarding.",
      409,
    );
  }
  const current = await loadCurrentMock(env.DB);
  if (!current) return json(unconfiguredMockState());
  const board = savedBoard(current);
  return board
    ? json(stateWithBoard(current, board))
    : json(stateWithBoardError(current));
}

async function changePaused(
  request: Request,
  env: MockApiEnv,
  paused: boolean,
): Promise<Response> {
  const target = validTarget(await requestBody(request));
  if (!target) {
    return error(
      "invalid_request",
      "Provide the displayed mock ID and revision.",
      400,
    );
  }
  const targeted = await currentTarget(env, target);
  if (targeted instanceof Response) return targeted;
  const result = await setMockPaused(
    env.DB,
    target.mock_id,
    target.expected_revision,
    paused,
  );
  if (result === "stale_mock") {
    return error("stale_mock", "The mock changed in another tab; reload and try again.", 409);
  }
  if (result === "invalid_state") {
    return error(
      "invalid_mock_state",
      paused ? "Only an active mock can be paused." : "Only a paused mock can be resumed.",
      409,
    );
  }
  return reloadedState(env, target.mock_id);
}

async function undoMock(request: Request, env: MockApiEnv): Promise<Response> {
  const target = validTarget(await requestBody(request));
  if (!target) {
    return error("invalid_request", "Provide the displayed mock ID and revision.", 400);
  }
  const targeted = await currentTarget(env, target);
  if (targeted instanceof Response) return targeted;
  const result = await undoLatestMockDecision(
    env.DB,
    target.mock_id,
    target.expected_revision,
  );
  if (result === "stale_mock") {
    return error("stale_mock", "The mock changed in another tab; reload and try again.", 409);
  }
  if (result === "no_user_decisions") {
    return error(
      "no_user_decisions",
      "There is no Brian decision to undo.",
      409,
    );
  }
  return reloadedState(env, target.mock_id);
}

async function resetMock(request: Request, env: MockApiEnv): Promise<Response> {
  const target = validTarget(await requestBody(request));
  if (!target) {
    return error("invalid_request", "Provide the displayed mock ID and revision.", 400);
  }
  const targeted = await currentTarget(env, target);
  if (targeted instanceof Response) return targeted;
  const board = savedBoard(targeted);
  if (!board) {
    return error("board_unreadable", "The mock's board snapshot is unreadable.", 503);
  }
  const strategy = strategyForVersion(targeted.aggregate.strategy_version);
  if (!strategy) {
    return error("board_unusable", "This mock uses an unsupported opponent strategy.", 503);
  }
  try {
    const restarted = startMock(
      board,
      {
        user_slot: targeted.aggregate.user_slot,
        seed: targeted.aggregate.seed,
        variance_preset: targeted.aggregate.variance_preset,
      },
      strategy,
    );
    const result = await resetCurrentMock(
      env.DB,
      target.mock_id,
      target.expected_revision,
      restarted,
    );
    if (result === "stale_mock") {
      return error("stale_mock", "The mock changed in another tab; reload and try again.", 409);
    }
    if (result === "invalid_state") {
      return error("invalid_mock_state", "The saved mock configuration cannot be restarted.", 409);
    }
    return reloadedState(env, target.mock_id);
  } catch (caught) {
    if (caught instanceof MockDomainError) {
      return error(caught.code, caught.message, 503);
    }
    return error("board_unusable", "The mock could not be restarted.", 503);
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
      if (!current) return json(unconfiguredMockState());
      const board = savedBoard(current);
      return board
        ? json(stateWithBoard(current, board))
        : json(stateWithBoardError(current));
    }
    if (request.method === "DELETE") {
      return discardMock(request, env);
    }
    return methodNotAllowed("GET, DELETE");
  }
  if (pathname === "/api/mocks/current/picks") {
    return request.method === "POST"
      ? recordMockPick(request, env)
      : methodNotAllowed("POST");
  }
  if (pathname === "/api/mocks/current/picks/latest") {
    return request.method === "DELETE"
      ? undoMock(request, env)
      : methodNotAllowed("DELETE");
  }
  if (pathname === "/api/mocks/current/pause") {
    return request.method === "POST"
      ? changePaused(request, env, true)
      : methodNotAllowed("POST");
  }
  if (pathname === "/api/mocks/current/resume") {
    return request.method === "POST"
      ? changePaused(request, env, false)
      : methodNotAllowed("POST");
  }
  if (pathname === "/api/mocks/current/reset") {
    return request.method === "POST"
      ? resetMock(request, env)
      : methodNotAllowed("POST");
  }
  return error("not_found", "Not found.", 404);
}
