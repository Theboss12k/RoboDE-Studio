"""GLB / STL of a generated URDF at its zero pose (boxes and cylinders only), built with trimesh."""
from __future__ import annotations

import xml.etree.ElementTree as ET

import numpy as np
import trimesh


def _floats(text, default):
    return [float(x) for x in (text or default).split()]


def _origin(node):
    o = node.find('origin') if node is not None else None
    xyz = _floats(o.get('xyz') if o is not None else None, '0 0 0')
    r, p, y = _floats(o.get('rpy') if o is not None else None, '0 0 0')
    cr, sr, cp, sp, cy, sy = np.cos(r), np.sin(r), np.cos(p), np.sin(p), np.cos(y), np.sin(y)
    T = np.eye(4)
    T[:3, :3] = [[cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr],
                 [sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr],
                 [-sp, cp * sr, cp * cr]]
    T[:3, 3] = xyz
    return T


def _scene(urdf_xml: str) -> trimesh.Scene:
    root = ET.fromstring(urdf_xml)
    joints = {j.find('child').get('link'): j for j in root.findall('joint')}
    links = {l.get('name'): l for l in root.findall('link')}
    cache = {}

    def world(name):
        if name not in cache:
            j = joints.get(name)
            cache[name] = np.eye(4) if j is None else world(j.find('parent').get('link')) @ _origin(j)
        return cache[name]

    scene = trimesh.Scene()
    for name, link in links.items():
        for vis in link.findall('visual'):
            g = vis.find('geometry')[0]
            if g.tag == 'box':
                mesh = trimesh.creation.box(extents=_floats(g.get('size'), '1 1 1'))
            elif g.tag == 'cylinder':
                mesh = trimesh.creation.cylinder(radius=float(g.get('radius')), height=float(g.get('length')))
            elif g.tag == 'sphere':
                mesh = trimesh.creation.icosphere(radius=float(g.get('radius')))
            else:
                continue
            c = vis.find('material/color')
            rgba = _floats(c.get('rgba'), '0.6 0.65 0.7 1') if c is not None else [0.6, 0.65, 0.7, 1.0]
            mesh.visual.vertex_colors = np.tile((np.array(rgba) * 255).astype(np.uint8), (len(mesh.vertices), 1))   # no scipy needed
            mesh.apply_transform(world(name) @ _origin(vis))
            scene.add_geometry(mesh, node_name=f'{name}_{len(scene.graph.nodes)}')
    return scene


def meshes(urdf_xml: str) -> bytes:
    """GLB bytes of the robot at the zero pose."""
    return bytes(_scene(urdf_xml).export(file_type='glb'))


def stl(urdf_xml: str) -> bytes:
    """STL bytes (all visuals merged) of the robot at the zero pose."""
    scene = _scene(urdf_xml)
    merged = trimesh.util.concatenate([g for g in scene.dump()])
    return bytes(merged.export(file_type='stl'))
