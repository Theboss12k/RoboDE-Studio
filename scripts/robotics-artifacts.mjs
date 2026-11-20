// Rebuild the reusable GLBs with: node scripts/robotics-artifacts.mjs
import { mkdir, writeFile } from "node:fs/promises";
import * as THREE from "three";
import { GLTFExporter } from "three/addons/exporters/GLTFExporter.js";
import { RoundedBoxGeometry } from "three/addons/geometries/RoundedBoxGeometry.js";

// GLTFExporter uses the browser FileReader API to assemble its binary buffers.
globalThis.FileReader ??= class {
  async readAsArrayBuffer(blob) {
    this.result = await blob.arrayBuffer();
    this.onloadend();
  }
};

const { PI, cos, sin } = Math;
const { lerp, clamp } = THREE.MathUtils;
const materials = {
  orange: paint("Safety orange enamel", "#e36a2e", .25, .39),
  teal: paint("Blue grey enamel", "#548479", .27, .43),
  frame: paint("Graphite powder coat", "#303b40", .35, .68),
  rubber: paint("Black elastomer", "#1c2224", 0, .86),
  alloy: paint("Satin anodized aluminium", "#a9b2b8", .65, .32),
  steel: paint("Ground steel", "#d4d9d9", .72, .26),
  base: paint("Steel fixture plate", "#414748", .48, .51),
  dark: paint("Recessed graphite", "#252b2c", .32, .57),
  brass: paint("Machined bronze", "#be9354", .69, .28),
  crate: paint("Reinforced blue polymer", "#4e777e", .06, .64),
  lens: paint("Aqua coated optics", "#408b97", .36, .2),
  lamp: paint("Green status indicator", "#83dfb6", 0, .29, "#3a9f71"),
  laser: paint("Structured light line", "#66e4ff", 0, .3, "#239ec3"),
};

function paint(name, color, metalness, roughness, emissive = "#000000") {
  return new THREE.MeshStandardMaterial({ name, color, metalness, roughness, emissive });
}

let serial = 0;
const geometries = new Map();
function geometry(key, build) {
  if (!geometries.has(key)) geometries.set(key, build());
  return geometries.get(key);
}

function locate(parent, object, name, position) {
  object.name = `${name}_${++serial}`;
  object.position.set(...position);
  parent.add(object);
  return object;
}

function group(parent, name, x = 0, y = 0, z = 0) {
  return locate(parent, new THREE.Group(), name, [x, y, z]);
}

function mesh(parent, name, shape, material, x, y, z) {
  return locate(parent, new THREE.Mesh(shape, materials[material]), name, [x, y, z]);
}

function box(parent, name, material, x, y, z, w, h, d, bevel = .018) {
  const radius = Math.min(bevel, w / 4, h / 4, d / 4);
  const shape = geometry(`box:${w},${h},${d},${radius}`, () => new RoundedBoxGeometry(w, h, d, 1, radius));
  return mesh(parent, name, shape, material, x, y, z);
}

function cylinder(parent, name, material, x, y, z, radius, length, axis = "y", segments = 24) {
  const shape = geometry(`cylinder:${radius},${length},${segments}`,
    () => new THREE.CylinderGeometry(radius, radius, length, segments));
  const part = mesh(parent, name, shape, material, x, y, z);
  if (axis === "x") part.rotation.z = PI / 2;
  if (axis === "z") part.rotation.x = PI / 2;
  return part;
}

function torus(parent, name, material, x, y, z, radius, tube, axis = "z") {
  const shape = geometry(`torus:${radius},${tube}`, () => new THREE.TorusGeometry(radius, tube, 8, 32));
  const part = mesh(parent, name, shape, material, x, y, z);
  if (axis === "y") part.rotation.x = PI / 2;
  if (axis === "x") part.rotation.y = PI / 2;
  return part;
}

function cable(parent, name, points, radius = .016, material = "rubber") {
  const curve = new THREE.CatmullRomCurve3(points.map((point) => new THREE.Vector3(...point)));
  return mesh(parent, name, new THREE.TubeGeometry(curve, Math.max(20, points.length), radius, 8), material, 0, 0, 0);
}

