"""Parametric CAD for the robot studio (OpenCascade through `cadquery-ocp`, imported lazily).

`available()` says whether OCP can be imported; the rest of the package imports without it.
Units: kernel shapes are in millimetres (CAD convention, STEP is written in mm); STL files and the URDF are in
metres. Parts are built in their URDF link frame.
"""
from __future__ import annotations


def available() -> bool:
    """True when OpenCascade (`cadquery-ocp`) can be imported."""
    try:
        import OCP  # noqa: F401
        return True
    except Exception:
        return False
