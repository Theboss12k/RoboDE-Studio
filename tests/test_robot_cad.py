"""Robot studio CAD (OpenCascade): parts, STEP/STL files, link meshes, the studio URDF and the geometry checks.
Motors here are built by hand (fixtures, not products); the design versions come from the scripted pipeline (arm) or
the family defaults (gantry, mobile base). Nothing here is a model evaluation."""
import asyncio
import math
import re
import struct
import time

import pytest

pytest.importorskip('OCP')

from studio.families import get as get_family  # noqa: E402
from studio.ingest.urdf import parse_urdf  # noqa: E402
from studio.llm.registry import LLMRegistry  # noqa: E402
from studio.robot import cad, urdf_mesh  # noqa: E402
from studio.robot.cad import assemble, kernel, parts  # noqa: E402
from studio.schemas import (DesignVersion, Drive, MotorChoice, RobotConcept, SpecValue,  # noqa: E402
                            StructureStyle)
from studio_scripts import llms  # noqa: E402

import test_pipeline as tp  # noqa: E402

PRINTED, MACHINED = 'extrusion_printed', 'machined_aluminium'


def style(name):
    return StructureStyle(style=name, proposed=name)


def motor(joint, frame='NEMA 23', drive='planetary', extra=()):
    values = [SpecValue(key='frame', text=frame, status='assumed')] if frame else []
    return MotorChoice(joint=joint, source='defined', name=f'Fixture motor {joint}', motor_type='stepper',
                       values=values + list(extra), drive=Drive(type=drive, ratio=10, efficiency=0.9))


def version_of(family, params=None):
    fam = get_family(family)
    p = fam.parse(params or {})
    return DesignVersion.model_construct(version=1, robot=RobotConcept(family=family, name='fixture', params=p.model_dump()),
                                         urdf=fam.build_urdf(p, 'fixture'))


def motors_for(version, drive_of=lambda j: 'planetary'):
    parsed = parse_urdf(version.urdf.encode())
    return [motor(j['name'], drive=drive_of(j['name'])) for j in parsed['joints'] if j['type'] != 'fixed']


@pytest.fixture(scope='module')
def arm_version(tmp_path_factory):
    return asyncio.run(tp.make_v1(LLMRegistry(tmp_path_factory.mktemp('reg'), llms())))


def build(version, st, tmp_path, drive_of=lambda j: 'planetary', motors=None, progress=None):
    out = tmp_path / st
    res = assemble.build_cad(version, motors if motors is not None else motors_for(version, drive_of), style(st), out,
                             progress or (lambda t: None))
    return res, out


@pytest.fixture(scope='module')
def arm_builds(arm_version, tmp_path_factory):
    root = tmp_path_factory.mktemp('arm')
    return {s: build(arm_version, s, root) for s in (PRINTED, MACHINED)}


@pytest.fixture(scope='module')
def gantry_builds(tmp_path_factory):
    root, v = tmp_path_factory.mktemp('gantry'), version_of('gantry')
    drive = lambda j: 'leadscrew' if j == 'z_axis' else 'belt'  # noqa: E731
    return v, {s: build(v, s, root, drive) for s in (PRINTED, MACHINED)}


@pytest.fixture(scope='module')
def mobile_builds(tmp_path_factory):
    root, v = tmp_path_factory.mktemp('mobile'), version_of('mobile_base')
    return v, {s: build(v, s, root, lambda j: 'wheel_gearbox') for s in (PRINTED, MACHINED)}


# --- reading files back (OpenCascade STEP reader; pattern as in the testbed's step_parts) ---------------------------------

