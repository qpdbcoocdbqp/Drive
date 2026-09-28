"""Convert Codex item notifications into a safe execution trace."""

from __future__ import annotations

import time
import uuid
from typing import Any

from openai_codex.generated.v2_all import (
    AgentMessageThreadItem,
    CommandExecutionThreadItem,
    DynamicToolCallThreadItem,
    FileChangeThreadItem,
    ItemCompletedNotification,
    ItemStartedNotification,
    McpToolCallThreadItem,
    WebSearchThreadItem,
)

from .models import ExecutionTrace, TraceEvent, TraceEventType, TraceStatus
from .sanitizer import summarize


def _now_ms() -> int:
    return int(time.time() * 1000)


def _item_root(item: Any) -> Any:
    return getattr(item, "root", item)


def _status(value: Any, *, fallback: TraceStatus) -> TraceStatus:
    normalized = str(getattr(value, "value", value) or "").lower()
    if normalized in {"failed", "error", "declined", "cancelled", "canceled"}:
        return TraceStatus.failed
    if normalized in {"completed", "success", "succeeded"}:
        return TraceStatus.completed
    return fallback


def _is_skill_call(item: Any) -> bool:
    arguments = getattr(item, "arguments", None)
    if hasattr(arguments, "model_dump"):
        arguments = arguments.model_dump(mode="json")
    arguments = arguments if isinstance(arguments, dict) else {}

    if isinstance(item, McpToolCallThreadItem):
        # Every operation routed directly to the skill MCP server is part of
        # skill discovery or loading, even when Codex exposes it through the
        # generic MCP resource methods rather than skill_view.
        return item.server == "skill_manager"
    tool = str(getattr(item, "tool", ""))
    return (
        arguments.get("server") == "skill_manager"
        or str(arguments.get("uri", "")).startswith("skill://")
    )


class TraceCollector:
    def __init__(self, message: str) -> None:
        user_id = f"user-{uuid.uuid4().hex}"
        assistant_id = f"assistant-{uuid.uuid4().hex}"
        now = _now_ms()
        self.trace = ExecutionTrace(
            events=[
                TraceEvent(
                    id=user_id,
                    sequence=1,
                    event_type=TraceEventType.user,
                    name="User message",
                    status=TraceStatus.completed,
                    started_at_ms=now,
                    completed_at_ms=now,
                    input_summary=summarize(message),
                ),
                TraceEvent(
                    id=assistant_id,
                    parent_id=user_id,
                    sequence=2,
                    event_type=TraceEventType.assistant,
                    name="Assistant turn",
                    status=TraceStatus.started,
                    started_at_ms=now,
                ),
            ]
        )
        self._assistant_id = assistant_id
        self._events = {event.id: event for event in self.trace.events}
        self._sequence = 2

    def record(self, notification: Any) -> None:
        payload = getattr(notification, "payload", None)
        if isinstance(payload, ItemStartedNotification):
            self._upsert_item(payload.item, payload.started_at_ms, completed=False)
        elif isinstance(payload, ItemCompletedNotification):
            event = self._upsert_item(
                payload.item,
                payload.completed_at_ms,
                completed=True,
            )
            if event is not None and event.started_at_ms is not None:
                event.duration_ms = max(
                    0, payload.completed_at_ms - event.started_at_ms
                )

    def finish(self, turn_id: str, *, failed: bool = False) -> ExecutionTrace:
        self.trace.turn_id = turn_id
        root = self._events[self._assistant_id]
        root.status = TraceStatus.failed if failed else TraceStatus.completed
        root.completed_at_ms = _now_ms()
        if root.started_at_ms is not None:
            root.duration_ms = max(0, root.completed_at_ms - root.started_at_ms)
        return self.trace

    def _upsert_item(
        self,
        wrapped_item: Any,
        timestamp_ms: int,
        *,
        completed: bool,
    ) -> TraceEvent | None:
        item = _item_root(wrapped_item)
        descriptor = self._describe(item)
        if descriptor is None:
            return None

        event_type, name, input_value, output_value = descriptor
        item_id = str(getattr(item, "id", f"item-{uuid.uuid4().hex}"))
        event = self._events.get(item_id)
        if event is None:
            self._sequence += 1
            event = TraceEvent(
                id=item_id,
                parent_id=self._assistant_id,
                sequence=self._sequence,
                event_type=event_type,
                name=name,
                status=TraceStatus.started,
                started_at_ms=timestamp_ms,
                input_summary=summarize(input_value),
            )
            self.trace.events.append(event)
            self._events[item_id] = event

        if completed:
            event.completed_at_ms = timestamp_ms
            event.status = _status(
                getattr(item, "status", None), fallback=TraceStatus.completed
            )
            event.output_summary = summarize(output_value)
            duration = getattr(item, "duration_ms", None)
            if duration is not None:
                event.duration_ms = duration
        return event

    def _describe(
        self, item: Any
    ) -> tuple[TraceEventType, str, Any, Any] | None:
        if isinstance(item, AgentMessageThreadItem):
            return (
                TraceEventType.assistant,
                "Assistant response",
                None,
                item.text,
            )
        if isinstance(item, McpToolCallThreadItem):
            category = (
                TraceEventType.skill_call
                if _is_skill_call(item)
                else TraceEventType.tool_call
            )
            return (
                category,
                f"{item.server} / {item.tool}",
                item.arguments,
                item.result or item.error,
            )
        if isinstance(item, DynamicToolCallThreadItem):
            category = (
                TraceEventType.skill_call
                if _is_skill_call(item)
                else TraceEventType.tool_call
            )
            return (
                category,
                item.tool,
                item.arguments,
                item.content_items,
            )
        if isinstance(item, CommandExecutionThreadItem):
            return (
                TraceEventType.tool_call,
                "Command execution",
                {"command": item.command, "cwd": str(item.cwd)},
                {
                    "exit_code": item.exit_code,
                    "output": item.aggregated_output,
                },
            )
        if isinstance(item, WebSearchThreadItem):
            return TraceEventType.tool_call, "Web search", item.query, item.results
        if isinstance(item, FileChangeThreadItem):
            return TraceEventType.tool_call, "File change", item.changes, item.status
        return None
