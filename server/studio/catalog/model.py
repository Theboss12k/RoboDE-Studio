"""Build a candidate robot's model from its datasheet. Only verified values drive kinematics and ratings."""
from ..families.params import GantryParams, MobileBaseParams, MobileManipulatorParams, SerialArmParams
from ..schemas import RobotConcept

LINKS = ('upper_arm_m', 'forearm_m', 'wrist_m')
CARRIED = ('tool_length_m', 'tool_mass_kg', 'gripper', 'gripper_force_n', 'gripper_stroke_m')
GRIPPER_FIELDS = ('tool_length_m', 'tool_mass_kg', 'gripper', 'gripper_force_n', 'gripper_stroke_m')


def reach_of(p):
    """Maximum reach of the generated serial arm, from the shoulder axis stretched out."""
    return p.upper_arm_m + p.forearm_m + p.wrist_m


def arm_params(v, base, notes, prov=None, carried=False):
    """SerialArmParams from verified values `v`, starting from `base` (a SerialArmParams). `prov` (a dict) receives the
    provenance of each parameter; `carried` says the tool and gripper values come from the generated design."""
    prov = {} if prov is None else prov
    p = base.model_copy(deep=True)
    if 'dof' in v:
        dof = max(4, min(6, int(round(v['dof']))))
        prov['dof'] = ('verified', 'dof')
        if dof != p.dof:
            p.dof, p.joint_limits_deg, p.max_speed_deg_s, p.max_torque_nm = dof, [], [], []
            notes.append(f'dof={dof} from the datasheet; per-joint lists reset to family defaults')
    if 'payload_kg' in v:
        p.payload_kg = v['payload_kg']
        prov['payload_kg'] = ('verified', 'payload_kg')
    if 'repeatability_mm' in v:
        p.repeatability_m = v['repeatability_mm'] / 1000
        prov['repeatability_m'] = ('verified', 'repeatability_mm')
    if 'base_height_m' in v:
        p.base_height_m = v['base_height_m']
        prov['base_height_m'] = ('verified', 'base_height_m')
    known = {k: v[k] for k in LINKS if k in v}
    for k, x in known.items():
        setattr(p, k, x)
        prov[k] = ('verified', k)
    missing = [k for k in LINKS if k not in known]
    if 'reach_m' in v and missing:
        free = v['reach_m'] - sum(known.values())
        old = sum(getattr(p, k) for k in missing)
        if free > 0 and old > 0:
            for k in missing:
                setattr(p, k, getattr(p, k) * free / old)
                prov[k] = ('derived', f"generated segment scaled so the reach equals the verified {v['reach_m']:g} m")
            notes.append(f"Link lengths ({', '.join(missing)}) are not published: the generated arm's segments were scaled so the "
                         f"reach equals the verified {v['reach_m']:g} m (shoulder to wrist-flange, stretched out)")
        else:
            notes.append('Verified reach is not larger than the published link lengths; link lengths left unchanged')
    elif missing:
        notes.append('Link lengths and reach are not verified; the generated arm geometry was kept')
    if 'mass_kg' in v:
        total = p.base_mass_kg + p.upper_arm_mass_kg + p.forearm_mass_kg + p.wrist_mass_kg
        if total > 0:
            for k in ('base_mass_kg', 'upper_arm_mass_kg', 'forearm_mass_kg', 'wrist_mass_kg'):
                setattr(p, k, getattr(p, k) * v['mass_kg'] / total)
                prov[k] = ('derived', f"generated mass scaled to the verified total {v['mass_kg']:g} kg")
            notes.append('Link masses are the generated ones scaled to the verified total mass (distribution assumed)')
    joints = range(1, p.dof + 1)
    limits = all(f'j{i}_min_deg' in v and f'j{i}_max_deg' in v for i in joints)
    speeds = all(f'j{i}_speed_deg_s' in v for i in joints)
    if limits:
        p.joint_limits_deg = [(v[f'j{i}_min_deg'], v[f'j{i}_max_deg']) for i in joints]
        prov['joint_limits_deg'] = ('verified', 'j*_min_deg, j*_max_deg')
    if speeds:
        p.max_speed_deg_s = [v[f'j{i}_speed_deg_s'] for i in joints]
        prov['max_speed_deg_s'] = ('verified', 'j*_speed_deg_s')
    if 'max_speed_m_s' in v:
        p.max_tcp_speed_m_s = v['max_speed_m_s']
        prov['max_tcp_speed_m_s'] = ('verified', 'max_speed_m_s')
        prov.setdefault('max_tcp_accel_m_s2', ('default', 'derived by the family from the TCP speed (speed / 0.25 s); no acceleration is published'))
    if carried:
        for k in CARRIED:
            prov[k] = ('carried', "from the generated design (arms are sold without grippers)")
    if any(k[0] == 'j' and k[1].isdigit() for k in v) and not (limits and speeds):
        notes.append("Not every joint's range or speed is verified; the generated or default per-joint values are used where missing")
    out = SerialArmParams.model_validate(p.model_dump())
    for k in out.model_dump():
        prov.setdefault(k, ('default', 'not published; family default or sized value'))
    prov.setdefault('max_tcp_accel_m_s2', ('default', 'derived by the family from the TCP speed (speed / 0.25 s); no acceleration is published'))
    return out


