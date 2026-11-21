import { test, expect, devices, type APIRequestContext, type Browser, type BrowserContext, type Locator, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";

const PHOTOS = path.resolve(__dirname, "..", ".photos");
const API = "/api/v1";

type Manifest = {
  file: string; width: number; height: number; focal_px: number; camera_height_m: number;
  size_m: number[]; corners_px: number[][]; exif_orientation: number;
};
const manifest: Manifest[] = JSON.parse(readFileSync(path.join(PHOTOS, "manifest.json"), "utf8"));
const truth = (file: string) => manifest.find((m) => m.file === file)!;
const photo = (file: string) => path.join(PHOTOS, file);

type Cal = { intrinsics: { fx: number; fy: number; width: number; height: number }; focal_source: string; scale_source: string;
  camera_height_m: number; residual_px: number | null };
type Scene = { id: string; width: number; height: number; image: string; reference: unknown; calibration: Cal | null; estimate: Cal | null;
  placement: { x: number; y: number; yaw_deg: number } };

test.describe.configure({ mode: "serial" });

let request: APIRequestContext;
let browser_: Browser;
let context: BrowserContext;
let page: Page;
let pid = "";
const consoleErrors: string[] = [];

async function scenes(): Promise<Scene[]> {
  const r = await request.get(`${API}/projects/${pid}/ar/scenes`);
  expect(r.ok()).toBeTruthy();
  return r.json();
}
async function newest(): Promise<Scene> { return (await scenes())[0]; }

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

/** Upload through the real "Add photo" input and wait for the new scene to be selected. */
async function uploadPhoto(file: string | { name: string; mimeType: string; buffer: Buffer }) {
  await page.locator('.ar-panel input[type="file"]').setInputFiles(file);
}
async function uploadAndWaitForScene(file: string): Promise<Scene> {
  const before = (await scenes()).length;
  await uploadPhoto(photo(file));
  await expect.poll(async () => (await scenes()).length).toBe(before + 1);
  const scene = await newest();
  await expect(page.getByRole("list", { name: "Photos" }).getByRole("listitem")).toHaveCount(before + 1);
  await expect(page.locator("svg.ar-calib, .ar-panel").first()).toBeVisible();
  return scene;
}

async function openCalibrator() {
  const open = page.getByRole("button", { name: "Calibrate with a rectangle" });
  if (await open.isVisible()) await open.click();
  await expect(page.getByRole("group", { name: "Photo with four corner handles" })).toBeVisible();
}

/** Screen position of an image pixel: the calibrator svg's own screen matrix (its bounding box and viewBox, including letterboxing). */
async function imageToScreen(px: number[]): Promise<{ x: number; y: number }> {
  const svg = page.locator("svg.ar-calib");
  await svg.scrollIntoViewIfNeeded();
  return svg.evaluate((el, [u, v]) => {
    const s = el as unknown as SVGSVGElement;
    const m = s.getScreenCTM()!;
    return { x: m.a * u + m.e, y: m.d * v + m.f };
  }, px as [number, number]);
}
async function center(loc: Locator) {
  const b = (await loc.boundingBox())!;
  return { x: b.x + b.width / 2, y: b.y + b.height / 2 };
}
function handle(i: number): Locator { return page.getByRole("button", { name: new RegExp(`^Corner ${i + 1} at`) }); }

async function dragHandle(i: number, target: number[]) {
  const from = await center(handle(i).locator("circle"));
  const to = await imageToScreen(target);
  await page.mouse.move(from.x, from.y);
  await page.mouse.down();
  await page.mouse.move((from.x + to.x) / 2, (from.y + to.y) / 2, { steps: 4 });
  await page.mouse.move(to.x, to.y, { steps: 6 });
  await page.mouse.up();
}
async function dragAllHandles(corners: number[][]) {
  for (let i = 0; i < 4; i++) await dragHandle(i, corners[i]);
  // Handles carry their position in the label; check they landed within a pixel or two of the target.
  for (let i = 0; i < 4; i++) {
    const label = (await handle(i).getAttribute("aria-label"))!;
    const m = /at (-?\d+), (-?\d+)/.exec(label)!;
    expect(Math.abs(Number(m[1]) - corners[i][0]), label).toBeLessThan(3);
    expect(Math.abs(Number(m[2]) - corners[i][1]), label).toBeLessThan(3);
  }
}

async function calibrateWith(file: string, preset: "a4" | "letter") {
  const t = truth(file);
  await openCalibrator();
  await dragAllHandles(t.corners_px);
  await page.getByLabel("Rectangle size").selectOption(preset);
  await page.getByRole("button", { name: "Calibrate", exact: true }).click();
  await expect(page.getByRole("status").filter({ hasText: /Calibrated\. Residual [\d.]+ px/ })).toBeVisible();
}

function expectCalibration(scene: Scene, t: Manifest) {
  const cal = scene.calibration!;
  expect(cal, "calibration stored").toBeTruthy();
  const fxErr = Math.abs(cal.intrinsics.fx - t.focal_px) / t.focal_px;
  const hErr = Math.abs(cal.camera_height_m - t.camera_height_m) / t.camera_height_m;
  console.log(`[${t.file}] fx=${cal.intrinsics.fx.toFixed(1)} (true ${t.focal_px}, ${(fxErr * 100).toFixed(2)}%), `
    + `height=${cal.camera_height_m.toFixed(3)} (true ${t.camera_height_m}, ${(hErr * 100).toFixed(2)}%), `
    + `residual=${cal.residual_px?.toFixed(3)} px, focal_source=${cal.focal_source}`);
  expect(cal.scale_source).toBe("reference");
  return { cal, fxErr, hErr };
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

  context = await browser.newContext({ baseURL: `http://127.0.0.1:${process.env.E2E_PORT || "8810"}`, acceptDownloads: true, viewport: { width: 1800, height: 1200 } });
  page = await context.newPage();
  page.on("console", (m) => { if (m.type() === "error") consoleErrors.push(m.text()); });
  page.on("pageerror", (e) => consoleErrors.push(`pageerror: ${e.message}`));
  await page.goto("/");
  await page.locator("#project-pick").selectOption(pid);
  const design = page.getByRole("button", { name: /^Design/ }).first();
  await design.click();
  await page.getByRole("radio", { name: "My photo" }).click();
  await expect(page.getByRole("heading", { name: "View in my space" })).toBeVisible();
});

