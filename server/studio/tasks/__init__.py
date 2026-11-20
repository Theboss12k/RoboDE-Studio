"""Subprocess entry points of the Java <-> Python task protocol (docs/PROTOCOL.md).

Run one task per process: `python -m studio.tasks <task>` with PYTHONPATH=server and the JSON request on stdin.
"""
from .runner import main

__all__ = ['main']