DEFAULT_TEXT = 'not published; family default or sized value'


def _flat(params, prefix=''):
    """Parameter names of a family's params dict; a mobile manipulator's `base` and `arm` get a prefix."""
    out = []
    for k, x in params.items():
        if isinstance(x, dict):
            out += _flat(x, f'{k}.')
        else:
            out.append(prefix + k)
    return out


def candidate_concept(mspec, generated, spec, candidate=None):
    """(RobotConcept, unverified, provenance). `candidate` (a Candidate) supplies the manufacturer and model for the name.
    `provenance` maps each parameter to 'verified|derived|carried|default: how' (a mobile manipulator's parameters are
    prefixed `base.` and `arm.`)."""
    verified = {x.key: x.value for x in mspec.values if x.verified}
    unverified = [f'{x.key} = {x.value:g} {x.unit}: not found in the document or number not in its quote'
                  for x in mspec.values if not x.verified]
    notes = []
    prov = {}
    family = mspec.family
    same = generated.family == family
    old = generated.params if same else {}

    def mark(names, kind, text, prefix=''):
        for k in names:
            prov[prefix + k] = (kind, text)

    if family == 'serial_arm':
        arm_prov = {}
        params = arm_params(verified, SerialArmParams.model_validate(old), notes, arm_prov, carried=same).model_dump()
        prov.update(arm_prov)
    elif family == 'gantry':
        p = GantryParams.model_validate(old)
        for axis, i in (('x', 0), ('y', 1), ('z', 2)):
            if f'{axis}_stroke_m' in verified:
                setattr(p, f'{axis}_stroke_m', verified[f'{axis}_stroke_m'])
                mark([f'{axis}_stroke_m'], 'verified', f'{axis}_stroke_m')
        for key, field in (('max_speed_m_s', 'max_speed_m_s'), ('max_accel_m_s2', 'max_accel_m_s2')):
            if key in verified:
                setattr(p, field, tuple(min(getattr(p, field)[i], verified[key]) if i == 2 else verified[key] for i in range(3)))
                mark([field], 'verified', f'{key} on the X and Y axes (Z keeps the lower generated value)')
        if 'payload_kg' in verified:
            p.payload_kg = verified['payload_kg']
            mark(['payload_kg'], 'verified', 'payload_kg')
        if 'repeatability_mm' in verified:
            p.repeatability_m = verified['repeatability_mm'] / 1000
            mark(['repeatability_m'], 'verified', 'repeatability_mm')
        params = GantryParams.model_validate(p.model_dump()).model_dump()
        for k in params:
            prov.setdefault(k, ('default', DEFAULT_TEXT))
        if same:
            mark(GRIPPER_FIELDS, 'carried', 'from the generated design (gantries are sold without grippers)')
    elif family in ('mobile_base', 'mobile_manipulator'):
        whole = MobileManipulatorParams.model_validate(old) if family == 'mobile_manipulator' else None
        b = (whole.base if whole else MobileBaseParams.model_validate(old)).model_copy()
        bp = 'base.' if whole else ''
        for key, field in (('wheel_radius_m', 'wheel_radius_m'), ('track_m', 'track_m'), ('battery_wh', 'battery_wh'),
                           ('max_slope_deg', 'max_slope_deg'), ('max_speed_m_s', 'max_speed_m_s'),
                           ('max_accel_m_s2', 'max_accel_m_s2')):
            if key in verified:
                setattr(b, field, verified[key])
                mark([field], 'verified', key, bp)
        if family == 'mobile_base':
            for key, field in (('mass_kg', 'mass_kg'), ('payload_kg', 'payload_kg')):
                if key in verified:
                    setattr(b, field, verified[key])
                    mark([field], 'verified', key)
            params = MobileBaseParams.model_validate(b.model_dump()).model_dump()
        else:
            arm_values = {k: x for k, x in verified.items() if k not in ('mass_kg', 'max_speed_m_s', 'max_accel_m_s2')}
            arm_prov = {}
            arm = arm_params(arm_values, whole.arm, notes, arm_prov, carried=same)
            prov.update({f'arm.{k}': x for k, x in arm_prov.items()})
            params = MobileManipulatorParams(base=MobileBaseParams.model_validate(b.model_dump()), arm=arm,
                                             arm_mount_m=whole.arm_mount_m).model_dump()
            if 'mass_kg' in verified:
                notes.append('Total mass of a mobile manipulator is not split between base and arm; masses unchanged')
        for k in _flat(params):
            prov.setdefault(k, ('default', DEFAULT_TEXT))
    else:
        raise ValueError(f'Candidate family {family!r} cannot be modelled')
    if not same:
        notes.append(f'The generated design is a {generated.family}; the candidate model starts from family defaults')
    for key in ('payload_kg', 'reach_m'):
        if key not in verified and not (key == 'reach_m' and family != 'serial_arm'):
            unverified.append(f'{key}: not published or not verified; the generated design\'s value is used')
    for key in ('max_accel_m_s2',):
        if key in verified and family == 'serial_arm':
            notes.append('A verified acceleration cannot be applied to a serial arm (the family derives it from the TCP speed); '
                         'the acceleration stays an assumption')
    unverified += notes
    name = f'{candidate.manufacturer} {candidate.model}' if candidate else (mspec.document_title or mspec.candidate_id)
    concept = RobotConcept(family=family, name=name[:120], params=params, base=generated.base,
                           rationale=('Built from verified datasheet values. ' + ' '.join(notes))[:2000])
    return concept, unverified, {k: f'{kind}: {text}' for k, (kind, text) in prov.items()}