def read_step(path):
    """{part name: (volume mm^3, bbox min mm, bbox max mm)} of the free shapes of a STEP file."""
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    from OCP.STEPCAFControl import STEPCAFControl_Reader
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.TDataStd import TDataStd_Name
    from OCP.TDF import TDF_Label
    from OCP.TDocStd import TDocStd_Document
    from OCP.XCAFDoc import XCAFDoc_DocumentTool
    kernel.silence_messenger()
    reader = STEPCAFControl_Reader()
    reader.SetNameMode(True)
    assert str(reader.ReadFile(str(path))).endswith('IFSelect_RetDone')
    doc = TDocStd_Document(TCollection_ExtendedString('XmlOcaf'))
    assert reader.Transfer(doc)
    tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    from OCP.collections import Sequence_TDF_Label
    roots = Sequence_TDF_Label()
    tool.GetFreeShapes(roots)
    found = {}
    for i in range(1, roots.Length() + 1):
        label = roots.Value(i)
        attr = TDataStd_Name()
        assert label.FindAttribute(TDataStd_Name.GetID_s(), attr)
        shape = tool.GetShape_s(label)
        box = Bnd_Box()
        BRepBndLib.AddOptimal_s(shape, box, False, False)
        props = GProp_GProps()
        BRepGProp.VolumeProperties_s(shape, props)
        lo, hi = box.CornerMin(), box.CornerMax()
        found[attr.Get().ToExtString()] = (abs(props.Mass()), (lo.X(), lo.Y(), lo.Z()), (hi.X(), hi.Y(), hi.Z()))
    return found


def read_stl(path):
    """(triangle count, min xyz, max xyz) of a binary or ASCII STL; raises on malformed files."""
    raw = path.read_bytes()
    assert raw, f'{path} is empty'
    tris = []
    if len(raw) >= 84 and 84 + 50 * struct.unpack('<I', raw[80:84])[0] == len(raw):          # binary
        n = struct.unpack('<I', raw[80:84])[0]
        for i in range(n):
            vals = struct.unpack('<12f', raw[84 + 50 * i:84 + 50 * i + 48])
            tris += [vals[3:6], vals[6:9], vals[9:12]]
    else:
        text = raw.decode()
        assert text.lstrip().startswith('solid')
        tris = [tuple(map(float, m)) for m in re.findall(r'vertex\s+(\S+)\s+(\S+)\s+(\S+)', text)]
    assert tris and len(tris) % 3 == 0 and all(math.isfinite(x) for v in tris for x in v)
    lo = tuple(min(v[i] for v in tris) for i in range(3))
    hi = tuple(max(v[i] for v in tris) for i in range(3))
    return len(tris) // 3, lo, hi


# --- tests -------------------------------------------------------------------------------------------------------------

def test_available():
    assert cad.available() is True


def test_kernel_primitives_valid_and_metres(tmp_path):
    ext = kernel.tslot_extrusion(20, 40, 300)
    for shape in (kernel.box((10, 20, 30)), kernel.cylinder(5, 20), kernel.tube(10, 8, 30), ext,
                  kernel.bolt_holes(kernel.box((50, 50, 5)), kernel.square_pattern(30), 4, 6)):
        assert kernel.valid(shape)
    assert kernel.volume(kernel.box((10, 20, 30))) == pytest.approx(6000)
    lo, hi = kernel.bbox(ext)
    assert hi[2] - lo[2] == pytest.approx(300) and hi[0] - lo[0] == pytest.approx(20) and hi[1] - lo[1] == pytest.approx(40)
    n, lo, hi = read_stl_path(tmp_path, ext)
    assert n > 100 and hi[2] - lo[2] == pytest.approx(0.3, abs=1e-5)              # STL in metres


def read_stl_path(tmp_path, shape):
    path = tmp_path / 'x.stl'
    kernel.write_stl(shape, path)
    return read_stl(path)


def test_arm_builds_files_and_hashes(arm_builds):
    for st, (res, out) in arm_builds.items():
        assert (out / 'assembly.step').is_file() and (out / 'urdf/robot.urdf').is_file()
        assert {f.path for f in res.files} >= {'assembly.step', 'urdf/robot.urdf', 'urdf/meshes/upper_arm.stl',
                                               'parts/upper_arm_' + ('extrusion' if st == PRINTED else 'tube') + '.step'}
        for f in res.files:
            data = (out / f.path).read_bytes()
            assert f.bytes == len(data) > 0 and f.sha256 == kernel.sha256(out / f.path)
        assert all(c.passed for c in res.checks), [c for c in res.checks if not c.passed]
        assert {c.requirement_id for c in res.checks} >= {'cad_valid', 'cad_link_frames', 'cad_joint_chain',
                                                          'cad_motor_axes'}