function bolts(parent, x, y, z, radius, boltRadius, axis, count = 6) {
  for (let i = 0; i < count; i++) {
    const a = i * 2 * PI / count;
    const dx = radius * cos(a);
    const dy = radius * sin(a);
    const points = axis === "x" ? [x, y + dx, z + dy] : axis === "y" ? [x + dx, y, z + dy] : [x + dx, y + dy, z];
    cylinder(parent, "Hex_fastener", "steel", ...points, boltRadius, .013, axis, 6);
  }
}

function fixture(title, width, depth) {
  serial = 0;
  const root = new THREE.Scene();
  root.name = title;
  box(root, "Base_plate", "base", 0, .07, 0, width, .14, depth, .045);
  box(root, "Machined_top", "frame", 0, .142, 0, width - .055, .024, depth - .055);
  for (const x of [-1, 1]) {
    for (const z of [-1, 1]) {
      cylinder(root, "Recessed_anchor", "dark", x * (width / 2 - .105), .156, z * (depth / 2 - .1), .038, .012);
      cylinder(root, "Anchor_bolt", "alloy", x * (width / 2 - .105), .167, z * (depth / 2 - .1), .023, .013, "y", 6);
    }
  }
  return root;
}

function extrusion(parent, name, x, y, z, w, h, d) {
  box(parent, name, "alloy", x, y, z, w, h, d, .012);
  box(parent, "Extrusion_slot", "dark", x, y, z + d / 2, w > .2 ? w - .05 : .018, h > .2 ? h - .05 : .015, .009, .002);
}

function industrialArm(parent, x, y, z, color, scale = 1) {
  const mount = group(parent, "Arm_mount", x, y, z);
  mount.scale.setScalar(scale);
  cylinder(mount, "Mounting_flange", "alloy", 0, .035, 0, .27, .07);
  cylinder(mount, "Pedestal", "frame", 0, .125, 0, .214, .14);
  bolts(mount, 0, .076, 0, .235, .02, "y");
  const slew = group(mount, "J1_base_yaw", 0, .195, 0);
  cylinder(slew, "Slew_bearing", "dark", 0, .025, 0, .214, .05);
  box(slew, "Turret_casting", color, 0, .126, 0, .345, .21, .348, .056);
  box(slew, "Motor_access_panel", "frame", -.173, .137, 0, .022, .135, .205);
  cylinder(slew, "Power_connector", "brass", -.189, .11, .05, .027, .024, "x");
  const shoulder = group(slew, "J2_shoulder", 0, .239, 0);
  const upper = .72;
  const lower = .66;
  gearbox(shoulder, .158, .31, color);
  box(shoulder, "Cast_upper_link", color, .355, 0, 0, .65, .222, .247, .055);
  box(shoulder, "Upper_link_access_cover", "frame", .355, .01, .127, .38, .088, .02, .009);
  cable(shoulder, "Upper_link_cable", [[.12, -.07, -.14], [.31, -.122, -.153], [.57, -.074, -.141]]);
  const elbow = group(shoulder, "J3_elbow", upper, 0, 0);
  gearbox(elbow, .123, .274, color);
  box(elbow, "Cast_forearm_link", color, .32, 0, 0, .554, .172, .168, .038);
  box(elbow, "Forearm_drive_cover", "frame", .37, .067, 0, .34, .046, .119);
  cable(elbow, "Forearm_cable", [[.09, -.075, -.06], [.28, -.118, -.084], [.49, -.078, -.083], [.57, -.043, -.048]], .013);
  const wrist = group(elbow, "J4_wrist", lower, 0, 0);
  gearbox(wrist, .081, .156, "alloy");
  cylinder(wrist, "Tool_adapter", "steel", 0, -.065, 0, .059, .061);
  box(wrist, "Gripper_body", "frame", 0, -.113, 0, .127, .069, .208);
  const fingers = [-1, 1].map((side) => {
    const jaw = group(wrist, side < 0 ? "Tool_jaw_rear" : "Tool_jaw_front", 0, -.178, side * .11);
    box(jaw, "Tool_finger", "alloy", 0, 0, 0, .1, .138, .027);
    box(jaw, "Grip_pad", "rubber", 0, -.029, -side * .015, .087, .062, .011);
    return jaw;
  });
  function pose(targetX, targetY, targetZ, jawGap) {
    const dx = (targetX - x) / scale;
    const dz = (targetZ - z) / scale;
    const reach = Math.hypot(dx, dz);
    const lift = (targetY - y) / scale + .206 - .434;
    const cosine = clamp((reach * reach + lift * lift - upper * upper - lower * lower) / (2 * upper * lower), -1, 1);
    const bend = -Math.acos(cosine);
    const pitch = Math.atan2(lift, reach) - Math.atan2(lower * sin(bend), upper + lower * cos(bend));
    slew.rotation.y = -Math.atan2(dz, dx);
    shoulder.rotation.z = pitch;
    elbow.rotation.z = bend;
    wrist.rotation.z = -pitch - bend;
    fingers[0].position.z = -jawGap;
    fingers[1].position.z = jawGap;
  }
  return { moving: [slew, shoulder, elbow, wrist, ...fingers], pose };
}