# --- what a check depends on ------------------------------------------------------------------------------------------

GEOMETRY = ('upper_arm_m', 'forearm_m', 'wrist_m', 'x_stroke_m', 'y_stroke_m', 'z_stroke_m')
NEEDS = {                                    # check id (or prefix) -> parameters whose value the check depends on
    'joint_torque_': ('max_torque_nm', 'max_force_n'),
    'joint_speed': ('max_speed_deg_s', 'max_speed_m_s'),
    'tcp_speed': ('max_tcp_speed_m_s', 'max_speed_m_s'),
    'tcp_accel': ('max_tcp_accel_m_s2', 'max_accel_m_s2'),
    'cycle_time': ('max_speed_deg_s', 'max_speed_m_s', 'max_tcp_accel_m_s2', 'max_accel_m_s2'),
    'grip_force': ('gripper_force_n', 'gripper'),
    'payload': ('payload_kg',),
    'accuracy': ('repeatability_m',),
    'drive_speed': ('max_speed_m_s',), 'drive_accel': ('max_accel_m_s2',), 'slope': ('max_slope_deg',),
    'battery': ('battery_wh',), 'wheel_torque': ('wheel_torque_nm',),
    'reach_max': GEOMETRY, 'reach_min': GEOMETRY, 'z_min': GEOMETRY, 'z_max': GEOMETRY,
    'collision_free': GEOMETRY, 'joint_limits': GEOMETRY,
}
NOT_PUBLISHED = 'Not published by the manufacturer'


def _kind(provenance, names):
    """{kind: [parameter, ...]} of the provenance entries named in `names` (a mobile manipulator's prefix is ignored)."""
    out = {}
    for k, text in provenance.items():
        if k.rsplit('.', 1)[-1] in names:
            out.setdefault(text.split(':', 1)[0], []).append(k)
    return out


def _value(check, name):
    if check.actual is None:
        return 'a family default'
    return f"{check.actual:.3g}{' ' + check.unit if check.unit else ''}"


def qualify_checks(checks, provenance):
    """Checks of the candidate's simulation, with the verdict withdrawn (passed=None) where the robot value depends on a
    parameter the manufacturer did not publish. The estimated value stays in `actual`."""
    out = []
    for c in checks:
        rid = c.requirement_id
        names = next((v for k, v in NEEDS.items() if rid == k or (k.endswith('_') and rid.startswith(k))), ())
        kinds = _kind(provenance, names)
        if kinds.get('default'):
            params = sorted({k.rsplit('.', 1)[-1] for k in kinds['default']})
            if rid == 'cycle_time':
                detail = (f"{NOT_PUBLISHED}; {', '.join(params)} assumed; estimated time {c.actual:.3g} s"
                          + (f' ({c.detail})' if c.detail else ''))
            else:
                detail = f"{NOT_PUBLISHED}; {', '.join(f'{k} assumed {_value(c, k)}' for k in params)}"
            c = c.model_copy(update={'passed': None, 'detail': detail})
        else:
            extra = []
            if kinds.get('carried'):
                extra.append("uses the generated design's gripper")
            if kinds.get('derived'):
                extra.append('link lengths estimated from the verified reach')
            if extra:
                c = c.model_copy(update={'detail': '; '.join(([c.detail] if c.detail else []) + extra)})
        out.append(c)
    return out
