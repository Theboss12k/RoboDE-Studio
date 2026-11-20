"""A tiny stand-in for `python -m studio.tasks <task>` that speaks docs/PROTOCOL.md (used by TaskRunnerTest)."""
import json
import os
import subprocess
import sys
import time

task = sys.argv[1]
envelope = json.load(sys.stdin)
inp = envelope["input"]
out_dir = envelope["out_dir"]


def emit(obj):
    sys.__stdout__.write(json.dumps(obj) + "\n")
    sys.__stdout__.flush()


if inp.get("dump"):  # lets the test inspect what the process was given
    with open(inp["dump"], "w") as f:
        json.dump({"argv": sys.argv, "env": dict(os.environ), "cwd": os.getcwd()}, f)

print("a stray non-JSON line")
sys.__stdout__.write("{not json at all\n")
emit({"type": "progress", "text": "Running the task: 12 waypoints"})
emit({"type": "usage", "role": "spec", "profile": "claude_opus",
      "entry": {"model": "m", "input": 3, "output": 7, "cost_usd": 0.01, "pages_opened": [1]}})
emit({"type": "heartbeat"})

if task == "ok":
    emit({"type": "result", "data": {"echo": inp, "task": envelope["task"], "context": envelope["context"],
                                     "roles": sorted(envelope["llm"]["roles"])}})
elif task == "file":
    with open(os.path.join(out_dir, "result.json"), "w") as f:
        json.dump({"big": True}, f)
    emit({"type": "result", "file": "result.json"})
elif task == "leak":  # prints the api key on stderr; the runner must redact it from the log
    key = envelope["llm"]["roles"]["spec"]["api_key"]
    sys.stderr.write("calling with " + key + "\n")
    emit({"type": "result", "data": {}})
elif task == "error":
    emit({"type": "error", "kind": inp["kind"], "code": inp.get("code"), "message": "boom " + inp["kind"]})
    sys.exit(1)
elif task == "crash":
    sys.stderr.write("Traceback: something\n")
    sys.exit(3)
elif task == "hang":
    child = subprocess.Popen(["sleep", "300"])
    with open(inp["pidfile"], "w") as f:
        f.write(f"{os.getpid()} {child.pid}")
    time.sleep(300)
