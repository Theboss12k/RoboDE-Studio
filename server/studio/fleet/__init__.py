"""Pure fleet planner (2-3 fixed-base robots sharing one task): assignment of the task's actions to members, cell
layout, member sub-specifications, shared-clock schedule, robot-robot clearance and fleet checks.

No LLM, no store, no I/O. Handovers go through a station the code generates (the giver places the object on a stand,
the receiver picks it from there). Numbers that decide pass/fail come from the kinematics code (families/, sim/).
"""
