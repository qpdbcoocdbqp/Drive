from __future__ import annotations

import unittest

from src.oai_skill.codex_wrap import (
    assert_no_skill_mcp_called,
    assert_skill_mcp_called,
)


class SkillCallAuditTests(unittest.TestCase):
    def test_accepts_real_skill_view_call(self) -> None:
        calls = [
            {
                "name": "mcp__skill_manager__skill_view",
                "arguments": {"name": "sepia"},
            }
        ]
        assert_skill_mcp_called(calls, "sepia")

    def test_accepts_real_skill_resource_call(self) -> None:
        calls = [
            {
                "name": "read_mcp_resource",
                "arguments": {
                    "server": "skill_manager",
                    "uri": "skill://sepia",
                },
            }
        ]
        assert_skill_mcp_called(calls, "sepia")

    def test_rejects_missing_skill_view_call(self) -> None:
        with self.assertRaisesRegex(AssertionError, "No MCP skill load"):
            assert_skill_mcp_called([], "sepia")

    def test_rejects_fake_shell_invocation(self) -> None:
        calls = [
            {
                "name": "shell_command",
                "arguments": {"command": "sepia.refactor('text')"},
            },
            {
                "name": "mcp__skill_manager__skill_view",
                "arguments": {"name": "sepia"},
            },
        ]
        with self.assertRaisesRegex(AssertionError, "shell command"):
            assert_skill_mcp_called(calls, "sepia")

    def test_accepts_message_without_skill_calls(self) -> None:
        assert_no_skill_mcp_called(
            [{"name": "shell_command", "arguments": {"command": "date"}}]
        )

    def test_rejects_unexpected_skill_call(self) -> None:
        calls = [
            {
                "name": "mcp__skill_manager__skill_view",
                "arguments": {"name": "sepia"},
            }
        ]
        with self.assertRaisesRegex(AssertionError, "Unexpected MCP skill"):
            assert_no_skill_mcp_called(calls)


if __name__ == "__main__":
    unittest.main()
