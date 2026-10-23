#!/usr/bin/env python3
"""Hybrid agent: a local LLM does the work, OpenAI improves how it works, Harness² runs the loop.

    executor  HARNESS2_SOLVER_MODEL=local/<id>  OpenCode on the local endpoint; every rollout
    improver  HARNESS2_OPENAI_MODEL=<id>        OpenAI Responses API; propose/compose/select/reflect
    method    Harness² run_task                 Propose-Probe-Compose + per-domain experience lib

The local model runs every rollout, including all tool calls on the task files. OpenAI reads
the evidence the improver needs (task text, trajectories, deliverables) and can only edit the
harness tree. It has no shell.

Run inside the harness2 container (usage notes: ../todo.md, "Hybrid agent"):

    python /opt/agent/hybrid_agent.py check
    python /opt/agent/hybrid_agent.py run  tasks/custom/task1 [--k 2] [--mode sequential]
    python /opt/agent/hybrid_agent.py fast tasks/custom/task1
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import threading
import time
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from harness2 import config
from harness2.benchmarks.jobbench import JobBenchAdapter
from harness2.core import library as library_store
from harness2.core import runmark
from harness2.core.proposer import Proposer
from harness2.core.schedule import mode_token, run_task
from harness2.core.spec import ChannelSpec
from harness2.preflight import provider_problems

AGENT_ROOT = config.RUNS / "agent"
TASKS = AGENT_ROOT / "tasks"
TASK_ID = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9_.-]*/[A-Za-z0-9_-][A-Za-z0-9_.-]*")

WRITABLE = "context/harness"
READ_LINES = 400
TOOL_OUTPUT_MAX = 40_000
LIST_MAX = 500
GREP_MAX = 200

SESSION_RULES = f"""\
# HOW THIS SESSION WORKS

