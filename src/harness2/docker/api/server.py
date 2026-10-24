#!/usr/bin/env python3
"""Resident HTTP API in front of the native harness2 CLI.

A job stages one JobBench task into the dataset, then runs the upstream commands
(`harness2`, `harness2-judge`, `harness2-report`), one job at a time. Nothing here changes how
harness2 improves a harness: the server only queues, runs and reports.

    POST   /jobs                         submit a task or a demo job
    GET    /jobs                         recent jobs
    GET    /jobs/<id>                    state, outcome, scores
    GET    /jobs/<id>/log?tail=200       command output
    GET    /jobs/<id>/outputs            deliverables per candidate (label)
    GET    /jobs/<id>/outputs/<label>/<path>   one deliverable
    DELETE /jobs/<id>                    cancel a queued or running job
    DELETE /jobs/<id>?purge=1            remove a finished task's staged files and results
    GET    /health

Standard library only. Set HARNESS2_API_TOKEN to require `Authorization: Bearer <token>`.
"""

from __future__ import annotations

import base64
import binascii
import hmac
import json
import os
import queue
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(os.environ.get("HARNESS2_ROOT", "/opt/harness2"))
DATASET = Path(
    os.environ.get("HARNESS2_API_DATASET", ROOT / "dependencies/job-bench-eval/dataset/main")
)
STATE = ROOT / "runs" / "api_server"  # job.json and log.txt per job
RUNS = ROOT / "runs" / "api"  # one harness2 results root per domain
TOKEN = os.environ.get("HARNESS2_API_TOKEN", "")
MAX_BODY = 64 * 1024 * 1024
MAX_FILES = 200
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,47}")
MODES = ("parallel", "sequential")
DEMO_MODES = ("parallel", "sequential", "both")
STATUS_LINE = re.compile(r"^\s*\[[^\]]+\]\s+\d+/\d+\s+(\S+)\s+(\S+)", re.M)
DEMO_LINE = re.compile(r"Local demo: (\d+) tasks completed; (\d+) failed")

JOBS: dict[str, dict] = {}
LOCK = threading.Lock()
QUEUE: queue.Queue[str] = queue.Queue()
RUNNING: dict[str, subprocess.Popen] = {}


def valid_name(s: object) -> bool:
    # "__" separates domain and task in harness2 labels
    return isinstance(s, str) and bool(NAME.fullmatch(s)) and "__" not in s


def safe_rel(p: str) -> str:
    """A relative POSIX path with no `..`; raises ValueError otherwise."""
    if not p or p.startswith("/") or "\\" in p or "\0" in p:
        raise ValueError(f"bad file path: {p!r}")
    parts = p.split("/")
    if any(x in ("", ".", "..") for x in parts):
        raise ValueError(f"bad file path: {p!r}")
    return "/".join(parts)


def now() -> float:
    return round(time.time(), 3)


def save(job: dict) -> None:
    """Persist a job; keys starting with `_` (staged upload data) stay in memory."""
    d = STATE / job["id"]
    d.mkdir(parents=True, exist_ok=True)
    data = {k: v for k, v in job.items() if not k.startswith("_")}
    tmp = d / "job.json.tmp"
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(d / "job.json")


def update(job: dict, **kw) -> None:
    with LOCK:
        job.update(kw)
        save(job)


