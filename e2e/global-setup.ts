// The runtime and photos are prepared by e2e/prepare.mjs, which the webServer command runs first (Playwright starts
// webServer before globalSetup). This only fails fast if that did not happen.
import { existsSync } from "node:fs";
import path from "node:path";

export default function globalSetup() {
  const root = path.resolve(__dirname);
  for (const f of [`${process.env.E2E_RUNTIME || ".runtime"}/llm/profiles.json`, ".photos/manifest.json", ".photos/room.mp4", ".photos/room_truth.json"]) {
    if (!existsSync(path.join(root, f))) throw new Error(`e2e/${f} is missing: e2e/prepare.mjs did not run`);
  }
}
