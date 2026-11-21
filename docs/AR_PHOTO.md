# Photo AR ("View in my space") — contract

The user uploads a photo of the real site. Code works out the camera and the floor, and the Design stage then renders the robot, task objects and playback over the photo at true scale. This is a visual check only. Pass/fail numbers still come only from the kinematic task check; nothing here is a collision check against the real room.

## Frames and units
- Metres, radians in maths, degrees only in `ArPlacement.yaw_deg`.
- **Floor frame (F):** right-handed, z up, z = 0 on the real floor.
  - With a reference: the origin is reference corner 0, +x runs corner 0 → corner 1, and +y runs corner 0 → corner 3.
  - With only an estimate: the origin is where the camera's optical axis meets the floor, and +x is the camera's x axis projected onto the floor.
- **Camera frame (C):** the OpenCV convention (x right, y down, z forward, out of the lens).
- **Design world (W):** the existing studio world (z-up metres, origin on the floor; see `schemas.py`).
- `world_from_camera` is the 4×4 matrix T_FC, mapping C points into F, row-major as `list[list[float]]`.
- Placement maps W into F: T_FW = Translate(x, y, 0) · RotZ(yaw_deg).
- The pinhole projection, with no distortion modelled: u = fx·Xc/Zc + cx, v = fy·Yc/Zc + cy, pixel origin at the top-left of the stored photo.

## Photo
- The client normalises every photo before upload:
  - EXIF orientation is applied by decoding with `createImageBitmap(file, { imageOrientation: "from-image" })`;
  - the long side is scaled down to at most 4096 px;
  - it is re-encoded as JPEG at quality 0.92.
- The stored photo therefore has no EXIF rotation, and its pixel grid is the one every number refers to.
- Server checks: `ingest/images.check_image` (15 MB, PNG/JPEG/WebP signature). Python reads width and height from the file header.

## Models (`server/studio/schemas.py`, exported to `contracts/`)
Use lists, not tuples: the Claude CLI rejects `prefixItems`.
```
ArIntrinsics:  fx, fy, cx, cy: float; width, height: int
ArReference:   corners_px: list[list[float]]   # exactly 4 [u, v], in order around the rectangle
               size_m: list[float]             # [length of corner0→1, length of corner1→2], each > 0
ArCalibration: intrinsics: ArIntrinsics
               world_from_camera: list[list[float]]   # 4x4 T_FC
               scale_source: "reference" | "estimate"
               focal_source: "reference" | "estimate" | "default"
               residual_px: float | None       # RMS reprojection of the 4 corners; None for estimate
               camera_height_m: float          # camera centre above the floor (z of T_FC translation)
               warnings: list[str] = []
ArPlacement:   x: float = 0, y: float = 0, yaw_deg: float = 0
ArScene:       id: str; image: str (file name in the scene folder); width: int; height: int
               created: str (ISO 8601 UTC)
               reference: ArReference | None
               calibration: ArCalibration | None    # from the reference (trusted scale)
               estimate: ArCalibration | None       # from the depth model (approximate scale)
               placement: ArPlacement
```
The view uses `calibration` when present, else `estimate`. With only an estimate, the UI shows "approximate scale".

## Python tasks (`python -m studio.tasks <task>`, stdin envelope per docs/PROTOCOL.md)
- `ar_calibrate`:
  - **Input:** `{ "image": {"path": str}, "reference": ArReference, "intrinsics": ArIntrinsics | null }`.
  - **Result:** an `ArCalibration` dict.
  - **Focal length:** taken from the reference homography (`focal_source: "reference"`) when that is well-conditioned. Otherwise it uses the given intrinsics (`"estimate"`), else the default 60° horizontal field of view with the principal point at the image centre (`"default"`). Each fallback adds a warning.
  - **Errors:** it fails with error code `ar_bad_reference` when the corners are not a convex quadrilateral or are degenerate, or when the camera would sit below the floor.
- `ar_estimate`:
  - **Input:** `{ "image": {"path": str} }`.
  - **Result:** an `ArCalibration` dict with `scale_source: "estimate"`.
  - **Missing backend:** fails with error code `ar_estimator_unavailable` when the optional depth backend (torch + MoGe) is not installed.
- **Pure geometry:** in `server/studio/ar/geometry.py`, numpy only, so OpenCV is not a dependency. It covers:
  - homography by normalised DLT;
  - focal length from the homography;
  - pose from the homography, with the rotation re-orthonormalised by SVD;
  - projection and residual;
  - RANSAC plane fit;
  - pose from a floor plane.

## HTTP (Java, under `/api/v1/projects/{pid}/ar/scenes`)
| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `?filename=<name>` | raw image bytes | `ArScene` (201) |
| GET | `` | — | `ArScene[]`, newest first |
| GET | `/{sid}` | — | `ArScene` |
| GET | `/{sid}/photo` | — | the image bytes, correct Content-Type |
| POST | `/{sid}/calibrate` | `{ "reference": ArReference }` | `ArScene` (runs `ar_calibrate` synchronously, like `inspect_upload`) |
| POST | `/{sid}/estimate` | `{}` | a job record (kind `ar_estimate`); on success the store sets `scene.estimate` |
| PUT | `/{sid}/placement` | `ArPlacement` | `ArScene` |
| DELETE | `/{sid}` | — | 204 |

