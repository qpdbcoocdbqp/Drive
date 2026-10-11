"""OpenAI-compatible skills runner.

Usage:
    runner = SkillsRunner("path/to/skills", base_url="http://localhost:19001/v1", model="sonnet")
    result = runner.run("task")

A skill location is a skill directory (containing SKILL.md) or a directory of skills.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# --------------------------------------------------------------------------
# Skills runner for the python `openai` client
# --------------------------------------------------------------------------

_FRONTMATTER = re.compile(r"\A﻿?---\s*\r?\n(.*?)\r?\n---\s*\r?\n?(.*)\Z", re.DOTALL)
_MAX_OUTPUT_CHARS = 20_000


def _decode(data: bytes) -> str:
    """Decode subprocess output: UTF-8 first, else the Windows OEM code page.

    Python scripts emit UTF-8 (PYTHONIOENCODING) but cmd.exe error messages use
    the console OEM code page (e.g. cp950), which is not valid UTF-8.
    """
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        encoding = "utf-8"
        if sys.platform == "win32":
            import ctypes

            encoding = f"cp{ctypes.windll.kernel32.GetOEMCP()}"
        return data.decode(encoding, errors="replace")


@dataclass(slots=True)
class Skill:
    name: str
    description: str
    path: Path  # directory holding SKILL.md
    body: str
    metadata: dict[str, Any] = field(default_factory=dict)


def parse_skill(skill_md: Path) -> Skill:
    """Parse one SKILL.md (YAML frontmatter + markdown body)."""
    text = skill_md.read_text(encoding="utf-8")
    match = _FRONTMATTER.match(text)
    meta: dict[str, Any] = {}
    body = text
    if match:
        import yaml

        loaded = yaml.safe_load(match.group(1))
        meta = loaded if isinstance(loaded, dict) else {}
        body = match.group(2)
    return Skill(
        name=str(meta.get("name") or skill_md.parent.name),
        description=" ".join(str(meta.get("description") or "").split()),
        path=skill_md.parent.resolve(),
        body=body.strip(),
        metadata=meta,
    )


def load_skills(*locations: str | os.PathLike[str]) -> dict[str, Skill]:
    """Load skills from skill dirs, or dirs containing skill dirs (recursive)."""
    skills: dict[str, Skill] = {}
    for location in locations:
        root = Path(location).expanduser()
        if not root.exists():
            raise FileNotFoundError(f"skill location not found: {root}")
        files = [root] if root.is_file() else sorted(root.rglob("SKILL.md"))
        for skill_md in files:
            if skill_md.name == "SKILL.md":
                skill = parse_skill(skill_md)
                skills.setdefault(skill.name, skill)
    return skills


@dataclass(slots=True)
class RunResult:
    content: str
    steps: list[dict[str, Any]]  # executed tool calls: name/arguments/result
    turns: int
    finished: bool  # False if max_turns hit before a final answer


def _tool(name: str, description: str, /, **props: str) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {k: {"type": "string", "description": v} for k, v in props.items()},
                "required": list(props),
            },
        },
    }


_TOOLS: list[dict[str, Any]] = [
    _tool(
        "load_skill",
        "Load the full instructions of a skill from the available skills list. "
        "Call this first when a skill matches the task. Returns the instructions, "
        "the skill directory and its bundled files.",
        name="Skill name.",
    ),
    _tool(
        "read_file",
        "Read a UTF-8 text file. Relative paths resolve against the last loaded "
        "skill directory, then the working directory.",
        path="File path.",
    ),
    _tool(
        "write_file",
        "Write a UTF-8 text file; relative paths resolve against the working directory.",
        path="File path.",
        content="Full file content.",
    ),
    _tool(
        "run_command",
        "Run a shell command in the working directory; returns exit code, stdout "
        "and stderr. Use absolute paths for skill scripts.",
        command="Shell command line.",
    ),
]

_SYSTEM = """You are an agent that completes the user's task using skills.

Skills are folders of instructions, scripts and references. Only their names and
descriptions are listed below. Workflow:
1. If a skill matches the task, call load_skill(name) and follow its instructions exactly.
2. A skill's SKILL.md may route you to reference files (e.g. "load ... in order").
   Before answering, read_file every reference that SKILL.md's routing selects for
   this task; do not answer from SKILL.md alone when it tells you to load more.