test.afterAll(async () => {
  await context?.close();
  await request?.dispose();
});

test("1. upload and calibrate an A4 sheet (landscape PNG)", async () => {
  const file = "desk_a4_landscape.png";
  const t = truth(file);
  const scene = await uploadAndWaitForScene(file);
  expect([scene.width, scene.height]).toEqual([t.width, t.height]);
  expect(scene.calibration).toBeNull();

  await openCalibrator();
  // Keyboard nudge on handle 1: moves by width/400 px, and back again.
  const label0 = (await handle(0).getAttribute("aria-label"))!;
  await handle(0).focus();
  await page.keyboard.press("ArrowRight");
  const label1 = (await handle(0).getAttribute("aria-label"))!;
  expect(label1).not.toBe(label0);
  const x0 = Number(/at (-?\d+),/.exec(label0)![1]);
  const x1 = Number(/at (-?\d+),/.exec(label1)![1]);
  expect(x1 - x0).toBeGreaterThanOrEqual(3);
  await page.keyboard.press("ArrowLeft");
  expect(await handle(0).getAttribute("aria-label")).toBe(label0);

  await calibrateWith(file, "a4");
  const { cal, fxErr, hErr } = expectCalibration(await newest(), t);
  expect(cal.focal_source).toBe("reference");
  expect(fxErr).toBeLessThan(0.02);
  expect(hErr).toBeLessThan(0.02);
  expect(cal.residual_px!).toBeLessThan(3);
  // One residual line at a time: the calibrator's own while it is open, the panel's once it is closed.
  await expect(page.getByText(/Calibration residual [\d.]+ px/)).toHaveCount(0);
  await page.getByRole("button", { name: "Close calibrator" }).click();
  await expect(page.getByText(/Calibration residual [\d.]+ px/)).toBeVisible();
  await expect(page.getByText(/Calibrated\. Residual/)).toHaveCount(0);
});

test("2. Letter sheet square to the camera (JPEG)", async () => {
  const file = "floor_letter_square.jpg";
  const t = truth(file);
  await uploadAndWaitForScene(file);
  await calibrateWith(file, "letter");
  const { cal, hErr, fxErr } = expectCalibration(await newest(), t);
  expect(cal.focal_source).toBe("reference");
  expect(hErr).toBeLessThan(0.02);
  expect(fxErr).toBeLessThan(0.02);
  expect(cal.residual_px!).toBeLessThan(3);
});

