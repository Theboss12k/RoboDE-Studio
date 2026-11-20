"""Build a candidate robot's model from its datasheet. Only verified values drive kinematics and ratings."""
from ..families.params import GantryParams, MobileBaseParams, MobileManipulatorParams, SerialArmParams
from ..schemas import RobotConcept

LINKS = ('upper_arm_m', 'forearm_m', 'wrist_m')


def reach_of(p):
    """Maximum reach of the generated serial arm, from the shoulder axis stretched out."""
    return p.upper_arm_m + p.forearm_m + p.wrist_m


def arm_params(v, base, notes):
    """SerialArmParams from verified values `v`, starting from `base` (a SerialArmParams)."""
    p = base.model_copy(deep=True)
    if 'dof' in v:
        dof = max(4, min(6, int(round(v['dof']))))
        if dof != p.dof:
            p.dof, p.joint_limits_deg, p.max_speed_deg_s, p.max_torque_nm = dof, [], [], []
            notes.append(f'dof={dof} from the datasheet; per-joint lists reset to family defaults')
    if 'payload_kg' in v:
        p.payload_kg = v['payload_kg']
    if 'repeatability_mm' in v:
        p.repeatability_m = v['repeatability_mm'] / 1000
    if 'base_height_m' in v:
        p.base_height_m = v['base_height_m']
    known = {k: v[k] for k in LINKS if k in v}
    for k, x in known.items():
        setattr(p, k, x)
    missing = [k for k in LINKS if k not in known]
    if 'reach_m' in v and missing:
        free = v['reach_m'] - sum(known.values())
        old = sum(getattr(p, k) for k in missing)
        if free > 0 and old > 0:
            for k in missing:
                setattr(p, k, getattr(p, k) * free / old)
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
            notes.append('Link masses are the generated ones scaled to the verified total mass (distribution assumed)')
    joints = range(1, p.dof + 1)
    limits = all(f'j{i}_min_deg' in v and f'j{i}_max_deg' in v for i in joints)
    speeds = all(f'j{i}_speed_deg_s' in v for i in joints)
    if limits:
        p.joint_limits_deg = [(v[f'j{i}_min_deg'], v[f'j{i}_max_deg']) for i in joints]
    if speeds:
        p.max_speed_deg_s = [v[f'j{i}_speed_deg_s'] for i in joints]
    if any(k[0] == 'j' and k[1].isdigit() for k in v) and not (limits and speeds):
        notes.append("Not every joint's range or speed is verified; the generated or default per-joint values are used where missing")
    return SerialArmParams.model_validate(p.model_dump())


def candidate_concept(mspec, generated, spec, candidate=None):
    """(RobotConcept, unverified). `candidate` (a Candidate) supplies the manufacturer and model for the name."""
    verified = {x.key: x.value for x in mspec.values if x.verified}
    unverified = [f'{x.key} = {x.value:g} {x.unit}: not found in the document or number not in its quote'
                  for x in mspec.values if not x.verified]
    notes = []
    family = mspec.family
    same = generated.family == family
    old = generated.params if same else {}
    if family == 'serial_arm':
        params = arm_params(verified, SerialArmParams.model_validate(old), notes).model_dump()
    elif family == 'gantry':
        p = GantryParams.model_validate(old)
        for axis, i in (('x', 0), ('y', 1), ('z', 2)):
            if f'{axis}_stroke_m' in verified:
                setattr(p, f'{axis}_stroke_m', verified[f'{axis}_stroke_m'])
        for key, field in (('max_speed_m_s', 'max_speed_m_s'), ('max_accel_m_s2', 'max_accel_m_s2')):
            if key in verified:
                setattr(p, field, tuple(min(getattr(p, field)[i], verified[key]) if i == 2 else verified[key] for i in range(3)))
        if 'payload_kg' in verified:
            p.payload_kg = verified['payload_kg']
        if 'repeatability_mm' in verified:
            p.repeatability_m = verified['repeatability_mm'] / 1000
        params = GantryParams.model_validate(p.model_dump()).model_dump()
    elif family in ('mobile_base', 'mobile_manipulator'):
        whole = MobileManipulatorParams.model_validate(old) if family == 'mobile_manipulator' else None
        b = (whole.base if whole else MobileBaseParams.model_validate(old)).model_copy()
        for key, field in (('wheel_radius_m', 'wheel_radius_m'), ('track_m', 'track_m'), ('battery_wh', 'battery_wh'),
                           ('max_slope_deg', 'max_slope_deg'), ('max_speed_m_s', 'max_speed_m_s'),
                           ('max_accel_m_s2', 'max_accel_m_s2')):
            if key in verified:
                setattr(b, field, verified[key])
        if family == 'mobile_base':
            for key, field in (('mass_kg', 'mass_kg'), ('payload_kg', 'payload_kg')):
                if key in verified:
                    setattr(b, field, verified[key])
            params = MobileBaseParams.model_validate(b.model_dump()).model_dump()
        else:
            arm_values = {k: x for k, x in verified.items() if k not in ('mass_kg', 'max_speed_m_s', 'max_accel_m_s2')}
            arm = arm_params(arm_values, whole.arm, notes)
            params = MobileManipulatorParams(base=MobileBaseParams.model_validate(b.model_dump()), arm=arm,
                                             arm_mount_m=whole.arm_mount_m).model_dump()
            if 'mass_kg' in verified:
                notes.append('Total mass of a mobile manipulator is not split between base and arm; masses unchanged')
    else:
        raise ValueError(f'Candidate family {family!r} cannot be modelled')
    if not same:
        notes.append(f'The generated design is a {generated.family}; the candidate model starts from family defaults')
    for key in ('payload_kg', 'reach_m'):
        if key not in verified and not (key == 'reach_m' and family != 'serial_arm'):
            unverified.append(f'{key}: not published or not verified; the generated design\'s value is used')
    unverified += notes
    name = f'{candidate.manufacturer} {candidate.model}' if candidate else (mspec.document_title or mspec.candidate_id)
    concept = RobotConcept(family=family, name=name[:120], params=params, base=generated.base,
                           rationale=('Built from verified datasheet values. ' + ' '.join(notes))[:2000])
    return concept, unverified
