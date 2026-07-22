// Worker entry: /api/* is handled here (auth-gated data + public health ping);
// everything else falls through to Static Assets (the mobile shell). The board
// blob is streamed from KV verbatim — the pipeline owns its shape (§5).

import { requireBearer } from "./auth";
import { getBoardText } from "./board";
import { handleDraftApi } from "./draft-api";

export interface Env {
  ASSETS: Fetcher;
  BOARD: KVNamespace;
  DB: D1Database;
  TRACKER_API_KEY: string;
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    const { pathname } = url;

    // Public liveness ping — no secrets, no auth.
    if (pathname === "/api/health") {
      return json({ ok: true });
    }

    if (pathname === "/api/board") {
      const denied = requireBearer(request, env);
      if (denied) return denied;
      const text = await getBoardText(env);
      if (text === null) {
        return json({ error: "no board published" }, 404);
      }
      return new Response(text, {
        headers: { "content-type": "application/json" },
      });
    }

    if (pathname === "/api/draft" || pathname === "/api/picks" || pathname === "/api/picks/latest") {
      const denied = requireBearer(request, env);
      if (denied) return denied;
      return handleDraftApi(request, env, pathname);
    }

    // Any other /api/* path is a real 404 (never a static asset).
    if (pathname.startsWith("/api/")) {
      return json({ error: "not found" }, 404);
    }

    // Static shell + assets (public — the user must load the page to enter a key).
    return env.ASSETS.fetch(request);
  },
} satisfies ExportedHandler<Env>;
