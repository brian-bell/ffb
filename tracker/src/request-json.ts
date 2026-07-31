export interface JsonRequestResult<T> {
  response: Response | null;
  value: T | null;
  transportError: string | null;
}

export type FetchJson = (path: string, init?: RequestInit) => Promise<Response>;

export async function requestJson<T>(
  fetchJson: FetchJson,
  path: string,
  init: RequestInit = {},
): Promise<JsonRequestResult<T>> {
  try {
    const response = await fetchJson(path, init);
    let value: T | null = null;
    try {
      value = (await response.json()) as T;
    } catch {
      value = null;
    }
    return { response, value, transportError: null };
  } catch {
    return {
      response: null,
      value: null,
      transportError: "Unable to reach the tracker. Check your connection and try again.",
    };
  }
}
