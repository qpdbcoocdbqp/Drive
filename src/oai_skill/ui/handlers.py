"""Gradio event handlers kept separate from component layout."""

from __future__ import annotations

from typing import Any

from ..runtime.codex_service import CodexService
from ..trace.plot import build_trace_figure


def add_user_message(
    message: str,
    history: list[dict[str, Any]] | None,
) -> tuple[list[dict[str, Any]], str, str]:
    text = (message or "").strip()
    current = list(history or [])
    if text:
        current.append({"role": "user", "content": text})
    return current, "", text


async def add_assistant_message(
    message: str,
    history: list[dict[str, Any]] | None,
    conversation_id: str | None,
    service: CodexService,
) -> tuple[list[dict[str, Any]], str | None, Any]:
    current = list(history or [])
    if not message:
        return current, conversation_id, build_trace_figure(None)
    try:
        execution = await service.send_message(
            message,
            conversation_id=conversation_id,
        )
    except Exception as exc:
        current.append(
            {
                "role": "assistant",
                "content": f"執行失敗：{exc}",
            }
        )
        trace = getattr(exc, "trace", None)
        return current, conversation_id, build_trace_figure(trace)

    current.append({"role": "assistant", "content": execution.content})
    return (
        current,
        execution.conversation_id,
        build_trace_figure(execution.trace),
    )


def clear_conversation() -> tuple[list, None, Any, str, str]:
    return [], None, build_trace_figure(None), "", ""
