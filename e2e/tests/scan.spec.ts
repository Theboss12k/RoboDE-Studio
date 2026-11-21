import { test, expect, devices, type APIRequestContext, type Browser, type BrowserContext, type Locator, type Page } from "@playwright/test";
import { mkdirSync, readFileSync } from "node:fs";
import path from "node:path";

// Site scan ("My site") end to end: a synthetic room video with ground truth (e2e/fixtures/make_scan.py), the real
// reconstruction on the GPU, the scale from a sheet in a frame, a measurement, edits applied to the design as a new
// version, the phone link and the view switch. The page is opened with ?e2e=1, which turns on a small read-only debug
// hook (window.__site) in the 3D layer: it lets the test aim the camera and ask where a point of the scan is on screen.

const PHOTOS = path.resolve(__dirname, "..", ".photos");
const VIDEO = path.join(PHOTOS, "room.mp4");
const API = "/api/v1";
const SHOTS = path.resolve(__dirname, "..", "..", "docs", "screenshots", "site");

type P3 = [number, number, number];
type SheetView = { pixels: number[][]; visible: boolean; area_px: number };
const truth: { width: number; height: number; fps: number; room: number[]; table: { size: number[]; top_height: number };
  sheet: { size_m: number[] }; sheet_views: SheetView[] } = JSON.parse(readFileSync(path.join(PHOTOS, "room_truth.json"), "utf8"));

type Scan = {
  id: string; status: string; error: string | null; placement: { x: number; y: number; yaw_deg: number };
  camera: { width: number; height: number; undistorted?: boolean } | null;
  frames: { index: number; t_s: number; world_from_camera: number[][] | null }[];
  scale: { method: string; factor: number; residual: number } | null;
  stats: { frames_total: number; frames_registered: number; reproj_px: number; points3d: number; extent: number[] } | null;
  mesh: { vertices: number; triangles: number } | null;
};

test.describe.configure({ mode: "serial" });

let request: APIRequestContext;
let browser_: Browser;
let context: BrowserContext;
let page: Page;
let pid = "";
const consoleErrors: string[] = [];

async function scans(): Promise<Scan[]> {
  const r = await request.get(`${API}/projects/${pid}/scans`);
  expect(r.ok()).toBeTruthy();
  return r.json();
}

async function runJob(startPath: string, body: unknown) {
  const r = await request.post(`${API}${startPath}`, { data: body });
  expect(r.status(), await r.text()).toBe(200);
  let job = await r.json();
  const end = Date.now() + 100_000;
  while (job.status === "running") {
    expect(Date.now()).toBeLessThan(end);
    await new Promise((res) => setTimeout(res, 200));
    job = await (await request.get(`${API}/jobs/${job.id}`)).json();
  }
  expect(job.status, JSON.stringify(job)).toBe("done");
  return job;
}

const sheetList = () => page.getByRole("list", { name: "Site scans" });
const hook = <T,>(fn: string, ...args: unknown[]) => page.evaluate(([name, a]) => {
  const h = (window as unknown as { __site?: Record<string, (...x: unknown[]) => unknown> }).__site;
  if (!h) throw new Error("window.__site is missing: is the page opened with ?e2e=1 and the scan drawn?");
  return h[name as string](...(a as unknown[]));
}, [fn, args] as const) as Promise<T>;

async function openSiteView() {
  await page.getByRole("radio", { name: "My site" }).click();
  await expect(page.getByRole("region", { name: "My site" })).toBeVisible();
}
async function tool(name: string) {
  const b = page.getByRole("toolbar", { name: "Site tools" }).getByRole("button", { name, exact: true });
  if ((await b.getAttribute("aria-pressed")) !== "true") await b.click();
  await expect(b).toHaveAttribute("aria-pressed", "true");
}

