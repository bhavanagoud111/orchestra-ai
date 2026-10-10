import { defineConfig, devices } from "@playwright/test";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

// Starts a real API (demo mode, throwaway SQLite) and the production web build.
const API_PORT = Number(process.env.E2E_API_PORT ?? 8300);
const WEB_PORT = Number(process.env.E2E_WEB_PORT ?? 3300);
const TOKEN = "e2e-admin-token-0123456789";
const dataDir = mkdtempSync(join(tmpdir(), "orchestra-e2e-"));

export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  fullyParallel: false,
  workers: 1,
  reporter: process.env.CI ? [["github"], ["html", { open: "never" }]] : "list",
  use: { baseURL: `http://127.0.0.1:${WEB_PORT}`, trace: "retain-on-failure" },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
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
