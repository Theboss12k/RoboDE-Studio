"""Small helpers shared by every module: IDs, timestamps and crash-safe JSON files."""
import json
import os
import re
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

ID = re.compile(r'^[a-z][a-z0-9_]{0,62}$')


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def slug(text, fallback='robot'):
    value = re.sub(r'[^a-z0-9]+', '_', text.lower()).strip('_')
    if not value or not value[0].isalpha():
        value = f'{fallback}_{value}' if value else fallback
    return value[:48]


def safe_id(value):
    if not isinstance(value, str) or not ID.match(value):
        raise ValueError('Invalid identifier')
    return value


def safe_name(filename):
    """A plain file name (no folders) that is safe to store."""
    name = Path(str(filename).replace('\\', '/')).name.strip()
    name = re.sub(r'[^A-Za-z0-9._ -]+', '_', name)[:160]
    if not name or name in {'.', '..'} or name.startswith('.'):
        raise ValueError('Invalid file name')
    return name


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except FileNotFoundError:
        return default


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix='.tmp-')
    with os.fdopen(fd, 'w') as f:
        json.dump(data, f, indent=2)
    os.replace(temp, path)


def monotonic():
    return time.monotonic()
