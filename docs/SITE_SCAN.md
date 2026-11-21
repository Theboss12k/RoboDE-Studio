# Site scan (video AR) — contract

A walk-around video from any phone becomes a 3D mesh of the real site. Users place the robot, add scene objects, put task points on real surfaces and measure in that mesh. They can then apply those edits to the design as a new version, deterministically and without an LLM. This is a visual 3D reconstruction, not a survey. Scan geometry is NOT part of pass/fail checks in this delivery; objects the user adds become modelled scene boxes, and those are checked.

## Frames and units
- **R (raw):** the structure-from-motion frame. Arbitrary rotation, origin and units. The mesh (`scan.glb`) and all frame poses are stored in R.
- **A (aligned):** z up, z = 0 on the floor, still in scan units. `alignment` is the 4×4 matrix `A_from_R` (rotation plus translation, no scale), row-major.
- **S (site):** metres. `S_from_R = diag(f, f, f, 1) · A_from_R`, where f = `scale.factor`. When `scale` is null, f = 1 and the UI says "unscaled".
- **W (design world):** the studio world (z-up metres, see `schemas.py`). Placement maps W into S: `T_SW = Translate(x, y, 0) · RotZ(yaw_deg)`, the same `ArPlacement` model as photo AR.
- **Cameras:** OpenCV convention (x right, y down, z forward). `world_from_camera` is `R_from_C`, row-major 4×4.
- **Three.js:** `Z_UP · S_from_R` for the mesh, and `Z_UP · T_SW` for the design world. In the client, a point clicked on the mesh is in R → S → W (with `T_SW⁻¹`).

## Models (`server/studio/schemas.py`, exported to `contracts/`; lists, never tuples)
```
ScanVideo:   file: str; bytes: int; duration_s: float; width: int; height: int; fps: float; rotation_deg: int
ScanCamera:  model: "SIMPLE_RADIAL"; fx: float; fy: float; cx: float; cy: float; k1: float; width: int; height: int; undistorted: bool = false
             # ONE camera for the whole clip (focal length shared by every frame), at keyframe resolution
             # undistorted: true = frames are stored undistorted (pure pinhole with the same fx, fy, cx, cy); k1 is informational.
             # Scans made before this field exist read as false: their frames still carry the k1 distortion.
ScanFrame:   index: int; t_s: float; image: str (frames/NNNN.jpg); world_from_camera: list[list[float]] | None (None = not registered)
ScanPlane:   normal: list[float] (3, unit, in R); d: float          # normal·p + d = 0
ScanScale:   method: "sheet" | "distance"; factor: float (> 0); residual: float (relative side disagreement, 0 for distance)
             frame: int | None; corners_px: list[list[float]] | None; size_m: list[float] | None
             p1: list[float] | None; p2: list[float] | None; distance_m: float | None          # p1, p2 in R
ScanMesh:    file: str ("scan.glb"); vertices: int; triangles: int
ScanStats:   frames_total: int; frames_registered: int; reproj_px: float; points3d: int; extent: list[float] (A-frame size, scan units)
SiteScan:    id: str; created: str; video: ScanVideo; status: "uploaded" | "reconstructing" | "reconstructed" | "failed"
             error: str | None; camera: ScanCamera | None; frames: list[ScanFrame]; floor: ScanPlane | None
             alignment: list[list[float]] | None; scale: ScanScale | None; mesh: ScanMesh | None
             placement: ArPlacement; stats: ScanStats | None
```
Scans are stored per project, never inside design versions.

## Storage (`ProjectStore` only)
- **Location:** `runtime/projects/<pid>/scans/<sid>/`.
- **Contents:** `video.<mp4|mov|webm>`, `scan.json`, `scan.glb` and `frames/0000.jpg…`.
- **Ids:** `sc_` followed by 10 hex characters.
- **Limits:** 5 scans per project; videos up to 500 MB and 180 s.

