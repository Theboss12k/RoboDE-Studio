"""URDF intake: safe archive reading, deterministic URDF parsing and mesh resolution.

Ported from RoboDE's importer (SERVER_CLIENT/server/services/imports/urdf.py) so the studio stands alone.
No xacro execution, no external resource fetching.
"""
import hashlib
import io
import math
import posixpath
import re
import stat
import xml.etree.ElementTree as ET
import zipfile
from pathlib import PurePosixPath

MAX_UPLOAD = 32 * 1024 * 1024
MAX_EXPANDED = 100 * 1024 * 1024
MAX_URDF = 2 * 1024 * 1024

def number(text, label, *, optional=False):
    if text is None:
        if optional:
            return None
        raise ValueError(f'{label}: missing number')
    try:
        value = float(text)
    except (ValueError, TypeError) as error:
        raise ValueError(f'{label}: expected a finite number') from error
    if not math.isfinite(value):
        raise ValueError(f'{label}: expected a finite number')
    return value


def vector(text, default, label):
    values = (text or default).split()
    if len(values) != 3:
        raise ValueError(f'{label}: expected three numbers')
    return [number(v, label) for v in values]


def archive_files(payload, filename, selected=''):
    """Return contained regular files only; never extract untrusted archive paths."""
    if len(payload) > MAX_UPLOAD:
        raise ValueError('URDF upload must be at most 32 MB')
    filename = PurePosixPath(filename.replace('\\', '/')).name
    if not filename or len(filename) > 180:
        raise ValueError('Choose a URDF or ZIP file with a valid filename')
    if filename.lower().endswith('.urdf'):
        files = {filename: payload}
        path = filename
    elif filename.lower().endswith('.zip'):
        try:
            files = {}
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                infos = archive.infolist()
                if len(infos) > 512 or sum(i.file_size for i in infos) > MAX_EXPANDED:
                    raise ValueError('ZIP must contain at most 512 entries and 100 MB of expanded files')
                for info in infos:
                    parts = info.filename.rstrip('/').split('/')
                    if ('\\' in info.filename or info.filename.startswith('/') or ':' in info.filename
                            or any(p in {'', '.', '..'} for p in parts)
                            or any(ord(c) < 32 for c in info.filename)
                            or stat.S_ISLNK(info.external_attr >> 16)):
                        raise ValueError('ZIP contains an unsafe path or symbolic link')
                    if info.is_dir():
                        continue
                    if info.filename in files:
                        raise ValueError('ZIP contains duplicate filenames')
                    if info.file_size > 25 * 1024 * 1024:
                        raise ValueError('Each packaged file must be at most 25 MB')
                    files[info.filename] = archive.read(info)
            candidates = sorted(p for p in files if p.lower().endswith('.urdf') and not p.startswith('__MACOSX/'))
            if selected:
                if selected not in candidates:
                    raise ValueError('Selected URDF path is not present in the ZIP')
                path = selected
            elif len(candidates) == 1:
                path = candidates[0]
            else:
                raise ValueError('ZIP needs a selected .urdf file. Enter its path from: ' + ', '.join(candidates[:12]) if candidates else 'ZIP contains no .urdf file. Expand xacro first.')
        except (zipfile.BadZipFile, RuntimeError, NotImplementedError, EOFError) as error:
            raise ValueError('Invalid or unsupported ZIP archive') from error
    else:
        raise ValueError('Upload .urdf, or a .zip containing URDF and meshes. Expand xacro before uploading.')
    if len(files[path]) > MAX_URDF:
        raise ValueError('The URDF document must be at most 2 MB')
    return filename, path, files


