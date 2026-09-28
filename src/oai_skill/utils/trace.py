"""Trace models, sanitization, Codex event collection, and Plotly rendering."""

from __future__ import annotations

import json
import time
import uuid
from collections import defaultdict
from collections.abc import Mapping, Sequence
from enum import StrEnum
from textwrap import wrap
from typing import Any

import plotly.graph_objects as go
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
from pydantic import BaseModel, Field


class TraceEventType(StrEnum):
    user = "user"
    assistant = "assistant"
    skill_call = "skill_call"
    tool_call = "tool_call"


class TraceStatus(StrEnum):
    started = "started"
    completed = "completed"
    failed = "failed"


class TraceEvent(BaseModel):
    id: str
    parent_id: str | None = None
    sequence: int = Field(ge=1)
    event_type: TraceEventType
    name: str
    status: TraceStatus
    started_at_ms: int | None = None
    completed_at_ms: int | None = None
    duration_ms: int | None = None
    input_summary: str | None = None
    output_summary: str | None = None


class ExecutionTrace(BaseModel):
    turn_id: str | None = None
    events: list[TraceEvent] = Field(default_factory=list)


class TraceSanitizer:
    """Remove likely secrets and bound serialized trace payloads."""

    sensitive_parts = (
        "api_key", "apikey", "authorization", "bearer", "password", "secret", "token"
    )

    @classmethod
    def _is_sensitive(cls, key: str) -> bool:
        lowered = key.lower()
        return any(part in lowered for part in cls.sensitive_parts)

    @classmethod
    def redact(cls, value: Any) -> Any:
        if isinstance(value, Mapping):
            return {
                str(key): "[REDACTED]" if cls._is_sensitive(str(key)) else cls.redact(item)
                for key, item in value.items()
            }
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            return [cls.redact(item) for item in value]
        return value

    @classmethod
    def summarize(cls, value: Any, *, limit: int = 800) -> str | None:
        if value is None:
            return None
        if hasattr(value, "model_dump"):
            value = value.model_dump(mode="json", by_alias=True)
        cleaned = cls.redact(value)
        if isinstance(cleaned, str):
            text = cleaned
        else:
            try:
                text = json.dumps(cleaned, ensure_ascii=False, default=str)
            except TypeError:
                text = str(cleaned)
        text = text.strip()
        return text[: limit - 1] + "…" if len(text) > limit else text or None


def redact(value: Any) -> Any:
    return TraceSanitizer.redact(value)


def summarize(value: Any, *, limit: int = 800) -> str | None:
    return TraceSanitizer.summarize(value, limit=limit)


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
        return item.server == "skill_manager"
    return (
        arguments.get("server") == "skill_manager"
        or str(arguments.get("uri", "")).startswith("skill://")
    )


class TraceCollector:
    """Convert Codex notifications into one normalized execution trace."""

    def __init__(self, message: str) -> None:
        user_id = f"user-{uuid.uuid4().hex}"
        assistant_id = f"assistant-{uuid.uuid4().hex}"
        now = _now_ms()
        self.trace = ExecutionTrace(events=[
            TraceEvent(
                id=user_id, sequence=1, event_type=TraceEventType.user,
                name="User message", status=TraceStatus.completed,
                started_at_ms=now, completed_at_ms=now, input_summary=summarize(message),
            ),
            TraceEvent(
                id=assistant_id, parent_id=user_id, sequence=2,
                event_type=TraceEventType.assistant, name="Assistant turn",
                status=TraceStatus.started, started_at_ms=now,
            ),
        ])
        self._assistant_id = assistant_id
        self._events = {event.id: event for event in self.trace.events}
        self._sequence = 2

    def record(self, notification: Any) -> None:
        payload = getattr(notification, "payload", None)
        if isinstance(payload, ItemStartedNotification):
            self._upsert_item(payload.item, payload.started_at_ms, completed=False)
        elif isinstance(payload, ItemCompletedNotification):
            event = self._upsert_item(payload.item, payload.completed_at_ms, completed=True)
            if event is not None and event.started_at_ms is not None:
                event.duration_ms = max(0, payload.completed_at_ms - event.started_at_ms)

    def finish(self, turn_id: str, *, failed: bool = False) -> ExecutionTrace:
        self.trace.turn_id = turn_id
        root = self._events[self._assistant_id]
        root.status = TraceStatus.failed if failed else TraceStatus.completed
        root.completed_at_ms = _now_ms()
        if root.started_at_ms is not None:
            root.duration_ms = max(0, root.completed_at_ms - root.started_at_ms)
        return self.trace

    def _upsert_item(
        self, wrapped_item: Any, timestamp_ms: int, *, completed: bool
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
                id=item_id, parent_id=self._assistant_id, sequence=self._sequence,
                event_type=event_type, name=name, status=TraceStatus.started,
                started_at_ms=timestamp_ms, input_summary=summarize(input_value),
            )
            self.trace.events.append(event)
            self._events[item_id] = event
        if completed:
            event.completed_at_ms = timestamp_ms
            event.status = _status(getattr(item, "status", None), fallback=TraceStatus.completed)
            event.output_summary = summarize(output_value)
            duration = getattr(item, "duration_ms", None)
            if duration is not None:
                event.duration_ms = duration
        return event

    def _describe(self, item: Any) -> tuple[TraceEventType, str, Any, Any] | None:
        if isinstance(item, AgentMessageThreadItem):
            return TraceEventType.assistant, "Assistant response", None, item.text
        if isinstance(item, McpToolCallThreadItem):
            category = TraceEventType.skill_call if _is_skill_call(item) else TraceEventType.tool_call
            return category, f"{item.server} / {item.tool}", item.arguments, item.result or item.error
        if isinstance(item, DynamicToolCallThreadItem):
            category = TraceEventType.skill_call if _is_skill_call(item) else TraceEventType.tool_call
            return category, item.tool, item.arguments, item.content_items
        if isinstance(item, CommandExecutionThreadItem):
            return (
                TraceEventType.tool_call, "Command execution",
                {"command": item.command, "cwd": str(item.cwd)},
                {"exit_code": item.exit_code, "output": item.aggregated_output},
            )
        if isinstance(item, WebSearchThreadItem):
            return TraceEventType.tool_call, "Web search", item.query, item.results
        if isinstance(item, FileChangeThreadItem):
            return TraceEventType.tool_call, "File change", item.changes, item.status
        return None