def test_link_frame_check_catches_a_part_in_the_wrong_frame(arm_version, tmp_path, monkeypatch):
    """The motor allowance must not hide a part built 300 mm off in its link frame."""
    real = assemble.run_checks

    def shifted(c, shapes_by_link, by_name):
        part = by_name['upper_arm_extrusion']
        part.shape = kernel.place(part.shape, parts.translate(300, 0, 0))
        shapes_by_link['upper_arm'] = [x.shape for x in c.parts if x.link == 'upper_arm' and x.shape is not None]
        return real(c, shapes_by_link, by_name)

    monkeypatch.setattr(assemble, 'run_checks', shifted)
    res, _ = build(arm_version, PRINTED, tmp_path)
    chk = next(c for c in res.checks if c.requirement_id == 'cad_link_frames')
    assert not chk.passed and chk.actual > 100 and 'upper_arm' in chk.detail


def test_link_frame_allowance_comes_from_the_motor_length(arm_version, tmp_path):
    """A long real motor (114 mm NEMA 23 with brake and encoder) passes; the allowance is the motor's own length."""
    parsed = parse_urdf(arm_version.urdf.encode())
    long = [motor(j['name'], extra=[SpecValue(key='length_mm', value=114.0, unit='mm', status='assumed')])
            for j in parsed['joints'] if j['type'] != 'fixed']
    res, _ = build(arm_version, PRINTED, tmp_path, motors=long)
    chk = next(c for c in res.checks if c.requirement_id == 'cad_link_frames')
    assert chk.passed, chk.detail
    assert 'motor allowance 114' in chk.detail


def test_arm_step_readback_matches_parts(arm_version, arm_builds):
    p = arm_version.robot.params
    res, out = arm_builds[PRINTED]
    modelled = {b.id for b in res.parts if b.files}
    assert modelled and all(not b.files for b in res.parts if b.process == 'standard')
    step = read_step(out / 'assembly.step')
    assert set(step) == modelled and len(step) == len(modelled)
    assert all(v > 0 for v, _, _ in step.values())
    # joints at q = 0: the arm points straight up, so link sections sit in the world z ranges of the chain
    h, L1, L2 = p['base_height_m'] * 1000, p['upper_arm_m'] * 1000, p['forearm_m'] * 1000
    _, lo, hi = step['upper_arm_extrusion'][0], *step['upper_arm_extrusion'][1:]
    assert lo[2] >= h - 1 and hi[2] <= h + L1 + 1 and hi[2] - lo[2] > 0.5 * L1
    _, lo, hi = step['forearm_extrusion'][0], *step['forearm_extrusion'][1:]
    assert lo[2] >= h + L1 - 1 and hi[2] <= h + L1 + L2 + 1 and hi[2] - lo[2] > 0.5 * L2
    # a single part file holds one named shape in its link frame (upper_arm: z from 0 along the link)
    one = read_step(out / 'parts/upper_arm_extrusion.step')
    assert list(one) == ['upper_arm_extrusion']
    assert one['upper_arm_extrusion'][1][2] >= -1 and one['upper_arm_extrusion'][2][2] <= L1 + 1


def test_arm_stl_files_valid_and_in_metres(arm_version, arm_builds):
    p = arm_version.robot.params
    res, out = arm_builds[PRINTED]
    stl = [f for f in res.files if f.kind == 'stl']
    assert len(stl) > 10
    for f in stl:
        n, lo, hi = read_stl(out / f.path)
        assert n > 0 and max(hi[i] - lo[i] for i in range(3)) < 3.0                     # metres, not millimetres
    n, lo, hi = read_stl(out / 'urdf/meshes/upper_arm.stl')                              # merged link mesh, link frame
    assert hi[2] > 0.7 * p['upper_arm_m'] and lo[2] > -0.12


