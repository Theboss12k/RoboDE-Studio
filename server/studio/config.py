"""Paths. Every value can be overridden with an environment variable."""
import os
from pathlib import Path

STUDIO_ROOT = Path(__file__).resolve().parents[2]
RUNTIME = Path(os.environ.get('STUDIO_RUNTIME', STUDIO_ROOT / 'runtime')).resolve()