## Python tasks (`python -m studio.tasks <task>`, docs/PROTOCOL.md)
- **`scan_probe`** (synchronous; container header only):
  - **Input:** `{ video: {path} }`.
  - **Result:** `ScanVideo` minus `file`.
  - **Errors:** `scan_bad_video` (not MP4/MOV/WebM, or unreadable) and `scan_too_long` (over 180 s).
- **`scan_reconstruct`** (a job; progress text per stage: frames, cameras, depth, fusion, mesh):
  - **Input:** `{ video: {path}, rotation_deg, out: {frames_dir, mesh_path} }`.
  - **Result:** `{ camera, frames, floor, alignment, mesh, stats }`.
  - **Frames:** sample about 3 fps, drop blurry frames (variance of the Laplacian below 40% of the median), keep at most 180, long side 1600 px, written to `frames_dir`.
  - **Cameras:** incremental structure-from-motion in our own code, using OpenCV and SciPy. (pycolmap is not used: Windows Application Control blocks its native DLL on this machine.)
    - SIFT features matched between neighbouring keyframes (ratio test), plus a few skip-frame pairs.
    - Initialise from the essential matrix on the best-baseline early pair (`findEssentialMat` with RANSAC, then `recoverPose`).
    - Add frames by PnP RANSAC (`solvePnPRansac`) and triangulate new tracks.
    - Bundle adjustment with `scipy.optimize.least_squares` (Huber loss, sparse Jacobian), refining ONE shared SIMPLE_RADIAL camera (f, k1; principal point fixed at the image centre). Run it locally every few frames and once over everything at the end.
    - The focal length starts from a sweep of candidate fields of view (50–80°), taking the lowest initial reprojection error.
    - Keep the largest connected reconstruction.
  - **Undistortion:** after SfM every registered keyframe JPEG is rewritten undistorted with the recovered k1 (`cv2` remap, same K as the new camera matrix), so it is a pure pinhole image with identical fx, fy, cx, cy; the result camera carries `undistorted: true`. `scan_scale` therefore ignores k1 when `undistorted` is set (corner pixels come from the stored frame).
  - **Depth:** MoGe-2 per registered (undistorted) frame, aligned to that frame's visible sparse points (robust scale plus shift).
  - **Fusion:** Open3D tensor VoxelBlockGrid TSDF, with the voxel at 1/384 of the scene extent (1/320 if it runs out of memory). Marching cubes, Taubin smoothing (10 iterations), decimation to at most 600k triangles, removal of degenerate and duplicated triangles and small floating pieces, vertex normals. Vertex colours come from every frame that sees the vertex (depth-tested), weighted by frame sharpness and viewing angle, the blurriest 20% of frames left out, then a light bilateral filter over mesh neighbours (2 iterations). GLB with vertex colours and normals (trimesh), in frame R.
  - **Floor:** RANSAC over the sparse and fused points. Pick the large plane whose normal is closest to the mean camera "up" (−y of the cameras), and orient that normal up. `alignment` maps the floor to z = 0 with the normal to +z.
  - **Errors:** `scan_backend_unavailable` (pycolmap, open3d, av or torch missing) and `scan_failed` (fewer than 50% of frames registered, or under 20 frames), with a hint (move slower, more light, more texture).
- **`scan_scale`** (synchronous):
  - **Input:** `{ scan: SiteScan, method, frame?, corners_px?, size_m?, p1?, p2?, distance_m? }`.
  - **Result:** `ScanScale`.
  - **Sheet method:** cast the 4 corner rays of that frame through `camera` and the frame pose, and intersect them with the floor plane in R. The factor is the mean of true ÷ measured over both side pairs; the residual is the relative difference between the two sides' factors.
  - **Errors:** `scan_bad_scale` (rays miss the floor, a degenerate quad, a residual above 0.15, a distance ≤ 0, or p1 = p2).
- **`scan_floor`** (synchronous):
  - **Input:** `{ points: [[x,y,z] × ≥3 in R], camera_up: [x,y,z] }`.
  - **Result:** `{ floor: ScanPlane, alignment }`, which re-picks the floor from clicked points (least-squares plane).