test("3. phone photo with EXIF orientation 6", async () => {
  const file = "phone_portrait_exif6.jpg";
  const t = truth(file);
  expect(t.exif_orientation).toBe(6);
  const scene = await uploadAndWaitForScene(file);
  expect([scene.width, scene.height]).toEqual([1500, 2000]);
  await calibrateWith(file, "a4");
  const { cal, hErr, fxErr } = expectCalibration(await newest(), t);
  expect(hErr).toBeLessThan(0.02);
  expect(fxErr).toBeLessThan(0.02);
  expect(cal.residual_px!).toBeLessThan(3);
});

test("4. the AR view: aspect, no console errors, drag and turn", async () => {
  const scene = await newest(); // the portrait phone photo, still selected
  const box = page.locator(".scene");
  await expect(box).toBeVisible();
  const b = (await box.boundingBox())!;
  const ratio = b.width / b.height;
  expect(Math.abs(ratio / (scene.width / scene.height) - 1)).toBeLessThan(0.01);
  expect(consoleErrors, consoleErrors.join("\n")).toEqual([]);

  const canvas = page.locator(".scene canvas");
  await canvas.scrollIntoViewIfNeeded();
  const c = (await canvas.boundingBox())!;
  const before = scene.placement;
  await page.mouse.move(c.x + c.width * 0.5, c.y + c.height * 0.72);
  await page.mouse.down();
  await page.mouse.move(c.x + c.width * 0.6, c.y + c.height * 0.8, { steps: 8 });
  await page.mouse.move(c.x + c.width * 0.62, c.y + c.height * 0.85, { steps: 4 });
  await page.mouse.up();
  await expect.poll(async () => {
    const p = (await newest()).placement;
    return Math.hypot(p.x - before.x, p.y - before.y);
  }).toBeGreaterThan(0.01);
  const moved = (await newest()).placement;
  console.log(`drag moved placement from (${before.x}, ${before.y}) to (${moved.x.toFixed(3)}, ${moved.y.toFixed(3)})`);

  await page.getByRole("spinbutton", { name: "Turn", exact: true }).fill("40");
  await expect.poll(async () => (await newest()).placement.yaw_deg).toBe(40);
  await page.getByRole("slider", { name: "Turn (degrees)" }).fill("-25");
  await expect.poll(async () => (await newest()).placement.yaw_deg).toBe(-25);
  expect(consoleErrors, consoleErrors.join("\n")).toEqual([]);
});

test("5. snapshot has the photo's size", async () => {
  const scene = await newest();
  const [download] = await Promise.all([
    page.waitForEvent("download"),
    page.getByRole("button", { name: "Save snapshot" }).click(),
  ]);
  expect(download.suggestedFilename()).toMatch(/\.png$/);
  const file = await download.path();
  const bytes = readFileSync(file);
  expect(bytes.subarray(1, 4).toString("latin1")).toBe("PNG");
  const w = bytes.readUInt32BE(16), h = bytes.readUInt32BE(20);
  console.log(`snapshot ${w}x${h}, photo ${scene.width}x${scene.height}`);
  expect([w, h]).toEqual([scene.width, scene.height]);
});

test("6. automatic estimate (real MoGe)", async () => {
  const file = "desk_a4_landscape.png";
  const t = truth(file);
  const scene = await uploadAndWaitForScene(file);
  expect(scene.estimate).toBeNull();
  await page.getByRole("button", { name: "Estimate automatically" }).click();
  await expect.poll(async () => (await newest()).estimate, { timeout: 90_000, intervals: [1000] }).toBeTruthy();
  const got = await newest();
  const est = got.estimate!;
  expect(est.scale_source).toBe("estimate");
  expect(est.camera_height_m).toBeGreaterThan(0);
  expect(got.calibration).toBeNull();
  console.log(`[estimate ${file}] fx=${est.intrinsics.fx.toFixed(1)} (true ${t.focal_px}), height=${est.camera_height_m.toFixed(3)} m `
    + `(true ${t.camera_height_m}), focal_source=${est.focal_source}`);
  await expect(page.getByText("Approximate scale", { exact: true })).toBeVisible();
});