/** The room as the mesh shows it: its two wall directions in the floor plane (a = the long one), the wall positions and the floor height. */
async function roomFromMesh() {
  await expect.poll(() => hook<boolean>("ready"), { timeout: 60_000 }).toBe(true);
  await expect.poll(async () => (await hook<P3[]>("vertices", 50)).length, { timeout: 60_000 }).toBeGreaterThan(200);
  const all = await hook<P3[]>("vertices", 6);
  const floorZ = (() => { const z = all.filter((v) => Math.abs(v[2]) < 0.3).map((v) => v[2]).sort((x, y) => x - y); return z[Math.floor(z.length / 2)] ?? 0; })();
  const walls = all.filter((v) => v[2] > floorZ + 0.2 && v[2] < floorZ + 2.2);
  const pct = (xs: number[], q: number) => { const s = [...xs].sort((x, y) => x - y); return s[Math.min(s.length - 1, Math.max(0, Math.round(q * (s.length - 1))))]; };
  let best = { area: Infinity, deg: 0, u: [0, 0], v: [0, 0] };
  for (let deg = 0; deg < 90; deg += 0.5) {
    const c = Math.cos((deg * Math.PI) / 180), s = Math.sin((deg * Math.PI) / 180);
    const us = walls.map((p) => c * p[0] + s * p[1]), vs = walls.map((p) => -s * p[0] + c * p[1]);
    const du = pct(us, 0.995) - pct(us, 0.005), dv = pct(vs, 0.995) - pct(vs, 0.005);
    if (du * dv < best.area) best = { area: du * dv, deg, u: [pct(us, 0.005), pct(us, 0.995)], v: [pct(vs, 0.005), pct(vs, 0.995)] };
  }
  const c = Math.cos((best.deg * Math.PI) / 180), s = Math.sin((best.deg * Math.PI) / 180);
  let dirA = [c, s], dirB = [-s, c], rangeA = best.u, rangeB = best.v;
  if (best.v[1] - best.v[0] > best.u[1] - best.u[0]) { dirA = [-s, c]; dirB = [-c, -s]; rangeA = best.v; rangeB = [-best.u[1], -best.u[0]]; }
  return {
    floorZ, rangeA, rangeB, lengthA: rangeA[1] - rangeA[0], lengthB: rangeB[1] - rangeB[0],
    at: (a: number, b: number, z: number): P3 => [a * dirA[0] + b * dirB[0], a * dirA[1] + b * dirB[1], z],
    centre: [(rangeA[0] + rangeA[1]) / 2, (rangeB[0] + rangeB[1]) / 2],
  };
}
type Room = Awaited<ReturnType<typeof roomFromMesh>>;

/** Looks straight down at the room's centre from above, so that floor points are visible and not hidden by walls. */
async function lookDown(room: Room, height = 7.5) {
  const [ca, cb] = room.centre;
  await page.locator(".scene").scrollIntoViewIfNeeded();
  await hook("view", room.at(ca + 0.02, cb + 0.02, room.floorZ + height), room.at(ca, cb, room.floorZ));
  await page.waitForTimeout(400);
}

/** Clicks the mesh (or a box) at a point of S; fails when the click would land more than `tol` m away from it. */
async function clickAt(p: P3, tol = 0.08) {
  await page.locator(".scene").scrollIntoViewIfNeeded();
  const xy = await hook<{ x: number; y: number } | null>("screen", p);
  expect(xy, `point ${p} is not on screen`).toBeTruthy();
  const hit = await hook<P3 | null>("hit", xy!.x, xy!.y);
  if (hit) expect(Math.hypot(hit[0] - p[0], hit[1] - p[1]), `a click at ${p.map((n) => n.toFixed(2))} would pick ${hit.map((n) => n.toFixed(2))}`).toBeLessThan(tol);
  await page.mouse.click(xy!.x, xy!.y);
}

async function center(loc: Locator) {
  const b = (await loc.boundingBox())!;
  return { x: b.x + b.width / 2, y: b.y + b.height / 2 };
}
const handle = (i: number): Locator => page.getByRole("button", { name: new RegExp(`^Corner ${i + 1} at`) });
async function imageToScreen(px: number[]) {
  return page.locator("svg.ar-calib").evaluate((el, [u, v]) => {
    const m = (el as unknown as SVGSVGElement).getScreenCTM()!;
    return { x: m.a * u + m.e, y: m.d * v + m.f };
  }, px as [number, number]);
}
async function dragHandle(i: number, target: number[]) {
  const from = await center(handle(i).locator("circle"));
  const to = await imageToScreen(target);
  await page.mouse.move(from.x, from.y);
  await page.mouse.down();
  await page.mouse.move((from.x + to.x) / 2, (from.y + to.y) / 2, { steps: 4 });
  await page.mouse.move(to.x, to.y, { steps: 6 });
  await page.mouse.up();
}

