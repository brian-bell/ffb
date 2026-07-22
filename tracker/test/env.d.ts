import type { D1Migration } from "@cloudflare/vitest-pool-workers/config";

declare module "cloudflare:test" {
  interface ProvidedEnv {
    BOARD: KVNamespace;
    DB: D1Database;
    TEST_MIGRATIONS: D1Migration[];
    TRACKER_API_KEY: string;
  }
}