3. Only run scripts that appear in the "Bundled files" list returned by load_skill.
   Never invent a script or command; if no script is listed, do the work yourself
   by reading references and reasoning. Call listed scripts by absolute path.
4. Do the work with tools; never claim a result you did not obtain from a tool.
5. When finished, reply with the final answer for the user and no tool call.

Available skills:
{catalog}

Working directory: {workdir}
"""


class SkillsRunner:
    """Drive a skill-enabled agent loop on an OpenAI-compatible chat API.

    Life cycle: discover -> catalog in system prompt -> model activates a skill
    (load_skill) -> model reads resources / runs scripts (tool loop) -> final
    answer. Tool errors are returned to the model so it can self-correct.
    """

    def __init__(
        self,
        skills: str | os.PathLike[str] | list[str | os.PathLike[str]] | dict[str, Skill],
        *,
        client: Any = None,
        base_url: str = "http://localhost:19001/v1",
        api_key: str = "sk-no-key-required",
        model: str = "sonnet",
        workdir: str | os.PathLike[str] | None = None,
        max_turns: int = 20,
        command_timeout: float = 120.0,
        temperature: float | None = 0.0,
        verbose: bool = False,
    ) -> None:
        if isinstance(skills, dict):
            self.skills = skills
        else:
            locations = [skills] if isinstance(skills, (str, os.PathLike)) else skills
            self.skills = load_skills(*locations)
        if client is None:
            from openai import OpenAI

            client = OpenAI(base_url=base_url, api_key=api_key)
        self.client = client
        self.model = model
        self.workdir = Path(workdir or os.getcwd()).resolve()
        self.max_turns = max_turns
        self.command_timeout = command_timeout
        self.temperature = temperature
        self.verbose = verbose
        self._current: Skill | None = None
        self._read: set[Path] = set()

    def system_prompt(self) -> str:
        catalog = "\n".join(f"- {s.name}: {s.description}" for s in self.skills.values())
        return _SYSTEM.format(catalog=catalog or "(none)", workdir=self.workdir)

    # ---- tools ----------------------------------------------------------
    @staticmethod
    def _resolve(raw: str, base: Path) -> Path:
        path = Path(raw).expanduser()
        return path if path.is_absolute() else (base / path).resolve()

    @staticmethod
    def _clip(text: str) -> str:
        if len(text) <= _MAX_OUTPUT_CHARS:
            return text
        half = _MAX_OUTPUT_CHARS // 2
        return f"{text[:half]}\n...[truncated {len(text) - _MAX_OUTPUT_CHARS} chars]...\n{text[-half:]}"

    def _tool_load_skill(self, name: str) -> str:
        skill = self.skills.get(name)
        if skill is None:
            return f"error: unknown skill {name!r}. Available: {', '.join(self.skills)}"
        self._current = skill
        files = sorted(
            p.relative_to(skill.path).as_posix()
            for p in skill.path.rglob("*")
            if p.is_file() and p.name != "SKILL.md" and "__pycache__" not in p.parts
        )
        listing = "\n".join(f"- {f}" for f in files[:100]) or "(none)"
        return (
            f"Skill directory: {skill.path}\n"
            f"Bundled files (relative to the skill directory):\n{listing}\n\n"
            f"=== SKILL.md ===\n{skill.body}"
        )

    def _tool_read_file(self, path: str) -> str:
        candidates = [self._resolve(path, self.workdir)]
        if self._current:
            candidates.insert(0, self._resolve(path, self._current.path))
        for target in candidates:
            if target.is_file():
                self._read.add(target)
                return self._clip(target.read_text(encoding="utf-8", errors="replace"))
        return f"error: file not found: {candidates[0]}"

    def _tool_write_file(self, path: str, content: str) -> str:
        target = self._resolve(path, self.workdir)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"wrote {len(content)} chars to {target}"

    def _tool_run_command(self, command: str) -> str:
        try:
            proc = subprocess.run(
                command,
                shell=True,
                cwd=self.workdir,
                capture_output=True,
                timeout=self.command_timeout,
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            )
        except subprocess.TimeoutExpired:
            return f"error: command timed out after {self.command_timeout:g}s"
        return self._clip(
            f"exit_code: {proc.returncode}\nstdout:\n{_decode(proc.stdout)}\n"
            f"stderr:\n{_decode(proc.stderr)}"
        )

    def _unread_references(self) -> list[str]:
        """Bundled files that the loaded SKILL.md names but the model has not read."""
        skill = self._current
        if skill is None:
            return []
        return sorted(
            rel
            for p in skill.path.rglob("*")
            if p.is_file() and p.name != "SKILL.md" and "__pycache__" not in p.parts
            and p.suffix.lower() in (".md", ".txt")  # scripts are run, not read
            and p.resolve() not in self._read
            and (rel := p.relative_to(skill.path).as_posix()) in skill.body
        )

    def call_tool(self, name: str, raw_arguments: str | None) -> str:
        try:
            args = json.loads(raw_arguments or "{}")
            if not isinstance(args, dict):
                raise ValueError("arguments must be a JSON object")
        except ValueError as exc:
            return f"error: invalid tool arguments: {exc}"
        handler = getattr(self, f"_tool_{name}", None)
        if handler is None:
            return f"error: unknown tool {name!r}"
        try:
            return handler(**args)
        except TypeError as exc:
            return f"error: bad arguments for {name}: {exc}"
        except Exception as exc:  # surface to the model, never crash the loop
            return f"error: {type(exc).__name__}: {exc}"

    # ---- loop -----------------------------------------------------------
    def run(
        self,
        prompt: str,
        *,
        skill: str | None = None,
        history: list[dict[str, Any]] | None = None,
    ) -> RunResult:
        """Run `prompt` to completion. `skill` pre-activates a skill by name."""
        messages: list[dict[str, Any]] = [{"role": "system", "content": self.system_prompt()}]
        messages += history or []
        messages.append({"role": "user", "content": prompt})
        steps: list[dict[str, Any]] = []
        self._current = None
        self._read = set()
        nudged = False

        if skill is not None:
            args = json.dumps({"name": skill})
            result = self.call_tool("load_skill", args)
            call = {"id": "preload_0", "type": "function",
                    "function": {"name": "load_skill", "arguments": args}}
            messages.append({"role": "assistant", "content": "", "tool_calls": [call]})
            messages.append({"role": "tool", "tool_call_id": "preload_0", "content": result})
            steps.append({"name": "load_skill", "arguments": {"name": skill}, "result": result})

        extra: dict[str, Any] = {}
        if self.temperature is not None:
            extra["temperature"] = self.temperature

        for turn in range(1, self.max_turns + 1):
            response = self.client.chat.completions.create(
                model=self.model, messages=messages, tools=_TOOLS, **extra
            )
            message = response.choices[0].message
            entry: dict[str, Any] = {"role": "assistant", "content": message.content or ""}
            if message.tool_calls:
                entry["tool_calls"] = [
                    {"id": c.id, "type": "function",
                     "function": {"name": c.function.name, "arguments": c.function.arguments}}
                    for c in message.tool_calls
                ]
            messages.append(entry)

            if not message.tool_calls:
                # Models at higher temperature often answer before reading the
                # references their skill's routing selects: ask once to confirm.
                unread = self._unread_references()
                if unread and not nudged and turn < self.max_turns:
                    nudged = True
                    messages.append({"role": "user", "content": (
                        "Before finalizing: check the routing table in the loaded SKILL.md for "
                        "this task's text type. If it selects reference files you have not "
                        "read yet, read exactly those now. Unread files mentioned in SKILL.md: "
                        + ", ".join(unread)
                        + ". Do NOT read files meant for other text types. If you already "
                        "read everything your route selects, give the final answer now."
                    )})
                    continue
                return RunResult(message.content or "", steps, turn, True)

            for call in message.tool_calls:
                result = self.call_tool(call.function.name, call.function.arguments)
                if self.verbose:
                    print(f"[tool] {call.function.name} {call.function.arguments}\n  -> {result[:300]}",
                          file=sys.stderr)
                try:
                    parsed = json.loads(call.function.arguments or "{}")
                except ValueError:
                    parsed = call.function.arguments
                steps.append({"name": call.function.name, "arguments": parsed, "result": result})
                messages.append({"role": "tool", "tool_call_id": call.id, "content": result})

        return RunResult("", steps, self.max_turns, False)