test("7. phone link", async ({ }, info) => {
  const before = (await scenes()).length;
  await page.getByRole("button", { name: "Link a phone" }).click();
  const region = page.getByRole("region", { name: "Link a phone" });
  await expect(region.getByRole("img", { name: /QR code/ })).toBeVisible();
  const shown = (await region.locator("code").innerText()).trim();
  expect(shown).toMatch(new RegExp(`^http://[^/]+:${Number(process.env.E2E_PORT || "8810") + 1}/p/[A-Za-z0-9_-]{43}$`));

  // Prefer the LAN address; fall back to loopback if this machine cannot reach itself that way.
  let phoneUrl = shown;
  let used = "LAN address";
  try {
    const probe = await request.get(shown, { timeout: 4000, failOnStatusCode: false });
    if (probe.status() !== 200) throw new Error(`status ${probe.status()}`);
  } catch {
    phoneUrl = shown.replace(/^http:\/\/[^/:]+/, "http://127.0.0.1");
    used = "127.0.0.1 (LAN address not reachable from this machine)";
  }
  console.log(`phone link shown as ${shown.replace(/\/p\/.*/, "/p/<code>")}; opened via ${used}`);
  info.annotations.push({ type: "phone-host", description: used });

  const phone = await browser_.newContext({ ...devices["Pixel 7"] });
  try {
    const ppage = await phone.newPage();
    await ppage.goto(phoneUrl);
    await expect(ppage.getByRole("heading", { name: "Send a photo to your computer" })).toBeVisible();
    await expect(ppage.getByText("Take a photo")).toBeVisible();
    await expect(ppage.getByText("Choose from gallery")).toBeVisible();
    await ppage.locator("#file").setInputFiles(photo("phone_portrait_exif6.jpg"));
    await expect(ppage.locator("#msg")).toContainText("Sent");
    await expect(ppage.locator("#msg.ok")).toBeVisible();

    await expect(page.getByText(/Photos received: 1/)).toBeVisible({ timeout: 5000 });
    await expect(page.getByRole("list", { name: "Photos" }).getByRole("listitem")).toHaveCount(before + 1, { timeout: 5000 });
    expect((await scenes()).length).toBe(before + 1);
    const sc = await newest();
    expect([sc.width, sc.height]).toEqual([1500, 2000]);

    await page.getByRole("button", { name: "Stop linking" }).click();
    await expect(region).toHaveCount(0);
    let outcome: string;
    try {
      const r = await request.get(phoneUrl, { failOnStatusCode: false, timeout: 5000 });
      outcome = String(r.status());
      expect(r.status()).toBe(404);
    } catch (e) {
      // The listener is closed by a 30 s sweep once no pairing is active; a closed port is the same "gone" answer.
      if (!/ECONNREFUSED|ECONNRESET|socket hang up/.test(String(e))) throw e;
      outcome = "connection refused (listener already stopped)";
    }
    console.log(`after Stop linking the phone URL answers: ${outcome}`);
  } finally {
    await phone.close();
  }
});

test("8. errors: bad reference and a non-image file", async () => {
  const file = "desk_a4_landscape.png";
  await uploadAndWaitForScene(file);
  await openCalibrator();
  const unchanged = await newest();
  const before = JSON.stringify(unchanged);
  expect(unchanged.calibration).toBeNull();

  // Three handles stacked on one line.
  const y = unchanged.height * 0.8;
  await dragHandle(0, [unchanged.width * 0.30, y]);
  await dragHandle(1, [unchanged.width * 0.45, y]);
  await dragHandle(2, [unchanged.width * 0.60, y]);
  await page.getByRole("button", { name: "Calibrate", exact: true }).click();
  const bad = page.getByRole("alert").filter({ hasText: /reference|convex|corner/i });
  await expect(bad.first()).toBeVisible();
  console.log(`bad-reference message: ${await bad.first().innerText()}`);
  expect(JSON.stringify(await newest())).toBe(before);

  // A text file renamed .jpg.
  const count = (await scenes()).length;
  await uploadPhoto({ name: "notes.jpg", mimeType: "image/jpeg", buffer: Buffer.from("this is not a picture\n") });
  const err = page.getByRole("alert").filter({ hasText: /cannot be read as a photo/ });
  await expect(err.first()).toBeVisible();
  console.log(`non-image message: ${await err.first().innerText()}`);
  expect((await scenes()).length).toBe(count);
  await expect(page.getByRole("list", { name: "Photos" }).getByRole("listitem")).toHaveCount(count);
});