test.beforeAll(async ({ playwright, browser }) => {
  request = await playwright.request.newContext({ baseURL: `http://127.0.0.1:${process.env.E2E_PORT || "8810"}` });
  browser_ = browser;
  const created = await request.post(`${API}/projects`, { data: { brief: "Move a 0.3 kg bottle from A to B" } });
  expect(created.status(), await created.text()).toBe(200);
  pid = (await created.json()).id;
  await runJob(`/projects/${pid}/design`, { answers: {} });
  const designed = await runJob(`/projects/${pid}/design`, { answers: { cycle: 6 } });
  expect(designed.result).toEqual({ status: "designed", version: 1 });

  context = await browser.newContext({ baseURL: `http://127.0.0.1:${process.env.E2E_PORT || "8810"}`, viewport: { width: 1800, height: 1200 } });
  page = await context.newPage();
  page.on("console", (m) => { if (m.type() === "error") consoleErrors.push(m.text()); });
  page.on("pageerror", (e) => consoleErrors.push(`pageerror: ${e.message}`));
  await page.goto("/?e2e=1");
  await page.locator("#project-pick").selectOption(pid);
  await page.getByRole("button", { name: /^Design/ }).first().click();
  await openSiteView();
});

test.afterAll(async () => {
  await context?.close();
  await request?.dispose();
});

test("1. upload the video, reconstruct it (real GPU run)", async () => {
  test.setTimeout(900_000);
  await page.locator('.site-panel input[type="file"][accept="video/*"]').setInputFiles(VIDEO);
  await expect.poll(async () => (await scans()).length, { timeout: 60_000 }).toBe(1);
  await expect(sheetList().getByRole("listitem")).toHaveCount(1);
  await expect(sheetList().getByText("Uploaded", { exact: true })).toBeVisible();
  expect((await scans())[0].status).toBe("uploaded");

  await page.getByRole("button", { name: "Reconstruct", exact: true }).click();
  const started = Date.now();
  let camerasLine = "";
  let status = "";
  for (;;) {
    const row = await sheetList().innerText().catch(() => "");
    const m = /Cameras: \d+\/\d+ registered/.exec(row);
    if (m) camerasLine = m[0];
    status = (await scans())[0].status;
    if (status === "reconstructed" || status === "failed") break;
    expect(Date.now() - started, "reconstruction takes too long").toBeLessThan(840_000);
    await page.waitForTimeout(300);
  }
  const scan = (await scans())[0];
  expect(status, scan.error ?? "").toBe("reconstructed");
  const seconds = (Date.now() - started) / 1000;
  await expect(sheetList().getByText("Reconstructed", { exact: true })).toBeVisible();
  expect(camerasLine, "the 'Cameras: N/N registered' progress line was shown in the scan list").toMatch(/^Cameras: \d+\/\d+ registered$/);
  const st = scan.stats!;
  console.log(`reconstruction ${seconds.toFixed(0)} s; progress seen: "${camerasLine}"; frames registered ${st.frames_registered}/${st.frames_total} `
    + `(${((100 * st.frames_registered) / st.frames_total).toFixed(1)}%), reproj ${st.reproj_px.toFixed(2)} px, points3d ${st.points3d}, `
    + `mesh ${scan.mesh!.vertices} vertices / ${scan.mesh!.triangles} triangles, extent ${st.extent.map((e) => e.toFixed(2)).join(" x ")} units`);
  expect(st.frames_registered / st.frames_total).toBeGreaterThanOrEqual(0.9);
  expect(st.points3d).toBeGreaterThan(0);
  expect(scan.mesh!.triangles).toBeGreaterThan(0);
  expect(scan.scale).toBeNull();
  await expect(page.getByText("Unscaled — set a scale")).toBeVisible();
});