def test_studio_urdf_reparses_with_meshes_and_gripper(arm_version, arm_builds):
    for st, (res, out) in arm_builds.items():
        parsed = parse_urdf((out / 'urdf/robot.urdf').read_bytes())
        assert parsed['links'] and 'gripper_finger' in parsed['links']
        g = next(j for j in parsed['joints'] if j['name'] == 'gripper')
        assert g['type'] == 'prismatic' and g['parent'] == 'tool' and g['min'] == 0.0 and g['max'] == pytest.approx(0.08)
        assert sorted(parsed['mesh_uris']) == sorted(res.link_meshes.values()) and parsed['mesh_uris']
        assert {x['shape'] for x in parsed['geometry'] if x['link'] == 'upper_arm'} == {'mesh'}
        assert all((out / 'urdf' / uri).is_file() for uri in parsed['mesh_uris'])
        # joint layout is the family's: same revolute joints and origins as the design URDF
        base = parse_urdf(arm_version.urdf.encode())
        assert [(j['name'], j['type'], j['origin']) for j in base['joints']] == \
               [(j['name'], j['type'], j['origin']) for j in parsed['joints'] if j['name'] != 'gripper']
        assert res.gripper and res.gripper['upper'] == pytest.approx(0.08)


def test_studio_urdf_without_gripper_and_bad_parent(arm_version):
    text = urdf_mesh.studio_urdf(arm_version, {'upper_arm': 'meshes/upper_arm.stl'}, None)
    assert 'gripper' not in parse_urdf(text.encode())['links']
    with pytest.raises(ValueError):
        urdf_mesh.studio_urdf(arm_version, {}, {'parent': 'nope'})


def test_styles_change_the_part_list(arm_builds, gantry_builds, mobile_builds):
    for builds in (arm_builds, gantry_builds[1], mobile_builds[1]):
        a, b = ({p.id: p for p in builds[s][0].parts} for s in (PRINTED, MACHINED))
        assert set(a) != set(b) or {p.material for p in a.values()} != {p.material for p in b.values()}
        assert {p.process for p in a.values()} >= {'printed'} and 'printed' not in {p.process for p in b.values()}
        assert 'machined' in {p.process for p in b.values()}
    p, m = ({q.id for q in arm_builds[s][0].parts} for s in (PRINTED, MACHINED))
    assert 'upper_arm_extrusion' in p - m and 'upper_arm_tube' in m - p


def test_motor_mount_takes_flange_and_pattern_from_the_motor(arm_version, tmp_path):
    n17 = [SpecValue(key='flange_mm', value=42.3, status='verified'), SpecValue(key='bolt_pitch_mm', value=31.0,
                                                                                status='verified')]
    ms = [motor(j['name'], frame=None, extra=n17 if j['name'] == 'j2' else []) for j in
          parse_urdf(arm_version.urdf.encode())['joints'] if j['type'] != 'fixed']
    res, out = build(arm_version, MACHINED, tmp_path, motors=ms)
    mounts = read_step(out / 'assembly.step')
    side = lambda n: max(mounts[n][2][i] - mounts[n][1][i] for i in range(3))  # noqa: E731
    assert side('mount_j2') == pytest.approx(42.3 + 16, abs=0.05)               # from the motor's flange value
    assert side('mount_j3') == pytest.approx(56.4 + 16, abs=0.05)               # no frame: documented NEMA 23 default
    spec = {b.id: b.spec for b in res.parts}
    assert 'bolt pitch 31 mm' in spec['motor_j2'] and 'default frame' in spec['motor_j3']
    g = parts.motor_geometry(motor('x', frame='NEMA 17'))
    assert (g.flange, g.pitch, g.pilot, g.shaft_d) == (42.3, 31.0, 22.0, 5.0)


def test_standard_parts_are_bom_rows_without_files(arm_builds, gantry_builds):
    res = arm_builds[PRINTED][0]
    by = {b.id: b for b in res.parts}
    assert by['bearing_j2'].process == 'standard' and by['bearing_j2'].source == 'standard' and not by['bearing_j2'].files
    assert '608' in by['bearing_j2'].spec and by['bearing_j2'].qty == 2        # 8 mm... NEMA 23 table shaft 6.35 -> 608
    assert 'M5' in by['bolts_j2'].spec and by['bolts_j2'].qty == 4
    assert by['gearbox_j2'].source == 'purchased' and by['motor_j2'].process == 'purchased' and by['motor_j2'].files
    g = {b.id: b for b in gantry_builds[1][PRINTED][0].parts}
    assert 'GT2' in g['belt_x_axis'].spec and 'GT2' in g['pulley_x_axis'].spec and g['belt_x_axis'].process == 'standard'
    assert 'lead screw' in g['leadscrew_z_axis'].spec and 'tslot_hardware_base_link' in g