You have no shell, only the file tools. Every path is relative to the session root, which holds
the mission's `context/`. You can read everything, but only `{WRITABLE}/` is writable, and the
edits you make there are your harness changes. Read large files in pages (read_file
offset/limit) and use grep to find things. When you are done, your final message must be
exactly the output the mission asks for."""


def _str(description: str) -> dict:
    return {"type": "string", "description": description}


def _tool(name: str, description: str, required: tuple[str, ...], **props: dict) -> dict:
    return {
        "type": "function",
        "name": name,
        "description": description,
        "parameters": {
            "type": "object",
            "properties": props,
            "required": list(required),
            "additionalProperties": False,
        },
    }


TOOLS = [
    _tool(
        "list_files",
        "List files (with sizes) under a directory of the session root.",
        (),
        path=_str("Directory relative to the session root; default '.'."),
        glob=_str("Glob relative to `path`; default '**/*'."),
    ),
    _tool(
        "read_file",
        f"Read a text file with line numbers, {READ_LINES} lines per call by default.",
        ("path",),
        path=_str("File relative to the session root."),
        offset={"type": "integer", "description": "First line to return (1-based)."},
        limit={"type": "integer", "description": "Number of lines to return."},
    ),
    _tool(
        "grep",
        f"Regex search over text files; returns path:line: text, at most {GREP_MAX} matches.",
        ("pattern",),
        pattern=_str("Python regular expression."),
        path=_str("File or directory relative to the session root; default '.'."),
        glob=_str("Glob relative to `path` when it is a directory; default '**/*'."),
    ),
    _tool(
        "write_file",
        f"Create or overwrite a file under {WRITABLE}/.",
        ("path", "content"),
        path=_str(f"File path starting with {WRITABLE}/."),
        content=_str("The complete new file content."),
    ),
    _tool(
        "replace_in_file",
        f"Replace one exact, unique occurrence of `old` with `new` in a file under {WRITABLE}/.",
        ("path", "old", "new"),
        path=_str(f"File path starting with {WRITABLE}/."),
        old=_str("Exact text to replace; must occur exactly once."),
        new=_str("Replacement text."),
    ),
    _tool(
        "delete_file",
        f"Delete a file under {WRITABLE}/.",
        ("path",),
        path=_str(f"File path starting with {WRITABLE}/."),
    ),
]


class Sandbox:
    """The improver's file tools for one session: read under `root`, write only the harness."""

    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.writable = self.root / WRITABLE

    def run(self, name: str, arguments: str) -> str:
        """Run one tool call; every failure comes back as text the model can act on."""
        if name not in {t["name"] for t in TOOLS}:
            return f"ERROR: unknown tool {name!r}"
        try:
            out = getattr(self, name)(**json.loads(arguments or "{}"))
        except Exception as exc:
            out = f"ERROR: {type(exc).__name__}: {exc}"
        if len(out) > TOOL_OUTPUT_MAX:
            out = out[:TOOL_OUTPUT_MAX] + f"\n... (output cut at {TOOL_OUTPUT_MAX} chars; page it)"
        return out

    def _path(self, rel: str, *, write: bool = False) -> Path:
        p = (self.root / (rel or ".")).resolve()
        if not p.is_relative_to(self.root):
            raise ValueError(f"{rel!r} is outside the session root")
        if write and not p.is_relative_to(self.writable):
            raise ValueError(f"only {WRITABLE}/ is writable; {rel!r} is evidence")
        return p

    def _rel(self, p: Path) -> str:
        return p.relative_to(self.root).as_posix()

    def list_files(self, path: str = ".", glob: str = "**/*") -> str:
        files = [p for p in sorted(self._path(path).glob(glob or "**/*")) if p.is_file()]
        rows = [f"{self._rel(p)}  ({p.stat().st_size} bytes)" for p in files[:LIST_MAX]]
        if len(files) > LIST_MAX:
            rows.append(f"... {len(files) - LIST_MAX} more; narrow `path` or `glob`")
        return "\n".join(rows) or "(no files)"

    def read_file(self, path: str, offset: int = 1, limit: int = READ_LINES) -> str:
        lines = self._path(path).read_text(encoding="utf-8", errors="replace").splitlines()
        start = max(1, int(offset))
        chunk = lines[start - 1 : start - 1 + max(1, int(limit))]
        body = "\n".join(f"{start + i}\t{line}" for i, line in enumerate(chunk))
        return f"{path}: lines {start}-{start + len(chunk) - 1} of {len(lines)}\n{body}"

    def grep(self, pattern: str, path: str = ".", glob: str = "**/*") -> str:
        rx = re.compile(pattern)
        base = self._path(path)
        files = [base] if base.is_file() else sorted(base.glob(glob or "**/*"))
        hits: list[str] = []
        for p in files:
            if not p.is_file():
                continue
            try:
                text = p.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for n, line in enumerate(text.splitlines(), 1):
                if rx.search(line):
                    hits.append(f"{self._rel(p)}:{n}: {line[:300]}")
                    if len(hits) >= GREP_MAX:
                        return "\n".join(hits) + f"\n... (stopped at {GREP_MAX} matches)"
        return "\n".join(hits) or "(no matches)"

    def write_file(self, path: str, content: str) -> str:
        p = self._path(path, write=True)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"wrote {self._rel(p)} ({len(content)} chars)"

    def replace_in_file(self, path: str, old: str, new: str) -> str:
        p = self._path(path, write=True)
        text = p.read_text(encoding="utf-8")
        n = text.count(old)
        if n != 1:
            raise ValueError(f"`old` occurs {n} times in {path}; it must occur exactly once")
        p.write_text(text.replace(old, new, 1), encoding="utf-8")
        return f"edited {self._rel(p)}"

    def delete_file(self, path: str) -> str:
        p = self._path(path, write=True)
        p.unlink()
        return f"deleted {self._rel(p)}"


