"""Shared fleet clock: start offsets of the members, robot-robot clearance over time and a deterministic delay fix.

Each member's simulation runs on its own clock from 0; on the shared clock it starts at its offset and, outside its
run, holds its first frame (before) or its last frame (after). One item is checked; the overlap of consecutive items
in steady state is not checked.
"""
from __future__ import annotations

import math
import time

import numpy as np

from .. import families
from ..schemas import FleetAssignment, FleetMember, SimEvent
from ..sim.collision import capsule_capsule_distance
from .subspec import handover_pick_label, handover_place_label, handover_roles

MAX_EVENTS = 20
MAX_DELAY_STEPS = 400


def _phase_span(sim, label):
    """(start, end) of a phase on the member clock: end = time of the first frame after the phase's last frame."""
    idx = [k for k, f in enumerate(sim.frames) if f.phase == label]
    if not idx:
        return None
    k = idx[-1]
    end = sim.frames[k + 1].t if k + 1 < len(sim.frames) else sim.frames[k].t
    return sim.frames[idx[0]].t, end


def offsets(members: list[FleetMember], assignment: FleetAssignment) -> dict[str, float]:
    """Start of every member on the shared clock: givers first; a receiver starts when its giver's frames leave the
    place-at-handover phase (the latest of its givers). Members without an incoming handover start at 0."""
    by = {m.id: m for m in members}
    off = dict.fromkeys(by, 0.0)
    for _ in range(len(members)):              # relax handover chains (r1 -> r2 -> r3)
        for h in assignment.handovers:
            g = by.get(h.from_member)
            if g is None or h.to_member not in off:
                continue
            span = _phase_span(g.sim, handover_place_label(h.object, h.id))
            end = span[1] if span else g.sim.duration_s
            off[h.to_member] = max(off[h.to_member], off[g.id] + end)
    return off


class _Track:
    """A member's motion sampled on the shared clock: world capsules at any time."""

    def __init__(self, m: FleetMember, offset: float):
        self.id, self.offset = m.id, offset
        self.fam = families.get(m.robot.family)
        self.p = self.fam.parse(m.robot.params)
        self.names = [j.name for j in self.fam.joints(self.p) if j.actuated]
        fr = m.sim.frames
        self.t = np.array([f.t for f in fr], float)
        self.q = np.array([[f.joints.get(n, 0.0) for n in self.names] for f in fr], float)
        self.phase = [f.phase for f in fr]
        self.radii = self.fam.chain_radii(self.p)
        b = m.robot.base
        self.x, self.y, self.z = (float(v) for v in b.position)
        self.c, self.s = math.cos(b.yaw), math.sin(b.yaw)

    def at(self, t_shared):
        """(world chain points, phase) at shared time t: interpolated joints, held before and after the run."""
        tau = t_shared - self.offset
        if len(self.t) == 0:
            q, ph = np.zeros(len(self.names)), ''
        elif tau <= self.t[0]:
            q, ph = self.q[0], self.phase[0]
        elif tau >= self.t[-1]:
            q, ph = self.q[-1], self.phase[-1]
        else:
            k = int(np.searchsorted(self.t, tau, side='right')) - 1
            t0, t1 = self.t[k], self.t[k + 1]
            u = (tau - t0) / (t1 - t0) if t1 > t0 else 0.0
            q, ph = self.q[k] + u * (self.q[k + 1] - self.q[k]), self.phase[k + 1]   # a frame's phase names the move into it
        pts = [np.array([self.x + self.c * a[0] - self.s * a[1], self.y + self.s * a[0] + self.c * a[1], a[2] + self.z])
               for a in self.fam.chain_points(self.p, q)]       # same transform as sim/task.py
        return pts, ph

    def end(self):
        return self.offset + (float(self.t[-1]) if len(self.t) else 0.0)


def _clearance(members, offs, dt):
    tracks = [_Track(m, offs.get(m.id, 0.0)) for m in members]
    T = max((tr.end() for tr in tracks), default=0.0)
    times = list(np.arange(0.0, T, dt)) + [T]
    active, hits, events, gap, first = set(), 0, [], math.inf, None
    for t in times:
        states = [tr.at(t) for tr in tracks]
        now = set()
        for x in range(len(tracks)):
            for y in range(x + 1, len(tracks)):
                (pa, fa), (pb, fb) = states[x], states[y]
                ra, rb = tracks[x].radii, tracks[y].radii
                for i in range(len(pa) - 1):
                    for j in range(len(pb) - 1):
                        d = capsule_capsule_distance(pa[i], pa[i + 1], ra[i], pb[j], pb[j + 1], rb[j])
                        gap = min(gap, d)
                        if d <= 0:
                            now.add((x, i, y, j))
        for key in sorted(now - active):
            x, i, y, j = key
            hits += 1
            if first is None:
                first = (tracks[x].id, tracks[y].id)
            if len(events) < MAX_EVENTS:
                events.append(SimEvent(t=float(t), kind='collision', text=(
                    f'Robot {tracks[x].id} segment {i + 1} collides with robot {tracks[y].id} segment {j + 1} during '
                    f'"{states[x][1]}" / "{states[y][1]}"')))
        active = now
    return hits, events, (float(gap) if math.isfinite(gap) else math.inf), first


