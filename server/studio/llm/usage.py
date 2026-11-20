"""Token usage ledger: one JSON line per model call in runtime/llm/usage.jsonl.

Adapters report usage through an `on_usage(entry)` callback that the registry attaches with the role and profile.
What the call was for (robot, task) comes from a context variable set by the API around each job or test run, so
adapters stay independent of robots and tasks.

Entry: {at, role, profile, model, robot_id, task, input, cache_write, cache_read, output, cost_usd, seconds,
        images, turns, structured, ok}
Token fields follow the provider's own counting: `input` is uncached input, `cache_write` input written to the
prompt cache, `cache_read` input served from it. `cost_usd` is the provider's estimate at list prices; on a
subscription plan it is an equivalent figure, not a charge.
"""
import contextvars
import json
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

usage_context = contextvars.ContextVar('usage_context', default={})
TOKENS = ('input', 'cache_write', 'cache_read', 'output')


def set_context(**fields):
    """Label every model call made from here on (in this task and the tasks it starts)."""
    usage_context.set({**usage_context.get(), **fields})


class UsageLedger:
    def __init__(self, runtime):
        self.path = runtime / 'llm' / 'usage.jsonl'
        self.lock = threading.Lock()

    def record(self, role, profile, entry):
        context = usage_context.get()
        line = {'at': datetime.now(timezone.utc).isoformat(timespec='seconds'), 'role': role, 'profile': profile,
                'robot_id': context.get('robot_id'), 'task': context.get('task'),
                **{k: v for k, v in entry.items() if k != 'pages_opened'}}
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open('a') as f:
                f.write(json.dumps(line) + '\n')

    def entries(self, since=None):
        if not self.path.exists():
            return []
        result = []
        for line in self.path.read_text().splitlines():
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if since is None or entry['at'] >= since:
                result.append(entry)
        return result

    def summary(self, period='7d', recent=40):
        now = datetime.now(timezone.utc)
        since = {'today': now.replace(hour=0, minute=0, second=0, microsecond=0), '7d': now - timedelta(days=7),
                 '30d': now - timedelta(days=30)}.get(period)
        entries = self.entries(since.isoformat(timespec='seconds') if since else None)

        def empty():
            return {'calls': 0, **{k: 0 for k in TOKENS}, 'cost_usd': 0.0, 'seconds': 0.0, 'images': 0, 'web_calls': 0}

        def add(bucket, e):
            bucket['calls'] += 1
            for k in TOKENS:
                bucket[k] += e.get(k) or 0
            bucket['cost_usd'] = round(bucket['cost_usd'] + (e.get('cost_usd') or 0), 6)
            bucket['seconds'] = round(bucket['seconds'] + (e.get('seconds') or 0), 1)
            bucket['images'] += e.get('images') or 0
            bucket['web_calls'] += e.get('web_calls') or 0
        totals = empty()
        groups = {name: defaultdict(empty) for name in ('role', 'model', 'robot_id', 'task', 'day')}
        for e in entries:
            add(totals, e)
            for name, group in groups.items():
                key = e['at'][:10] if name == 'day' else (e.get(name) or 'other')
                add(group[key], e)
        input_total = totals['input'] + totals['cache_write'] + totals['cache_read']
        totals['cache_hit_rate'] = round(totals['cache_read'] / input_total, 3) if input_total else None
        return {'period': period, 'totals': totals,
                **{f'by_{name}': dict(sorted(group.items())) for name, group in groups.items()},
                'recent': entries[-recent:][::-1]}

    def reset(self):
        """Start a new ledger; the old one is kept beside it with a timestamp."""
        with self.lock:
            if self.path.exists():
                self.path.rename(self.path.with_name(f'usage-{int(time.time())}.jsonl'))
