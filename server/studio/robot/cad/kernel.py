"""Thin OpenCascade layer: primitives, booleans, placement, validity, STEP (XCAF names) and STL.

Shapes are in millimetres. `matrix` arguments are 4x4 row-major homogeneous transforms (translation in mm).
OCP is imported inside the functions so importing this module never needs it.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import math
import os
import re
import sys
from pathlib import Path

Vec = tuple[float, float, float]
STL_LINEAR_TOL_M = 0.0005       # chord deflection of the STL surface, metres
STL_ANGULAR_TOL_RAD = 0.3
ROUND_MM = 4                    # inputs are rounded to 0.1 um so equal designs give equal shapes
STEP_FIXED_TIME = '2000-01-01T00:00:00'


def silence_messenger() -> None:
    """Remove OpenCascade's stream printers (they print transfer statistics on C++ stdout)."""
    from OCP.Message import Message, Message_PrinterOStream
    Message.DefaultMessenger_s().RemovePrinters(Message_PrinterOStream.get_type_descriptor_s())


@contextlib.contextmanager
def _quiet():
    """OpenCascade prints transfer statistics on the process's stdout, which carries the task's JSON lines
    (docs/PROTOCOL.md): send file descriptor 1 and Python's stdout and stderr to nowhere while it runs."""
    sys.stdout.flush()
    saved, null = os.dup(1), os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(null, 1)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            yield
    finally:
        os.dup2(saved, 1)
        os.close(saved)
        os.close(null)


def _r(x: float) -> float:
    return round(float(x), ROUND_MM)


def _pt(v):
    from OCP.gp import gp_Pnt
    return gp_Pnt(_r(v[0]), _r(v[1]), _r(v[2]))


def _ax2(base: Vec, axis: Vec):
    from OCP.gp import gp_Ax2, gp_Dir
    return gp_Ax2(_pt(base), gp_Dir(*[float(a) for a in axis]))


def box(size: Vec, corner: Vec = (0, 0, 0)):
    """Box of `size` (mm) with its minimum corner at `corner`."""
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    return BRepPrimAPI_MakeBox(_pt(corner), _r(size[0]), _r(size[1]), _r(size[2])).Shape()


def box_range(x0, x1, y0, y1, z0, z1):
    """Box from coordinate ranges (any order)."""
    lo = (min(x0, x1), min(y0, y1), min(z0, z1))
    return box((abs(x1 - x0), abs(y1 - y0), abs(z1 - z0)), lo)


def cylinder(radius: float, height: float, base: Vec = (0, 0, 0), axis: Vec = (0, 0, 1)):
    """Solid cylinder from `base` along `axis` for `height` (mm)."""
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
    return BRepPrimAPI_MakeCylinder(_ax2(base, axis), _r(radius), _r(height)).Shape()


def tube(r_out: float, r_in: float, height: float, base: Vec = (0, 0, 0), axis: Vec = (0, 0, 1)):
    """Hollow cylinder (round tube or ring)."""
    return cut(cylinder(r_out, height, base, axis), cylinder(r_in, height + 2, _shift(base, axis, -1), axis))


def _shift(base, axis, d):
    n = math.sqrt(sum(a * a for a in axis))
    return tuple(b + d * a / n for b, a in zip(base, axis))


def cut(a, *tools):
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    out = a
    for t in tools:
        out = BRepAlgoAPI_Cut(out, t).Shape()
    return out


def fuse(*shapes):
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse
    out = shapes[0]
    for s in shapes[1:]:
        out = BRepAlgoAPI_Fuse(out, s).Shape()
    return out


def compound(shapes):
    """Compound of shapes (no boolean): used for one merged mesh per link."""
    from OCP.BRep import BRep_Builder
    from OCP.TopoDS import TopoDS_Compound
    c, b = TopoDS_Compound(), BRep_Builder()
    b.MakeCompound(c)
    for s in shapes:
        b.Add(c, s)
    return c


def tslot_extrusion(width: float, height: float, length: float):
    """Simplified aluminium T-slot extrusion (20x20, 20x40 ... 20 mm modules), along +z from 0 to `length`, centred in
    x and y: square section, one T-slot (6.2 mm opening, 11 mm chamber) per 20 mm module and side, 4.2 mm centre bore
    per module. A modelling simplification of a catalogue profile, not a drawing of one."""
    nx, ny = max(1, round(width / 20)), max(1, round(height / 20))
    w, h = nx * 20.0, ny * 20.0
    body = box((w, h, length), (-w / 2, -h / 2, 0))
    cuts = []
    for k in range(nx):
        cx = -w / 2 + 10 + 20 * k
        for sgn in (-1, 1):
            edge = sgn * h / 2
            cuts.append(box((6.2, 1.8, length + 2), (cx - 3.1, edge - (1.8 if sgn > 0 else 0), -1)))
            cuts.append(box((11, 4.2, length + 2), (cx - 5.5, edge - (6.0 if sgn > 0 else -1.8), -1)))
    for k in range(ny):
        cy = -h / 2 + 10 + 20 * k
        for sgn in (-1, 1):
            edge = sgn * w / 2
            cuts.append(box((1.8, 6.2, length + 2), (edge - (1.8 if sgn > 0 else 0), cy - 3.1, -1)))
            cuts.append(box((4.2, 11, length + 2), (edge - (6.0 if sgn > 0 else -1.8), cy - 5.5, -1)))
    for kx in range(nx):
        for ky in range(ny):
            cuts.append(cylinder(2.1, length + 2, (-w / 2 + 10 + 20 * kx, -h / 2 + 10 + 20 * ky, -1)))
    return cut(body, *cuts)