def robot_clearance(members: list[FleetMember], offsets: dict[str, float], dt: float = 0.08
                    ) -> tuple[int, list[SimEvent], float]:
    """Robot-robot capsule check on the shared clock: (contact episodes, collision events capped at MAX_EVENTS,
    smallest gap in m; negative = overlap). An episode is a new contact of one segment pair."""
    hits, events, gap, _ = _clearance(members, offsets, dt)
    return hits, events, gap


def _links(members):
    """[(handover id, giver, receiver)] read from the member specs."""
    give, take = {}, {}
    for m in members:
        for hid, (kind, obj) in handover_roles(m.spec).items():
            (give if kind == 'give' else take)[hid] = (m, obj)
    return [(hid, give[hid][0], take[hid][0], give[hid][1]) for hid in sorted(give) if hid in take]


def _buffer_ok(members, offs, cycle_s, links):
    """One-item station buffer: the receiver must have lifted item n before the giver puts item n + 1 down (one cycle
    later). Members without an incoming handover may start at most one cycle late."""
    receivers = {r.id for _, _, r, _ in links}
    for hid, g, r, obj in links:
        put = _phase_span(g.sim, handover_place_label(obj, hid))
        lift = _phase_span(r.sim, handover_pick_label(obj, hid))
        if put and lift and offs[r.id] + lift[1] > offs[g.id] + cycle_s + put[0] + 1e-9:
            return False
    return all(offs[m.id] <= cycle_s + 1e-9 for m in members if m.id not in receivers)


PROGRESS_EVERY_S = 10.0       # resolve_by_delay reports at most this long apart while it works


def resolve_by_delay(members: list[FleetMember], offsets: dict[str, float], cycle_s: float, step: float = 0.2,
                     progress=None) -> tuple[dict[str, float], list[str]]:
    """Delay the later robot of the first colliding pair (and the robots it hands on to) in `step` increments until
    the robots stay clear, or until a further delay would break the cycle: a receiver would lift an item later than
    one cycle after its giver started putting it down (one-item station buffer). Returns (offsets, log lines).
    `progress(text)`, when given, gets a line at least every PROGRESS_EVERY_S seconds of work."""
    off = dict(offsets)
    last = time.monotonic()
    links = _links(members)
    order = [m.id for m in members]
    downstream = {m.id: set() for m in members}
    for _ in members:
        for _, g, r, _ in links:
            downstream[g.id] |= {r.id} | downstream[r.id]
    hits, _, _, first = _clearance(members, off, 0.08)
    if hits == 0:
        return off, []
    log = [f'{hits} robot-robot contact episode(s) with the planned start times']
    delayed: dict[str, float] = {}
    for _ in range(MAX_DELAY_STEPS):
        a, b = first
        later = max((a, b), key=lambda i: (off[i], order.index(i)))
        trial = dict(off)
        for i in {later} | downstream[later]:
            trial[i] = off[i] + step
        if not _buffer_ok(members, trial, cycle_s, links):
            log.append(f'Stopped: delaying {later} further would break the {cycle_s:g} s cycle '
                       f'(one-item handover station); {hits} contact episode(s) remain')
            break
        off = trial
        delayed[later] = delayed.get(later, 0.0) + step
        hits, _, _, first = _clearance(members, off, 0.08)
        if progress is not None and time.monotonic() - last >= PROGRESS_EVERY_S:
            last = time.monotonic()
            progress(f'Delaying {later} to clear the robots: {delayed[later]:.1f} s so far, {hits} contact episode(s) left')
        if hits == 0:
            break
    for i, d in delayed.items():
        log.append(f'Delayed {i} by {d:.1f} s (start at {off[i]:.2f} s on the shared clock)')
    log.append('Robot-robot contact cleared by delaying' if hits == 0 else
               f'{hits} robot-robot contact episode(s) remain after delaying')
    return off, log


def fleet_period(members: list[FleetMember]) -> float:
    """Steady-state period with a one-item station buffer: the slowest member's run."""
    return max((m.sim.duration_s for m in members), default=0.0)
