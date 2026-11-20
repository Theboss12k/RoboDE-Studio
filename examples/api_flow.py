#!/usr/bin/env python3
"""Run one RoboDE Studio project through the API, from brief to export.

Standard library only (Python 3.8 or newer). Each step prints what the server returned.

  python3 examples/api_flow.py --base http://127.0.0.1:8800/api/v1
  python3 examples/api_flow.py --base http://127.0.0.1:8800/api/v1 --token T --brief "..." --no-catalog

The token defaults to $STUDIO_TOKEN. Exit status is 0 when the flow finished, 1 when a call or a job failed.
"""
import argparse
import json
import os
import sys
import tempfile
import urllib.error
import urllib.request
import uuid
import zipfile

MAX_ROUNDS = 6  # question rounds before the script gives up
MAX_POLLS = 60  # 60 polls of 60 s each, longer than the server's 45 minute job ceiling


class StudioError(Exception):
    """A failed call or a failed job. The message is printed as it is."""


class Client:
    def __init__(self, base, token):
        self.base = base.rstrip("/")
        self.token = token

    def call(self, method, path, body=None, raw=None, headers=None):
        """Send one request. Returns the parsed JSON, or the bytes when the answer is not JSON."""
        head = dict(headers or {})
        if self.token:
            head["Authorization"] = "Bearer " + self.token
        data = None
        if raw is not None:
            data, head["Content-Type"] = raw, "application/octet-stream"
        elif body is not None:
            data, head["Content-Type"] = json.dumps(body).encode("utf-8"), "application/json"
        request = urllib.request.Request(self.base + path, data=data, method=method, headers=head)
        try:
            with urllib.request.urlopen(request, timeout=700) as reply:
                payload = reply.read()
                if "json" in reply.headers.get("Content-Type", ""):
                    return json.loads(payload) if payload else None
                return payload
        except urllib.error.HTTPError as err:
            try:
                info = json.loads(err.read())
            except ValueError:
                info = {}
            code = f" (error_code {info['error_code']})" if info.get("error_code") else ""
            raise StudioError(f"{method} {path} failed with HTTP {err.code}: {info.get('detail', err.reason)}{code}")
        except urllib.error.URLError as err:
            raise StudioError(f"cannot reach {self.base}: {err.reason}")


def finish(client, job):
    """Poll a job in 60 s waits until it ends. Returns the final record (with its resource) or raises."""
    for _ in range(MAX_POLLS):
        if job["status"] != "running":
            break
        job = client.call("GET", f"/jobs/{job['id']}?wait=60&expand=resource")
    if job["status"] == "running":
        raise StudioError(f"job {job['id']} is still running after an hour")
    if job["status"] != "done":
        code = f" (error_code {job['error_code']})" if job.get("error_code") else ""
        raise StudioError(f"{job['kind']} job {job['id']} {job['status']}: {job.get('error')}{code}")
    return job


def start(client, path, body=None):
    """Start a job and wait up to 600 s for it, with the resource in the answer."""
    return finish(client, client.call("POST", path + "?wait=600&expand=resource", body))


def design(client, pid):
    """Answer each question round with its defaults (one comment, once) until the robot is designed."""
    body = {"answers": {}}
    commented = False
    for _ in range(MAX_ROUNDS):
        job = start(client, f"/projects/{pid}/design", body)
        result = job["result"]
        if result["status"] == "designed":
            print(f"design: version {result['version']} is ready")
            return job
        questions = result["questions"]
        rnd = (job.get("resource") or {}).get("round", result.get("round"))
        print(f"design: round {rnd} asks {', '.join(q['id'] for q in questions)}")
        body = {"answers": {q["id"]: q["default"] for q in questions if q.get("default") is not None}}
        if questions and not commented:
            body["comments"] = {questions[0]["id"]: "Answered with the default (api_flow.py)."}
            commented = True
    raise StudioError(f"the design still asks questions after {MAX_ROUNDS} rounds")


