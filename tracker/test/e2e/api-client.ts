import { SELF } from "cloudflare:test";
import type { DraftConfigInput, DraftState } from "../../src/draft-store";
import type { Board } from "../../src/types";

const API_ORIGIN = "https://e2e.test";
const AUTHORIZATION = "Bearer test-secret-key";

export interface ApiResult<T> {
  status: number;
  headers: Headers;
  body: string;
  json?: T;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<ApiResult<T>> {
  const headers = new Headers(init.headers);
  headers.set("Authorization", AUTHORIZATION);
  if (init.body !== undefined) headers.set("content-type", "application/json");

  const response = await SELF.fetch(`${API_ORIGIN}${path}`, { ...init, headers });
  const body = await response.text();
  let decoded: T | undefined;
  try {
    decoded = body ? (JSON.parse(body) as T) : undefined;
  } catch {
    decoded = undefined;
  }
  return { status: response.status, headers: response.headers, body, json: decoded };
}

export const api = {
  health: () => request<{ ok: boolean }>("/api/health"),
  getBoard: () => request<Board>("/api/board"),
  getDraft: () => request<DraftState>("/api/draft"),
  configureDraft: (config: DraftConfigInput) =>
    request<DraftState>("/api/draft", { method: "PUT", body: JSON.stringify(config) }),
  recordBoardPlayer: (playerKey: string, expectedOverallPick: number) =>
    request<DraftState>("/api/picks", {
      method: "POST",
      body: JSON.stringify({ player_key: playerKey, expected_overall_pick: expectedOverallPick }),
    }),
  resetDraft: () => request<DraftState>("/api/draft", { method: "DELETE" }),
};
