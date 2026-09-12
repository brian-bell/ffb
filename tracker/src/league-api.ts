import {
  getLeagueBundleText,
  leagueBundleSummary,
  parseBundle,
  putLeagueBundle,
  type LeagueBundleEnv,
} from "./league-bundle";

function json(body: unknown, status = 200, headers: HeadersInit = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", ...headers },
  });
}

function methodNotAllowed(allow: string): Response {
  return json({ error: "method_not_allowed", message: "Method not allowed." }, 405, {
    Allow: allow,
  });
}

export async function handleLeagueApi(
  request: Request,
  env: LeagueBundleEnv,
): Promise<Response> {
  if (request.method === "GET") {
    const text = await getLeagueBundleText(env);
    if (text === null) {
      return json({ error: "no league bundle" }, 404);
    }
    return new Response(text, { headers: { "content-type": "application/json" } });
  }

  if (request.method !== "POST") {
    return methodNotAllowed("GET, POST");
  }

  let payload: unknown;
  try {
    payload = await request.json();
  } catch {
    return json({ error: "invalid_json", message: "body must be JSON" }, 400);
  }

  let bundle;
  try {
    bundle = parseBundle(payload);
  } catch (caught) {
    const message = caught instanceof Error ? caught.message : "invalid league bundle";
    return json({ error: "invalid_bundle", message }, 400);
  }

  await putLeagueBundle(env, bundle);
  return json(leagueBundleSummary(bundle));
}