@dataclass
class OpenAIProposer(Proposer):
    """Harness²'s improver and selector seat, served by the OpenAI Responses API.

    `Proposer.call` sends every non-claude_cli channel through `_opencode(cwd, mission, system,
    wall)`. Overriding only that transport leaves upstream's cache, audit trail, retries and
    `context/harness` harvest as they are.
    """

    client: Any = None
    usd_per_mtok: tuple[float, float] = (0.0, 0.0)
    tokens: dict[str, int] = field(default_factory=lambda: {"input": 0, "output": 0})
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def _opencode(self, cwd: Path, mission: str, system: str, wall: int) -> tuple[str, float, str]:
        box = Sandbox(cwd)
        deadline = time.monotonic() + wall
        common: dict[str, Any] = {
            "model": self.spec.model.split("/", 1)[-1],
            "instructions": "\n\n".join(s for s in (system, SESSION_RULES) if s),
            "tools": TOOLS,
        }
        if self.spec.effort:
            common["reasoning"] = {"effort": self.spec.effort}
        # Stateless turns: resend the whole history rather than chaining previous_response_id,
        # which llama.cpp and other Responses API servers reject.
        history: list[Any] = [{"role": "user", "content": mission}]
        cost = 0.0
        steps = 0
        while True:
            resp = self.client.responses.create(input=history, **common)
            cost += self._account(resp.usage)
            calls = [item for item in resp.output if item.type == "function_call"]
            if not calls:
                return resp.output_text or "", cost, ""
            steps += 1
            if steps > self.spec.steps:
                return "", cost, f"no final reply within {self.spec.steps} tool steps"
            if time.monotonic() > deadline:
                return "", cost, f"timeout after {wall}s"
            history += resp.output
            history += [
                {
                    "type": "function_call_output",
                    "call_id": c.call_id,
                    "output": box.run(c.name, c.arguments),
                }
                for c in calls
            ]

    def _account(self, usage: Any) -> float:
        tin = int(getattr(usage, "input_tokens", 0) or 0)
        tout = int(getattr(usage, "output_tokens", 0) or 0)
        with self._lock:
            self.tokens["input"] += tin
            self.tokens["output"] += tout
        return (tin * self.usd_per_mtok[0] + tout * self.usd_per_mtok[1]) / 1e6


class AgentAdapter(JobBenchAdapter):
    """JobBench's OpenCode executor and base harness, reading tasks from the agent's store.

    Task ids are `<domain>/<name>`, laid out as TASKS/<domain>/<name>/task_folder/.
    """

    def __init__(self, channel: ChannelSpec | None = None):
        super().__init__("oc", store_root=AGENT_ROOT / "store")
        self.spec = replace(
            self.spec,
            name="agent",
            runs_root=AGENT_ROOT / "runs",
            channels={"proposer": channel, "selector": channel} if channel else self.spec.channels,
        )

    def tasks(self, split: str = "all") -> list[str]:
        return sorted(
            p.parent.parent.relative_to(TASKS).as_posix()
            for p in TASKS.glob("*/*/task_folder/TASK_INSTRUCTIONS.txt")
        )

    def _dataset_task(self, task_id: str) -> Path:
        if not TASK_ID.fullmatch(task_id):
            raise ValueError(f"malformed task id {task_id!r} (expected '<domain>/<name>')")
        return TASKS / task_id


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", text).strip("-.") or "task"


def stage(src: Path, domain: str | None, name: str | None) -> str:
    """Copy a task folder into the agent store and return its id.

    The id includes a digest of the folder's contents. An edited task gets fresh rollouts;
    an unchanged one resumes from its earlier summary.
    """
    src = src.resolve()
    if src.name == "task_folder":
        src = src.parent
    folder = src / "task_folder" if (src / "task_folder").is_dir() else src
    if not (folder / "TASK_INSTRUCTIONS.txt").is_file():
        raise SystemExit(f"{folder} has no TASK_INSTRUCTIONS.txt")
    domain = _slug(domain or (src.parent.name if folder != src else "general"))
    task_id = f"{domain}/{_slug(name or src.name)}-{runmark.fingerprint(folder)[:8]}"
    dst = TASKS / task_id / "task_folder"
    if not dst.is_dir():
        partial = dst.with_name("task_folder.partial")
        shutil.rmtree(partial, ignore_errors=True)
        shutil.copytree(folder, partial)
        partial.rename(dst)
    return task_id