# ------------------------------------------------------------------ submit
def parse_submission(body: dict, job_id: str) -> tuple[str, dict, dict]:
    """Validate a POST /jobs body. Returns (kind, params, files-to-stage)."""
    kind = body.get("kind", "task")
    if kind == "demo":
        k, mode = body.get("k", 2), body.get("mode", "both")
        if not isinstance(k, int) or isinstance(k, bool) or not 1 <= k <= 16:
            raise ValueError("k must be an integer 1..16")
        if mode not in DEMO_MODES:
            raise ValueError(f"mode must be one of {DEMO_MODES}")
        return kind, {"k": k, "mode": mode}, {}
    if kind != "task":
        raise ValueError("kind must be 'task' or 'demo'")

    k, mode = body.get("k", 1), body.get("mode", "parallel")
    if not isinstance(k, int) or isinstance(k, bool) or not 1 <= k <= 16:
        raise ValueError("k must be an integer 1..16")
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    domain = body.get("domain") or f"job-{job_id}"
    name = body.get("name") or "task"
    if not valid_name(domain) or not valid_name(name):
        raise ValueError("domain and name: letters, digits, '-' and '_' (max 48), no '__'")

    files: dict[str, bytes] = {}
    for path, b64 in (body.get("files") or {}).items():
        rel = safe_rel(path)
        if rel.rsplit("/", 1)[-1] == "RUBRICS.json":
            raise ValueError("RUBRICS.json would reach the agent; send it in the 'rubrics' field")
        try:
            files[rel] = base64.b64decode(b64, validate=True)
        except (binascii.Error, TypeError) as e:
            raise ValueError(f"files[{path!r}] is not base64") from e
    if isinstance(body.get("instructions"), str):
        files["TASK_INSTRUCTIONS.txt"] = body["instructions"].encode("utf-8")
    if "TASK_INSTRUCTIONS.txt" not in files:
        raise ValueError("TASK_INSTRUCTIONS.txt (or 'instructions') is required")
    if len(files) > MAX_FILES:
        raise ValueError(f"at most {MAX_FILES} files")

    rubrics = body.get("rubrics")
    if rubrics is not None and not (
        isinstance(rubrics, dict) and isinstance(rubrics.get("rubrics"), list)
    ):
        raise ValueError("rubrics must be {'rubrics': [...]}")
    dry_run = bool(body.get("dry_run", False))
    judge = bool(body.get("judge", True)) and rubrics is not None and not dry_run
    params = {
        "domain": domain,
        "task_id": f"{name}-{job_id[:6]}",
        "k": k,
        "mode": mode,
        "judge": judge,
        "dry_run": dry_run,
        "has_rubrics": rubrics is not None,
    }
    return kind, params, {"files": files, "rubrics": rubrics}


def stage_task(params: dict, staged: dict) -> None:
    task = DATASET / params["domain"] / params["task_id"]
    if task.exists():
        raise FileExistsError(f"{task} already exists")
    folder = task / "task_folder"
    for rel, data in staged["files"].items():
        dst = folder / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(data)
    if staged["rubrics"] is not None:
        (task / "RUBRICS.json").write_text(
            json.dumps(staged["rubrics"], indent=2, ensure_ascii=False), encoding="utf-8"
        )


# ------------------------------------------------------------------ run
def result_dir(job: dict) -> Path:
    p = job["params"]
    return RUNS / p["domain"] / "jb" / "dataset" / p["domain"] / p["task_id"]


def purge(job: dict) -> None:
    """Remove a task's staged files and results. Experience libraries are left alone."""
    p = job["params"]
    for d in (DATASET / p["domain"] / p["task_id"], result_dir(job)):
        shutil.rmtree(d, ignore_errors=True)
    try:
        (DATASET / p["domain"]).rmdir()  # only when empty
    except OSError:
        pass
    if p["domain"] == f"job-{job['id']}":  # isolated domain: nothing else lives there
        shutil.rmtree(RUNS / p["domain"], ignore_errors=True)


def phases(job: dict) -> list[tuple[str, list[str]]]:
    p = job["params"]
    if job["kind"] == "demo":
        out = STATE / job["id"] / "demo"
        return [("demo", ["harness2-demo", "--output", str(out), "--k", str(p["k"]), "--mode", p["mode"]])]
    base = ["--bench", "jb", "--k", str(p["k"]), "--mode", p["mode"]]
    run = ["harness2", *base, "--only-domains", p["domain"]]
    if p["dry_run"]:
        return [("improve", run + ["--dry-run"])]
    out = [("improve", run)]
    if p["judge"]:
        out += [("judge", ["harness2-judge", *base]), ("report", ["harness2-report", *base])]
    return out