test("2. set the scale from the sheet in a frame", async () => {
  test.setTimeout(240_000);
  const scan = (await scans())[0];
  const camera = scan.camera!;
  const resize = camera.width / truth.width;
  const candidates = scan.frames.filter((f) => f.world_from_camera).map((f) => {
    const idx = Math.min(truth.sheet_views.length - 1, Math.round(f.t_s * truth.fps));
    return { f, view: truth.sheet_views[idx] };
  }).filter((c) => c.view.visible);
  expect(candidates.length, "no registered frame sees the whole sheet").toBeGreaterThan(0);
  const best = candidates.reduce((a, b) => (b.view.area_px > a.view.area_px ? b : a));
  const corners = best.view.pixels.map(([u, v]) => [u * resize, v * resize]);

  await tool("Set scale");
  await page.getByRole("button", { name: "Use frame " + best.f.index, exact: true }).click();
  await expect(page.getByRole("group", { name: "Photo with four corner handles" })).toBeVisible();
  await page.locator("svg.ar-calib").scrollIntoViewIfNeeded();
  for (let i = 0; i < 4; i++) await dragHandle(i, corners[i]);
  for (let i = 0; i < 4; i++) {
    const label = (await handle(i).getAttribute("aria-label"))!;
    const m = /at (-?\d+), (-?\d+)/.exec(label)!;
    expect(Math.abs(Number(m[1]) - corners[i][0]), label).toBeLessThan(4);
    expect(Math.abs(Number(m[2]) - corners[i][1]), label).toBeLessThan(4);
  }
  await page.getByLabel("Rectangle size").selectOption("a4");
  await page.getByRole("button", { name: "Calculate scale", exact: true }).click();
  await expect(page.getByRole("status").filter({ hasText: /Scale set\./ }).first()).toBeVisible();

  const got = (await scans())[0];
  expect(got.scale, "scale stored").toBeTruthy();
  console.log(`sheet scale from frame ${best.f.index} (${best.view.area_px.toFixed(0)} px^2 in the video): factor ${got.scale!.factor.toFixed(5)}, `
    + `residual ${got.scale!.residual.toFixed(4)}, method ${got.scale!.method}`);
  expect(got.scale!.method).toBe("sheet");
  expect(got.scale!.factor).toBeGreaterThan(0);
  expect(got.scale!.residual).toBeLessThan(0.05);
  await expect(page.getByText(/Scale: factor [\d.]+, residual [\d.]+/)).toBeVisible();
  await expect(page.getByText("Unscaled — set a scale")).toHaveCount(0);
});

test("3. measure the room between two walls", async () => {
  test.setTimeout(240_000);
  const room = await roomFromMesh();
  const { floorZ } = room;
  console.log(`mesh room (metres): ${room.lengthA.toFixed(2)} x ${room.lengthB.toFixed(2)}, floor z ${floorZ.toFixed(3)}; truth ${truth.room[0]} x ${truth.room[1]}`);
  await lookDown(room);
  await tool("Measure");
  // The points sit 3 cm in front of the two opposite walls, on the floor (a top-down view cannot see a wall face);
  // the true width of the room is 5 m between the walls.
  const wall = 0.03;
  await clickAt(room.at(room.rangeA[0] + wall, room.centre[1], floorZ));
  await clickAt(room.at(room.rangeA[1] - wall, room.centre[1], floorZ));
  const result = page.locator(".measure-result");
  await expect(result).toContainText(/Distance: [\d.]+ m/);
  const shown = Number(/Distance: ([\d.]+) m/.exec(await result.innerText())![1]);
  const trueWidth = Math.max(truth.room[0], truth.room[1]);
  const error = Math.abs(shown - trueWidth) / trueWidth;
  console.log(`measured ${shown.toFixed(3)} m between the long walls (clicked ${(room.lengthA - 2 * wall).toFixed(3)} m apart in the mesh), true room width ${trueWidth} m, error ${(error * 100).toFixed(2)}%`);
  expect(error).toBeLessThan(0.03);
  await expect(page.locator(".site-label")).toContainText(/[\d.]+ m/);
  await page.keyboard.press("Escape");
  await expect(page.getByRole("button", { name: "Measure", exact: true })).toHaveAttribute("aria-pressed", "false");
});