function gearbox(parent, radius, width, color) {
  cylinder(parent, "Joint_casting", color, 0, 0, 0, radius, width, "z");
  for (const side of [-1, 1]) {
    cylinder(parent, "Reducer_ring", "alloy", 0, 0, side * width / 2, radius * .83, .021, "z");
    cylinder(parent, "Reducer_cap", "frame", 0, 0, side * (width / 2 + .014), radius * .61, .029, "z");
    bolts(parent, 0, 0, side * (width / 2 + .019), radius * .73, radius * .062, "z", 5);
  }
}

function tote(parent, x, y, z) {
  const part = group(parent, "Transfer_tote", x, y, z);
  box(part, "Tote_body", "crate", 0, 0, 0, .186, .135, .165);
  box(part, "Tote_rim", "teal", 0, .064, 0, .204, .026, .18);
  box(part, "Tote_top_inset", "dark", 0, .079, 0, .167, .006, .146, .002);
  for (const rib of [-.052, 0, .052]) box(part, "Tote_reinforcement_rib", "teal", rib, -.006, .084, .011, .098, .008);
  return part;
}

function phase(keys, time) {
  const next = keys.findIndex(([end]) => end >= time);
  if (next < 1) return keys[next < 0 ? keys.length - 1 : 0].slice(1);
  const [start, ...from] = keys[next - 1];
  const [end, ...to] = keys[next];
  const t = (time - start) / (end - start);
  const ease = t * t * t * (10 + t * (-15 + 6 * t));
  return from.map((value, i) => lerp(value, to[i], ease));
}

function animate(name, seconds, nodes, pose) {
  const frames = Math.ceil(seconds * 24);
  const times = Array.from({ length: frames + 1 }, (_, i) => i * seconds / frames);
  const channels = nodes.map((node) => [
    [THREE.VectorKeyframeTrack, "position", node.position],
    [THREE.QuaternionKeyframeTrack, "quaternion", node.quaternion],
    [THREE.VectorKeyframeTrack, "scale", node.scale],
  ].map(([type, property, value]) => ({ type, name: `${node.name}.${property}`, value, samples: [], previous: null })));
  for (const time of times) {
    pose(time);
    for (const set of channels) for (const channel of set) {
      // Keep quaternion signs on the same hemisphere across revolutions.
      const current = channel.value.clone();
      if (current.isQuaternion && channel.previous && current.dot(channel.previous) < 0) {
        current.set(-current.x, -current.y, -current.z, -current.w);
      }
      current.toArray(channel.samples, channel.samples.length);
      channel.previous = current;
    }
  }
  pose(0);
  const tracks = channels.flat().filter(({ value, samples }) => {
    const width = value.isQuaternion ? 4 : 3;
    return samples.some((component, i) => Math.abs(component - samples[i % width]) > 1e-6);
  }).map(({ type, name: path, samples }) => new type(path, times, samples).optimize());
  return new THREE.AnimationClip(name, seconds, tracks);
}