def parse_urdf(data):
    try:
        text = data.decode('utf-8-sig')
    except UnicodeDecodeError as error:
        raise ValueError('URDF must be UTF-8 XML') from error
    if re.search(r'<!\s*(?:DOCTYPE|ENTITY)', text, re.I) or re.search(r'<\s*(?:\w+:)?include\b', text, re.I):
        raise ValueError('DTD, entities, includes and xacro execution are not supported; upload expanded URDF')
    try:
        root = ET.fromstring(text)
    except ET.ParseError as error:
        raise ValueError('Invalid URDF XML') from error
    if root.tag != 'robot' or any('xacro' in node.tag.lower() for node in root.iter()):
        raise ValueError('Expected an expanded URDF <robot> document')
    name = root.get('name', '').strip()
    if not name or len(name) > 200:
        raise ValueError('URDF robot name is required and must be at most 200 characters')
    links = [node.get('name', '') for node in root.findall('link')]
    if not links or len(links) > 256 or len(set(links)) != len(links) or any(not n or len(n) > 200 for n in links):
        raise ValueError('URDF needs 1–256 uniquely named links')
    nodes = root.findall('joint')
    if len(nodes) > 256:
        raise ValueError('URDF supports at most 256 joints per import')
    joints = []
    names = set()
    children = set()
    unsupported = []
    for node in nodes:
        jid, kind = node.get('name', ''), node.get('type', '')
        if not jid or len(jid) > 200 or jid in names:
            raise ValueError('URDF joints need unique names of at most 200 characters')
        names.add(jid)
        parent_node, child_node = node.find('parent'), node.find('child')
        parent = parent_node.get('link') if parent_node is not None else None
        child = child_node.get('link') if child_node is not None else None
        if parent not in links or child not in links or child == parent or child in children:
            raise ValueError(f'{jid}: invalid parent/child link or multiple parents')
        children.add(child)
        origin = node.find('origin')
        xyz = vector(origin.get('xyz') if origin is not None else None, '0 0 0', f'{jid} origin xyz')
        rpy = vector(origin.get('rpy') if origin is not None else None, '0 0 0', f'{jid} origin rpy')
        axis_node = node.find('axis')
        axis = vector(axis_node.get('xyz') if axis_node is not None else None, '1 0 0', f'{jid} axis')
        length = math.sqrt(sum(v*v for v in axis))
        if not math.isfinite(length) or length < 1e-12:
            raise ValueError(f'{jid}: axis must have a nonzero finite length')
        axis = [v / length for v in axis]
        limits = node.find('limit')
        values = {key: number(limits.get(attr) if limits is not None else None, f'{jid} {attr}', optional=True)
                  for key, attr in [('min', 'lower'), ('max', 'upper'), ('velocity', 'velocity'), ('acceleration', 'acceleration')]}
        if kind == 'continuous':
            values['min'] = values['max'] = None
        for key in ('velocity', 'acceleration'):
            if values[key] is not None and values[key] <= 0:
                raise ValueError(f'{jid}: {key} must be positive')
        if values['min'] is not None and values['max'] is not None and values['min'] >= values['max']:
            raise ValueError(f'{jid}: lower limit must be less than upper limit')
        if kind not in {'fixed', 'revolute', 'continuous', 'prismatic'}:
            unsupported.append(f'{jid}: {kind or "unspecified"} joints are not supported by this importer')
        mimic = node.find('mimic')
        if mimic is not None:
            if mimic.get('joint')==jid:raise ValueError('A joint cannot mimic itself')
            unsupported.append(f'{jid}: mimic coupling requires a dedicated model integration; it cannot be imported as an independent actuator')
        joints.append({'name': jid, 'type': kind, 'parent': parent, 'child': child, 'axis': axis,
                       'origin': {'xyz': xyz, 'rpy': rpy}, 'mimic':{'joint':mimic.get('joint'),'multiplier':number(mimic.get('multiplier','1'),f'{jid} coupling multiplier'),'offset':number(mimic.get('offset','0'),f'{jid} coupling offset')} if mimic is not None else None, **values,
                       'effort': number(limits.get('effort') if limits is not None else None, f'{jid} effort', optional=True)})
    roots = set(links) - children
    if len(roots) != 1:
        raise ValueError('URDF must form one connected, acyclic rooted tree')
    base = next(iter(roots))
    ordered, reached, remaining = [], {base}, list(joints)
    while remaining:
        ready = [j for j in remaining if j['parent'] in reached]
        if not ready:
            raise ValueError('URDF contains a cycle or disconnected joint tree')
        for j in ready:
            reached.add(j['child']); ordered.append(j); remaining.remove(j)
    if reached != set(links):
        raise ValueError('URDF contains disconnected links')
    movable = [j for j in ordered if j['type'] != 'fixed']
    if not movable:
        unsupported.append('This URDF has no moving joints; import static geometry as an environment object instead')
    tips = sorted(set(links) - {j['parent'] for j in ordered})
    meshes = sorted({n.get('filename', '') for n in root.findall('.//mesh')})
    geometry=[]
    for link in root.findall('link'):
        for kind in ('visual','collision'):
            for item in link.findall(kind):
                g=item.find('geometry');origin=item.find('origin')
                if g is None or len(g)!=1:continue
                shape=g[0];parameters={}
                if shape.tag=='box':parameters['size']=vector(shape.get('size'), '1 1 1', 'box size')
                elif shape.tag=='sphere':parameters['radius']=number(shape.get('radius'),'sphere radius')
                elif shape.tag=='cylinder':parameters={'radius':number(shape.get('radius'),'cylinder radius'),'length':number(shape.get('length'),'cylinder length')}
                elif shape.tag=='mesh':parameters={'uri':shape.get('filename',''),'scale':vector(shape.get('scale'),'1 1 1','mesh scale')}
                else:continue
                geometry.append({'link':link.get('name'),'kind':kind,'shape':shape.tag,'parameters':parameters,
                    'origin':{'xyz':vector(origin.get('xyz') if origin is not None else None,'0 0 0','geometry xyz'),
                              'rpy':vector(origin.get('rpy') if origin is not None else None,'0 0 0','geometry rpy')}})
    return {'name': name, 'base_link': base, 'links': links, 'joints': ordered, 'tips': tips,'geometry':geometry,
            'mesh_uris': meshes, 'unsupported': unsupported}


