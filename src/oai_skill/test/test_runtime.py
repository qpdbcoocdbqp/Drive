from __future__ import annotations

import unittest

from openai_codex import Sandbox

from src.oai_skill.skills.runtime import CodexConfigBuilder, _sdk_sandbox
from src.oai_skill.utils.core import AppSettings


class SandboxConfigurationTests(unittest.TestCase):
    def test_local_default_remains_read_only(self) -> None:
        settings = AppSettings()
        self.assertEqual(settings.sandbox_mode, "read-only")
        self.assertEqual(_sdk_sandbox(settings.sandbox_mode), Sandbox.read_only)

    def test_docker_policy_maps_cli_name_to_sdk_enum(self) -> None:
        settings = AppSettings(sandbox_mode="danger-full-access")
        config = CodexConfigBuilder(settings).build()
        self.assertIn(
            'sandbox_mode="danger-full-access"', config.config_overrides
        )
        self.assertEqual(_sdk_sandbox(settings.sandbox_mode), Sandbox.full_access)


if __name__ == "__main__":
    unittest.main()