- **Storage:** `runtime/projects/<pid>/ar/<sid>/photo.<ext>` and `scene.json`, written only by `ProjectStore`.
- **Limits:** at most 10 scenes per project.
- **Isolation:** AR data is never part of a design version, so existing projects are unchanged byte for byte.

## Client
- **Toggle:** on the Design stage, a "View in my space" toggle next to the player.
- **Panel:** upload, a list of scenes, the calibrator, "Estimate automatically", and placement controls.
- **Calibrator:**
  - the photo with 4 draggable corner handles;
  - a size preset: A4 (0.297 × 0.210 m), US Letter (0.2794 × 0.2159 m) or custom;
  - the clicking order is shown on the handles (1–4);
  - after calibration, the residual is shown in pixels.
- **AR view:**
  - **Background:** the canvas keeps the photo's aspect ratio, with the photo behind a transparent canvas.
  - **Camera:**
    - the projection comes from the intrinsics;
    - the camera pose is Z_UP · T_FC · diag(1, −1, −1, 1);
    - OrbitControls are off.
  - **Scene:**
    - a shadow-only floor;
    - the design world is wrapped in T_FW;
    - the grid and grey floor are hidden;
    - scene boxes are an optional toggle.
  - **Placement:** drag on the floor to move, a slider for yaw.
  - **Snapshot:** saves a PNG of the photo and the render combined.
- **Shared maths:** in `packages/core/src/ar.ts`, with no imports, so `node --test` can run its tests directly.


## Phone link
"Link a phone" lets a phone on the same Wi-Fi add photos to a project without any account or install.

### Routes
Desktop, on the normal loopback server under `/api/v1/projects/{pid}/ar/pairing`:
| Method | Path | Returns |
|---|---|---|
| POST | `` | `{ url, urls[], qr_svg, expires_at, uploads: 0, max_uploads: 20 }`; replaces any pairing the project already had. 409 "No Wi-Fi or network connection found for a phone to reach" when there is no usable address |
| GET | `` | the same without `qr_svg`; 404 when none is active |
| DELETE | `` | 204, ends the pairing |
| GET | `/qr?url=<one of urls>` | the QR code as SVG; 400 for any other URL |

Phone, on a separate JDK `HttpServer` (`dev.robode.studio.pairing`), and nothing else:
- `GET /p/{code}` is the page.
- `POST /p/{code}/photo` takes the raw image bytes, calls `ProjectService.uploadArScene(pid, "phone-<timestamp>.jpg", bytes)` and answers 200 "Sent", or the failure's status with its plain-text message.
- Everything else, including a wrong or expired code, is an empty 404.

### Security design
- The Spring app and `/api/v1` stay on loopback. The phone listener binds 0.0.0.0 on `STUDIO_PAIR_PORT` (default 8801, 0 picks a free port). It starts when the first pairing starts and is stopped by a 30 s sweep once no pairing is active (ended or expired).
- The code is 32 bytes from `SecureRandom`, base64url (43 characters), compared in constant time. It expires after 10 minutes, belongs to one project, and allows at most 20 uploads (429 after that). A project has one pairing at a time.
- Uploads are read up to 15 MB. Beyond that the answer is 413, the rest of the body is read and discarded (at most 64 MB more), never stored. The existing validation (`inspect_upload`, 10 scenes per project) applies unchanged.
- Every response carries `Cache-Control: no-store`, `Referrer-Policy: no-referrer`, `X-Content-Type-Options: nosniff` and the CSP `default-src 'none'; img-src blob: data:; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; form-action 'none'`. There are no CORS headers. The code is never logged.
- The URL and QR code use the first of the LAN addresses: up, non-loopback, non-virtual, non point-to-point, site-local IPv4 interfaces, Wi-Fi/WLAN first, then Ethernet; names with vEthernet, VirtualBox, VMware, Hyper-V, WSL, Docker or Loopback are skipped.
- The traffic is plain HTTP on the local network: anyone on that network who sees the link can use it until it expires, so stop the link when you are done. The first start may make Windows ask to allow RoboDE through the firewall: allow Private networks only.

### Phone page and client
- One self-contained HTML page (inline CSS and JS). "Take a photo" uses `<input type=file accept="image/*" capture="environment">`, "Choose from gallery" the same input without `capture`. The photo is normalised as above (`createImageBitmap` with `from-image`, long side at most 4096 px, JPEG 0.92; the original file is sent if `createImageBitmap` is missing) and POSTed to `./<code>/photo`, which resolves against `/p/` to `/p/<code>/photo`.
- The AR panel's "Link a phone" button shows the QR code (as an `<img>` data URL), the address (a select when there are several), the countdown, "Photos received: N" and "Stop linking". While open it polls the pairing every 2 s, refetches the scenes when `uploads` grows, and clears the panel on 404.

### Limits
- 10 minutes, 20 photos, 15 MB per upload per link; one link per project.
- The link works only on a network where devices can reach each other (guest Wi-Fi with client isolation will not work).