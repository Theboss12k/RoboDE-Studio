"""Protocol output: JSON lines on a private dup of the real stdout fd, a heartbeat thread, error mapping."""
import json
import os
import sys
import threading
from pathlib import Path

from ..llm.base import LLMError

HEARTBEAT_S = 15.0
RESULT_INLINE_MAX = 256 * 1024      # bytes of serialised result data; larger results go to out_dir/result.json


class TaskError(Exception):
    """A failure with an explicit protocol kind (value | not_found | llm | internal) and optional code."""

    def __init__(self, kind, message, code=None):
        super().__init__(message)
        self.kind, self.code = kind, code


def classify(error):
    """(kind, code) of an exception, as docs/PROTOCOL.md maps them: ValueError -> value, FileNotFoundError and a
    KeyError whose message says "not found" -> not_found, LLMError -> llm (+ its code), anything else -> internal.
    A `code` attribute (DatasheetError.code = 'datasheet_download') is passed through."""
    code = getattr(error, 'code', None)
    code = code if isinstance(code, str) else None
    if isinstance(error, TaskError):
        return error.kind, error.code
    if isinstance(error, LLMError):
        return 'llm', code
    if isinstance(error, FileNotFoundError):
        return 'not_found', code
    if isinstance(error, KeyError) and 'not found' in str(error).lower():
        return 'not_found', code
    if isinstance(error, ValueError):
        return 'value', code
    return 'internal', code


def message_of(error):
    if isinstance(error, KeyError) and error.args:
        return str(error.args[0])
    return str(error) or error.__class__.__name__


class Emitter:
    """Writes protocol lines. `Emitter.install()` dups fd 1 for the protocol and points fd 1 and sys.stdout at stderr,
    so a stray print() (or a child process writing to fd 1) cannot corrupt the stream."""

    def __init__(self, stream, heartbeat_s=HEARTBEAT_S):
        self.stream, self.lock = stream, threading.Lock()
        self.heartbeat_s = heartbeat_s
        self.secrets = []
        self._stop = threading.Event()
        self._thread = None

    @classmethod
    def install(cls):
        heartbeat = float(os.environ.get('STUDIO_HEARTBEAT_S') or HEARTBEAT_S)
        stream = os.fdopen(os.dup(1), 'w', encoding='utf-8', buffering=1)
        sys.stdout.flush()
        os.dup2(2, 1)
        sys.stdout = sys.stderr
        return cls(stream, heartbeat)

    def redact(self, text):
        for secret in self.secrets:
            if secret and len(secret) >= 4:
                text = text.replace(secret, '[redacted]')
        return text

    def line(self, obj):
        data = json.dumps(obj, ensure_ascii=False, default=str, separators=(',', ':'))
        with self.lock:
            try:
                self.stream.write(data + '\n')
                self.stream.flush()
            except (BrokenPipeError, ValueError):
                pass

    def progress(self, text):
        self.line({'type': 'progress', 'text': self.redact(str(text))})

    def usage(self, role, profile, entry):
        self.line({'type': 'usage', 'role': role, 'profile': profile,
                   'entry': {k: v for k, v in entry.items() if k != 'pages_opened'}})

    def start_heartbeat(self):
        def beat():
            while not self._stop.wait(self.heartbeat_s):
                self.line({'type': 'heartbeat'})
        self._thread = threading.Thread(target=beat, name='heartbeat', daemon=True)
        self._thread.start()

    def stop_heartbeat(self):
        self._stop.set()

    def result(self, data, out_dir):
        text = json.dumps(data, ensure_ascii=False, default=str, separators=(',', ':'))
        if len(text.encode('utf-8')) > RESULT_INLINE_MAX:
            path = Path(out_dir) / 'result.json'
            path.write_text(text, encoding='utf-8')
            self.line({'type': 'result', 'file': 'result.json'})
        else:
            self.line({'type': 'result', 'data': json.loads(text)})

    def error(self, error):
        kind, code = classify(error)
        self.line({'type': 'error', 'kind': kind, 'code': code, 'message': self.redact(message_of(error))})
