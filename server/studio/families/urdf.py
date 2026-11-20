"""Tiny URDF writer: links with box/cylinder visual + collision + inertial, joints with limits, colours.

Every robot's tool chain ends in a link named `tcp`; the root link is `base_link`.
"""
from __future__ import annotations

from xml.sax.saxutils import quoteattr

RPY_Y_AXIS_CYLINDER = (1.5707963, 0.0, 0.0)     # cylinder (z axis) turned to lie along y (wheels)
RPY_X_AXIS_CYLINDER = (0.0, 1.5707963, 0.0)


def _v(values):
    return ' '.join(f'{x:.6g}' for x in values)


def box(size, xyz=(0, 0, 0), rpy=(0, 0, 0), color=(0.6, 0.65, 0.7, 1.0)):
    return dict(kind='box', size=size, xyz=xyz, rpy=rpy, color=color)


def cylinder(radius, length, xyz=(0, 0, 0), rpy=(0, 0, 0), color=(0.6, 0.65, 0.7, 1.0)):
    return dict(kind='cylinder', radius=radius, length=length, xyz=xyz, rpy=rpy, color=color)


def vcyl(radius, z0, z1, x=0.0, y=0.0, color=(0.6, 0.65, 0.7, 1.0)):
    """Vertical cylinder from z0 to z1 (link frame)."""
    return cylinder(radius, abs(z1 - z0), (x, y, (z0 + z1) / 2), color=color)


def _extent(s):
    if s['kind'] == 'box':
        return s['size']
    return (2 * s['radius'], 2 * s['radius'], s['length'])


class Urdf:
    def __init__(self, name):
        self.name = name
        self.parts = []
        self.links = set()

    def link(self, name, shapes=(), mass=0.0, com=(0, 0, 0)):
        self.links.add(name)
        out = [f'  <link name={quoteattr(name)}>']
        if mass > 0:
            ext = [max(e, 0.01) for e in (_extent(shapes[0]) if shapes else (0.1, 0.1, 0.1))]
            x, y, z = ext
            i = (mass / 12 * (y * y + z * z), mass / 12 * (x * x + z * z), mass / 12 * (x * x + y * y))
            out.append(f'    <inertial><origin xyz="{_v(com)}" rpy="0 0 0"/><mass value="{mass:.6g}"/>'
                       f'<inertia ixx="{i[0]:.6g}" iyy="{i[1]:.6g}" izz="{i[2]:.6g}" ixy="0" ixz="0" iyz="0"/>'
                       f'</inertial>')
        for tag in ('visual', 'collision'):
            for s in shapes:
                g = (f'<box size="{_v(s["size"])}"/>' if s['kind'] == 'box'
                     else f'<cylinder radius="{s["radius"]:.6g}" length="{s["length"]:.6g}"/>')
                mat = (f'<material name="m{abs(hash(s["color"])) % 100000}"><color rgba="{_v(s["color"])}"/></material>'
                       if tag == 'visual' else '')
                out.append(f'    <{tag}><origin xyz="{_v(s["xyz"])}" rpy="{_v(s["rpy"])}"/>'
                           f'<geometry>{g}</geometry>{mat}</{tag}>')
        out.append('  </link>')
        self.parts.append('\n'.join(out))

    def joint(self, name, kind, parent, child, xyz=(0, 0, 0), rpy=(0, 0, 0), axis=(0, 0, 1), lower=None, upper=None,
              velocity=1.0, effort=1.0):
        out = [f'  <joint name={quoteattr(name)} type="{kind}">',
               f'    <parent link={quoteattr(parent)}/><child link={quoteattr(child)}/>',
               f'    <origin xyz="{_v(xyz)}" rpy="{_v(rpy)}"/>']
        if kind != 'fixed':
            out.append(f'    <axis xyz="{_v(axis)}"/>')
            lim = f'effort="{effort:.6g}" velocity="{velocity:.6g}"'
            if kind != 'continuous':
                lim = f'lower="{lower:.6g}" upper="{upper:.6g}" ' + lim
            out.append(f'    <limit {lim}/>')
        out.append('  </joint>')
        self.parts.append('\n'.join(out))

    def xml(self):
        return f'<?xml version="1.0"?>\n<robot name={quoteattr(self.name)}>\n' + '\n'.join(self.parts) + '\n</robot>\n'
