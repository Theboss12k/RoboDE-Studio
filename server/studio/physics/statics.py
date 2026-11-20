"""Static joint loads by the Jacobian transpose: tau = sum_i J_i(q)^T F_i, with F_i = (0, 0, -m_i g) for gravity.

Families call `gravity_torques` with the positional Jacobian of each mass point (link centres of mass, payload at
the TCP). The result is the torque (revolute) or force (prismatic) each joint must hold, sign included.
"""
import numpy as np

G = 9.80665


def joint_loads(jacobians, forces):
    """tau = sum J_i^T F_i. `jacobians`: list of (3, n) arrays; `forces`: list of (3,) world-frame forces in N."""
    if not jacobians:
        raise ValueError('No mass points')
    n = np.asarray(jacobians[0]).shape[1]
    tau = np.zeros(n)
    for J, F in zip(jacobians, forces, strict=True):
        tau += np.asarray(J, dtype=float).T @ np.asarray(F, dtype=float)
    return tau


def gravity_torques(jacobians, masses, g=G):
    """Torques/forces that hold point masses against gravity (the joint must supply -tau_gravity)."""
    return -joint_loads(jacobians, [np.array([0.0, 0.0, -m * g]) for m in masses])


def numeric_jacobian(point_fn, q, eps=1e-6):
    """(3, n) Jacobian of point_fn(q) -> (3,) by central differences. Families may use it for any mass point."""
    q = np.asarray(q, dtype=float)
    J = np.zeros((3, q.size))
    for i in range(q.size):
        dq = np.zeros_like(q)
        dq[i] = eps
        J[:, i] = (np.asarray(point_fn(q + dq)) - np.asarray(point_fn(q - dq))) / (2 * eps)
    return J