def mesh_manifest(parsed, path, files):
    result = []
    for uri in parsed['mesh_uris']:
        ref = uri.removeprefix('package://')
        parts = PurePosixPath(ref).parts
        matches = []
        if uri and not ref.startswith('/') and '\\' not in ref and ':' not in ref:
            direct = posixpath.normpath((PurePosixPath(path).parent / ref).as_posix())
            candidates = [ref, direct]
            if uri.startswith('package://') and len(parts) > 1:
                candidates.append('/'.join(parts[1:]))
            matches = sorted({p for p in files if p in candidates})
            if not matches:
                matches = sorted({p for p in files if p.endswith('/'+ref) or (uri.startswith('package://') and len(parts)>1 and p.endswith('/'+'/'.join(parts[1:])))})
        resolved = matches[0] if len(matches) == 1 else None
        result.append({'uri': uri, 'status': 'retained' if resolved else ('ambiguous' if matches else 'missing'),
                       'path': resolved, 'sha256': hashlib.sha256(files[resolved]).hexdigest() if resolved else None})
    return result


def link_inertials(data):
    """{link: {'mass': kg or None, 'has_inertia': bool}} for every link, used by the twin builder."""
    root = ET.fromstring(data.decode('utf-8-sig'))
    result = {}
    for link in root.findall('link'):
        inertial = link.find('inertial')
        mass = inertial.find('mass') if inertial is not None else None
        origin = inertial.find('origin') if inertial is not None else None
        com = [float(v) for v in (origin.get('xyz') if origin is not None and origin.get('xyz') else '0 0 0').split()]
        tensor = inertial.find('inertia') if inertial is not None else None
        inertia = [[float(tensor.get(f'i{a}{b}' if f'i{a}{b}' in tensor.attrib else f'i{b}{a}', 0)) for b in 'xyz'] for a in 'xyz'] if tensor is not None else None
        result[link.get('name')] = {'mass': float(mass.get('value')) if mass is not None and mass.get('value') else None, 'com': com, 'inertia': inertia,
                                    'has_inertia': inertial is not None and inertial.find('inertia') is not None,
                                    'has_geometry': link.find('visual') is not None or link.find('collision') is not None}
    return result


def summary(parsed):
    """A compact, model-friendly description of the robot (SI units)."""
    joints = []
    for j in parsed['joints']:
        if j['type'] == 'fixed':
            continue
        unit = 'm' if j['type'] == 'prismatic' else 'rad'
        joints.append({'name': j['name'], 'type': j['type'], 'parent': j['parent'], 'child': j['child'], 'axis': j['axis'],
                       'unit': unit, 'min': j['min'], 'max': j['max'], 'velocity': j['velocity'], 'effort': j['effort']})
    wheels = {}
    for g in parsed['geometry']:
        if g['shape'] == 'cylinder' and g['kind'] == 'collision':
            for j in parsed['joints']:
                if j['child'] == g['link'] and j['type'] == 'continuous':
                    wheels[j['name']] = {'radius_m': g['parameters']['radius']}
    if len(wheels) == 2:
        (a, wa), (b, wb) = wheels.items()
        ya = next(j['origin']['xyz'][1] for j in parsed['joints'] if j['name'] == a)
        yb = next(j['origin']['xyz'][1] for j in parsed['joints'] if j['name'] == b)
        wheels = {'joints': wheels, 'track_width_m': round(abs(ya - yb), 4)}
    elif wheels:
        wheels = {'joints': wheels}
    return {'name': parsed['name'], 'base_link': parsed['base_link'], 'links': parsed['links'], 'tips': parsed['tips'],
            'moving_joints': joints, 'wheels': wheels or None, 'unsupported': parsed['unsupported']}