def bolt_holes(shape, points: list[Vec], diameter: float, depth: float, axis: Vec = (0, 0, 1)):
    """Cut round holes of `diameter` and `depth` starting at each point and going along `axis`."""
    tools = [cylinder(diameter / 2, depth, p, axis) for p in points]
    return cut(shape, *tools) if tools else shape


def circle_pattern(n: int, radius: float, z: float = 0.0, phase: float = 0.0) -> list[Vec]:
    return [(_r(radius * math.cos(phase + 2 * math.pi * i / n)), _r(radius * math.sin(phase + 2 * math.pi * i / n)), z)
            for i in range(n)]


def square_pattern(pitch: float, z: float = 0.0) -> list[Vec]:
    h = pitch / 2
    return [(sx * h, sy * h, z) for sx in (-1, 1) for sy in (-1, 1)]


def place(shape, matrix):
    """Apply a 4x4 homogeneous transform (rows; translation in mm)."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCP.gp import gp_Trsf
    m = [[float(x) for x in row] for row in matrix]
    t = gp_Trsf()
    t.SetValues(m[0][0], m[0][1], m[0][2], _r(m[0][3]), m[1][0], m[1][1], m[1][2], _r(m[1][3]),
                m[2][0], m[2][1], m[2][2], _r(m[2][3]))
    return BRepBuilderAPI_Transform(shape, t, True).Shape()


def valid(shape) -> bool:
    """True when the B-rep passes BRepCheck_Analyzer and the volume is positive."""
    from OCP.BRepCheck import BRepCheck_Analyzer
    return bool(not shape.IsNull() and BRepCheck_Analyzer(shape).IsValid() and volume(shape) > 0)


def volume(shape) -> float:
    """Volume in mm^3."""
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props)
    return abs(props.Mass())


def bbox(shape) -> tuple[Vec, Vec]:
    """(min, max) corners in mm (exact bounding box where the surface type allows it)."""
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    b = Bnd_Box()
    BRepBndLib.AddOptimal_s(shape, b, False, False)
    lo, hi = b.CornerMin(), b.CornerMax()
    return (lo.X(), lo.Y(), lo.Z()), (hi.X(), hi.Y(), hi.Z())


def cylinder_axes(shape) -> list[tuple[float, Vec, Vec]]:
    """[(radius mm, point on axis, unit direction)] of every cylindrical face (to check shaft axes)."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cylinder
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS
    out, ex = [], TopExp_Explorer(shape, TopAbs_FACE)
    while ex.More():
        face = TopoDS.Face(ex.Current())
        s = BRepAdaptor_Surface(face)
        if s.GetType() == GeomAbs_Cylinder:
            cyl = s.Cylinder()
            a = cyl.Axis()
            p, d = a.Location(), a.Direction()
            out.append((cyl.Radius(), (p.X(), p.Y(), p.Z()), (d.X(), d.Y(), d.Z())))
        ex.Next()
    return out


def write_step(parts: list[tuple[str, object]], path: Path) -> None:
    """Write named shapes as free shapes of one XCAF document (`TDataStd_Name` = part id) to a STEP file in mm.
    The header time stamp and the OCC file name are fixed, so equal shapes give byte-equal files."""
    from OCP.STEPCAFControl import STEPCAFControl_Writer
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.TDataStd import TDataStd_Name
    from OCP.TDocStd import TDocStd_Document
    from OCP.XCAFDoc import XCAFDoc_DocumentTool
    doc = TDocStd_Document(TCollection_ExtendedString('MDTV-XCAF'))
    tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    for name, shape in parts:
        label = tool.AddShape(shape, False)
        TDataStd_Name.Set_s(label, TCollection_ExtendedString(name))
    writer = STEPCAFControl_Writer()
    writer.SetNameMode(True)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    silence_messenger()
    with _quiet():
        ok = writer.Transfer(doc)
        status = str(writer.Write(str(path))) if ok else ''
    if not ok or not status.endswith('IFSelect_RetDone'):
        raise RuntimeError(f'could not write {path}')
    text = path.read_text(encoding='latin-1')
    text = re.sub(r"FILE_NAME\('[^']*','[^']*'", f"FILE_NAME('{path.stem}','{STEP_FIXED_TIME}'", text, count=1)
    path.write_text(text, encoding='latin-1')


def write_stl(shape, path: Path, linear_tol: float = STL_LINEAR_TOL_M, angular_tol: float = STL_ANGULAR_TOL_RAD) -> int:
    """Binary STL in metres (the mm shape is scaled by 0.001 before meshing). Returns the triangle count."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.gp import gp_Trsf
    from OCP.StlAPI import StlAPI_Writer
    t = gp_Trsf()
    t.SetScaleFactor(0.001)
    metres = BRepBuilderAPI_Transform(shape, t, True).Shape()
    BRepMesh_IncrementalMesh(metres, linear_tol, False, angular_tol, False)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    w = StlAPI_Writer()
    w.ASCIIMode = False
    if not w.Write(metres, str(path)):
        raise RuntimeError(f'could not write {path}')
    raw = path.read_bytes()
    path.write_bytes(b'RoboDE Studio binary STL, metres'.ljust(80, b' ') + raw[80:])   # fixed header
    return (len(raw) - 84) // 50


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