def deliver(run_dir: Path, out: Path) -> list[str]:
    """Copy a run's deliverables into `out`. Files already in `out` are never removed."""
    shutil.copytree(run_dir, out, dirs_exist_ok=True)
    return sorted(p.relative_to(run_dir).as_posix() for p in run_dir.rglob("*") if p.is_file())


def library_for(adapter: AgentAdapter, task_id: str, mode: str) -> library_store.Library:
    return library_store.Library(
        root=AGENT_ROOT / "library" / mode,
        domain=adapter.domain_of(task_id),
        surface=adapter.substrate.surface(),
    )


def newest_harness(lib: library_store.Library) -> Path | None:
    """The most recently adopted harness in this domain, or None for the base harness."""
    found = [lib.dir / "harnesses" / e / "harness" for e in lib.entries()]
    found = [p for p in found if p.is_dir()]
    return max(found, key=lambda p: p.stat().st_mtime) if found else None


def take_openai_key() -> str:
    """Read the OpenAI key and remove it from the environment. Rollouts run a local agent
    with a shell and network access, and they must not inherit the key."""
    key = os.environ.pop("HARNESS2_OPENAI_API_KEY", "")
    plain = os.environ.pop("OPENAI_API_KEY", "")
    return key or plain


def openai_channel() -> ChannelSpec:
    # "opencode" is the only non-claude_cli channel kind; OpenAIProposer replaces its transport.
    effort = config.env("HARNESS2_OPENAI_EFFORT", "high")
    return ChannelSpec(
        "opencode",
        "openai/" + config.env("HARNESS2_OPENAI_MODEL", "gpt-5"),
        effort="" if effort == "none" else effort,
    )


def openai_client(key: str) -> Any:
    try:
        from openai import OpenAI
    except ImportError:
        raise SystemExit("the openai package is missing; rebuild the image (docker compose build)")
    if not key:
        raise SystemExit("set HARNESS2_OPENAI_API_KEY (or OPENAI_API_KEY) in harness2.env")
    return OpenAI(
        api_key=key,
        base_url=config.env("HARNESS2_OPENAI_BASE_URL"),
        timeout=900.0,
        max_retries=3,
    )


def make_proposer(key: str) -> OpenAIProposer:
    return OpenAIProposer(
        openai_channel(),
        audit_dir=AGENT_ROOT / "audit",
        client=openai_client(key),
        usd_per_mtok=(
            float(config.env("HARNESS2_OPENAI_USD_IN", 0)),
            float(config.env("HARNESS2_OPENAI_USD_OUT", 0)),
        ),
    )


