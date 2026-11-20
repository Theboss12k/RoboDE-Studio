"""Shared test vectors for the digital-twin math (server/studio/robot/twin.py and packages/core/src/twin.ts).

    .venv/bin/python scripts/twin_vectors.py          # rewrite contracts/twin_vectors.json
    .venv/bin/python scripts/twin_vectors.py --check  # exit 1 when the committed file is stale

Chains come from the bottle-task serial arm and the pallet-task gantry (family sizing, deterministic). The TS side
(`node scripts/twin-parity.mjs`) replays every vector and compares with the stored Python results.
"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'server'), str(ROOT / 'tests')]

from fixtures_specs import bottle_spec, pallet_spec  # noqa: E402

from studio import families, physics  # noqa: E402
from studio.robot import twin  # noqa: E402

OUT = ROOT / 'contracts' / 'twin_vectors.json'
ND = 9


def r(x):
    """JSON-ready: nested lists of floats rounded to ND decimals."""
    if isinstance(x, np.ndarray):
        return r(x.tolist())
    if isinstance(x, (list, tuple)):
        return [r(v) for v in x]
    if isinstance(x, dict):
        return {k: r(v) for k, v in x.items()}
    if isinstance(x, np.bool_):
        return bool(x)
    if isinstance(x, (float, np.floating)):
        return round(float(x), ND) + 0.0
    return x


def chain_of(family, spec):
    base = physics.place_base(spec)
    reqs = physics.derive(spec, base)
    fam = families.get(family)
    p = fam.size_from_physics(reqs, spec, {})
    return fam, p, twin.chain_for(family, p)


def lim(fam, p, scale=1.0):
    out = []
    for j in fam.joints(p):
        v = j.max_velocity * scale
        out.append(dict(lower=j.lower, upper=j.upper, max_speed=v, max_accel=v / 0.25, max_torque=j.max_effort))
    return out


def ik_case(name, chain, target, q0, **kw):
    res = twin.ik_solve(chain, target, q0, **kw)
    return dict(name=name, chain=chain, target=target, q0=list(q0), opts=dict(toolDown=kw.get('tool_down', True)),
                expected=dict(q=res['q'], ok=res['ok'], posError=res['pos_error'], toolDown=res['tool_down']),
                compareQ=bool(res['ok']))


def clamp_case(name, limits, q0, target, steps, dt=0.02, scale=1.0, cap=None, qd0=None):
    q, qd = np.array(q0, float), np.array(qd0 if qd0 is not None else np.zeros(len(q0)))
    L = [SimpleNamespace(**d) for d in limits]
    seq = []
    for _ in range(steps):
        q, qd, hits = twin.clamp_step(q, target, dt, L, qd, scale, cap)
        seq.append(dict(q=q, qd=qd, hits=sorted(f'{i}:{k}' for i, k in hits)))
    return dict(name=name, limits=limits, q0=list(q0), qd0=list(qd0 if qd0 is not None else [0.0] * len(q0)),
                target=list(target), dt=dt, speedScale=scale, accelCap=cap, steps=seq)


def build():
    arm_fam, arm_p, arm = chain_of('serial_arm', bottle_spec())
    gan_fam, gan_p, gan = chain_of('gantry', pallet_spec())
    nq, ng = len([e for e in arm['elements'] if e['type'] != 'fixed']), 3
    q_arm = [0.4, 0.6, -1.1, 0.5, 0.2, -0.3][:nq]
    fk = [dict(name='arm', chain=arm, q=q_arm, tcp=twin.chain_fk(arm, q_arm)[:3, 3].tolist()),
          dict(name='gantry', chain=gan, q=[0.3, -0.2, 0.15][:ng],
               tcp=twin.chain_fk(gan, [0.3, -0.2, 0.15][:ng])[:3, 3].tolist())]
    q_a = [0.5, 0.7, 0.9, np.pi - 1.6, 0.0, 0.0][:nq]
    q_b = [-0.4, 0.5, 1.2, np.pi - 1.7, 0.0, 0.0][:nq]
    ik = [ik_case('arm tool-down point', arm, twin.chain_fk(arm, q_a)[:3, 3].tolist(), [0.0] * nq),
          ik_case('arm second point from a bent pose', arm, twin.chain_fk(arm, q_b)[:3, 3].tolist(),
                  [0.5, 0.4, -0.6, 0.3, 0.0, 0.0][:nq]),
          ik_case('arm unreachable', arm, [3.0, 0.0, 0.5], [0.0] * nq),
          ik_case('gantry point', gan, twin.chain_fk(gan, [0.4, -0.1, 0.3])[:3, 3].tolist(), [0.0] * ng),
          ik_case('gantry outside the strokes', gan, [30.0, 0.0, 0.9], [0.0] * ng)]
    g3 = lim(gan_fam, gan_p)[:3]
    a3 = lim(arm_fam, arm_p)[:3]
    clamp = [clamp_case('arm joint move', a3, [0, 0, 0], [0.9, -0.5, 0.3], 60),
             clamp_case('speed_scale 1.5 binds speed and acceleration', a3, [0, 0, 0], [1.5, 1.0, -1.0], 50, scale=1.5),
             clamp_case('target beyond the range', a3, [0, 0, 0], [9.0, 0.2, 0.0], 40),
             clamp_case('per-joint scales', a3, [0, 0, 0], [1.0, 1.0, 1.0], 40, scale=[1.2, 0.6, 0.3]),
             clamp_case('torque cap on the acceleration', a3, [0, 0, 0], [1.0, 0.8, -0.8], 40, cap=[2.0, 1.0, 0.5]),
             clamp_case('gantry axes', g3, [0, 0, 0], [0.5, -0.3, 0.2], 80)]
    torque = []
    for name, chain, q, pay, qdd in (('arm stretched', arm, [0.0, 1.5708, 0.0, 0.0, 0.0, 0.0][:nq], 0.3, [1.0, 2.0, -1.0, 0.5, 0.0, 0.0][:nq]),
                                      ('gantry', gan, [0.1, 0.1, 0.2], 5.0, [1.0, 1.0, 2.0])):
        g, inertia = twin.gravity_inertia(chain, q, pay)
        mt = [10.0] * len(q)
        torque.append(dict(name=name, chain=chain, q=q, payload=pay, qdd=qdd, maxTorque=mt, gravity=g, inertia=inertia,
                           use=twin.torque_use(g, inertia, qdd, mt),
                           cap=twin.accel_cap_from_torque(g, inertia, mt, [5.0] * len(q))))
    return dict(version=1, note='Generated by scripts/twin_vectors.py; do not edit.', fk=fk, ik=ik, clamp=clamp,
                torque=torque)


def render():
    return json.dumps(r(build()), indent=1) + '\n'


if __name__ == '__main__':
    text = render()
    if '--check' in sys.argv:
        if not OUT.exists() or OUT.read_text() != text:
            print('contracts/twin_vectors.json is stale: run .venv/bin/python scripts/twin_vectors.py')
            sys.exit(1)
        print('contracts/twin_vectors.json is up to date')
    else:
        OUT.write_text(text)
        print(f'wrote {OUT.relative_to(ROOT)}')