function pickAndPlace() {
  const root = fixture("Pick and place cell", 2.44, 1.56);
  const arm = industrialArm(root, -.59, .154, 0, "orange");
  const x = .48;
  const near = .43;
  const far = -.43;
  const seat = .365;
  const up = .67;
  for (const z of [near, far]) {
    box(root, "Nest_stand", "frame", x, .22, z, .35, .14, .34);
    box(root, "Nest_top", "alloy", x, .285, z, .404, .024, .39);
    for (const rail of [-.134, .134]) box(root, "Nest_guide", "dark", x + rail, .304, z, .026, .032, .265);
  }
  const part = tote(root, x, seat, near);
  // Five columns describe tool height, bay, jaw opening, tote height and tote bay.
  const cycle = [
    [0, up, near, .13, seat, near], [1.0, seat, near, .13, seat, near], [1.45, seat, near, .103, seat, near],
    [2.35, up, near, .103, up, near], [3.5, up, far, .103, up, far], [4.45, seat, far, .103, seat, far],
    [4.85, seat, far, .13, seat, far], [5.55, up, far, .13, seat, far], [5.85, up, far, .13, seat, far],
    [6.65, seat, far, .13, seat, far], [7.0, seat, far, .103, seat, far], [7.9, up, far, .103, up, far],
    [9.05, up, near, .103, up, near], [10, seat, near, .103, seat, near], [10.4, seat, near, .13, seat, near],
    [11.25, up, near, .13, seat, near], [11.6, up, near, .13, seat, near],
  ];
  const motion = animate("Transfer between two nests", 11.6, [...arm.moving, part], (time) => {
    const [height, bay, gap, toteHeight, toteBay] = phase(cycle, time);
    arm.pose(x, height, bay, gap);
    part.position.set(x, toteHeight, toteBay);
    part.rotation.y = -Math.atan2(toteBay, x + .59);
  });
  return { root, motion };
}

function referenceScanner() {
  const root = fixture("Machine vision inspection", 2.15, 1.45);
  const turntable = group(root, "Indexed_reference_fixture", .32, .22);
  cylinder(root, "Turntable_motor", "frame", .32, .202, 0, .425, .102);
  cylinder(turntable, "Turntable_rim", "alloy", 0, .06, 0, .4, .073);
  cylinder(turntable, "Fixture_face", "dark", 0, .104, 0, .354, .026);
  bolts(turntable, 0, .123, 0, .31, .013, "y", 8);
  box(turntable, "Reference_billet", "alloy", 0, .175, 0, .41, .12, .29);
  box(turntable, "Reference_step", "steel", -.055, .267, 0, .29, .091, .27);
  cylinder(turntable, "Reference_boss", "brass", -.104, .329, .024, .058, .04);
  box(root, "Column_foot", "dark", -.69, .2, -.37, .44, .11, .42);
  extrusion(root, "Scanner_column", -.69, .63, -.37, .16, .9, .17);
  extrusion(root, "Optical_slide", -.178, 1.025, -.37, 1.20, .12, .17);
  box(root, "Linear_guide", "steel", -.18, .956, -.267, 1.03, .033, .038);
  const head = group(root, "Vision_head_carriage", -.03, .97, -.35);
  box(head, "Carriage_mount", "frame", 0, -.013, .04, .244, .24, .195);
  box(head, "Camera_housing", "orange", 0, -.095, .35, .25, .18, .537);
  for (const x of [-.065, .064]) {
    cylinder(head, "Optical_bezel", "frame", x, -.188, .386, .05, .039);
    cylinder(head, "Optical_window", "lens", x, -.216, .386, .038, .022);
  }
  box(head, "Camera_status", "lamp", 0, -.11, .626, .092, .02, .013);
  cable(root, "Shielded_camera_lead", [[-.744, .23, -.37], [-.802, .56, -.50], [-.793, 1.05, -.55], [-.51, 1.095, -.5]], .019);
  const stripe = box(root, "Scan_trace", "laser", .32, .337, 0, .013, .007, .24, .001);
  const scan = [
    [0, .08, 0, 0], [.65, .087, 0, 0], [1.1, .116, 0, 1], [2.8, .449, 0, 1], [3.25, .48, 0, 0],
    [4.4, .48, PI, 0], [4.8, .446, PI, 1], [6.5, .115, PI, 1], [6.85, .08, PI, 0], [8.1, .08, 2 * PI, 0],
  ];
  const motion = animate("Scan and index the reference piece", 8.1, [head, turntable, stripe], (time) => {
    const [travel, rotation, beam] = phase(scan, time);
    head.position.x = travel;
    turntable.rotation.y = rotation;
    stripe.position.x = travel;
    stripe.position.y = .456 + .079 * (cos(rotation) > 0 ? (travel < .39 ? 1 : 0) : (travel > .256 ? 1 : 0));
    stripe.scale.setScalar(Math.max(beam, .001));
  });
  return { root, motion };
}

