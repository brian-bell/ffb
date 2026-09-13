import { readFileSync } from "node:fs";
import { defineWorkersConfig, readD1Migrations } from "@cloudflare/vitest-pool-workers/config";

const boardPath = process.env.FFB_E2E_BOARD_PATH;
if (!boardPath) {
  throw new Error("FFB_E2E_BOARD_PATH is required");
}

const boardJson = readFileSync(boardPath, "utf8");
const inseasonDir = process.env.FFB_E2E_INSEASON_DIR;
if (!inseasonDir) {
  throw new Error("FFB_E2E_INSEASON_DIR is required");
}
const inseasonJson = JSON.stringify(
  Object.fromEntries(
    ["lineup", "digest", "retro", "ros"].map((kind) => [kind, readFileSync(`${inseasonDir}/${kind}.json`, "utf8")]),
  ),
);
const migrations = await readD1Migrations("./migrations");

export default defineWorkersConfig({
  test: {
    include: ["test/e2e/**/*.test.ts"],
    setupFiles: ["./test/apply-migrations.ts"],
    poolOptions: {
      workers: {
        isolatedStorage: true,
        wrangler: { configPath: "./wrangler.jsonc" },
        miniflare: {
          bindings: {
            TRACKER_API_KEY: "test-secret-key",
            TEST_MIGRATIONS: migrations,
            E2E_BOARD_JSON: boardJson,
            E2E_INSEASON_JSON: inseasonJson,
          },
        },
      },
    },
  },
});
