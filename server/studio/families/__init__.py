"""Robot families (plug-ins implementing `base.Family`). Add a family: one module and one line below."""
from __future__ import annotations

from .gantry import Gantry
from .mobile_base import MobileBase
from .mobile_manipulator import MobileManipulator
from .serial_arm import SerialArm

REGISTRY = {f.name: f for f in (SerialArm(), Gantry(), MobileBase(), MobileManipulator())}


def get(name: str):
    try:
        return REGISTRY[name]
    except KeyError:
        raise KeyError(f'Unknown robot family {name!r}; available: {", ".join(REGISTRY)}') from None


def describe() -> list[dict]:
    return [dict(name=f.name, title=f.title, description=f.description, mobile=f.mobile,
                 params_schema=f.Params.model_json_schema()) for f in REGISTRY.values()]
