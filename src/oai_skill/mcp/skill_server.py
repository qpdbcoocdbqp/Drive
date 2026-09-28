"""Read-only MCP tools for discovering and loading local Codex skills."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any, Iterable, Sequence

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

try:
    from ..skills.runner import SkillCatalog
except ImportError:  # pragma: no cover - direct script compatibility
    from oai_skill.skills.runner import SkillCatalog


MAX_FILES_PER_SKILL = 500
MAX_VIEW_BYTES = 1_000_000
SKILL_ROOTS_ENV = "OAI_SKILL_ROOTS"

READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)

server = FastMCP(
    "skill-manager",
    instructions=(
        "Read-only local skill manager. A skill is instructions, not a command or "
        "an MCP server. Use skill_list to discover skills, skill_files to inspect "
        "their files, and skill_view to load SKILL.md or a required supporting file. "
        "Never execute a skill name in the shell."
    ),
)

_configured_roots: tuple[Path, ...] | None = None
_catalog: SkillCatalog | None = None


def _default_roots() -> tuple[Path, ...]:
    repository_root = Path(__file__).resolve().parents[3]
    return (
        repository_root / ".agents" / "skills",
        Path.home() / ".codex" / "skills",
    )


def _environment_roots() -> tuple[Path, ...]:
    value = os.getenv(SKILL_ROOTS_ENV, "").strip()
    if not value:
        return ()
    return tuple(Path(item) for item in value.split(os.pathsep) if item.strip())


def configure_skill_roots(roots: Iterable[str | Path] | None = None) -> tuple[Path, ...]:
    """Configure trusted skill roots and reset the discovery cache."""
    global _catalog, _configured_roots

    selected = tuple(Path(root).expanduser().resolve() for root in (roots or ()))
    if not selected:
        selected = tuple(root.expanduser().resolve() for root in _environment_roots())
    if not selected:
        selected = tuple(root.expanduser().resolve() for root in _default_roots())

    _configured_roots = selected
    _catalog = SkillCatalog(selected)
    return selected


def _get_catalog() -> SkillCatalog:
    global _catalog
    if _catalog is None:
        configure_skill_roots()
    assert _catalog is not None
    return _catalog


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


def _view_target(name: str, file_path: str | None) -> tuple[Path, str | None]:
    skill = _get_catalog().get(name)
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
        raise ValueError(
            f"file_path {file_path!r} escapes the skill directory."
        ) from exc
    if not target.is_file():
        raise FileNotFoundError(
            f"Supporting file {file_path!r} not found in skill {name!r}."
        )
    return target, normalized


@server.tool(
    title="List local skills",
    annotations=READ_ONLY,
    structured_output=True,
)
def skill_list(refresh: bool = False) -> dict[str, Any]:
    """List available local skills and their compact metadata.

    Call this when the requested skill name is unknown. Use ``skill_view`` after
    selecting a skill; listing does not load its instructions.
    """
    catalog = _get_catalog()
    skills = catalog.discover(refresh=refresh)
    roots = _configured_roots or catalog.roots

    return {
        "roots": [str(root) for root in roots],
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


@server.tool(
    title="List skill files",
    annotations=READ_ONLY,
    structured_output=True,
)
def skill_files(name: str) -> dict[str, Any]:
    """List files inside one skill without loading their contents."""
    skill = _get_catalog().get(name)
    files, truncated = _safe_files(skill.directory)
    return {
        "name": skill.name,
        "directory": str(skill.directory),
        "files": files,
        "truncated": truncated,
    }


@server.tool(
    title="Load skill instructions or a supporting file",
    annotations=READ_ONLY,
)
def skill_view(name: str, file_path: str | None = None) -> str:
    """Load a skill's instructions or one required supporting file.

    Omit ``file_path`` to load the main ``SKILL.md`` instructions. Supply a path
    such as ``references/style-pass.md`` only when the loaded skill requires it.
    This is the correct way to load local skill files; do not treat ``name`` as
    an MCP server name or an executable command.
    """
    target, selected = _view_target(name, file_path)
    size = target.stat().st_size
    if size > MAX_VIEW_BYTES:
        raise ValueError(
            f"Skill file is {size} bytes; the maximum is {MAX_VIEW_BYTES} bytes."
        )
    content = _get_catalog().view(name, file_path=selected)

    selected_file = selected or "SKILL.md"
    return f"Skill: {name}\nFile: {selected_file}\n\n{content}"


@server.resource(
    "skill://{name}",
    title="Skill instructions",
    description=(
        "Read a local skill's main SKILL.md instructions. Use this resource "
        "when the client exposes MCP resources but not custom MCP tools."
    ),
    mime_type="text/markdown",
)
def skill_instructions_resource(name: str) -> str:
    return skill_view(name)


@server.resource(
    "skill://{name}/{folder}/{file_name}",
    title="Skill supporting file",
    description=(
        "Read one supporting file from a local skill, for example "
        "skill://sepia/references/style-pass.md."
    ),
    mime_type="text/markdown",
)
def skill_support_resource(name: str, folder: str, file_name: str) -> str:
    return skill_view(name, f"{folder}/{file_name}")


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        action="append",
        default=[],
        help=(
            "Trusted skill root. Repeat for multiple roots. Defaults to the repository "
            ".agents/skills directory and the user's .codex/skills directory."
        ),
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = _parse_args(argv)
    configure_skill_roots(args.root)
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
