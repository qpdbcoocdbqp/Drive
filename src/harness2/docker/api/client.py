#!/usr/bin/env python3
"""Python client and CLI for the harness2 API server (api/server.py). Standard library only.

    python api/client.py health
    python api/client.py demo
    python api/client.py task tasks/local_eval/t1_easy --wait --fetch out/
    python api/client.py status <id> | log <id> [--tail N] | outputs <id> | cancel <id> | purge <id>

As a library:

    from client import Harness2Client
    h = Harness2Client()
    job = h.submit_task(instructions="Write output/hello.txt containing 'hi'.", k=1)
    job = h.wait(job["id"])
    print(job["outcome"], job.get("scores"))
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

TERMINAL = ("completed", "failed", "cancelled", "interrupted")


class ApiError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status


class Harness2Client:
    def __init__(self, base_url: str | None = None, token: str | None = None, timeout: float = 60):
        self.base = (base_url or os.environ.get("HARNESS2_API_URL", "http://127.0.0.1:8765")).rstrip("/")
        self.token = token if token is not None else os.environ.get("HARNESS2_API_TOKEN", "")
        self.timeout = timeout

    def _call(self, method: str, path: str, body: dict | None = None, *, raw: bool = False):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                payload = r.read()
        except urllib.error.HTTPError as e:
            try:
                msg = json.loads(e.read()).get("error", e.reason)
            except ValueError:
                msg = e.reason
            raise ApiError(e.code, str(msg)) from None
        return payload if raw else json.loads(payload)

    # ---- submit
    def health(self) -> dict:
        return self._call("GET", "/health")

    def submit_demo(self, k: int = 2, mode: str = "both") -> dict:
        """Run `harness2-demo` on the server: no model calls, checks the install."""
        return self._call("POST", "/jobs", {"kind": "demo", "k": k, "mode": mode})

    def submit_task(
        self,
        instructions: str | None = None,
        files: dict[str, bytes | str] | None = None,
        rubrics: dict | None = None,
        *,
        name: str = "task",
        domain: str | None = None,
        k: int = 1,
        mode: str = "parallel",
        judge: bool = True,
        dry_run: bool = False,
    ) -> dict:
        """Submit a task. `files` maps a path inside task_folder/ to its content.

        `domain=None` isolates the job. Reuse one domain name to let the experience library of
        that domain accumulate; jobs of one domain re-run its unfinished tasks too.
        """
        enc = {
            p: base64.b64encode(c.encode() if isinstance(c, str) else c).decode()
            for p, c in (files or {}).items()
        }
        body = {"kind": "task", "name": name, "k": k, "mode": mode, "judge": judge, "dry_run": dry_run,
                "files": enc}
        if instructions is not None:
            body["instructions"] = instructions
        if rubrics is not None:
            body["rubrics"] = rubrics
        if domain:
            body["domain"] = domain
        return self._call("POST", "/jobs", body)

    def submit_folder(self, task_dir: str | Path, **kw) -> dict:
        """Submit a JobBench-layout task: <dir>/task_folder/** and an optional <dir>/RUBRICS.json."""
        task_dir = Path(task_dir)
        folder = task_dir / "task_folder"
        if not (folder / "TASK_INSTRUCTIONS.txt").is_file():
            raise FileNotFoundError(f"{folder}/TASK_INSTRUCTIONS.txt not found")
        files = {f.relative_to(folder).as_posix(): f.read_bytes() for f in sorted(folder.rglob("*")) if f.is_file()}
        rub = task_dir / "RUBRICS.json"
        rubrics = json.loads(rub.read_text(encoding="utf-8")) if rub.is_file() else None
        kw.setdefault("name", task_dir.name.replace("_", "-"))
        return self.submit_task(files=files, rubrics=rubrics, **kw)

    # ---- inspect
    def job(self, job_id: str) -> dict:
        return self._call("GET", f"/jobs/{job_id}")

    def jobs(self) -> list[dict]:
        return self._call("GET", "/jobs")["jobs"]

    def log(self, job_id: str, tail: int | None = None) -> str:
        q = f"?tail={tail}" if tail else ""
        return self._call("GET", f"/jobs/{job_id}/log{q}", raw=True).decode("utf-8", "replace")

    def outputs(self, job_id: str) -> dict[str, list[dict]]:
        return self._call("GET", f"/jobs/{job_id}/outputs")["outputs"]

    def download(self, job_id: str, label: str, path: str, dest: str | Path) -> Path:
        data = self._call("GET", f"/jobs/{job_id}/outputs/{label}/{path}", raw=True)
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return dest

    def fetch_outputs(self, job_id: str, dest: str | Path, label: str | None = None) -> list[Path]:
        """Download the deliverables of `label` (default: the judged one, else `agg1`, else the first)."""
        outs = self.outputs(job_id)
        if not outs:
            return []
        if label is None:
            scores = self.job(job_id).get("scores") or []
            label = scores[0]["label"] if scores else next(
                (lab for lab in outs if lab.endswith("__agg1")), next(iter(outs)))
        return [self.download(job_id, label, f["path"], Path(dest) / label / f["path"]) for f in outs[label]]

    def cancel(self, job_id: str) -> dict:
        return self._call("DELETE", f"/jobs/{job_id}")

    def purge(self, job_id: str) -> dict:
        """Remove a finished task's staged files and results from the server (the library stays)."""
        return self._call("DELETE", f"/jobs/{job_id}?purge=1")

    def wait(self, job_id: str, poll: float = 5, timeout: float | None = None, on_update=None) -> dict:
        """Poll until the job reaches a terminal state; calls on_update(job) when the phase changes."""
        start, last = time.time(), None
        while True:
            job = self.job(job_id)
            key = (job["state"], job.get("phase"))
            if on_update and key != last:
                on_update(job)
            last = key
            if job["state"] in TERMINAL:
                return job
            if timeout is not None and time.time() - start > timeout:
                raise TimeoutError(f"job {job_id} still {job['state']}/{job.get('phase')}")
            time.sleep(poll)


def _show(job: dict) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {job['id']} {job['state']}/{job.get('phase', '-')}"
          + (f" outcome={job['outcome']}" if job.get("outcome") else ""), flush=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default=None, help="default $HARNESS2_API_URL or http://127.0.0.1:8765")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("health")
    sub.add_parser("jobs")
    d = sub.add_parser("demo")
    d.add_argument("--k", type=int, default=2)
    d.add_argument("--mode", default="both", choices=("parallel", "sequential", "both"))
    t = sub.add_parser("task", help="submit a JobBench-layout task directory")
    t.add_argument("dir")
    t.add_argument("--domain")
    t.add_argument("--k", type=int, default=1)
    t.add_argument("--mode", default="parallel", choices=("parallel", "sequential"))
    t.add_argument("--no-judge", action="store_true")
    t.add_argument("--dry-run", action="store_true")
    t.add_argument("--fetch", metavar="DIR", help="download the deliverables here when done (implies --wait)")
    for c in ("demo", "task"):
        sub.choices[c].add_argument("--wait", action="store_true")
    for c in ("status", "log", "outputs", "cancel", "purge"):
        p = sub.add_parser(c)
        p.add_argument("id")
        if c == "log":
            p.add_argument("--tail", type=int)
    a = ap.parse_args(argv)

    h = Harness2Client(a.url)
    try:
        if a.cmd == "health":
            print(json.dumps(h.health(), indent=2))
        elif a.cmd == "jobs":
            for j in h.jobs():
                print(j["id"], j["kind"], j["state"], j.get("outcome", ""), j["params"].get("task_id", ""))
        elif a.cmd in ("demo", "task"):
            job = (h.submit_demo(a.k, a.mode) if a.cmd == "demo" else
                   h.submit_folder(a.dir, domain=a.domain, k=a.k, mode=a.mode,
                                   judge=not a.no_judge, dry_run=a.dry_run))
            _show(job)
            if a.wait or getattr(a, "fetch", None):
                job = h.wait(job["id"], on_update=_show)
                print(json.dumps(job, indent=2))
                if a.cmd == "task" and a.fetch:
                    for p in h.fetch_outputs(job["id"], a.fetch):
                        print("saved", p)
                return 0 if job["state"] == "completed" else 1
        elif a.cmd == "status":
            print(json.dumps(h.job(a.id), indent=2))
        elif a.cmd == "log":
            print(h.log(a.id, a.tail))
        elif a.cmd == "outputs":
            print(json.dumps(h.outputs(a.id), indent=2))
        elif a.cmd == "cancel":
            print(json.dumps(h.cancel(a.id), indent=2))
        elif a.cmd == "purge":
            print(json.dumps(h.purge(a.id), indent=2))
    except (ApiError, urllib.error.URLError, FileNotFoundError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