def cmd_check(key: str) -> int:
    """Look for problems before a run: model config, the local endpoint, OpenCode, and the
    OpenAI key and model. With a custom HARNESS2_OPENAI_BASE_URL this sends one 16-token
    request to that server's /responses endpoint."""
    problems: list[str] = []
    try:
        config.validate_models()
    except config.ConfigError as exc:
        problems.append(str(exc))
    solver = config.provider_model(config.SOLVER_MODEL_OC)
    if solver and config.PROVIDER_BASE_URL:
        problems += provider_problems({solver})
    try:
        AgentAdapter().substrate.preflight()
    except RuntimeError as exc:
        problems.append(str(exc))
    model = openai_channel().model.split("/", 1)[1]
    try:
        client = openai_client(key)
        if config.env("HARNESS2_OPENAI_BASE_URL"):
            # Local Responses API servers often lack GET /models/<id>, so probe the endpoint
            # the improver actually calls, with a minimal request.
            client.responses.create(model=model, input="ping", max_output_tokens=16)
        else:
            client.models.retrieve(model)
    except SystemExit as exc:
        problems.append(str(exc))
    except Exception as exc:
        problems.append(f"OpenAI model {model!r}: {type(exc).__name__}: {exc}")
    endpoint = config.PROVIDER_BASE_URL or "(OpenCode default)"
    print(f"executor: {config.SOLVER_MODEL_OC} @ {endpoint}")
    print(f"improver: openai/{model}")
    for p in problems:
        print(f"  problem: {p}")
    print("ready" if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0


def cmd_run(args: argparse.Namespace, key: str) -> int:
    """One Harness² step on the task, then deliver the selected final's output."""
    config.validate_models()
    prop = make_proposer(key)
    adapter = AgentAdapter(prop.spec)
    task_id = stage(args.task, args.domain, args.name)
    mode = mode_token(args.mode)
    print(
        f"[agent] {task_id}: k={args.k} {args.mode}, executor {config.SOLVER_MODEL_OC}, "
        f"improver {prop.spec.model}",
        flush=True,
    )
    res = run_task(
        task_id,
        args.k,
        adapter=adapter,
        prop=prop,
        selector_prop=prop,
        runs_root=adapter.spec.runs_root,
        modes=(mode,),
        libraries={mode: library_for(adapter, task_id, mode)},
    )
    print(f"[agent] status: {res.status}")
    for err in res.errors:
        print(f"  error: {err}")
    print(f"[agent] openai tokens: in={prop.tokens['input']} out={prop.tokens['output']}")
    sel = res.selections.get(mode) or {}
    if not (sel.get("ok") and sel.get("chosen_run_dir")):
        return 1
    out = args.out or AGENT_ROOT / "out" / task_id
    files = deliver(Path(sel["chosen_run_dir"]), out)
    kind = "base harness" if sel.get("chosen_tree_is_base") else "refined harness"
    print(f"[agent] chose {sel['chosen_label']} ({kind}); deliverables -> {out}")
    for f in files:
        print(f"    {f}")
    return 0 if res.status in ("ok", "skipped") else 1


def cmd_fast(args: argparse.Namespace) -> int:
    """One local rollout under the domain's newest adopted harness. Makes no OpenAI calls."""
    config.validate_models()
    adapter = AgentAdapter()
    task_id = stage(args.task, args.domain, args.name)
    harness = newest_harness(library_for(adapter, task_id, mode_token(args.mode)))
    print(
        f"[agent] {task_id}: fast rollout on {config.SOLVER_MODEL_OC}, "
        f"harness {harness or 'base'}",
        flush=True,
    )
    stamp = f"{datetime.now():%Y%m%d_%H%M%S}"
    try:
        run_dir = adapter.execute(task_id, harness, f"agent-oc-fast/{task_id}/{stamp}")
    except RuntimeError as exc:
        print(f"[agent] rollout failed: {exc}")
        return 1
    out = args.out or AGENT_ROOT / "fast" / task_id / stamp
    files = deliver(Path(run_dir), out)
    print(f"[agent] deliverables -> {out}")
    for f in files:
        print(f"    {f}")
    return 0 if files else 1


def main(argv: list[str] | None = None) -> int:
    key = take_openai_key()
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check", help="check config, local endpoint, OpenCode and OpenAI")
    commands = {
        "run": "refine the harness on this task with Harness², then deliver the selected output",
        "fast": "one local rollout with the newest adopted harness; no OpenAI calls",
    }
    for name, help_text in commands.items():
        s = sub.add_parser(name, help=help_text)
        s.add_argument("task", type=Path, help="a task dir with task_folder/TASK_INSTRUCTIONS.txt")
        s.add_argument("--domain", help="experience-library domain (default: task's parent dir)")
        s.add_argument("--name", help="task name (default: the task dir name)")
        s.add_argument("--mode", choices=("parallel", "sequential"), default="parallel")
        s.add_argument("--out", type=Path, help=f"deliverables dir (default: runs/agent/{name}/…)")
        if name == "run":
            s.add_argument("--k", type=int, default=1, help="task-level recursion budget")
    args = parser.parse_args(argv)
    if getattr(args, "k", 1) < 1:
        parser.error("--k must be positive")
    try:
        if args.cmd == "check":
            return cmd_check(key)
        if args.cmd == "run":
            return cmd_run(args, key)
        return cmd_fast(args)
    except config.ConfigError as exc:
        raise SystemExit(f"config: {exc}") from None


if __name__ == "__main__":
    raise SystemExit(main())
