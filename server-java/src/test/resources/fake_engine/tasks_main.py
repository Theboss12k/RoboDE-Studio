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


# ---- minimal stand-ins for the design flow and the fleet tasks (used by FleetApiTest) ----
URDF = '<robot name="fake"><link name="base"/></robot>'
PARSED = {"links": [{"name": "base"}], "joints": []}


def fake_version(title, urdf=URDF):
    return {"schemaVersion": 1, "version": 0, "parent_version": None, "created": "", "approved": False,
            "spec": {"title": title}, "requirements": {"ok": True}, "scene": {"objects": []},
            "robot": {"family": "serial_arm", "name": "Fake arm", "params": {}}, "urdf": urdf,
            "sim": {"ok": True, "duration_s": 4.0, "checks": [], "events": [], "frames": [[0, 0]]}}


def wait_for_release(note):  # limits.note "wait:<file>" blocks the task until the test creates the file
    if isinstance(note, str) and note.startswith("wait:"):
        end = time.time() + 60
        while not os.path.exists(note[5:]) and time.time() < end:
            time.sleep(0.05)


if task == "design":
    version = fake_version("Fake design")
    data = {"status": "designed", "round": 1, "version": version, "parsed": PARSED}
    if "FLEET_MARKER" in inp.get("brief", ""):
        data["fleet_advice"] = {"robots_needed": 2, "reasons": ["The reach is too long for one arm"], "source": "spec"}
    emit({"type": "result", "data": data})
elif task == "revise":
    base = inp["base"]
    emit({"type": "result", "data": {"version": {**base, "parent_version": base["version"]}, "parsed": PARSED}})
elif task == "export_assets":
    if "glb" in inp.get("want", []):
        with open(os.path.join(out_dir, "design.glb"), "wb") as f:
            f.write(b"glTF-fake")
        emit({"type": "result", "data": {}})
    else:
        emit({"type": "result", "data": {"parsed": PARSED}})
elif task == "fleet_assign":
    given = inp.get("assignment")
    assignment = given or {"members": [{"id": "r1", "actions": ["a1"]}, {"id": "r2", "actions": ["a2"]}],
                           "handovers": [], "source": "auto"}
    emit({"type": "result", "data": {"assignment": assignment, "feasible": True, "notes": [],
                                     "preview": {"bases": {"r1": [0, 0, 0], "r2": [1, 0, 0]}, "handovers": [], "parks": {},
                                                 "workspace_samples": {}}}})
elif task == "fleet_design":
    wait_for_release(inp["limits"].get("note"))
    base = dict(inp["base"])
    members = [{"id": rid, "name": "Member " + rid, "spec": base["spec"], "requirements": base["requirements"],
                "robot": base["robot"], "urdf": base["urdf"], "sim": base["sim"], "t_offset_s": 0.0,
                "limit_checks": []} for rid in ("r1", "r2")]
    base["parent_version"] = inp["base"]["version"]
    base["fleet"] = {"task": base["spec"], "limits": inp["limits"], "assignment": inp["assignment"], "members": members,
                     "primary": "r1", "checks": [], "events": [], "duration_s": 4.0, "period_s": None, "ok": True,
                     "trigger": {"reasons": []}, "planner_log": []}
    emit({"type": "result", "data": {"version": base, "parsed": PARSED, "fleet_parsed": {"r1": PARSED, "r2": PARSED}}})


# ---- robot studio stand-ins (used by StudioApiTest; fixture answers, not a model evaluation) ----
def build_file(path, kind, data=b"x"):
    return {"path": path, "kind": kind, "sha256": "0" * 64, "bytes": len(data)}


def vendor_note(inp):  # the preferred vendors the server passed, so a test can see them
    domains = [v["domain"] for v in inp.get("preferred_vendors") or []]
    return "; vendors " + ",".join(domains) if domains else ""


def fake_find(inp):
    domains = [v["domain"] for v in inp.get("preferred_vendors") or []]
    return {"candidates": [{"id": "fake_one", "manufacturer": "Fake", "model": "vendors " + ",".join(domains), "family": "serial_arm",
                            "product_url": "https://fake.example/p", "image_url": "", "datasheet_url": "", "price": None,
                            "payload_kg": None, "reach_m": None, "dof": None, "repeatability_mm": None, "key_specs": [],
                            "confidence": "medium", "screen": []}], "dropped": {}}


def fake_studio_build(inp):
    base = inp.get("base_build")
    pinned = inp.get("pinned") or {}
    return {
        "build": inp["build"], "design_version": inp["version"]["version"], "member": inp.get("member"),
        "parent_build": base["build"] if base else None, "created": "2026-01-01T00:00:00Z",
        "request": inp.get("request") or "",
        "summary": "Fake build; pinned " + ",".join(sorted(pinned)) + vendor_note(inp),
        "family": "serial_arm", "sizing": [], "motors": [],
        "electronics": [], "style": {"style": "machined_aluminium", "proposed": "machined_aluminium", "reasons": [], "forced": False, "checks": []},
        "parts": [{"id": "p1", "name": "Base plate", "qty": 1, "material": "6061", "process": "machined", "source": "custom",
                   "mass_kg": 0.5, "files": [build_file("parts/p1.step", "step"), build_file("parts/p1.stl", "stl")]}],
        "urdf": URDF, "twin_limits": {"joints": [], "gripper": None, "dt_s": 0.02, "accel_ramp_s": 0.2}, "checks": [],
        "files": [build_file("assembly.step", "step"), build_file("urdf/robot.urdf", "urdf")],
        "approved": False, "approved_at": None,
    }