function gripperFixture() {
  const root = fixture("Adjustable gripper on a fixture", 2.13, 1.35);
  box(root, "Bench_support", "frame", 0, .29, -.13, 1.38, .26, .41);
  box(root, "Gripper_housing", "orange", 0, .46, -.105, 1.42, .224, .42);
  box(root, "Bearing_backplate", "dark", 0, .505, .141, 1.279, .155, .119);
  for (const height of [.46, .577]) cylinder(root, "Jaw_guide_rod", "steel", 0, height, .236, .026, 1.266, "x");
  const spindle = group(root, "Opposed_lead_screw", 0, .515, .26);
  cylinder(spindle, "Lead_screw", "brass", 0, 0, 0, .027, 1.27, "x");
  const threads = Array.from({ length: 134 }, (_, i) => {
    const t = i / 133;
    const a = t * PI * 2 * 15;
    return [lerp(-.6, .6, t), .028 * cos(a), .028 * sin(a)];
  });
  cable(spindle, "Visible_screw_thread", threads, .006, "steel");
  const jaws = [-1, 1].map((side) => {
    const jaw = group(root, side < 0 ? "Left_sliding_jaw" : "Right_sliding_jaw", side * .48, .54, .28);
    box(jaw, "Bearing_block", "alloy", 0, -.022, 0, .185, .228, .164);
    box(jaw, "Jaw_finger", "alloy", 0, -.026, .215, .132, .162, .319);
    box(jaw, "Grip_insert", "rubber", -side * .071, -.036, .252, .018, .137, .205);
    for (const z of [.204, .316]) cylinder(jaw, "Finger_bolt", "frame", 0, .064, z, .021, .018, "y", 6);
    return jaw;
  });
  box(root, "Specimen_pedestal", "dark", 0, .24, .52, .254, .17, .29);
  box(root, "Measured_workpiece", "teal", 0, .415, .52, .301, .203, .242);
  const cycle = [[0, .51], [.55, .51], [2.1, .225], [2.95, .225], [4.55, .51], [5.2, .51]];
  const motion = animate("Close, check and reopen the gripper", 5.2, [...jaws, spindle], (time) => {
    const [opening] = phase(cycle, time);
    jaws[0].position.x = -opening;
    jaws[1].position.x = opening;
    spindle.rotation.x = (.51 - opening) / .08 * 2 * PI;
  });
  return { root, motion };
}

function cartesianGantry() {
  const root = fixture("Cartesian gantry prototype", 2.41, 1.75);
  for (const side of [-1, 1]) {
    const x = side * .94;
    for (const z of [-.61, .61]) {
      box(root, "Foot_bracket", "frame", x, .19, z, .25, .095, .236);
      extrusion(root, "T_slot_upright", x, .707, z, .117, 1.02, .117);
      box(root, "Corner_gusset", "orange", x - side * .06, 1.099, z + .03, .15, .184, .116);
    }
    extrusion(root, "Y_axis_extrusion", x, 1.231, 0, .126, .132, 1.45);
    box(root, "Y_axis_rail", "steel", x, 1.317, 0, .08, .039, 1.38);
  }
  const bridge = group(root, "Y_axis_bridge", 0, 1.327, .4);
  extrusion(bridge, "X_axis_bridge", 0, .056, 0, 2.05, .147, .164);
  box(bridge, "X_axis_rail", "steel", 0, .063, .104, 1.855, .054, .051);
  for (const x of [-.94, .94]) box(bridge, "Y_axis_truck", "orange", x, .018, 0, .234, .111, .272);
  const carriage = group(bridge, "X_axis_carriage", -.52, 0, 0);
  box(carriage, "Carriage_body", "orange", 0, .054, .148, .304, .237, .181);
  for (const x of [-.074, .074]) cylinder(carriage, "Vertical_linear_guide", "steel", x, -.107, .231, .024, .458);
  const quill = group(carriage, "Z_axis_quill", 0, -.066, .264);
  cylinder(quill, "Quill_rod", "steel", 0, -.16, 0, .055, .335);
  cylinder(quill, "Tool_head", "frame", 0, -.345, 0, .099, .08);
  cylinder(quill, "Vacuum_cup", "rubber", 0, -.402, 0, .086, .045);
  for (const [x, z] of [[-.52, .4], [.51, -.46]]) {
    box(root, "Gantry_workholding_bed", "dark", x, .221, z + .264, .389, .137, .376);
    box(root, "Datum_plate", "alloy", x, .293, z + .264, .334, .02, .33);
    cylinder(root, "Datum_insert", "brass", x, .341, z + .264, .087, .083);
  }
  const itinerary = [
    [0, -.52, .4, -.045], [.85, -.52, .4, -.525], [1.4, -.52, .4, -.525], [2.15, -.52, .4, -.045],
    [3.45, .51, -.46, -.045], [4.25, .51, -.46, -.525], [4.8, .51, -.46, -.525], [5.55, .51, -.46, -.045],
    [7, -.52, .4, -.045],
  ];
  const motion = animate("Jog the X, Y and Z axes", 7, [bridge, carriage, quill], (time) => {
    const [x, z, y] = phase(itinerary, time);
    carriage.position.x = x;
    bridge.position.z = z;
    quill.position.y = y;
  });
  return { root, motion };
}

