from __future__ import annotations

import tempfile
import unittest
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters, stdio_client
from pydantic import AnyUrl

from src.oai_skill.skill_mcp_server import (
    configure_skill_roots,
    skill_files,
    skill_list,
    skill_view,
)


class SkillMcpServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        skill_directory = self.root / "example"
        (skill_directory / "references").mkdir(parents=True)
        (skill_directory / "SKILL.md").write_text(
            "---\n"
            "name: example\n"
            "description: Example test skill.\n"
            "---\n\n"
            "# Example\n\n"
            "Load `references/rules.md`.\n",
            encoding="utf-8",
        )
        (skill_directory / "references" / "rules.md").write_text(
            "Always preserve placeholders.\n",
            encoding="utf-8",
        )
        configure_skill_roots([self.root])

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_skill_list_returns_metadata(self) -> None:
        result = skill_list()
        self.assertEqual([item["name"] for item in result["skills"]], ["example"])
        self.assertEqual(result["skills"][0]["description"], "Example test skill.")

    def test_skill_files_lists_main_and_supporting_files(self) -> None:
        result = skill_files("example")
        paths = [item["path"] for item in result["files"]]
        self.assertEqual(paths, ["SKILL.md", "references/rules.md"])

    def test_skill_view_loads_main_and_supporting_content(self) -> None:
        self.assertIn("# Example", skill_view("example"))
        self.assertIn(
            "Always preserve placeholders.",
            skill_view("example", "references/rules.md"),
        )

    def test_skill_view_rejects_path_traversal(self) -> None:
        with self.assertRaises(ValueError):
            skill_view("example", "../outside.txt")


class SkillMcpTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_stdio_server_advertises_and_calls_skill_view(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            skill_directory = root / "transport-example"
            skill_directory.mkdir()
            (skill_directory / "SKILL.md").write_text(
                "---\n"
                "name: transport-example\n"
                "description: MCP transport test.\n"
                "---\n\n"
                "# Transport example\n",
                encoding="utf-8",
            )

            server_path = Path(__file__).with_name("skill_mcp_server.py").resolve()
            parameters = StdioServerParameters(
                command=sys.executable,
                args=[str(server_path), "--root", str(root)],
            )
            async with stdio_client(parameters) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    self.assertEqual(
                        {tool.name for tool in tools.tools},
                        {"skill_list", "skill_files", "skill_view"},
                    )

                    result = await session.call_tool(
                        "skill_view",
                        {"name": "transport-example"},
                    )
                    self.assertFalse(result.isError)
                    self.assertIn("# Transport example", result.content[0].text)

                    templates = await session.list_resource_templates()
                    self.assertIn(
                        "skill://{name}",
                        {str(item.uriTemplate) for item in templates.resourceTemplates},
                    )
                    resource = await session.read_resource(
                        AnyUrl("skill://transport-example")
                    )
                    self.assertIn("# Transport example", resource.contents[0].text)


if __name__ == "__main__":
    unittest.main()