test("9. Show scene boxes changes what is drawn", async () => {
  const errorsBefore = consoleErrors.length; // earlier tests provoke 404/422 console lines on purpose
  const file = "desk_a4_landscape.png";
  await uploadAndWaitForScene(file);
  await calibrateWith(file, "a4");
  await page.getByRole("button", { name: "Close calibrator" }).click();
  const boxes = page.getByRole("checkbox", { name: "Show scene boxes" });
  const canvas = page.locator(".scene canvas");
  await canvas.scrollIntoViewIfNeeded();
  const clip = (await canvas.boundingBox())!;
  const settle = () => page.waitForTimeout(700);
  const shot = async () => { await settle(); return page.screenshot({ clip }); };

  await boxes.uncheck();
  await expect(boxes).not.toBeChecked();
  const off1 = await shot();
  await boxes.check();
  await expect(boxes).toBeChecked();
  const on = await shot();
  await boxes.uncheck();
  await expect(boxes).not.toBeChecked();
  const off2 = await shot();
  await boxes.check();
  await expect(boxes).toBeChecked();
  const on2 = await shot();

  console.log(`canvas screenshots: off=${off1.length} B, on=${on.length} B`);
  expect(on.equals(off1), "boxes on must differ from boxes off").toBe(false);
  expect(on2.equals(off2), "boxes on must differ from boxes off (second round)").toBe(false);
  await boxes.uncheck();
  expect(consoleErrors.slice(errorsBefore), consoleErrors.slice(errorsBefore).join("\n")).toEqual([]);
});

test("10. delete a scene through the UI", async () => {
  const file = "floor_letter_square.jpg";
  const made = await uploadAndWaitForScene(file);
  const before = (await scenes()).length;
  const photoUrl = `${API}/projects/${pid}/ar/scenes/${made.id}/photo`;
  expect((await request.get(photoUrl)).status()).toBe(200);

  page.once("dialog", (d) => { void d.accept(); });
  await page.locator(".ar-panel").getByRole("button", { name: "Delete", exact: true }).click();

  await expect.poll(async () => (await scenes()).map((s) => s.id)).not.toContain(made.id);
  expect((await scenes()).length).toBe(before - 1);
  expect((await request.get(photoUrl)).status()).toBe(404);
  expect((await request.get(`${API}/projects/${pid}/ar/scenes/${made.id}`)).status()).toBe(404);
  await expect(page.getByRole("list", { name: "Photos" }).getByRole("listitem")).toHaveCount(before - 1);
});

test("11. the 10-scene limit", async () => {
  const start = (await scenes()).length;
  const bytes = readFileSync(photo("floor_letter_square.jpg"));
  const added: string[] = [];
  for (let n = start; n < 10; n++) {
    const r = await request.post(`${API}/projects/${pid}/ar/scenes?filename=fill-${n}.jpg`, { data: bytes, headers: { "Content-Type": "application/octet-stream" } });
    expect(r.status(), await r.text()).toBe(201);
    added.push((await r.json()).id);
  }
  expect((await scenes()).length).toBe(10);

  await page.reload();
  await page.locator("#project-pick").selectOption(pid);
  await page.getByRole("button", { name: /^Design/ }).first().click();
  await page.getByRole("radio", { name: "My photo" }).click();
  await expect(page.getByRole("list", { name: "Photos" }).getByRole("listitem")).toHaveCount(10);

  try {
    await uploadPhoto(photo("desk_a4_landscape.png"));
    const msg = page.getByRole("alert").filter({ hasText: "At most 10 AR scenes per project" });
    await expect(msg.first()).toBeVisible();
    console.log(`limit message: ${await msg.first().innerText()}`);
    expect((await scenes()).length).toBe(10);
    await expect(page.getByRole("list", { name: "Photos" }).getByRole("listitem")).toHaveCount(10);
  } finally {
    for (const id of added) await request.delete(`${API}/projects/${pid}/ar/scenes/${id}`);
  }
  expect((await scenes()).length).toBe(start);
});
