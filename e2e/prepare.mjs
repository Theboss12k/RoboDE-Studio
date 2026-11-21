// Prepares the isolated runtime and the sample photos. It runs as part of the web server command (see
// playwright.config.ts): Playwright starts webServer BEFORE globalSetup, so a globalSetup could not wipe the runtime.
import { execFileSync } from "node:child_process";
import { mkdirSync, rmSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const runtime = path.join(repo, "e2e", process.env.E2E_RUNTIME || ".runtime");
const photos = path.join(repo, "e2e", ".photos");

rmSync(runtime, { recursive: true, force: true });
rmSync(photos, { recursive: true, force: true });
mkdirSync(path.join(runtime, "llm"), { recursive: true });

// Same content as FlowSupport.writeScriptedProfiles.
const profiles = [
  { id: "claude_opus", name: "Claude Opus 5.5 (Claude Code CLI)", kind: "claude_cli", model: "claude-opus-5-5", effort: "", timeout_s: 240, cli_path: "" },
  { id: "claude_sonnet", name: "Claude Sonnet 5.5 (Claude Code CLI)", kind: "claude_cli", model: "claude-sonnet-5-5", effort: "", timeout_s: 240, cli_path: "" },
  { id: "fx", name: "Scripted fixture", kind: "scripted", model: "fixture",
    fixture: path.join(repo, "server-java", "src", "test", "resources", "scripted", "rules.json"), effort: "", timeout_s: 240, cli_path: "" },
];
const roles = Object.fromEntries(["spec", "scene", "robot", "revise", "search", "extract", "analysis"].map((r) => [r, "fx"]));
writeFileSync(path.join(runtime, "llm", "profiles.json"), JSON.stringify({ profiles, roles }, null, 2));

execFileSync(path.join(repo, ".venv", "Scripts", "python.exe"), [path.join(repo, "e2e", "fixtures", "make_photos.py"), photos],
  { stdio: "inherit", env: { ...process.env, PYTHONUTF8: "1" } });

// The synthetic room video of the site-scan tests (rendered once, cached in tests/.cache) and its ground truth.
execFileSync(path.join(repo, ".venv", "Scripts", "python.exe"), [path.join(repo, "e2e", "fixtures", "make_scan.py"), photos],
  { stdio: "inherit", env: { ...process.env, PYTHONUTF8: "1" } });
