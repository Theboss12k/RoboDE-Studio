"""Geometry helpers of the fleet planner (re-exported so callers have one import)."""
from __future__ import annotations

from ..physics.requirements import enclosing_circle
from ..sim.collision import capsule_capsule_distance, segment_distance

__all__ = ['segment_distance', 'capsule_capsule_distance', 'enclosing_circle']
