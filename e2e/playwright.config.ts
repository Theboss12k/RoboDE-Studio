import { defineConfig } from "@playwright/test";
import path from "node:path";

const repo = path.resolve(__dirname, "..");
const slash = (p: string) => p.replace(/\\/g, "/");
// Defaults are the usual ones; E2E_PORT / E2E_RUNTIME let a run live next to a demo server on 8810 that uses e2e/.runtime.
const PORT = process.env.E2E_PORT || "8810";
const PAIR_PORT = String(Number(PORT) + 1);
const RUNTIME = process.env.E2E_RUNTIME || ".runtime";

export default defineConfig({
  testDir: path.join(__dirname, "tests"),
  workers: 1,
  fullyParallel: false,
  timeout: 120_000,
  expect: { timeout: 15_000 },
  reporter: [["list"], ["html", { outputFolder: path.join(__dirname, "report"), open: "never" }]],
  outputDir: path.join(repo, "test-results"),
  globalSetup: path.join(__dirname, "global-setup.ts"),
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: "retain-on-failure",
    viewport: { width: 1800, height: 1200 },
    // Optional: E2E_CHANNEL=chrome (or msedge) runs an installed browser instead of the downloaded Chromium.
    channel: process.env.E2E_CHANNEL || undefined,
  },
  webServer: {
    command: "node e2e/prepare.mjs && java -jar server-java/target/studio-server-0.1.0.jar",
    cwd: repo,
    url: `http://127.0.0.1:${PORT}/api/v1/health`,
    reuseExistingServer: false,
    timeout: 120_000,
    stdout: "pipe",
    stderr: "pipe",
    env: {
      ...(process.env as Record<string, string>),
      E2E_RUNTIME: RUNTIME,
      STUDIO_PORT: PORT,
      STUDIO_PAIR_PORT: PAIR_PORT,
      STUDIO_RUNTIME: slash(path.join(repo, "e2e", RUNTIME)),
      STUDIO_ALLOW_SCRIPTED: "1",
      STUDIO_PYTHON: slash(path.join(repo, ".venv", "Scripts", "python.exe")),
      PYTHONUTF8: "1",
      STUDIO_WARM_META: "false",
    },
  },
});