test("4. place the robot, add a table, put the drop marker on it, apply to the design", async () => {
  test.setTimeout(300_000);
  const room = await roomFromMesh();
  const { floorZ } = room;
  await lookDown(room);
  const [ca, cb] = room.centre;
  const robotAt = room.at(ca - 1.6, cb - 0.9, floorZ);
  const tableAt = room.at(ca - 1.6, cb + 0.2, floorZ);

  await tool("Place robot");
  await clickAt(robotAt);
  await expect.poll(async () => { const p = (await scans())[0].placement; return Math.hypot(p.x, p.y); }, { timeout: 10_000 }).toBeGreaterThan(0.01);
  await page.getByRole("slider", { name: "Robot turn (degrees)" }).fill("30");
  await expect.poll(async () => (await scans())[0].placement.yaw_deg).toBe(30);

  await tool("Add object");
  await expect(page.getByRole("combobox", { name: "Kind" })).toHaveValue("table");
  await clickAt(tableAt);
  const objects = page.getByRole("list", { name: "Edits" });
  await expect(objects.getByRole("listitem")).toHaveCount(1);
  await expect(objects).toContainText("site_table_1");

  await tool("Task point");
  const select = page.getByRole("combobox", { name: "Task point" });
  await expect(select.locator("option")).toHaveText(["A", "B"]);
  await select.selectOption("b");
  // The table top is at the clicked floor height plus 0.75 m (the app treats a floor within 2 cm of zero as zero). The ray to it
  // lands on the floor about 0.2 m further out in the mesh, so the pre-click check is loose here.
  const topZ = (Math.abs(tableAt[2]) < 0.02 ? 0 : tableAt[2]) + truth.table.top_height;
  await clickAt([tableAt[0], tableAt[1], topZ], 0.6);
  await expect(objects).toContainText("Task point B moved");

  // B has left its old table; that table (table_b) now stands within 0.3 m of the base and would be refused, so take it out.
  await page.getByText(/^Boxes in the design/).click();
  await page.getByRole("button", { name: "Remove table_b from the design" }).click();
  await expect(objects).toContainText("table_b removed from the design");

  const before = (await request.get(`${API}/projects/${pid}`).then((r) => r.json())).versions;
  expect(before).toEqual([1]);
  await page.getByRole("button", { name: "Apply to design" }).click();
  const tab = page.getByRole("tab", { name: /^v2/ });
  await expect(tab).toBeVisible({ timeout: 120_000 });
  await expect(tab).toContainText("based on v1");
  await expect(page.getByText(/Edited in the site scan/).first()).toBeVisible();

  const v2 = await (await request.get(`${API}/projects/${pid}/versions/2`)).json();
  expect(v2.parent_version).toBe(1);
  const b = v2.spec.markers.find((m: { id: string }) => m.id === "b").position as P3;
  const table = v2.scene.boxes.find((x: { id: string }) => x.id === "site_table_1");
  const tableTop = table.center[2] + table.size[2] / 2;
  console.log(`v2 parent ${v2.parent_version}; marker B at (${b.map((n) => n.toFixed(3)).join(", ")}); added table top ${tableTop.toFixed(3)} m `
    + `(floor of the scan at z ${floorZ.toFixed(3)}); change: ${v2.change_summary}`);
  expect(Math.abs(b[2] - tableTop)).toBeLessThanOrEqual(0.03);
  expect(Math.abs(b[2] - truth.table.top_height)).toBeLessThanOrEqual(0.03);
  expect(Math.abs(b[0] - table.center[0])).toBeLessThanOrEqual(table.size[0] / 2);
  expect(Math.abs(b[1] - table.center[1])).toBeLessThanOrEqual(table.size[1] / 2);
  expect(v2.change_summary).toContain("Edited in the site scan");
  // The old version is untouched.
  const v1 = await (await request.get(`${API}/projects/${pid}/versions/1`)).json();
  expect(v1.spec.markers.find((m: { id: string }) => m.id === "b").position[1]).toBeCloseTo(-0.3, 6);
  const ids = (v: { scene: { boxes: { id: string }[] } }) => v.scene.boxes.map((x) => x.id).sort();
  expect(ids(v1)).toEqual(["table_a", "table_b"]);
  expect(ids(v2)).toEqual(["site_table_1", "table_a"]);
});

