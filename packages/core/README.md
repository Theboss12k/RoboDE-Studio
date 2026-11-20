# @robode/studio-core

DOM-free code shared by the Studio web app and future React Native / WebXR clients. Nothing here may touch
`window`, `document` or any browser API; `three` is a peer dependency (math only).

- `src/generated/types.ts` - TypeScript types generated from `contracts/schemas/*.json`
  (`npm run contracts:types`; `npm run contracts:check` fails when stale). Fields with a default are typed as
  required because the server always serialises them.
- `src/types.ts` - hand-written envelopes: `Project`, `ProjectSummary`, `Job`, `Parsed` (URDF), `DesignVersion` with `parsed`.
- `src/api.ts` - `createApi({ baseUrl, fetch, token? })` returning `get/post/put/patch/del/upload/runJob`. The caller
  injects `fetch`, so there is no global dependency.
- `src/kinematics.ts` - `linkFrames` (forward kinematics), `robotExtent`, `Z_UP`.
- `src/playback.ts` - `frameAt`, `baseAt`, `objectCenters`.

Consumed as TypeScript source through the npm workspace (`import ... from "@robode/studio-core"`); Vite and `tsc`
compile it directly, so there is no build step.

## Coordinate frames (the anchor for AR clients)

- Units: metres and radians; times in seconds.
- World frame: z up, floor at z = 0, origin at the scene origin (as the server, the simulator and the URDF use).
  A robot's `base_link` is placed in it by the base pose (`x, y, z, yaw` about z).
- URDF z-up to three.js / glTF y-up: rotate by -90 degrees about X (`Z_UP`), i.e. `(x, y, z)_world -> (x, z, -y)_three`.
  Render world-frame content inside a group with `matrix = Z_UP`. GLB assets served by the API are already y-up.
- To anchor in AR, place the three.js origin on the physical floor point that corresponds to the scene origin and align
  its -z axis with world +y; scale is 1 unit = 1 m.
- `Frame.base` is `[x, y, yaw]` of a driven base (z stays the mounting height); `Frame.tcp` is the world-frame tool point.