def test_gantry_and_mobile_base_produce_files_and_pass_checks(gantry_builds, mobile_builds):
    for v, builds in (gantry_builds, mobile_builds):
        for st, (res, out) in builds.items():
            assert all(c.passed for c in res.checks), [(c.requirement_id, c.detail) for c in res.checks if not c.passed]
            step = read_step(out / 'assembly.step')
            assert set(step) == {b.id for b in res.parts if b.files} and all(vol > 0 for vol, _, _ in step.values())
            parsed = parse_urdf((out / 'urdf/robot.urdf').read_bytes())
            assert set(parsed['mesh_uris']) == {f'meshes/{k}.stl' for k in res.link_meshes}
            for f in res.files:
                assert (out / f.path).stat().st_size == f.bytes > 0
    # gantry: the posts reach the frame height; Z leadscrew motor sits on the Z axis
    v, builds = gantry_builds
    res, out = builds[PRINTED]
    step = read_step(out / 'assembly.step')
    H = v.robot.params['frame_height_m'] * 1000
    assert step['post_1'][2][2] == pytest.approx(H, abs=1) and step['post_1'][1][2] == pytest.approx(0, abs=1)
    assert any(c.requirement_id == 'cad_motor_axes' and 'worst' in c.detail for c in res.checks)
    assert res.gripper is not None
    # mobile base: no gripper joint, two wheels, wheel parts sit at their joint origins
    v, builds = mobile_builds
    res, out = builds['machined_aluminium']
    assert res.gripper is None and 'gripper' not in parse_urdf(res.urdf.encode())['links']
    w = read_step(out / 'assembly.step')['wheel_wheel_left']
    r = v.robot.params['wheel_radius_m'] * 1000
    assert (w[2][2] - w[1][2]) == pytest.approx(2 * r, abs=0.5)


def test_determinism_same_inputs_same_bytes(arm_version, tmp_path):
    ms = motors_for(arm_version)
    a, oa = build(arm_version, PRINTED, tmp_path / 'a', motors=ms)
    b, ob = build(arm_version, PRINTED, tmp_path / 'b', motors=ms)
    assert [(f.path, f.sha256) for f in a.files] == [(f.path, f.sha256) for f in b.files]
    # STEP carries a fixed header time stamp (kernel.STEP_FIXED_TIME), so STEP files are byte-equal too
    assert (oa / 'parts/upper_arm_extrusion.step').read_bytes() == (ob / 'parts/upper_arm_extrusion.step').read_bytes()
    assert kernel.STEP_FIXED_TIME.encode() in (oa / 'assembly.step').read_bytes()


def test_missing_motor_is_reported_not_hidden(arm_version, tmp_path):
    ms = [m for m in motors_for(arm_version) if m.joint != 'j3']
    res, _ = build(arm_version, MACHINED, tmp_path, motors=ms)
    bad = next(c for c in res.checks if c.requirement_id == 'cad_motors')
    assert bad.passed is False and 'j3' in bad.detail
    assert 'motor_j3' not in {b.id for b in res.parts} and 'motor_j2' in {b.id for b in res.parts}


def test_mobile_manipulator_is_not_in_v1(tmp_path):
    v = version_of('mobile_manipulator')
    with pytest.raises(ValueError, match='not in v1'):
        assemble.build_cad(v, [], style(PRINTED), tmp_path, lambda t: None)


def test_progress_lines_and_heartbeat(arm_version, tmp_path, monkeypatch):
    lines = []
    build(arm_version, MACHINED, tmp_path, progress=lines.append)
    assert lines[0].startswith('CAD: building serial_arm') and lines[-1] == 'CAD: done'
    monkeypatch.setattr(assemble, 'HEARTBEAT_S', 0.3)
    got = []
    with assemble.Progress(got.append) as prog:
        prog('slow step')
        time.sleep(1.5)
    assert got[0] == 'slow step' and any('still working' in t for t in got[1:])


def test_stdout_stays_clean_during_cad(arm_version, tmp_path, capfd):
    build(arm_version, MACHINED, tmp_path)
    assert capfd.readouterr().out == ''                  # OpenCascade statistics must not reach the task's stdout