test("5. phone link: Record a site video from a phone", async ({ }, info) => {
  test.setTimeout(240_000);
  const before = (await scans()).length;
  await page.getByRole("button", { name: "Record with phone" }).click();
  const region = page.getByRole("region", { name: "Link a phone" });
  await expect(region.getByRole("img", { name: /QR code/ })).toBeVisible();
  const shown = (await region.locator("code").innerText()).trim();
  expect(shown).toMatch(new RegExp(`^http://[^/]+:${Number(process.env.E2E_PORT || "8810") + 1}/p/[A-Za-z0-9_-]{43}$`));
  let phoneUrl = shown;
  let used = "LAN address";
  try {
    const probe = await request.get(shown, { timeout: 4000, failOnStatusCode: false });
    if (probe.status() !== 200) throw new Error(`status ${probe.status()}`);
  } catch {
    phoneUrl = shown.replace(/^http:\/\/[^/:]+/, "http://127.0.0.1");
    used = "127.0.0.1 (LAN address not reachable from this machine)";
  }
  console.log(`phone link opened via ${used}`);
  info.annotations.push({ type: "phone-host", description: used });

  const phone = await browser_.newContext({ ...devices["Pixel 7"] });
  try {
    const ppage = await phone.newPage();
    await ppage.goto(phoneUrl);
    await expect(ppage.getByRole("button", { name: "Record a site video" })).toBeVisible();
    await ppage.locator("#video").setInputFiles(VIDEO);
    await expect(ppage.locator("#msg.ok")).toContainText("Sent", { timeout: 120_000 });

    await expect.poll(async () => (await scans()).length, { timeout: 15_000 }).toBe(before + 1);
    await expect(sheetList().getByRole("listitem")).toHaveCount(before + 1, { timeout: 15_000 });
    await expect(page.getByText(/Sent from the phone: 1/)).toBeVisible();
    const newest = (await scans())[0];
    expect(newest.status).toBe("uploaded");
    console.log(`phone video became scan ${newest.id} (${newest.status}); scans ${before} -> ${(await scans()).length}`);
    await page.getByRole("button", { name: "Stop linking" }).click();
    await expect(region).toHaveCount(0);
  } finally {
    await phone.close();
  }
});

test("6. the three views render without console errors", async () => {
  const errorsBefore = consoleErrors.length;
  const group = page.getByRole("radiogroup", { name: "View" });
  await expect(group.getByRole("radio")).toHaveText(["Modelled scene", "My photo", "My site"]);
  // The switch sits beside the title of the player panel, on the same line.
  const title = (await page.getByRole("heading", { name: "Simulated task" }).boundingBox())!;
  const seg = (await group.boundingBox())!;
  expect(Math.abs(seg.y + seg.height / 2 - (title.y + title.height / 2))).toBeLessThan(30);

  for (const name of ["Modelled scene", "My photo", "My site", "Modelled scene", "My site"]) {
    await group.getByRole("radio", { name }).click();
    await expect(group.getByRole("radio", { name })).toHaveAttribute("aria-checked", "true");
    await expect(page.locator(".scene canvas")).toBeVisible();
    if (name === "My photo") await expect(page.getByRole("heading", { name: "View in my space" })).toBeVisible();
    if (name === "My site") await expect(page.getByRole("region", { name: "My site" })).toBeVisible();
    if (name === "Modelled scene") await expect(page.getByRole("region", { name: "My site" })).toHaveCount(0);
    await page.waitForTimeout(800);
  }
  await expect(page.getByRole("toolbar", { name: "Site tools" })).toBeVisible();
  expect(consoleErrors.slice(errorsBefore), consoleErrors.slice(errorsBefore).join("\n")).toEqual([]);
});

// ---- navigation and "Through the camera" (viewport 1440 x 1000, as the screenshots) -----------------------------------------------

const modeButton = (name: "3D view" | "Through the camera") => page.getByRole("group", { name: "Site view" }).getByRole("button", { name });
type RobotBox = { height: number; width: number; canvasHeight: number; canvasWidth: number; fraction: number; cx: number; cy: number };

