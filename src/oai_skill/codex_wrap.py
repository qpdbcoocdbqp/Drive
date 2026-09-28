"""Compatibility ASGI entry point for the reorganized API server.

Prefer ``oai_skill.api.app:app`` for new deployments.
"""

from __future__ import annotations

import json
import os

import uvicorn

try:
    from .api.app import app, create_app
except ImportError:  # Executed directly from the repository root.
    from src.oai_skill.api.app import app, create_app

__all__ = [
    "app",
    "create_app",
    "assert_skill_mcp_called",
    "assert_no_skill_mcp_called",
]


def assert_skill_mcp_called(function_calls, skill_name):
    """Backward-compatible audit helper retained for existing callers."""
    matching = [
        call
        for call in function_calls
        if (
            call["name"].endswith("skill_view")
            and call["arguments"].get("name") == skill_name
        )
        or (
            call["name"] == "read_mcp_resource"
            and call["arguments"].get("server") == "skill_manager"
            and call["arguments"].get("uri", "").startswith(
                f"skill://{skill_name}"
            )
        )
    ]
    fake_shell_calls = [
        call
        for call in function_calls
        if call["name"] in {"shell_command", "exec_command"}
        and f"{skill_name}." in json.dumps(call["arguments"], ensure_ascii=False)
    ]
    if fake_shell_calls:
        raise AssertionError(
            f"Model treated {skill_name} as a shell command: {fake_shell_calls}"
        )
    if not matching:
        names = [call["name"] for call in function_calls]
        raise AssertionError(
            f"No MCP skill load found for {skill_name!r}; calls={names}"
        )


def assert_no_skill_mcp_called(function_calls):
    """Backward-compatible audit helper retained for existing callers."""
    unexpected = [
        call
        for call in function_calls
        if call["name"].endswith("skill_view")
        or (
            call["name"] == "read_mcp_resource"
            and call["arguments"].get("server") == "skill_manager"
            and call["arguments"].get("uri", "").startswith("skill://")
        )
    ]
    if unexpected:
        raise AssertionError(f"Unexpected MCP skill calls: {unexpected}")


def main() -> None:
    uvicorn.run(
        app,
        host=os.getenv("APP_HOST", "0.0.0.0"),
        port=int(os.getenv("APP_PORT", "8080")),
    )


if __name__ == "__main__":
    main()