function candidateLineup() {
  const root = fixture("Mobile robot and fixed arm lineup", 2.65, 1.83);
  box(root, "Robot_mounting_island", "alloy", -.64, .177, -.36, 1.03, .051, .77);
  const arm = industrialArm(root, -.78, .205, -.42, "teal", .59);
  arm.pose(-.17, .47, -.40, .084);
  for (const z of [.187, .743]) box(root, "Travel_lane_marker", "alloy", .0, .158, z, 2.36, .009, .017, .002);
  for (const x of [-1.027, 1.027]) box(root, "Dock_marker", "orange", x, .16, .467, .11, .012, .55, .003);
  const mobile = group(root, "Autonomous_mobile_robot", -.78, .155, .472);
  box(mobile, "Shock_absorbing_bumper", "rubber", 0, .116, 0, .63, .142, .48, .062);
  box(mobile, "Mobile_robot_housing", "orange", 0, .175, 0, .597, .116, .443, .054);
  box(mobile, "Cargo_deck", "alloy", 0, .238, 0, .503, .028, .358, .02);
  box(mobile, "Safety_lidar_aperture", "dark", .28, .183, 0, .049, .066, .234);
  box(mobile, "Front_lidar_window", "lens", .308, .182, 0, .016, .03, .176);
  box(mobile, "Vehicle_status_lamp", "lamp", 0, .198, .228, .208, .027, .01, .004);
  const wheels = [-1, 1].flatMap((side) => [-.155, .155].map((x) => {
    const wheel = group(mobile, "Drive_wheel", x, .088, side * .19);
    cylinder(wheel, "Tread", "rubber", 0, 0, 0, .087, .092, "z");
    cylinder(wheel, "Hub", "alloy", 0, 0, side * .047, .047, .015, "z");
    cylinder(wheel, "Keyed_axle", "frame", 0, 0, side * .054, .023, .013, "z", 6);
    return wheel;
  }));
  tote(mobile, -.077, .33, 0);
  const itinerary = [[0, -.84], [.6, -.84], [3.15, .84], [4, .84], [6.6, -.84], [7.15, -.84]];
  const motion = animate("Drive between docking markers", 7.15, [mobile, ...wheels], (time) => {
    const [x] = phase(itinerary, time);
    mobile.position.x = x;
    for (const wheel of wheels) wheel.rotation.z = -(x + .84) / .087;
  });
  return { root, motion };
}

