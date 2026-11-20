import os
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / 'fixtures'


def pytest_collection_modifyitems(config, items):
    for item in items:
        if 'claude' in item.keywords and not os.environ.get('STUDIO_CLAUDE'):
            item.add_marker(pytest.mark.skip(reason='set STUDIO_CLAUDE=1 to call the real Claude CLI'))
        if 'web' in item.keywords and not os.environ.get('STUDIO_WEB'):
            item.add_marker(pytest.mark.skip(reason='set STUDIO_WEB=1 to make real network requests'))
