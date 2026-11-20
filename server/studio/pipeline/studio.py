"""Robot studio build: size the joints, source motors, choose the structure style, generate the CAD and the twin limits.

Orchestration only. Every pass/fail number comes from code (`robot/sizing`, `robot/style` checks, `robot/cad`
checks); the models only propose (shop candidates, structure style) and code verifies them. See
docs/STUDIO_ROBOT_DESIGN.md.

Sourcing rules of this module:
* one shop search per build, from the needs of the first sizing, padded by NEED_MARGIN so that the motor and gearbox
  masses added by `iterate_sizing` do not make the found motors fail;
* a pin-only rebuild (`base_build` given, empty request, same design version) makes no search: the alternatives kept
  on the base build's motor choices are re-evaluated by `select`, and the base build's `search_date` is kept (the
  prices are as seen then);
* a change request is a hint for the search and style agents ("cheaper motors", "use machined aluminium"); code decides.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

from .. import families
from ..robot import sizing as sz
from ..robot import sourcing, style as style_mod, twin
from ..schemas import Check, DesignVersion, MotorAlternative, StudioBuild
from .fleet import member_view

NEED_MARGIN = 1.3                   # search needs are the first sizing's motor-side loads times this
FAMILIES = ('serial_arm', 'gantry', 'mobile_base')
MOTOR_KINDS = {'wheel': 'dc_gear'}   # everything else is searched as a stepper (the family rules of the design doc)


class Hinted:
    """An LLM adapter that appends the user's change request (a hint, not an instruction for code) to every input."""

    def __init__(self, inner, request: str):
        self.inner, self.request = inner, request

    def _hint(self, text):
        return f'{text}\n\nThe user asked for this change (a hint; code checks the result): {self.request}'

    async def invoke(self, input, system_prompt):
        return await self.inner.invoke(self._hint(input), system_prompt)

    async def invoke_multimodal(self, input, system_prompt, images):
        return await self.inner.invoke_multimodal(self._hint(input), system_prompt, images)

    def structured(self, schema):
        return Hinted(self.inner.structured(schema), self.request)

    def __getattr__(self, name):
        return getattr(self.inner, name)


class HintedLLMs:
    """`llms` with the `robot` role (the style agent) seeing the change request."""

    def __init__(self, llms, request: str):
        self.llms, self.request = llms, request

    def for_role(self, role):
        adapter = self.llms.for_role(role)
        return Hinted(adapter, self.request) if role == 'robot' and self.request else adapter

    def __getattr__(self, name):
        return getattr(self.llms, name)


def needs_of(version: DesignVersion, sizing, request: str) -> list[dict]:
    """The search needs: per joint the motor-side loads through its default drive, padded by NEED_MARGIN."""
    family = version.robot.family
    out = []
    for i, s in enumerate(sizing):
        side = sz.motor_side(s, sz.default_drive(s, family, i))
        out.append({'joint': s.joint, 'kind': MOTOR_KINDS.get(s.kind, 'stepper'), 'family': family,
                    'hint': f'{s.joint} ({s.kind}); {request}'.strip('; ') if request else f'{s.joint} ({s.kind})',
                    'motor_side': {'continuous_nm': round(side['continuous_nm'] * NEED_MARGIN, 4),
                                   'peak_nm': round(side['peak_nm'] * NEED_MARGIN, 4),
                                   'speed_rpm': round(side['speed_rpm'], 2)}})
    return out


def _alternatives(base_build: StudioBuild) -> dict[str, list[MotorAlternative]]:
    return {m.joint: [a.model_copy(deep=True) for a in m.alternatives] for m in base_build.motors}


def _summary(motors, parts, checks, request) -> str:
    shop = sum(1 for m in motors if m.source == 'shop')
    passed = sum(1 for c in checks if c.passed is True)
    failed = sum(1 for c in checks if c.passed is False)
    text = (f'{shop} of {len(motors)} motors from shops, {len(motors) - shop} defined for procurement; {len(parts)} parts; '
            f'{passed} of {len(checks)} checks passed' + (f', {failed} failed' if failed else '') + '.')
    return (text + (f' Change: {request}' if request else ''))[:2000]