function loadTest() {
  const root = fixture("Instrumented load test", 1.97, 1.43);
  for (const x of [-.59, .53]) {
    box(root, "Press_column_foot", "dark", x, .198, -.17, .275, .09, .36);
    extrusion(root, "Press_upright", x, .741, -.19, .136, 1.06, .169);
  }
  box(root, "Press_crosshead", "orange", -.03, 1.299, -.067, 1.287, .188, .45, .035);
  for (const x of [-.59, .53]) bolts(root, x, 1.4, -.17, .084, .015, "y", 4);
  cylinder(root, "Linear_actuator_housing", "frame", 0, 1.139, .033, .168, .249);
  cylinder(root, "Actuator_lower_bearing", "alloy", 0, 1.005, .033, .133, .045);
  box(root, "Compression_fixture", "alloy", 0, .273, .032, .66, .227, .55);
  cylinder(root, "Specimen_support", "frame", 0, .413, .033, .22, .071);
  const sample = group(root, "Compression_spring", 0, .451, .033);
  const spring = Array.from({ length: 121 }, (_, i) => {
    const t = i / 120;
    return [.123 * cos(t * PI * 12), .027 + .25 * t, .123 * sin(t * PI * 12)];
  });
  cable(sample, "Steel_spring", spring, .022, "steel");
  cylinder(sample, "Upper_spring_seat", "alloy", 0, .28, 0, .185, .03);
  const ram = group(root, "Press_ram", 0, .841, .033);
  cylinder(ram, "Exposed_piston", "steel", 0, .188, 0, .067, .391);
  cylinder(ram, "Load_transducer", "orange", 0, .026, 0, .107, .075);
  cylinder(ram, "Pressure_foot", "alloy", 0, -.035, 0, .152, .059);
  const gauge = group(root, "Load_gauge", .58, 1.032, .176);
  cylinder(gauge, "Gauge_body", "frame", 0, 0, 0, .155, .082, "z");
  cylinder(gauge, "Gauge_face", "alloy", 0, 0, .045, .126, .017, "z");
  for (let i = 0; i < 9; i++) {
    const a = (i - 4) * PI / 6;
    const tick = box(gauge, "Dial_graduation", "dark", .103 * sin(a), .103 * cos(a), .056, .007, .025, .007);
    tick.rotation.z = -a;
  }
  const needle = group(gauge, "Load_needle", 0, 0, .065);
  box(needle, "Indicator_needle", "orange", 0, .042, 0, .013, .112, .011);
  cylinder(gauge, "Needle_hub", "dark", 0, 0, .074, .018, .04, "z");
  const cycle = [[0, .88], [.65, .88], [1.8, .81], [3.15, .68], [4.1, .68], [5.35, .81], [6.2, .88], [6.75, .88]];
  const motion = animate("Apply and release a test load", 6.75, [sample, ram, needle], (time) => {
    const [height] = phase(cycle, time);
    const contact = .81;
    const compression = Math.max(0, contact - height);
    ram.position.y = height;
    sample.scale.y = 1 - compression / .29;
    needle.rotation.z = 1.9 - compression * 24;
  });
  return { root, motion };
}

function palletIndexer() {
  const root = fixture("Rotary pallet handoff", 2.34, 1.6);
  cylinder(root, "Indexer_gearbox", "frame", -.28, .251, 0, .558, .205);
  torus(root, "Stationary_bearing_seal", "rubber", -.28, .362, 0, .55, .027, "y");
  const table = group(root, "Indexed_table", -.28, .373, 0);
  cylinder(table, "Index_table", "alloy", 0, .022, 0, .612, .148);
  cylinder(table, "Center_hub", "frame", 0, .138, 0, .123, .089);
  bolts(table, 0, .101, 0, .548, .015, "y", 12);
  for (let i = 0; i < 4; i++) {
    const a = i * PI / 2;
    const nest = group(table, "Pallet_nest", .365 * cos(a), .111, .365 * sin(a));
    nest.rotation.y = -a;
    box(nest, "Kinematic_nest", "dark", 0, 0, 0, .322, .069, .28);
    box(nest, "Pallet", i === 0 ? "orange" : "teal", 0, .065, 0, .286, .065, .244);
    for (const side of [-1, 1]) box(nest, "Pallet_runner", "alloy", -.02, .105, side * .087, .214, .028, .029);
  }
  box(root, "Index_lock_pedestal", "frame", .649, .32, 0, .34, .321, .398);
  box(root, "Latch_housing", "orange", .619, .536, 0, .334, .116, .306);
  box(root, "Lock_sensor", "lamp", .756, .565, .063, .067, .041, .038);
  const latch = group(root, "Index_lock_slide", .509, .5, 0);
  cylinder(latch, "Latch_piston", "steel", 0, -.023, 0, .047, .271, "x");
  box(latch, "Latch_stop", "alloy", -.134, -.022, 0, .071, .081, .109);
  const cycle = [[0, 0, .495]];
  for (let i = 1; i <= 4; i++) {
    const start = (i - 1) * 2.25;
    cycle.push([start + .46, (i - 1) * PI / 2, .61], [start + 1.45, i * PI / 2, .61],
      [start + 1.82, i * PI / 2, .495], [start + 2.25, i * PI / 2, .495]);
  }
  const motion = animate("Advance and lock each pallet", 9, [table, latch], (time) => {
    const [angle, pin] = phase(cycle, time);
    table.rotation.y = angle;
    latch.position.x = pin;
  });
  return { root, motion };
}