test("7. Focus robot: the robot fills at least a quarter of the canvas height", async () => {
  test.setTimeout(180_000);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await modeButton("3D view").click();
  await expect.poll(() => hook<string>("mode")).toBe("orbit");
  const room = await roomFromMesh();
  // Far away and high up: the robot is a speck in the room.
  await lookDown(room, 9);
  const far = await hook<RobotBox | null>("robotScreen");
  expect(far, "the robot is drawn").toBeTruthy();
  await page.getByRole("button", { name: "Focus robot", exact: true }).click();
  await expect.poll(() => hook<boolean>("moving"), { timeout: 5000 }).toBe(true).catch(() => undefined);
  await expect.poll(() => hook<boolean>("moving"), { timeout: 10_000 }).toBe(false);
  await page.waitForTimeout(300);
  const near = (await hook<RobotBox | null>("robotScreen"))!;
  console.log(`robot screen height: ${(far!.fraction * 100).toFixed(1)}% of the canvas before Focus robot, ${(near.fraction * 100).toFixed(1)}% after `
    + `(${near.height.toFixed(0)} of ${near.canvasHeight.toFixed(0)} px)`);
  expect(near.fraction).toBeGreaterThanOrEqual(0.25);
  expect(near.cx).toBeGreaterThan(0);
  expect(near.cx).toBeLessThan(near.canvasWidth);
  expect(near.cy).toBeGreaterThan(0);
  expect(near.cy).toBeLessThan(near.canvasHeight);

  // Reset view frames the scan and then focuses the robot again.
  await lookDown(room, 9);
  await page.getByRole("button", { name: "Reset view", exact: true }).click();
  await expect.poll(() => hook<boolean>("moving"), { timeout: 10_000 }).toBe(false);
  await page.waitForTimeout(300);
  const reset = (await hook<RobotBox | null>("robotScreen"))!;
  console.log(`after Reset view the robot is ${(reset.fraction * 100).toFixed(1)}% of the canvas height`);
  expect(reset.fraction).toBeGreaterThanOrEqual(0.25);

  await page.locator(".scene").evaluate((el) => el.scrollIntoView({ block: "center" }));
  mkdirSync(SHOTS, { recursive: true });
  await page.screenshot({ path: path.join(SHOTS, "5-focus-robot.png") });
});

