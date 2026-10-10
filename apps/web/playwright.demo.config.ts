/**
 * Records the README demo video: `npm run demo:record` (see docs/demo-video.md).
 * Not part of CI. Starts a real API in demo mode (with the Docker test sandbox enabled)
 * and the production web build, then drives the UI with on-screen captions.
 */
import { defineConfig } from "@playwright/test";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const API_PORT = Number(process.env.DEMO_API_PORT ?? 8400);
const WEB_PORT = Number(process.env.DEMO_WEB_PORT ?? 3400);
const TOKEN = "demo-recording-admin-token";
const dataDir = mkdtempSync(join(tmpdir(), "orchestra-demo-"));

export default defineConfig({
  testDir: "./demo",
  outputDir: "./demo-output",
  timeout: 240_000,
  workers: 1,
  reporter: "list",
  use: {
    baseURL: `http://127.0.0.1:${WEB_PORT}`,
    viewport: { width: 1440, height: 900 },
    video: { mode: "on", size: { width: 1440, height: 900 } },
    colorScheme: "dark",
  },
  webServer: [
    {
      command: "uv run orchestra-api",
      cwd: "../../services/api",
      url: `http://127.0.0.1:${API_PORT}/api/health`,
      env: {
        ORCHESTRA_MODE: "demo",
        ORCHESTRA_PORT: String(API_PORT),
        ORCHESTRA_DATA_DIR: dataDir,
        ORCHESTRA_DATABASE_URL: `sqlite+aiosqlite:///${dataDir}/orchestra.db`,
        ORCHESTRA_ADMIN_TOKEN: TOKEN,
        ORCHESTRA_RATE_LIMIT_PER_MINUTE: "0",
        ORCHESTRA_SANDBOX_ENABLED: process.env.DEMO_SANDBOX ?? "true",
      },
      reuseExistingServer: false,
      timeout: 60_000,
    },
    {
      command: `npx next start -p ${WEB_PORT}`,
      url: `http://127.0.0.1:${WEB_PORT}`,
      env: { ORCHESTRA_API_URL: `http://127.0.0.1:${API_PORT}`, ORCHESTRA_API_TOKEN: TOKEN },
      reuseExistingServer: false,
      timeout: 60_000,
    },
  ],
});