def run_job(job: dict) -> None:
    jid, kind = job["id"], job["kind"]
    update(job, state="running", started=now(), phase="staging")
    log = STATE / jid / "log.txt"
    try:
        if kind == "task":
            stage_task(job["params"], job.pop("_staged"))
    except Exception as e:  # staging problems are the caller's to fix
        update(job, state="failed", finished=now(), error=f"staging: {e}")
        return

    env = dict(os.environ)
    if kind == "task":
        root = RUNS / job["params"]["domain"]
        root.mkdir(parents=True, exist_ok=True)
        env["HARNESS2_RUNS_DIR"] = str(root)
    codes: dict[str, int] = {}
    with log.open("ab") as fh:
        for phase, cmd in phases(job):
            with LOCK:
                if job["state"] == "cancelled":
                    return
                job["phase"] = phase
                save(job)
            fh.write(f"\n$ {' '.join(cmd)}\n".encode())
            fh.flush()
            proc = subprocess.Popen(
                cmd, cwd=ROOT, env=env, stdout=fh, stderr=subprocess.STDOUT, start_new_session=True
            )
            RUNNING[jid] = proc
            codes[phase] = proc.wait()
            RUNNING.pop(jid, None)
            update(job, exit_codes=dict(codes))
            if job["state"] == "cancelled":
                return
            # harness2 exits 2 when a task is not `ok`; judge and report still have to run
            if phase in ("improve", "demo") and codes[phase] not in (0, 2):
                update(job, state="failed", finished=now(), error=f"{phase} exited {codes[phase]}")
                return
    outcome = outcome_of(job, log)
    if kind == "task" and job["params"]["dry_run"]:
        purge(job)  # a preview must not leave a task behind in the shared dataset
    update(job, state="completed", finished=now(), phase="done", outcome=outcome)


def outcome_of(job: dict, log: Path) -> str:
    text = log.read_text(encoding="utf-8", errors="replace")
    if job["kind"] == "demo":
        m = DEMO_LINE.search(text)
        return "ok" if m and m.group(2) == "0" else "failed"
    if job["params"]["dry_run"]:
        return "dry-run"
    want = f"{job['params']['domain']}/{job['params']['task_id']}"
    for status, task in STATUS_LINE.findall(text):
        if task == want:
            return status  # ok | base-failed | partial
    return "unknown"


def worker() -> None:
    while True:
        jid = QUEUE.get()
        job = JOBS[jid]
        if job["state"] == "queued":
            try:
                run_job(job)
            except Exception as e:
                update(job, state="failed", finished=now(), error=f"server: {e}")