def collect_checks(motors, electronics, style, cad_checks) -> list[Check]:
    """Sizing margins (the motor choices' checks), electronics, structure style and CAD checks."""
    return ([c for m in motors for c in m.checks] + [c for e in electronics for c in e.checks] + list(style.checks)
            + list(cad_checks))


async def build(llms, version: DesignVersion, member: str | None, base_build: StudioBuild | None, request: str,
                pinned: dict[str, str], build_no: int, out_dir: Path, progress, *, download=None,
                now=None, vendors=None) -> StudioBuild:
    """One studio build. `now` (tests) returns the datetime used for `created` and `search_date`. `vendors` are the
    user's preferred vendors (a list of catalog.vendors.Vendor); passed to the shop search only when there are any."""
    request = (request or '').strip()[:2000]
    pinned = dict(pinned or {})
    version = member_view(version, member)          # no member (or the primary): the top-level fields are the robot
    family = version.robot.family
    if family not in FAMILIES:
        raise ValueError(f'{family} is not in v1 of the robot studio')
    stamp = (now or (lambda: datetime.now(timezone.utc)))()

    progress('Sizing the joints')
    first = await asyncio.to_thread(sz.size_joints, version)
    reuse = (base_build is not None and not request and base_build.design_version == version.version
             and base_build.family == family)
    search_notes: list[str] = []
    if reuse:
        options, search_date = _alternatives(base_build), base_build.search_date
        search_notes.append(f'No new search: candidates and prices are from the search of {search_date}.')
    else:
        progress('Searching the shops')
        extra = {'vendors': vendors} if vendors else {}
        options = await sourcing.search_motors(llms, needs_of(version, first, request),
                                               stamp.date().isoformat(), progress, download=download, notes=search_notes,
                                               **extra)
        search_date = stamp.date().isoformat()

    def choose(sizing):
        return sourcing.select(sizing, options, family, pinned)       # never searches

    sizing, motors = await asyncio.to_thread(sz.iterate_sizing, version, choose)
    p = families.get(family).parse(version.robot.params)
    electronics = sz.electronics_for(motors, family, getattr(p, 'battery_wh', None) if family == 'mobile_base' else None)
    try:                                                # shop drivers and supplies replace the defined ones that pass
        if reuse:
            cands = sourcing.reuse_candidates(base_build.electronics)
        else:
            needs = sourcing.electronics_needs(electronics)
            cands = (await sourcing.search_electronics(llms, needs, stamp.date().isoformat(), progress, download=download,
                                                       notes=search_notes, **({'vendors': vendors} if vendors else {}))
                     if needs else {})
        electronics = sourcing.select_electronics(electronics, cands)
    except Exception as error:                          # optional step: the defined drivers and supplies stay
        search_notes.append(f'Electronics search failed ({type(error).__name__}): drivers and power supplies stay '
                            f'defined specs.')

    structure = await style_mod.choose_style(HintedLLMs(llms, request), version, motors, progress)

    from ..robot import cad
    if not cad.available():
        raise RuntimeError('The CAD kernel (cadquery-ocp) is not installed')
    from ..robot.cad import assemble, kernel
    kernel.silence_messenger()
    progress('Generating the CAD')
    cad_result = await asyncio.to_thread(assemble.build_cad, version, motors, structure, Path(out_dir), progress)
    limits = await asyncio.to_thread(twin.twin_limits, version, motors, cad_result.gripper)
    checks = collect_checks(motors, electronics, structure, cad_result.checks)
    return StudioBuild(
        build=build_no, design_version=version.version, member=member or None,
        parent_build=base_build.build if base_build else None,
        created=stamp.strftime('%Y-%m-%dT%H:%M:%SZ'), request=request, summary=_summary(motors, cad_result.parts, checks, request),
        family=family, search_date=search_date, sizing=sizing, motors=motors, electronics=electronics, style=structure,
        parts=cad_result.parts, urdf=cad_result.urdf, twin_limits=limits, checks=checks, files=cad_result.files,
        sourcing_notes=sourcing.sourcing_notes(motors, electronics, search_notes))