test("8. Through the camera: the frame, the page camera, the walk", async () => {
  test.setTimeout(240_000);
  const errorsBefore = consoleErrors.length;
  const scan = (await scans()).find((s) => s.status === "reconstructed")!;
  const cam = scan.camera!;
  const registered = scan.frames.filter((f) => f.world_from_camera);
  console.log(`scan camera undistorted: ${cam.undistorted === true}; ${registered.length} registered frames`);

  await modeButton("Through the camera").click();
  await expect.poll(() => hook<string>("mode").catch(() => "none"), { timeout: 30_000 }).toBe("camera");
  await expect(page.getByRole("slider", { name: "Walk position" })).toBeVisible();
  if (cam.undistorted === true) await expect(page.getByText("Reconstruct again for exact alignment")).toHaveCount(0);
  else await expect(page.getByText("Reconstruct again for exact alignment")).toBeVisible();

  // The canvas keeps the frame's aspect ratio.
  const size = await hook<{ width: number; height: number; frame: number }>("canvas");
  const aspect = size.width / size.height, want = cam.width / cam.height;
  console.log(`canvas ${size.width.toFixed(1)} x ${size.height.toFixed(1)} (aspect ${aspect.toFixed(4)}), frame ${cam.width} x ${cam.height} (aspect ${want.toFixed(4)})`);
  expect(Math.abs(aspect - want) / want).toBeLessThan(0.01);
  const background = await page.locator(".scene").evaluate((el) => getComputedStyle(el).backgroundImage);
  expect(background).toMatch(/\/frames\/\d+/);

  // A sheet corner: the floor point seen in the best frame, projected into other frames by the page camera, lands on its true pixel.
  const resize = cam.width / truth.width;
  const views = registered.map((f, pos) => ({ f, pos, view: truth.sheet_views[Math.min(truth.sheet_views.length - 1, Math.round(f.t_s * truth.fps))] })).filter((c) => c.view.visible);
  expect(views.length, "frames that see the whole sheet").toBeGreaterThanOrEqual(3);
  const showFrame = async (pos: number) => {
    await page.getByRole("slider", { name: "Walk position" }).fill(String(pos));
    await expect.poll(async () => (await hook<{ frame: number }>("canvas").catch(() => ({ frame: -1 }))).frame).toBe(registered[pos].index);
    await page.waitForTimeout(150);
  };
  const a = views.reduce((x, y) => (y.view.area_px > x.view.area_px ? y : x));
  await showFrame(a.pos);
  const corners = a.view.pixels.map(([u, v]) => [u * resize, v * resize]);
  const floor = [];
  for (const [u, v] of corners) { const p = await hook<P3 | null>("floorPoint", u, v); expect(p).toBeTruthy(); floor.push(p!); }
  // Frames of the walk well away from the first one (at least 8 steps), spread over the walk.
  const others = views.filter((c) => Math.abs(c.pos - a.pos) >= 8);
  const picks = others.length <= 4 ? others : [0, 1, 2, 3].map((i) => others[Math.floor((i * (others.length - 1)) / 3)]);
  expect(picks.length).toBeGreaterThan(0);
  let worst = 0, sum = 0, n = 0;
  for (const c of picks) {
    await showFrame(c.pos);
    for (let i = 0; i < 4; i++) {
      const px = await hook<{ u: number; v: number }>("pixel", floor[i]);
      const err = Math.hypot(px.u - c.view.pixels[i][0] * resize, px.v - c.view.pixels[i][1] * resize);
      worst = Math.max(worst, err); sum += err; n++;
    }
  }
  console.log(`sheet corners from frame ${registered[a.pos].index} projected into ${picks.length} other frames (${picks.map((c) => registered[c.pos].index).join(", ")}): `
    + `mean error ${(sum / n).toFixed(2)} px, worst ${worst.toFixed(2)} px (frame is ${cam.width} px wide)`);
  expect(worst).toBeLessThan(4);

  // Best view of the robot: a registered frame, with the robot inside the picture.
  await page.getByRole("button", { name: "Best view of the robot", exact: true }).click();
  await page.waitForTimeout(400);
  const robot = await hook<RobotBox | null>("robotScreen");
  console.log(`best view: frame ${(await hook<{ frame: number }>("canvas")).frame}, robot box ${robot ? `${robot.width.toFixed(0)} x ${robot.height.toFixed(0)} px at (${robot.cx.toFixed(0)}, ${robot.cy.toFixed(0)}) in a ${robot.canvasWidth.toFixed(0)} x ${robot.canvasHeight.toFixed(0)} canvas` : "none"}`);
  expect(robot).toBeTruthy();
  expect(robot!.cx).toBeGreaterThan(0);
  expect(robot!.cx).toBeLessThan(robot!.canvasWidth);
  expect(robot!.cy).toBeGreaterThan(0);
  expect(robot!.cy).toBeLessThan(robot!.canvasHeight);

  // Play walk steps the frames (about 6 per second) while the robot's task plays on its own.
  const before = (await hook<{ frame: number }>("canvas")).frame;
  await page.getByRole("button", { name: "Play walk", exact: true }).click();
  await expect(page.getByRole("button", { name: "Pause walk", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Play", exact: true }).click();
  await expect(page.getByRole("button", { name: "Pause", exact: true })).toBeVisible();
  const seen = new Set<number>();
  const t0 = Date.now();
  while (Date.now() - t0 < 1500) { seen.add((await hook<{ frame: number }>("canvas")).frame); await page.waitForTimeout(60); }
  console.log(`walk played ${seen.size} different frames in 1.5 s (started at frame ${before})`);
  expect(seen.size).toBeGreaterThanOrEqual(3);
  expect([...seen].some((i) => i !== before)).toBe(true);
  await page.getByRole("button", { name: "Pause", exact: true }).click();
  await page.getByRole("button", { name: "Pause walk", exact: true }).click();
  await expect(page.getByRole("button", { name: "Play walk", exact: true })).toBeVisible();

  await page.getByRole("button", { name: "Best view of the robot", exact: true }).click();
  await page.waitForTimeout(500);
  await page.locator(".scene").evaluate((el) => el.scrollIntoView({ block: "center" }));
  mkdirSync(SHOTS, { recursive: true });
  await page.screenshot({ path: path.join(SHOTS, "6-through-the-camera.png") });

  await modeButton("3D view").click();
  await expect.poll(() => hook<string>("mode").catch(() => "none"), { timeout: 30_000 }).toBe("orbit");
  await expect(page.locator(".scene canvas")).toBeVisible();
  await page.waitForTimeout(500);
  expect(consoleErrors.slice(errorsBefore), consoleErrors.slice(errorsBefore).join("\n")).toEqual([]);
});
