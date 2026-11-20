# Animated robotics artwork

Studio uses these illustrations in the page headers. Each GLB contains geometry, materials and one looping animation. Its matching WebP shows the opening pose.

| Page | GLB and WebP stem | Motion |
| --- | --- | --- |
| Brief | `pick-and-place` | An articulated arm picks up a tote and transfers it between two nests. |
| Reference | `reference-scanner` | A vision head traverses a workpiece while its turntable indexes. |
| Questions | `gripper-fixture` | Two lead-screw-driven jaws close around a sample and reopen. |
| Design | `cartesian-gantry` | The three axes of a gantry position a tool over two fixtures. |
| Robot options | `robot-lineup` | A mobile carrier travels between docks next to a parked arm. |
| Analysis | `load-test` | A press compresses a spring while the force gauge needle moves. |
| Decision | `pallet-indexer` | A rotary table advances and locks four pallets into position. |
| Settings | `servo-calibration` | An encoder disc indexes the servo shaft through a calibration sequence. |

Import a GLB into a glTF 2.0 viewer or use its embedded clip with Three.js. The models use metres and Y-up. The clips illustrate machine movement for the UI.

To regenerate the GLBs from their editable geometry and motion, run `node scripts/robotics-artifacts.mjs` from the repository root.