- **`apply_scene`** (a job; deterministic, NO LLM):
  - **Input:** `{ base: DesignVersion, scene: SceneSpec, markers: [{id, position:[x,y,z]}], auto_repair: bool, note: str }`, in W metres.
  - **Steps:**
    1. Move the spec markers to the given positions, and move each object whose `start_marker` moved.
    2. Run `validate_spec` and `validate_scene`.
    3. Run `place_base`, `derive`, then `size_and_simulate`.
    4. Run `repair_collisions` only when `auto_repair` is set.
  - **Result:** `{ version, parsed }`, like revise, with `change_summary` "Edited in the site scan: <note>." followed by `checked_outcome`.
  - **Errors:** `scene_invalid` (with the validation message).

## HTTP (Java, `/api/v1`)
| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/projects/{pid}/scans?filename=` | raw video bytes, streamed to disk, at most 500 MB | `SiteScan` (201) |
| GET | `/projects/{pid}/scans` | — | `SiteScan[]`, newest first |
| GET | `/projects/{pid}/scans/{sid}` | — | `SiteScan` |
| DELETE | `/projects/{pid}/scans/{sid}` | — | 204 |
| GET | `/projects/{pid}/scans/{sid}/video` | — | the video, streamed, with HTTP Range support |
| GET | `/projects/{pid}/scans/{sid}/mesh` | — | `model/gltf-binary`, streamed |
| GET | `/projects/{pid}/scans/{sid}/frames/{index}` | — | `image/jpeg` |
| POST | `/projects/{pid}/scans/{sid}/reconstruct` | `{}` | a job record (kind `scan_reconstruct`); on success it sets `status` to reconstructed and fills the fields, on failure it sets `failed` plus `error` |
| POST | `/projects/{pid}/scans/{sid}/scale` | the `scan_scale` inputs minus `scan` | `SiteScan` |
| PUT | `/projects/{pid}/scans/{sid}/floor` | `{ points }` | `SiteScan` |
| PUT | `/projects/{pid}/scans/{sid}/placement` | `ArPlacement` | `SiteScan` |
| POST | `/projects/{pid}/apply-scene` | `{ base_version, scene, markers, auto_repair, note }` | a job record (kind `apply_scene`); the new version is stored with `parent_version` = base, like revise |

- **Body limits:** only `POST …/scans` is exempt from the 64 MB body filter; it streams to disk with its own 500 MB cap. Over 500 MB gives 413.
- **Approval:** a project that is already approved refuses `apply-scene`, the same rule as revise.

## Phone link
The phone page adds a "Record a site video" button, using `<input type=file accept="video/*" capture="environment">`.
- **Upload:** the file is sent unchanged with its own Content-Type, and the page shows upload progress (XHR).
- **Route:** `POST /p/{code}/video` is streamed to disk with the 500 MB cap. It counts as one upload of the link and becomes a scan through `ProjectService.uploadScan`.
- **Unchanged:** photos keep their 15 MB route.
- **CSP:** adds `media-src blob:`.

## Client (Design stage)
- **View switch:** Modelled scene, My photo or My site.
- **My site view:**
  - the mesh, with orbit controls;
  - the design world drawn through `T_SW`;
  - the badge "Unscaled — set a scale" until a scale exists.
- **Tools:** each works by raycasting against the mesh; every number shown is computed in code.
  - **Place robot:** sets the base.
  - **Add object:** table, station, box or keep-out; snapped to the clicked surface, with size fields.
  - **Task point:** choose a marker, then click a surface.
  - **Measure:** two clicks, distance in metres.
  - **Set scale:** sheet in a frame (reuses `ArCalibrator` on the frame image), or a measured distance.
  - **Pick floor:** three clicks.
- **Apply to design:** posts `apply-scene`, then selects the new version. Without a scale, it says "Set a scale first", and the button stays enabled.
