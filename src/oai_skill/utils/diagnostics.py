"""Diagnostic access to Codex SDK discovery endpoints."""

from __future__ import annotations

import json
from typing import Any

from openai_codex import Codex


class CodexDiagnostics:
    """Collect diagnostic payloads while isolating private SDK access."""

    methods = {
        "skills": "skills/list",
        "plugins": "plugin/list",
        "models": "model/list",
    }

    def __init__(self, client: Codex | None = None) -> None:
        self._client = client
        self._owns_client = client is None

    def inspect(self) -> dict[str, Any]:
        client = self._client or Codex()
        self._client = client
        return {
            name: client._client._request_raw(method, {})  # noqa: SLF001
            for name, method in self.methods.items()
        }

    def close(self) -> None:
        if self._owns_client and self._client is not None:
            self._client.close()
        self._client = None

    def __enter__(self) -> "CodexDiagnostics":
        return self

    def __exit__(self, *_args) -> None:
        self.close()


class SkillCallAudit:
    """Validate that a model used the skill MCP interface as expected."""

    @staticmethod
    def assert_called(function_calls, skill_name: str) -> None:
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
                and call["arguments"].get("uri", "").startswith(f"skill://{skill_name}")
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

    @staticmethod
    def assert_not_called(function_calls) -> None:
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


def assert_skill_mcp_called(function_calls, skill_name: str) -> None:
    SkillCallAudit.assert_called(function_calls, skill_name)


def assert_no_skill_mcp_called(function_calls) -> None:
    SkillCallAudit.assert_not_called(function_calls)


def main() -> None:
    with CodexDiagnostics() as diagnostics:
        print(json.dumps(diagnostics.inspect(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
