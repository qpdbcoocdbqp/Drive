"""Build stable developer instructions for progressive skill discovery."""

from __future__ import annotations

from .catalog import SkillCatalog


def build_skills_developer_instructions(catalog: SkillCatalog) -> str:
    index = catalog.index()
    return (
        "Local skills are read-only instructions exposed by the skill_manager MCP "
        "server. Before answering, inspect the compact index below. When a skill is "
        "relevant, load it with skill_view and follow it. Load supporting files only "
        "when the skill instructions require them. Never execute a skill name as a "
        "shell command.\n\n"
        f"{index}"
    )