def describe(version):
    """Print the family, the numeric parameters and the check counts of a version."""
    robot, sim = version["robot"], version["sim"]
    checks = sim.get("checks", [])
    passed = sum(c["passed"] is True for c in checks)
    failed = sum(c["passed"] is False for c in checks)
    print(f"version {version['version']}: {version['spec']['title']}")
    print(f"  robot: {robot['family']} ({robot['name']})")
    numbers = [(k, v) for k, v in robot["params"].items() if isinstance(v, (int, float)) and not isinstance(v, bool)]
    for key, value in numbers[:8]:
        print(f"    {key} = {value}")
    undecided = len(checks) - passed - failed
    print(f"  checks: {passed} pass, {failed} fail, {undecided} not decided; simulation ok: {sim.get('ok')}")


def revise(client, pid, base):
    """Ask for a change at the middle of the base version's simulation and print what moved."""
    t = round(base["sim"]["duration_s"] / 2, 3)
    job = start(client, f"/projects/{pid}/revise",
                {"request": "Make the arm 5 cm longer", "base_version": base["version"], "t": t})
    new = job["resource"]
    print(f"revise: version {new['version']} from version {new['parent_version']}, at t = {t} s")
    before, after = base["robot"]["params"], new["robot"]["params"]
    for key in sorted(set(before) | set(after)):
        if before.get(key) != after.get(key):
            print(f"  {key}: {before.get(key)} -> {after.get(key)}")
    return new


def catalog(client, pid):
    """Search for real robots, analyse the first one whose family the designer knows, return its id."""
    candidates = start(client, f"/projects/{pid}/search")["resource"]
    if isinstance(candidates, dict):  # tolerate the {candidates: [...]} shape
        candidates = candidates.get("candidates", [])
    print(f"search: {len(candidates)} candidates")
    for c in candidates:
        price = c.get("price") or {}
        cost = f"{price['low']}-{price.get('high')} {price.get('currency', '')}" if price.get("low") is not None else "no price"
        print(f"  {c['id']}: {c['manufacturer']} {c['model']} ({c['family']}), {cost}")
    families = {f["name"] for f in client.call("GET", "/families")}
    usable = [c for c in candidates if c["family"] in families]
    if not usable:
        raise StudioError("no candidate is in a family the designer can analyse")
    chosen = usable[0]["id"]
    analysis = start(client, f"/projects/{pid}/candidates/{chosen}/analyse")["resource"]
    print(f"analyse {chosen}: {len(analysis['unverified'])} values could not be verified")
    for v in analysis["manufacturer_spec"]["values"]:
        if v["verified"]:
            print(f"  verified {v['key']} = {v['value']} {v['unit']}")
    for d in analysis["differences"]:
        print(f"  [{d['severity']}] {d['topic']}: generated {d['generated']}; candidate {d['candidate']}")
    return chosen


def export(client, pid):
    """Download the export ZIP to a temporary file and list what it holds."""
    data = client.call("GET", f"/projects/{pid}/export")
    fd, path = tempfile.mkstemp(prefix="studio-export-", suffix=".zip")
    with os.fdopen(fd, "wb") as out:
        out.write(data)
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
    print(f"export: {path} ({len(data)} bytes)")
    for name in names:
        print(f"  {name}")


def run(client, args):
    project = client.call("POST", "/projects", {"brief": args.brief},
                          headers={"Idempotency-Key": "api-flow-" + uuid.uuid4().hex})
    pid = project["id"]
    print(f"project {pid}")
    first = design(client, pid)["resource"]
    describe(first)
    frames = client.call("GET", f"/projects/{pid}/versions/{first['version']}/assets/frames.json")
    print(f"  frames asset: {len(frames)} frames")
    new = revise(client, pid, first)
    client.call("POST", f"/projects/{pid}/approve", {"version": new["version"]})
    print(f"approved version {new['version']}")
    if not args.no_catalog:
        chosen = catalog(client, pid)
        client.call("POST", f"/projects/{pid}/decision", {"candidate_id": chosen, "action": "approve"})
        print(f"decision: approve {chosen}")
    export(client, pid)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run one project through the RoboDE Studio API.")
    parser.add_argument("--base", default="http://127.0.0.1:8800/api/v1")
    parser.add_argument("--token", default=os.environ.get("STUDIO_TOKEN", ""), help="bearer token (default: $STUDIO_TOKEN)")
    parser.add_argument("--brief", default="Move a 0.3 kg bottle from table A to table B")
    parser.add_argument("--no-catalog", action="store_true", help="skip search, analysis and decision")
    args = parser.parse_args(argv)
    try:
        run(Client(args.base, args.token), args)
    except StudioError as err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
