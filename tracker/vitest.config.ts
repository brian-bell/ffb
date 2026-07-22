import { defineWorkersConfig } from "@cloudflare/vitest-pool-workers/config";

// Tests run in the workers pool so `env.BOARD` (KV) and `env.TRACKER_API_KEY`
// are real bindings in-test (Miniflare). The secret is injected here rather than
// committed to wrangler.jsonc.
export default defineWorkersConfig({
  test: {
    poolOptions: {
      workers: {
        wrangler: { configPath: "./wrangler.jsonc" },
        miniflare: {
          bindings: { TRACKER_API_KEY: "test-secret-key" },
        },
      },
    },
  },
});
