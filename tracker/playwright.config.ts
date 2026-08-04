import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./test/browser",
  fullyParallel: false,
  workers: 1,
  reporter: "line",
  use: {
    baseURL: "http://127.0.0.1:4173",
    browserName: "chromium",
  },
  webServer: {
    command: "exec node test/browser/fixture-server.mjs",
    url: "http://127.0.0.1:4173/mock",
    reuseExistingServer: false,
  },
});