# ------------------------------------------------------------------ results
def scores_of(job: dict) -> list[dict]:
    out = []
    for f in sorted(result_dir(job).glob("eval_result/*/*_judge.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        out.append(
            {
                "label": f.parent.name.removeprefix("eval_"),
                "judge_model": d.get("judge_model"),
                "score": d.get("total_score"),
                "max_score": d.get("max_score"),
                "criteria_passed": d.get("passed_count"),
                "criteria_total": d.get("total_count"),
            }
        )
    return out


def outputs_of(job: dict) -> dict[str, list[dict]]:
    base = result_dir(job) / "model_output"
    res: dict[str, list[dict]] = {}
    if base.is_dir():
        for lab in sorted(p for p in base.iterdir() if p.is_dir()):
            res[lab.name] = [
                {"path": f.relative_to(lab).as_posix(), "size": f.stat().st_size}
                for f in sorted(lab.rglob("*"))
                if f.is_file()
            ]
    return res


def view(job: dict, detail: bool = False) -> dict:
    keep = ("id", "kind", "state", "phase", "outcome", "created", "started", "finished", "error", "params", "exit_codes", "purged")
    v = {k: job[k] for k in keep if k in job}
    if detail and job["kind"] == "task" and job["state"] == "completed":
        sc = scores_of(job)
        v["scores"] = sc
        v["selected"] = sc[0]["label"] if sc else None
    return v


# ------------------------------------------------------------------ http
class Handler(BaseHTTPRequestHandler):
    server_version = "harness2-api/1"

    def _send(self, code: int, obj=None, *, raw: bytes | None = None, ctype="application/json"):
        body = raw if raw is not None else json.dumps(obj, indent=2).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _err(self, code: int, msg: str):
        self._send(code, {"error": msg})

    def _authorized(self) -> bool:
        if not TOKEN:
            return True
        got = self.headers.get("Authorization", "")
        return hmac.compare_digest(got, f"Bearer {TOKEN}")

    def _job(self, jid: str):
        job = JOBS.get(jid)
        if job is None:
            self._err(404, f"no job {jid}")
        return job

    def do_GET(self):
        url = urlparse(self.path)
        seg = [unquote(s) for s in url.path.strip("/").split("/")]
        if seg == ["health"]:
            return self._send(
                200,
                {
                    "ok": True,
                    "dataset_ready": DATASET.is_dir(),
                    "queued": QUEUE.qsize(),
                    "running": list(RUNNING),
                    "models": {
                        k: os.environ.get(f"HARNESS2_{k}_MODEL", "")
                        for k in ("SOLVER", "IMPROVER", "JUDGE")
                    },
                },
            )
        if not self._authorized():
            return self._err(401, "bad or missing token")
        if seg == ["jobs"]:
            recent = sorted(JOBS.values(), key=lambda j: j["created"], reverse=True)[:50]
            return self._send(200, {"jobs": [view(j) for j in recent]})
        if len(seg) >= 2 and seg[0] == "jobs":
            job = self._job(seg[1])
            if job is None:
                return
            if len(seg) == 2:
                return self._send(200, view(job, detail=True))
            if seg[2] == "log" and len(seg) == 3:
                p = STATE / job["id"] / "log.txt"
                text = p.read_text(encoding="utf-8", errors="replace") if p.is_file() else ""
                tail = parse_qs(url.query).get("tail", [""])[0]
                if tail.isdigit():
                    text = "\n".join(text.splitlines()[-int(tail):])
                return self._send(200, raw=text.encode(), ctype="text/plain; charset=utf-8")
            if seg[2] == "outputs":
                if job["kind"] != "task":
                    return self._err(404, "demo jobs have no outputs")
                if len(seg) == 3:
                    return self._send(200, {"outputs": outputs_of(job)})
                if len(seg) >= 5:
                    base = (result_dir(job) / "model_output" / seg[3]).resolve()
                    f = (base / "/".join(seg[4:])).resolve()
                    if base.is_dir() and f.is_file() and f.is_relative_to(base):
                        return self._send(200, raw=f.read_bytes(), ctype="application/octet-stream")
                    return self._err(404, "no such output")
        self._err(404, "not found")

    def do_POST(self):
        if not self._authorized():
            return self._err(401, "bad or missing token")
        if urlparse(self.path).path.rstrip("/") != "/jobs":
            return self._err(404, "not found")
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > MAX_BODY:
            return self._err(413 if n > MAX_BODY else 400, f"body must be 1..{MAX_BODY} bytes")
        try:
            body = json.loads(self.rfile.read(n))
            if not isinstance(body, dict):
                raise ValueError("body must be a JSON object")
            jid = uuid.uuid4().hex[:8]
            kind, params, staged = parse_submission(body, jid)
        except (ValueError, TypeError) as e:
            return self._err(400, str(e))
        job = {"id": jid, "kind": kind, "state": "queued", "created": now(), "params": params}
        if staged:
            job["_staged"] = staged
        with LOCK:
            JOBS[jid] = job
            save(job)
        QUEUE.put(jid)
        self._send(202, view(job))

    def do_DELETE(self):
        if not self._authorized():
            return self._err(401, "bad or missing token")
        seg = urlparse(self.path).path.strip("/").split("/")
        if len(seg) != 2 or seg[0] != "jobs":
            return self._err(404, "not found")
        job = self._job(seg[1])
        if job is None:
            return
        if "purge" in parse_qs(urlparse(self.path).query):
            if job["state"] in ("queued", "running"):
                return self._err(409, "cancel the job first")
            if job["kind"] == "task":
                purge(job)
            update(job, purged=True)
            return self._send(200, view(job))
        if job["state"] not in ("queued", "running"):
            return self._err(409, f"job is {job['state']}")
        update(job, state="cancelled", finished=now())
        proc = RUNNING.get(job["id"])
        if proc:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        self._send(200, view(job))


def load_jobs() -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    for p in sorted(STATE.glob("*/job.json")):
        try:
            job = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if job.get("state") in ("queued", "running"):
            job.update(state="interrupted", finished=now())
            save(job)
        JOBS[job["id"]] = job


def main() -> None:
    host = os.environ.get("HARNESS2_API_HOST", "0.0.0.0")
    port = int(os.environ.get("HARNESS2_API_PORT", "8765"))
    load_jobs()
    threading.Thread(target=worker, daemon=True).start()
    srv = ThreadingHTTPServer((host, port), Handler)

    def stop(*_):
        for proc in list(RUNNING.values()):
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        threading.Thread(target=srv.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    print(f"harness2 API on {host}:{port} (token {'on' if TOKEN else 'off'}), dataset {DATASET}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    sys.exit(main())
