// Single shared bearer-key gate for /api/* (slice-6 §3b). Missing / malformed /
// mismatched Authorization → 401. The compare is constant-time to avoid the
// timing side-channel (low-stakes here, but it sets the pattern for slice-7's
// write routes).

export interface AuthEnv {
  TRACKER_API_KEY: string;
}

function unauthorized(): Response {
  return new Response(JSON.stringify({ error: "unauthorized" }), {
    status: 401,
    headers: { "content-type": "application/json" },
  });
}

// Length-independent byte compare: folds a length mismatch into the accumulator
// and always walks the longer of the two so it never short-circuits on length.
function timingSafeEqual(a: string, b: string): boolean {
  const enc = new TextEncoder();
  const ab = enc.encode(a);
  const bb = enc.encode(b);
  const len = Math.max(ab.length, bb.length);
  let diff = ab.length ^ bb.length;
  for (let i = 0; i < len; i++) {
    diff |= (ab[i] ?? 0) ^ (bb[i] ?? 0);
  }
  return diff === 0;
}

/** Returns a 401 Response when the bearer key is absent/wrong, else null (pass). */
export function requireBearer(request: Request, env: AuthEnv): Response | null {
  const header = request.headers.get("Authorization") ?? "";
  const match = /^Bearer (.+)$/.exec(header);
  if (!match) return unauthorized();
  if (!timingSafeEqual(match[1], env.TRACKER_API_KEY)) return unauthorized();
  return null;
}
