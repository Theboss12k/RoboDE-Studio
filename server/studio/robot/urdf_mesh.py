"""Studio URDF: the design's joint layout with mesh visuals and collisions and a prismatic `gripper` joint.

`studio_urdf` rewrites the family's URDF text: every link named in `link_meshes` gets `<mesh filename=.../>` for both
visual and collision (the first visual's colour is kept); inertials, joints and limits are unchanged. Mesh paths are
relative to the URDF file's folder (`meshes/<link>.stl`); the STL files are in metres.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

from ..schemas import DesignVersion

GRIPPER_DEFAULTS = {'joint': 'gripper', 'parent': 'tool', 'link': 'gripper_finger', 'xyz': (0.0, 0.0, 0.0),
                    'axis': (0.0, 1.0, 0.0), 'lower': 0.0, 'upper': 0.08, 'velocity': 0.1, 'effort': 40.0,
                    'mass_kg': 0.05}


def _fmt(values) -> str:
    return ' '.join(f'{float(v):.6g}' for v in values)


def studio_urdf(version: DesignVersion, link_meshes: dict[str, str], gripper: dict | None) -> str:
    """The studio URDF text. `gripper` (None = no gripper joint) keys: joint, parent, link, xyz, axis, lower, upper,
    velocity, effort, mass_kg; missing keys take GRIPPER_DEFAULTS. The finger link uses `link_meshes[link]`."""
    root = ET.fromstring(version.urdf)
    for link in root.findall('link'):
        mesh = link_meshes.get(link.get('name', ''))
        if not mesh:
            continue
        rgba = None
        first = link.find('visual/material/color')
        if first is not None:
            rgba = first.get('rgba')
        for tag in ('visual', 'collision'):
            for node in link.findall(tag):
                link.remove(node)
        for tag in ('visual', 'collision'):
            node = ET.SubElement(link, tag)
            ET.SubElement(node, 'origin', xyz='0 0 0', rpy='0 0 0')
            ET.SubElement(ET.SubElement(node, 'geometry'), 'mesh', filename=mesh)
            if tag == 'visual' and rgba:
                ET.SubElement(ET.SubElement(node, 'material', name=f'{link.get("name")}_color'), 'color', rgba=rgba)
    if gripper:
        g = {**GRIPPER_DEFAULTS, **gripper}
        names = {link.get('name') for link in root.findall('link')}
        if g['parent'] not in names:
            raise ValueError(f'gripper parent link {g["parent"]!r} is not in the URDF')
        link = ET.SubElement(root, 'link', name=g['link'])
        inertial = ET.SubElement(link, 'inertial')
        ET.SubElement(inertial, 'origin', xyz='0 0 0', rpy='0 0 0')
        ET.SubElement(inertial, 'mass', value=f'{g["mass_kg"]:.6g}')
        ET.SubElement(inertial, 'inertia', ixx='1e-5', iyy='1e-5', izz='1e-5', ixy='0', ixz='0', iyz='0')
        mesh = link_meshes.get(g['link'])
        for tag in ('visual', 'collision'):
            if mesh:
                node = ET.SubElement(link, tag)
                ET.SubElement(node, 'origin', xyz='0 0 0', rpy='0 0 0')
                ET.SubElement(ET.SubElement(node, 'geometry'), 'mesh', filename=mesh)
        joint = ET.SubElement(root, 'joint', name=g['joint'], type='prismatic')
        ET.SubElement(joint, 'parent', link=g['parent'])
        ET.SubElement(joint, 'child', link=g['link'])
        ET.SubElement(joint, 'origin', xyz=_fmt(g['xyz']), rpy='0 0 0')
        ET.SubElement(joint, 'axis', xyz=_fmt(g['axis']))
        ET.SubElement(joint, 'limit', lower=f'{g["lower"]:.6g}', upper=f'{g["upper"]:.6g}',
                      velocity=f'{g["velocity"]:.6g}', effort=f'{g["effort"]:.6g}')
    ET.indent(root, space='  ')
    return '<?xml version="1.0"?>\n' + ET.tostring(root, encoding='unicode') + '\n'
