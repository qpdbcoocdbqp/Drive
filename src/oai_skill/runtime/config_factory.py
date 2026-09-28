"""Translate validated application settings into Codex configuration."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from openai_codex import CodexConfig

from ..core.settings import Settings


LOCAL_API_KEY_ENV = "OAI_SKILL_LOCAL_API_KEY"


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def build_codex_config(settings: Settings) -> CodexConfig:
    env = dict(os.environ)
    mcp_launcher = Path(__file__).resolve().parents[1] / "mcp" / "__main__.py"
    if settings.codex_home is not None:
        env["CODEX_HOME"] = str(settings.codex_home)

    overrides = [
        f"model_provider={_toml_string(settings.model_provider)}",
        "approval_policy=never",
        "sandbox_mode=read-only",
        f"mcp_servers.skill_manager.command={_toml_string(sys.executable)}",
        (
            "mcp_servers.skill_manager.args="
            + json.dumps(
                [
                    str(mcp_launcher),
                    *[
                        value
                        for root in settings.resolved_skill_roots
                        for value in ("--root", str(root))
                    ],
                ],
                ensure_ascii=False,
            )
        ),
        "mcp_servers.skill_manager.required=true",
        "mcp_servers.skill_manager.startup_timeout_sec=20",
        "mcp_servers.skill_manager.tool_timeout_sec=60",
    ]

    if settings.llm_mode == "local":
        provider = settings.local_provider_id
        prefix = f"model_providers.{provider}"
        overrides.extend(
            [
                f"{prefix}.name={_toml_string('Local Runtime')}",
                f"{prefix}.base_url={_toml_string(settings.llm_base_url or '')}",
                f"{prefix}.wire_api={_toml_string('responses')}",
                f"{prefix}.requires_openai_auth=false",
            ]
        )
        if settings.llm_api_key is not None:
            env[LOCAL_API_KEY_ENV] = settings.llm_api_key.get_secret_value()
            overrides.append(f"{prefix}.env_key={_toml_string(LOCAL_API_KEY_ENV)}")
    elif settings.llm_api_key is not None:
        env["OPENAI_API_KEY"] = settings.llm_api_key.get_secret_value()

    return CodexConfig(
        cwd=str(settings.workspace.resolve()),
        env=env,
        config_overrides=tuple(overrides),
    )
