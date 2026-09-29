"""Print the model names exposed by the Claude Agent SDK.

Run:
    python src/acc_skill/acc_client.py

The returned catalog reflects the current login, provider, gateway, and
organization model policy. Connecting only performs the SDK initialization
handshake; this script does not submit a prompt to a model.
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any


def extract_model_names(server_info: dict[str, Any] | None) -> list[str]:
    """Extract selectable model values from an SDK initialization response."""
    if not server_info:
        raise RuntimeError("Claude Agent SDK did not return server information.")

    models = server_info.get("models")
    if not isinstance(models, list):
        raise RuntimeError(
            "The SDK initialization response has no 'models' list. "
            "Upgrade claude-agent-sdk and its bundled Claude Code CLI."
        )

    names: list[str] = []
    seen: set[str] = set()
    for model in models:
        if not isinstance(model, dict):
            continue

        # `value` is the selectable name accepted by ClaudeAgentOptions(model=...).
        value = model.get("value")
        if isinstance(value, str) and value and value not in seen:
            seen.add(value)
            names.append(value)

    if not names:
        raise RuntimeError("The SDK returned an empty model catalog.")

    return names


async def get_model_name_list() -> list[str]:
    """Return model names available to the current Claude Code session."""
    try:
        from claude_agent_sdk import ClaudeSDKClient
    except ImportError as exc:
        raise RuntimeError(
            "claude-agent-sdk is not installed. Run: pip install -U claude-agent-sdk"
        ) from exc

    async with ClaudeSDKClient() as client:
        server_info = await client.get_server_info()

    return extract_model_names(server_info)


async def main() -> None:
    model_names = await get_model_name_list()
    print(json.dumps(model_names, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (RuntimeError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
