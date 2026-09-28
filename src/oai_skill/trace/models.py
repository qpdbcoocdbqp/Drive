"""Serializable execution-trace models shared by API and UI."""

from __future__ import annotations

from enum import StrEnum

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
