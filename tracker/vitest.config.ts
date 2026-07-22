import { defineWorkersConfig, readD1Migrations } from "@cloudflare/vitest-pool-workers/config";

const migrations = await readD1Migrations("./migrations");

// Tests run in the workers pool so `env.BOARD` (KV) and `env.TRACKER_API_KEY`
// are real bindings in-test (Miniflare). The secret is injected here rather than
// committed to wrangler.jsonc.
export default defineWorkersConfig({
  test: {
    setupFiles: ["./test/apply-migrations.ts"],
    poolOptions: {
      workers: {
        wrangler: { configPath: "./wrangler.jsonc" },
        miniflare: {
          bindings: { TRACKER_API_KEY: "test-secret-key", TEST_MIGRATIONS: migrations },
        },
      },
    },
  },
});
