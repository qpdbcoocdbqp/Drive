"""Read-only MCP server for discovering and loading local skills."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any, Iterable, Sequence

# Codex launches MCP subprocesses with a sanitized environment that may omit
# PYTHONPATH. Support execution by absolute file path without relying on an
# installed package or inherited import configuration.
if not __package__:
    script_directory = Path(__file__).resolve().parent
    sys.path = [
        entry
        for entry in sys.path
        if Path(entry or ".").resolve() != script_directory
    ]
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    __package__ = "oai_skill.server"

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from ..skills.skills import SkillCatalog

MAX_FILES_PER_SKILL = 500
MAX_VIEW_BYTES = 1_000_000
SKILL_ROOTS_ENV = "OAI_SKILL_ROOTS"
READ_ONLY = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
)


class SkillMcpServer:
    """Own MCP transport state and an injected skill catalog."""

    def __init__(
        self,
        roots: Iterable[str | Path] | None = None,
        *,
        catalog: SkillCatalog | None = None,
    ) -> None:
        self.roots = self.resolve_roots(roots)
        self.catalog = catalog or SkillCatalog(self.roots)
        self.server = FastMCP(
            "skill-manager",
            instructions=(
                "Read-only local skill manager. A skill is instructions, not a command or "
                "an MCP server. Use skill_list to discover skills, skill_files to inspect "
                "their files, and skill_view to load SKILL.md or a required supporting file. "
                "Never execute a skill name in the shell."
            ),
        )
        self._register()

    @staticmethod
    def default_roots() -> tuple[Path, ...]:
        repository_root = Path(__file__).resolve().parents[3]
        return (
            repository_root / ".agents" / "skills",
            Path.home() / ".codex" / "skills",
        )

    @staticmethod
    def environment_roots() -> tuple[Path, ...]:
        value = os.getenv(SKILL_ROOTS_ENV, "").strip()
        return tuple(Path(item) for item in value.split(os.pathsep) if item.strip())

    @classmethod
    def resolve_roots(
        cls, roots: Iterable[str | Path] | None = None
    ) -> tuple[Path, ...]:
        selected = tuple(Path(root).expanduser().resolve() for root in (roots or ()))
        if not selected:
            selected = tuple(root.expanduser().resolve() for root in cls.environment_roots())
        if not selected:
            selected = tuple(root.expanduser().resolve() for root in cls.default_roots())
        return selected

    def configure(self, roots: Iterable[str | Path] | None = None) -> tuple[Path, ...]:
        self.roots = self.resolve_roots(roots)
        self.catalog = SkillCatalog(self.roots)
        return self.roots

    def _register(self) -> None:
        self.server.tool(
            title="List local skills", annotations=READ_ONLY, structured_output=True
        )(self.skill_list)
        self.server.tool(
            title="List skill files", annotations=READ_ONLY, structured_output=True
        )(self.skill_files)
        self.server.tool(
            title="Load skill instructions or a supporting file", annotations=READ_ONLY
        )(self.skill_view)
        self.server.resource(
            "skill://{name}", title="Skill instructions",
            description="Read a local skill's main SKILL.md instructions.",
            mime_type="text/markdown",
        )(self.skill_instructions_resource)
        self.server.resource(
            "skill://{name}/{folder}/{file_name}", title="Skill supporting file",
            description="Read one supporting file from a local skill.",
            mime_type="text/markdown",
        )(self.skill_support_resource)

    @staticmethod
    def _safe_files(skill_directory: Path) -> tuple[list[dict[str, Any]], bool]:
        base = skill_directory.resolve()
        files: list[dict[str, Any]] = []
        candidates = sorted(
            skill_directory.rglob("*"),
            key=lambda item: (
                item.relative_to(skill_directory).as_posix() != "SKILL.md",
                item.relative_to(skill_directory).as_posix().lower(),
            ),
        )
        for candidate in candidates:
            relative = candidate.relative_to(skill_directory)
            if any(part.startswith(".") for part in relative.parts):
                continue
            try:
                resolved = candidate.resolve()
                resolved.relative_to(base)
            except ValueError:
                continue
            if not resolved.is_file():
                continue
            if len(files) >= MAX_FILES_PER_SKILL:
                return files, True
            files.append({"path": relative.as_posix(), "size_bytes": resolved.stat().st_size})
        return files, False

    def _view_target(self, name: str, file_path: str | None) -> tuple[Path, str | None]:
        skill = self.catalog.get(name)
        selected = file_path.strip() if file_path else None
        if selected is None:
            return skill.path.resolve(), None
        normalized = selected.replace("\\", "/")
        if ".." in normalized.split("/"):
            raise ValueError(f"Path traversal not allowed in file_path: {file_path!r}")
        target = (skill.directory / normalized).resolve()
        try:
            target.relative_to(skill.directory.resolve())
        except ValueError as exc:
            raise ValueError(f"file_path {file_path!r} escapes the skill directory.") from exc
        if not target.is_file():
            raise FileNotFoundError(
                f"Supporting file {file_path!r} not found in skill {name!r}."
            )
        return target, normalized

    def skill_list(self, refresh: bool = False) -> dict[str, Any]:
        """List available local skills and their compact metadata."""
        skills = self.catalog.discover(refresh=refresh)
        return {
            "roots": [str(root) for root in self.roots],
            "skills": [
                {
                    "name": skill.name,
                    "description": skill.description,
                    "path": str(skill.path),
                    "relative_directory": skill.path.parent.relative_to(skill.root).as_posix(),
                }
                for skill in sorted(skills.values(), key=lambda item: item.name.lower())
            ],
        }

    def skill_files(self, name: str) -> dict[str, Any]:
        """List files inside one skill without loading their contents."""
        skill = self.catalog.get(name)
        files, truncated = self._safe_files(skill.directory)
        return {
            "name": skill.name, "directory": str(skill.directory),
            "files": files, "truncated": truncated,
        }

    def skill_view(self, name: str, file_path: str | None = None) -> str:
        """Load a skill's instructions or one required supporting file."""
        target, selected = self._view_target(name, file_path)
        size = target.stat().st_size
        if size > MAX_VIEW_BYTES:
            raise ValueError(
                f"Skill file is {size} bytes; the maximum is {MAX_VIEW_BYTES} bytes."
            )
        content = self.catalog.view(name, file_path=selected)
        return f"Skill: {name}\nFile: {selected or 'SKILL.md'}\n\n{content}"

    def skill_instructions_resource(self, name: str) -> str:
        return self.skill_view(name)

    def skill_support_resource(self, name: str, folder: str, file_name: str) -> str:
        return self.skill_view(name, f"{folder}/{file_name}")

    def run(self) -> None:
        self.server.run(transport="stdio")


_default = SkillMcpServer()
server = _default.server


def configure_skill_roots(roots: Iterable[str | Path] | None = None) -> tuple[Path, ...]:
    return _default.configure(roots)


def skill_list(refresh: bool = False) -> dict[str, Any]:
    return _default.skill_list(refresh)


def skill_files(name: str) -> dict[str, Any]:
    return _default.skill_files(name)


def skill_view(name: str, file_path: str | None = None) -> str:
    return _default.skill_view(name, file_path)


def skill_instructions_resource(name: str) -> str:
    return _default.skill_instructions_resource(name)


def skill_support_resource(name: str, folder: str, file_name: str) -> str:
    return _default.skill_support_resource(name, folder, file_name)


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", action="append", default=[], help="Trusted skill root.")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = _parse_args(argv)
    SkillMcpServer(args.root).run()


if __name__ == "__main__":
    main()


__all__ = [
    "MAX_FILES_PER_SKILL", "MAX_VIEW_BYTES", "READ_ONLY", "SKILL_ROOTS_ENV",
    "SkillMcpServer", "configure_skill_roots", "main", "server", "skill_files",
    "skill_instructions_resource", "skill_list", "skill_support_resource", "skill_view",
]
