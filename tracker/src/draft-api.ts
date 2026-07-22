import { BOARD_KEY, BOARD_VERSION } from "./board";
import { configureDraft, getDraftState, recordPick, resetDraft, undoLatestPick, type DraftConfigInput } from "./draft-store";
import type { Player } from "./types";

export interface DraftApiEnv {
  BOARD: KVNamespace;
  DB: D1Database;
}

function json(body: unknown, status = 200, headers: HeadersInit = {}): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json", ...headers } });
}

function error(code: string, message: string, status: number): Response {
  return json({ error: code, message }, status);
}

function methodNotAllowed(allow: string): Response {
  return json({ error: "method_not_allowed", message: "Method not allowed." }, 405, { Allow: allow });
}

async function body(request: Request): Promise<unknown | null> {
  try {
    return await request.json();
  } catch {
    return null;
  }
}

function defaultName(season: unknown): string {
  return typeof season === "number" ? `${season} Draft` : "Draft";
}

function validConfig(value: unknown, fallbackName: string): DraftConfigInput | null {
  if (!value || typeof value !== "object") return null;
  const input = value as { name?: unknown; rounds?: unknown; teams?: unknown };
  if (!Number.isInteger(input.rounds) || (input.rounds as number) < 1 || (input.rounds as number) > 30 || !Array.isArray(input.teams) || input.teams.length < 2 || input.teams.length > 20) return null;
  const teams = input.teams.map((raw) => {
    if (!raw || typeof raw !== "object") return null;
    const team = raw as { name?: unknown; is_user?: unknown };
    const name = typeof team.name === "string" ? team.name.trim() : "";
    return name && typeof team.is_user === "boolean" ? { name, is_user: team.is_user } : null;
  });
  if (teams.some((team) => team === null)) return null;
  const validated = teams as DraftConfigInput["teams"];
  if (new Set(validated.map((team) => team.name.toLocaleLowerCase())).size !== validated.length || validated.filter((team) => team.is_user).length !== 1) return null;
  const name = typeof input.name === "string" && input.name.trim() ? input.name.trim() : fallbackName;
  return { name, rounds: input.rounds as number, teams: validated };
}

async function currentBoard(env: DraftApiEnv): Promise<{ board: { season?: unknown; players?: unknown; version?: unknown } } | { response: Response }> {
  const text = await env.BOARD.get(BOARD_KEY);
  if (text === null) return { response: error("no_board_published", "No board has been published.", 404) };
  try {
    const board = JSON.parse(text) as { season?: unknown; players?: unknown; version?: unknown };
    if (board.version !== BOARD_VERSION || !Array.isArray(board.players)) return { response: error("board_unreadable", "Board format is unreadable or unsupported.", 503) };
    return { board };
  } catch {
    return { response: error("board_unreadable", "Board format is unreadable or unsupported.", 503) };
  }
}

async function putDraft(request: Request, env: DraftApiEnv): Promise<Response> {
  const input = await body(request);
  let fallback = "Draft";
  const loaded = await currentBoard(env);
  if ("board" in loaded) fallback = defaultName(loaded.board.season);
  const config = validConfig(input, fallback);
  if (!config) return error("invalid_draft", "Provide 2–20 uniquely named teams, one user team, and 1–30 rounds.", 400);
  try {
    return json(await configureDraft(env.DB, config));
  } catch (caught) {
    if (caught instanceof Error && caught.message === "draft_started") return error("draft_started", "Reset the draft before changing its configuration.", 409);
    return error("invalid_draft", "Draft configuration could not be saved.", 400);
  }
}

async function postPick(request: Request, env: DraftApiEnv): Promise<Response> {
  const input = await body(request);
  if (!input || typeof input !== "object") return error("invalid_request", "Provide a player key and expected pick.", 400);
  const value = input as { player_key?: unknown; expected_overall_pick?: unknown };
  if (typeof value.player_key !== "string" || !value.player_key || !Number.isInteger(value.expected_overall_pick) || (value.expected_overall_pick as number) < 1) return error("invalid_request", "Provide a player key and expected pick.", 400);
  const state = await getDraftState(env.DB);
  if (!state.configured) return error("draft_unconfigured", "Configure the draft before recording a pick.", 409);
  if (!state.next) return error("draft_complete", "The draft is complete.", 409);
  if (state.next.overall_pick !== value.expected_overall_pick) return error("stale_draft", "The draft changed in another tab; reload before recording.", 409);
  const loaded = await currentBoard(env);
  if ("response" in loaded) return loaded.response;
  const player = (loaded.board.players as Player[]).find((candidate) => candidate && candidate.key === value.player_key);
  if (!player) return error("unknown_player", "Player is not on the current board.", 422);
  const result = await recordPick(env.DB, player, value.expected_overall_pick as number);
  if (result !== "ok") return error(result, result.replaceAll("_", " "), 409);
  return json(await getDraftState(env.DB), 201);
}

async function deleteLatestPick(request: Request, env: DraftApiEnv): Promise<Response> {
  const input = await body(request);
  const expected = input && typeof input === "object" ? (input as { expected_overall_pick?: unknown }).expected_overall_pick : null;
  if (!Number.isInteger(expected) || (expected as number) < 1) return error("invalid_request", "Provide the displayed latest pick number.", 400);
  const result = await undoLatestPick(env.DB, expected as number);
  if (result !== "ok") return error(result, result.replaceAll("_", " "), 409);
  return json(await getDraftState(env.DB));
}

/** Authenticated router for state that belongs in D1, never the board KV. */
export async function handleDraftApi(request: Request, env: DraftApiEnv, pathname: string): Promise<Response> {
  if (pathname === "/api/draft") {
    if (request.method === "GET") return json(await getDraftState(env.DB));
    if (request.method === "PUT") return putDraft(request, env);
    if (request.method === "DELETE") {
      await resetDraft(env.DB);
      return json(await getDraftState(env.DB));
    }
    return methodNotAllowed("GET, PUT, DELETE");
  }
  if (pathname === "/api/picks") return request.method === "POST" ? postPick(request, env) : methodNotAllowed("POST");
  if (pathname === "/api/picks/latest") return request.method === "DELETE" ? deleteLatestPick(request, env) : methodNotAllowed("DELETE");
  return error("not_found", "Not found.", 404);
}