function servoCalibration() {
  const root = fixture("Robot servo and encoder calibration", 2.27, 1.40);
  box(root, "Motor_foot", "frame", -.28, .241, 0, 1.1, .177, .56);
  box(root, "Servo_motor_casting", "frame", -.329, .557, 0, .724, .494, .49, .036);
  for (const offset of [-.208, -.139, -.069, 0, .069, .139, .208]) {
    box(root, "Cooling_fin", "alloy", -.329 + offset, .801, 0, .026, .069, .424);
    box(root, "Cooling_fin_front", "alloy", -.329 + offset, .557, .25, .027, .368, .04);
  }
  box(root, "Output_flange", "orange", .074, .557, 0, .132, .519, .519);
  bolts(root, .145, .557, 0, .201, .02, "x", 4);
  cylinder(root, "Outer_bearing", "alloy", .146, .557, 0, .175, .05, "x");
  torus(root, "Shaft_seal", "rubber", .179, .557, 0, .123, .018, "x");
  const rotor = group(root, "Encoder_indexed_shaft", .166, .557, 0);
  cylinder(rotor, "Motor_drive_shaft", "steel", .23, 0, 0, .077, .49, "x");
  box(rotor, "Shaft_key", "alloy", .318, .071, 0, .24, .023, .031);
  cylinder(rotor, "Encoder_wheel", "frame", .326, 0, 0, .215, .049, "x");
  torus(rotor, "Encoder_disc_rim", "alloy", .359, 0, 0, .193, .02, "x");
  for (let i = 0; i < 16; i++) {
    const a = i * PI / 8;
    const slot = box(rotor, "Encoder_index", i === 0 ? "orange" : "alloy", .353, .153 * cos(a), .153 * sin(a), .018, .046, .018);
    slot.rotation.x = a;
  }
  box(root, "Encoder_sensor_bracket", "orange", .505, .823, 0, .193, .142, .117);
  box(root, "Sensor_status_lamp", "lamp", .531, .901, 0, .079, .022, .065);
  box(root, "Servo_drive_module", "frame", -.435, .295, -.47, .521, .246, .252);
  box(root, "Drive_status_panel", "lamp", -.435, .427, -.45, .105, .024, .058);
  cable(root, "Power_and_feedback_cable", [[-.7, .64, 0], [-.834, .59, -.03], [-.857, .32, -.258], [-.719, .328, -.476]], .026);
  const cycle = [[0, 0], [.7, 0], [2, PI * 1.5], [2.75, PI * 1.5], [3.85, PI * .5],
    [4.55, PI * .5], [5.8, 2 * PI], [6.5, 2 * PI]];
  const motion = animate("Index and calibrate the servo", 6.5, [rotor], (time) => {
    const [angle] = phase(cycle, time);
    rotor.rotation.x = angle;
  });
  return { root, motion };
}

const models = {
  "pick-and-place": pickAndPlace,
  "reference-scanner": referenceScanner,
  "gripper-fixture": gripperFixture,
  "cartesian-gantry": cartesianGantry,
  "robot-lineup": candidateLineup,
  "load-test": loadTest,
  "pallet-indexer": palletIndexer,
  "servo-calibration": servoCalibration,
};

const directory = new URL("../client/public/robotics-artifacts/", import.meta.url);
await mkdir(directory, { recursive: true });
const exporter = new GLTFExporter();
for (const [id, make] of Object.entries(models)) {
  const { root, motion } = make();
  const buffer = await exporter.parseAsync(root, { binary: true, animations: [motion] });
  await writeFile(new URL(`${id}.glb`, directory), new Uint8Array(buffer));
  console.log(`${id}.glb: ${Math.round(buffer.byteLength / 1024)} KiB · ${motion.duration} s`);
}
