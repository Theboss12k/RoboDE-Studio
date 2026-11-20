"""Run the studio API: PYTHONPATH=server python -m studio.api  (STUDIO_HOST, STUDIO_PORT; default 127.0.0.1:8800)."""
import os

import uvicorn

from .app import create_app

uvicorn.run(create_app(), host=os.environ.get('STUDIO_HOST', '127.0.0.1'), port=int(os.environ.get('STUDIO_PORT', '8800')),
            log_level='info', timeout_graceful_shutdown=5)