class TraceRenderer:
    """Render an execution trace as a Plotly flow diagram."""

    lanes = {
        TraceEventType.user: 3, TraceEventType.assistant: 2,
        TraceEventType.skill_call: 1, TraceEventType.tool_call: 0,
    }
    colors = {
        TraceEventType.user: "#2563eb", TraceEventType.assistant: "#7c3aed",
        TraceEventType.skill_call: "#059669", TraceEventType.tool_call: "#d97706",
    }

    @staticmethod
    def _format_hover(value: str | None, *, limit: int = 96, width: int = 40) -> str:
        text = (value or "—").strip()
        if len(text) > limit:
            text = text[: limit - 1] + "…"
        text = text.translate(str.maketrans({"&": "＆", "<": "‹", ">": "›"}))
        lines: list[str] = []
        for source_line in text.splitlines() or [""]:
            lines.extend(wrap(source_line, width=width) or [""])
        return "<br>".join(lines)

    @classmethod
    def build(cls, trace: ExecutionTrace | None) -> go.Figure:
        figure = go.Figure()
        events = [] if trace is None else sorted(trace.events, key=lambda item: item.sequence)
        if not events:
            figure.add_annotation(
                text="送出訊息後，Codex 執行流程會顯示在這裡。", x=0.5, y=0.5,
                xref="paper", yref="paper", showarrow=False,
            )
            return cls._style(figure)
        positions = {event.id: (event.sequence, cls.lanes[event.event_type]) for event in events}
        edge_x: list[float | None] = []
        edge_y: list[float | None] = []
        for event in events:
            if event.parent_id not in positions:
                continue
            parent_x, parent_y = positions[event.parent_id]
            child_x, child_y = positions[event.id]
            edge_x.extend([parent_x, child_x, None])
            edge_y.extend([parent_y, child_y, None])
        figure.add_trace(go.Scatter(
            x=edge_x, y=edge_y, mode="lines",
            line={"color": "#94a3b8", "width": 1.5}, hoverinfo="skip", showlegend=False,
        ))
        grouped: defaultdict[TraceEventType, list] = defaultdict(list)
        for event in events:
            grouped[event.event_type].append(event)
        for event_type, items in grouped.items():
            figure.add_trace(go.Scatter(
                x=[item.sequence for item in items],
                y=[cls.lanes[event_type] for _ in items], mode="markers+text",
                name=event_type.value.replace("_", " ").title(),
                text=[item.name for item in items], textposition="top center",
                marker={
                    "size": 18, "color": cls.colors[event_type],
                    "symbol": ["x" if item.status.value == "failed" else "circle" for item in items],
                },
                customdata=[[
                    item.status.value, item.duration_ms,
                    cls._format_hover(item.input_summary), cls._format_hover(item.output_summary),
                ] for item in items],
                hovertemplate=(
                    "<b>%{text}</b><br>status=%{customdata[0]}"
                    "<br>duration=%{customdata[1]} ms<br>input=%{customdata[2]}"
                    "<br>output=%{customdata[3]}<extra></extra>"
                ),
            ))
        return cls._style(figure)

    @staticmethod
    def _style(figure: go.Figure) -> go.Figure:
        figure.update_layout(
            title="Codex execution flow", template="plotly_white", height=440,
            margin={"l": 30, "r": 30, "t": 60, "b": 40},
            legend={"orientation": "h", "y": 1.12, "x": 0},
            xaxis={"title": "Execution order", "showgrid": False, "zeroline": False},
            yaxis={
                "tickmode": "array", "tickvals": [0, 1, 2, 3],
                "ticktext": ["Tool", "Skill", "Assistant", "User"],
                "range": [-0.5, 3.5], "showgrid": True, "zeroline": False,
            },
            hoverlabel={"align": "left"},
        )
        return figure


def build_trace_figure(trace: ExecutionTrace | None) -> go.Figure:
    return TraceRenderer.build(trace)


__all__ = [
    "ExecutionTrace", "TraceCollector", "TraceEvent", "TraceEventType",
    "TraceRenderer", "TraceSanitizer", "TraceStatus", "build_trace_figure",
    "redact", "summarize",
]
