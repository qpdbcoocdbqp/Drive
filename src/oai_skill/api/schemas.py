"""HTTP request and response schemas."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

from ..runtime.codex_service import MessageExecution
from ..trace.models import ExecutionTrace


class MessageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=100_000)
    conversation_id: str | None = None

    @field_validator("message")
    @classmethod
    def reject_blank_message(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message cannot be blank")
        return value


class MessageResponse(BaseModel):
    conversation_id: str
    turn_id: str
    status: str
    content: str
    model: str
    usage: dict[str, Any] | None = None
    trace: ExecutionTrace

    @classmethod
    def from_execution(cls, execution: MessageExecution) -> "MessageResponse":
        return cls(
            conversation_id=execution.conversation_id,
            turn_id=execution.turn_id,
            status=execution.status,
            content=execution.content,
            model=execution.model,
            usage=execution.usage,
            trace=execution.trace,
        )


class HealthResponse(BaseModel):
    status: str


class SkillSummary(BaseModel):
    name: str
    description: str
