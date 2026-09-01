"""Load SKILL.md documents and make them available to an OpenAI chat model.

The module deliberately treats a skill as instructions, not executable Python.
The model can discover compact metadata in its system prompt and requests the
full document through ``skill_view`` only when it is relevant.  This mirrors
Hermes' progressive-disclosure design while remaining independent of Hermes.

Progressive-disclosure tiers (matching Hermes):
  Tier 1 — compact index in the system prompt (name + description only).
  Tier 2 — full skill body loaded on demand via the ``skill_view`` tool call.
  Tier 3 — supporting files (references/, templates/, assets/, scripts/) loaded
            via ``skill_view`` with an explicit ``file_path`` argument.

Explicit invocation (``skills=[...]`` to ``run()``) mirrors Hermes'
``/skill-name`` slash-command path: the body is embedded directly in the user
message with Hermes-compatible scaffolding markers, so the model does not need
a tool-call round-trip to read it.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


_DEBUG = False
_FRONTMATTER = re.compile(r"\A---\s*\r?\n(.*?)\r?\n---\s*\r?\n?", re.DOTALL)
_SLUG = re.compile(r"[^a-z0-9-]+")

# Support directories that live inside a skill package and are loaded
# explicitly via skill_view(file_path=…).  They must not be treated as
# independent skill roots during discovery.
_SKILL_SUPPORT_DIRS = frozenset(("references", "templates", "assets", "scripts"))

# Scaffolding markers — byte-identical to Hermes' single-skill path so any
# tool that strips skill scaffolding from memory (e.g. extract_user_instruction)
# works correctly on messages produced here.
_SKILL_INVOCATION_PREFIX = "[IMPORTANT: The user has invoked the "
_SINGLE_SKILL_MARKER = "The full skill content is loaded below.]"
_SINGLE_SKILL_INSTRUCTION = (
    "The user has provided the following instruction alongside the skill invocation: "
)


class SkillNotFoundError(LookupError):
    """Raised when a requested skill does not exist in the configured roots."""


@dataclass(frozen=True)
class Skill:
    """The discoverable metadata and body of one ``SKILL.md`` file."""

    name: str
    description: str
    path: Path
    root: Path
    body: str

    @property
    def directory(self) -> Path:
        return self.path.parent

    @property
    def command(self) -> str:
        return "/" + _slug(self.name)


def _slug(value: str) -> str:
    return _SLUG.sub("-", value.lower().replace("_", "-").replace(" ", "-")).strip("-")


def _scalar(value: str) -> str:
    """Strip optional surrounding quotes from a single frontmatter value."""
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _is_support_path(path: Path, root: Path) -> bool:
    """Return True when *path* is inside a skill-package support directory.

    ``references/``, ``templates/``, ``assets/``, and ``scripts/`` are
    progressive-disclosure support areas when they sit directly inside a
    directory that already contains ``SKILL.md``.  A skill named ``scripts``
    at the root level is fine; only the nested case is excluded.
    """
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        return False
    for idx, part in enumerate(parts[:-1]):
        if part not in _SKILL_SUPPORT_DIRS or idx == 0:
            continue
        candidate = root.joinpath(*parts[:idx])
        if (candidate / "SKILL.md").exists():
            return True
    return False


def _parse_skill_document(path: Path, root: Path) -> Skill:
    """Parse a SKILL.md file, stripping a leading UTF-8 BOM if present."""
    text = path.read_text(encoding="utf-8-sig")
    match = _FRONTMATTER.match(text)
    metadata: dict[str, str] = {}
    body = text
    if match:
        body = text[match.end():]
        for line in match.group(1).splitlines():
            key, separator, value = line.partition(":")
            if separator and key.strip() in {"name", "description"}:
                metadata[key.strip()] = _scalar(value)
    name = metadata.get("name") or path.parent.name
    description = metadata.get("description") or next(
        (line.strip().lstrip("#").strip() for line in body.splitlines() if line.strip()),
        "",
    )
    return Skill(name=name, description=description, path=path, root=root, body=body.strip())


class SkillCatalog:
    """A deterministic, read-only catalog of one or more trusted skill roots.

    Pass a *list* (or any iterable) of directory paths — not a bare string::

        catalog = SkillCatalog(["./skills"])          # one root
        catalog = SkillCatalog(["./skills", "~/shared-skills"])  # multiple roots

    Earlier roots win when two skills share the same name (same as Hermes'
    project → local → external precedence).
    """

    def __init__(self, roots: Iterable[str | Path]):
        self.roots = tuple(Path(root).expanduser().resolve() for root in roots)
        if not self.roots:
            raise ValueError("at least one skill root is required")
        self._skills: dict[str, Skill] | None = None

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def discover(self, *, refresh: bool = False) -> Mapping[str, Skill]:
        """Return all skills keyed by frontmatter name; earlier roots win."""
        if self._skills is not None and not refresh:
            return self._skills
        found: dict[str, Skill] = {}
        for root in self.roots:
            if not root.is_dir():
                continue
            for path in sorted(root.rglob("SKILL.md")):
                # Never follow a symlink that exits the trusted root.
                try:
                    path.resolve().relative_to(root)
                except ValueError:
                    continue
                # Skip hidden directories (e.g. .git, .archive).
                if any(part.startswith(".") for part in path.relative_to(root).parts):
                    continue
                # Skip skill-package support subdirectories (references/ etc.).
                if _is_support_path(path, root):
                    continue
                skill = _parse_skill_document(path, root)
                found.setdefault(skill.name, skill)
        self._skills = found
        return found

    # ------------------------------------------------------------------
    # Resolution
    # ------------------------------------------------------------------

    def get(self, name: str) -> Skill:
        """Resolve a skill by frontmatter name, slash command, or relative directory path.

        Resolution order (mirrors Hermes ``skill_view`` strategy 1–3):
        1. Exact frontmatter-name match.
        2. Slug match (hyphens/underscores/spaces normalised).
        3. Relative directory path match (e.g. ``"mlops/axolotl"``).
        """
        candidate = name.strip().lstrip("/")
        skills = self.discover()

        # 1. Exact name
        if candidate in skills:
            return skills[candidate]

        # 2. Slug / slash-command normalisation
        wanted_slug = _slug(candidate)
        matches = [s for s in skills.values() if _slug(s.name) == wanted_slug]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise SkillNotFoundError(
                f"Ambiguous skill {name!r}: {len(matches)} slug matches — "
                "use the full relative path to disambiguate."
            )

        # 3. Relative directory path (useful for categorised skills)
        wanted_path = Path(candidate).as_posix().rstrip("/")
        matches = [
            s for s in skills.values()
            if s.path.parent.relative_to(s.root).as_posix() == wanted_path
        ]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise SkillNotFoundError(
                f"Ambiguous skill {name!r}: {len(matches)} path matches — "
                "use the exact frontmatter name."
            )

        raise SkillNotFoundError(f"Unknown skill: {name!r}")

    # ------------------------------------------------------------------
    # Index (Tier 1 — system prompt)
    # ------------------------------------------------------------------

    def index(self) -> str:
        """Compact, prompt-stable skill listing — name and description only.

        Matches Hermes' system-prompt strategy: the model sees a short index
        and calls ``skill_view`` when a skill is relevant, rather than having
        every skill body in the context at all times.
        """
        skills = self.discover()
        if not skills:
            return "(No skills installed.)"

        # Group by top-level category directory, mirroring Hermes' category display.
        by_category: dict[str, list[Skill]] = {}
        for skill in sorted(skills.values(), key=lambda s: s.name.lower()):
            parts = skill.path.parent.relative_to(skill.root).parts
            category = parts[0] if len(parts) >= 2 else "general"
            by_category.setdefault(category, []).append(skill)

        lines: list[str] = []
        for category in sorted(by_category):
            lines.append(f"  {category}:")
            for skill in by_category[category]:
                desc = skill.description.rstrip()
                lines.append(f"    - {skill.name}: {desc}" if desc else f"    - {skill.name}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # View (Tier 2 — full body, Tier 3 — supporting files)
    # ------------------------------------------------------------------

    def view(self, name: str, *, file_path: str | None = None) -> str:
        """Return skill content for the model.

        Without *file_path*: returns the full skill body (Tier 2) with an
        absolute skill-directory annotation and a catalogue of supporting files.

        With *file_path*: returns the content of that supporting file (Tier 3).
        Path traversal (``..``) is rejected.
        """
        skill = self.get(name)

        # --- Tier 3: supporting file ---
        if file_path is not None:
            fp = file_path.replace("\\", "/")
            if ".." in fp.split("/"):
                raise ValueError(f"Path traversal not allowed in file_path: {file_path!r}")
            target = skill.directory / fp
            # Ensure the resolved path stays inside the skill directory.
            try:
                target.resolve().relative_to(skill.directory.resolve())
            except ValueError:
                raise ValueError(f"file_path {file_path!r} escapes the skill directory.")
            if not target.is_file():
                raise FileNotFoundError(
                    f"Supporting file {file_path!r} not found in skill {name!r}."
                )
            return target.read_text(encoding="utf-8-sig")

        # --- Tier 2: full body ---
        extras: list[str] = []
        for folder in ("references", "templates", "scripts", "assets"):
            base = skill.directory / folder
            if base.is_dir():
                extras.extend(
                    str(item.relative_to(skill.directory))
                    for item in sorted(base.rglob("*"))
                    if item.is_file()
                )

        parts = [skill.body, f"\n[Skill directory: {skill.directory}]"]
        if extras:
            parts.append(
                "[Supporting files — load with skill_view(name, file_path=...)]\n"
                + "\n".join(f"- {f}" for f in extras)
            )
        return "\n".join(parts)


class OpenAISkillRunner:
    """Run a chat-completions conversation that can progressively load skills.

    ``client`` is normally ``openai.OpenAI()``.  Keeping it injected makes the
    class easy to test and lets callers use an Azure-compatible client too.

    Usage::

        runner = OpenAISkillRunner(client, SkillCatalog(["./skills"]), model="gpt-4.1-mini")

        # Auto-discovery: model reads the index and may call skill_view itself.
        result = runner.run("Review this pull request")

        # Explicit load: one or more skills are embedded in the user message
        # before the model sees the prompt (Hermes /skill-name equivalent).
        result = runner.run("Review this pull request", skills=["code-review"])
    """

    _TOOL: dict[str, Any] = {
        "type": "function",
        "function": {
            "name": "skill_view",
            "description": (
                "Load the complete instructions for a named skill before doing a relevant task. "
                "Also use this to load a supporting file within a skill by passing file_path."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Skill name exactly as listed in the available_skills index.",
                    },
                    "file_path": {
                        "type": "string",
                        "description": (
                            "Optional relative path to a supporting file inside the skill, "
                            "e.g. 'references/api.md' or 'templates/report.md'."
                        ),
                    },
                },
                "required": ["name"],
                "additionalProperties": False,
            },
        },
    }

    _BASH_TOOL: dict[str, Any] = {
        "type": "function",
        "function": {
            "name": "bash",
            "description": (
                "Execute a shell command and return its stdout + stderr. "
                "Use this to run git commands (e.g. git diff, git log), "
                "read files, or gather any information from the local environment."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The shell command to execute.",
                    },
                    "cwd": {
                        "type": "string",
                        "description": (
                            "Optional working directory for the command. "
                            "Defaults to the current working directory."
                        ),
                    },
                },
                "required": ["command"],
                "additionalProperties": False,
            },
        },
    }

    def __init__(self, client: Any, catalog: SkillCatalog, *, model: str, cwd: str | Path | None = None) -> None:
        self.client = client
        self.catalog = catalog
        self.model = model
        self.cwd = Path(cwd).resolve() if cwd else None

    # ------------------------------------------------------------------
    # System prompt (Tier 1)
    # ------------------------------------------------------------------

    def system_prompt(self) -> str:
        """Build the stable system prompt with a compact skill index.

        Mirrors Hermes' ``## Skills`` block: the model sees name + description
        only and is instructed to load a skill with ``skill_view`` before
        answering any task where a skill is even partially relevant.
        """
        index = self.catalog.index()
        return (
            "## Skills\n"
            "Before replying, scan the skills below. If a skill name or description "
            "is relevant — even partially — to your task, you MUST load it with "
            "skill_view(name) and follow its instructions. Err on the side of loading: "
            "skills contain specialised workflows, API details, and quality standards "
            "that outperform general-purpose approaches.\n"
            "Only proceed without loading a skill if genuinely none are relevant.\n\n"
            "<available_skills>\n"
            + index
            + "\n</available_skills>"
        )

    # ------------------------------------------------------------------
    # Explicit invocation (Hermes /skill-name path)
    # ------------------------------------------------------------------

    def invocation_message(self, prompt: str, skills: Sequence[str]) -> str:
        """Build the user message for an explicit skill invocation.

        Each skill is embedded using Hermes' scaffolding markers so any
        downstream tool that strips skill bodies from memory (e.g.
        ``extract_user_instruction_from_skill_message``) works correctly.

        The skill body is followed by the user's instruction, matching the
        layout of ``_build_skill_message`` in Hermes' ``skill_commands.py``.
        """
        if not skills:
            return prompt

        blocks: list[str] = []
        for skill_name in skills:
            skill = self.catalog.get(skill_name)
            body = self.catalog.view(skill.name)
            activation_note = (
                f'{_SKILL_INVOCATION_PREFIX}"{skill.name}" skill, indicating they want '
                f"you to follow its instructions. {_SINGLE_SKILL_MARKER}"
            )
            blocks.append(activation_note + "\n\n" + body)

        stable_scaffold = "\n\n".join(blocks)
        return (
            stable_scaffold
            + "\n\n"
            + _SINGLE_SKILL_INSTRUCTION
            + prompt
        )

    # ------------------------------------------------------------------
    # Tool-call loop
    # ------------------------------------------------------------------

    def run(
        self,
        prompt: str,
        *,
        skills: Sequence[str] = (),
        temperature: float | None = None,
        max_tool_rounds: int = 8,
    ) -> str:
        """Return the model's final text response.

        Explicit *skills* are embedded directly in the user message (no tool
        round-trip required).  The model may still call ``skill_view`` for
        additional skills it finds relevant in the index.

        Raises ``RuntimeError`` if the model keeps calling tools for more than
        *max_tool_rounds* iterations without producing a final text response.
        """
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt()},
            {"role": "user", "content": self.invocation_message(prompt, skills)},
        ]

        for round_num in range(max_tool_rounds + 1):
            kwargs: dict[str, Any] = {
                "model": self.model,
                "messages": messages,
                "tools": [self._TOOL, self._BASH_TOOL],
            }
            if temperature is not None:
                kwargs["temperature"] = temperature

            response = self.client.chat.completions.create(**kwargs)
            message = response.choices[0].message
            tool_calls = getattr(message, "tool_calls", None) or []

            # --- debug ---
            if _DEBUG:
                finish_reason = response.choices[0].finish_reason
                print(f"[Round {round_num}] finish_reason={finish_reason!r}  tool_calls={len(tool_calls)}")
                if message.content:
                    preview = message.content[:300].replace("\n", " ")
                    print(f"  content preview: {preview!r}")
                for i, call in enumerate(tool_calls):
                    print(f"  tool_call[{i}]: {call.function.name}({call.function.arguments})")
            # --- end debug ---

            messages.append(self._assistant_message(message))

            if not tool_calls:
                return getattr(message, "content", None) or ""

            for call in tool_calls:
                result = self._dispatch_tool(call)
                if _DEBUG:
                    print(f"  -> tool result preview: {str(result)[:200]!r}")  # debug
                messages.append({
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": result,
                })
    
        raise RuntimeError(f"Skill tool loop exceeded {max_tool_rounds} rounds without a final response.")

    def _dispatch_tool(self, call: Any) -> str:
        """Execute a single tool call and return the string result for the model."""
        if call.function.name == "skill_view":
            try:
                args: dict[str, Any] = json.loads(call.function.arguments)
                name: str = args["name"]
                file_path: str | None = args.get("file_path")
                return self.catalog.view(name, file_path=file_path)
            except (KeyError, TypeError, ValueError, SkillNotFoundError, FileNotFoundError) as exc:
                return f"skill_view error: {exc}"

        if call.function.name == "bash":
            try:
                args = json.loads(call.function.arguments)
                command: str = args["command"]
                cwd = args.get("cwd") or self.cwd
                result = subprocess.run(
                    command,
                    shell=True,
                    capture_output=True,
                    text=True,
                    cwd=cwd,
                    timeout=60,
                )
                output = result.stdout
                if result.stderr:
                    output += "\n[stderr]\n" + result.stderr
                if result.returncode != 0:
                    output += f"\n[exit code: {result.returncode}]"
                return output or "(no output)"
            except subprocess.TimeoutExpired:
                return "bash error: command timed out after 60 seconds"
            except Exception as exc:
                return f"bash error: {exc}"

        return f"Unknown tool: {call.function.name!r}"

    @staticmethod
    def _assistant_message(message: Any) -> dict[str, Any]:
        """Serialise an assistant turn, preserving tool_calls when present."""
        result: dict[str, Any] = {
            "role": "assistant",
            "content": getattr(message, "content", None),
        }
        calls = getattr(message, "tool_calls", None)
        if calls:
            result["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.function.name,
                        "arguments": call.function.arguments,
                    },
                }
                for call in calls
            ]
        return result