def write_out(rel, data):
    path = os.path.join(out_dir, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


def twin_result(scale=1.0):
    return {"frames": [{"t": 0.0, "joints": {"j1": 0.0}}, {"t": 1.0, "joints": {"j1": 0.5}}], "reached": True,
            "duration_s": 1.0 / scale, "limits": [], "torque_use": {"j1": 0.4}, "ik_error_m": None}


if task == "find":
    emit({"type": "result", "data": fake_find(inp)})
elif task == "studio_build":
    wait_for_release(inp.get("request"))
    for rel in ("assembly.step", "parts/p1.step", "parts/p1.stl", "urdf/robot.urdf"):
        write_out(rel, b"fake " + rel.encode())
    emit({"type": "result", "data": {"build": fake_studio_build(inp), "parsed": {
        "name": "fake", "base_link": "base", "links": ["base"], "joints": [], "geometry": [], "mesh_uris": []}}})
elif task == "studio_twin_move":
    if not isinstance(inp.get("version"), dict) or not isinstance(inp.get("build"), dict):
        emit({"type": "error", "kind": "value", "code": None, "message": "input.version is required"})
        sys.exit(1)
    if (inp["request"].get("target") or {}).get("cartesian") == [9, 9, 9]:
        emit({"type": "error", "kind": "value", "code": None, "message": "The target is out of reach"})
        sys.exit(1)
    emit({"type": "result", "data": twin_result(inp["request"].get("speed_scale") or 1.0)})
elif task == "studio_run_task":
    emit({"type": "result", "data": twin_result(inp["speed_scale"])})
elif task == "studio_pdf":
    import zipfile
    write_out("manufacturer.pdf", b"%PDF-1.4 fake")
    with zipfile.ZipFile(os.path.join(out_dir, "package.zip"), "w") as z:
        z.writestr("bom.csv", "id,name\np1,Base plate\n")
    write_out("bom.csv", b"id,name\np1,Base plate\n")
    emit({"type": "result", "data": {"pdf": "manufacturer.pdf", "package": "package.zip", "snapshots_seen": [s["view"] for s in inp["snapshots"]],
                                     "files": [build_file("manufacturer.pdf", "pdf"), build_file("package.zip", "zip"), build_file("bom.csv", "csv")]}})


# ---- Ask bar stand-in (used by AskApiTest; canned proposals by question keyword, not a model answer) ----
def fake_ask(inp):
    q = inp["question"]
    g = inp.get("grounding") or {}
    out = {"answer": "Canned answer.", "citations": [{"kind": "version", "ref": "1", "label": "v1"},
                                                     {"kind": "weird", "ref": "x", "label": "x"}],
           "confirm": [], "navigate": None, "proposals": []}
    if "GROUNDING" in q:  # lets the test see what the server put into the grounding
        text = json.dumps(g)
        out["answer"] = json.dumps({"bytes": len(text), "keys": sorted(g), "has_frames": '"frames"' in text,
                                    "project_keys": sorted((g.get("project") or {}).keys()),
                                    "version_keys": sorted((g.get("version") or {}).keys()),
                                    "context": inp.get("context"), "history": len(inp.get("history") or []),
                                    "moment": (g.get("version") or {}).get("moment"),
                                    "events": len((g.get("version") or {}).get("events") or []),
                                    "sizes": {k: len(json.dumps(v)) for k, v in (g.get("version") or {}).items()}})
    if "REVISE" in q:
        out["proposals"] = [{"kind": "revise", "args": {"request": "make the reach 20 cm longer", "ignored": 1}}]
    if "DELETE" in q:
        out["proposals"] = [{"kind": "delete_project", "args": {}}]
    if "THREE" in q:
        out["proposals"] = [{"kind": "search", "args": {}}, {"kind": "update_brief", "args": {"text": "A brand new brief for the job"}},
                            {"kind": "set_baseline", "args": {"operators": 2}}]
    if "BAD" in q:
        out["proposals"] = [{"kind": "set_baseline", "args": {"operators": 0}}, {"kind": "analyse", "args": {"candidate": "nobody"}}]
    if "APPROVE" in q:
        out["confirm"] = [{"kind": "approve", "label": "Approve v1", "request": {"version": 1}},
                          {"kind": "approve", "label": "Approve v9", "request": {"version": 9}},
                          {"kind": "decision", "label": "Build it myself", "request": {"action": "self"}},
                          {"kind": "decision", "label": "Buy", "request": {"action": "sell"}}]
        out["navigate"] = {"stage": "decision", "version": 1}
    return out


if task == "ask":
    emit({"type": "result", "data": fake_ask(inp)})
