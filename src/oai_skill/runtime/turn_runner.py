"""Run a Codex turn while collecting its public execution events."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from openai_codex import TextInput, TurnResult
from openai_codex.generated.v2_all import (
    AgentMessageThreadItem,
    ItemCompletedNotification,
    MessagePhase,
    ThreadTokenUsageUpdatedNotification,
    TurnCompletedNotification,
    TurnStatus,
)

from ..core.exceptions import TurnTimeoutError
from ..trace.collector import TraceCollector
from ..trace.models import ExecutionTrace


@dataclass(slots=True)
class CollectedTurn:
    result: TurnResult
    trace: ExecutionTrace


def _final_response(items: list) -> str | None:
    fallback: str | None = None
    for wrapped in reversed(items):
        item = getattr(wrapped, "root", wrapped)
        if not isinstance(item, AgentMessageThreadItem):
            continue
        if item.phase == MessagePhase.final_answer:
            return item.text
        if item.phase is None and fallback is None:
            fallback = item.text
    return fallback


async def run_turn(
    thread,
    message: str,
    *,
    timeout_seconds: float,
    interrupt_grace_seconds: float,
) -> CollectedTurn:
    handle = await thread.turn(TextInput(text=message))
    collector = TraceCollector(message)
    completed: TurnCompletedNotification | None = None
    items: list = []
    usage = None

    try:
        async with asyncio.timeout(timeout_seconds):
            async for notification in handle.stream():
                collector.record(notification)
                payload = notification.payload
                if (
                    isinstance(payload, ItemCompletedNotification)
                    and payload.turn_id == handle.id
                ):
                    items.append(payload.item)
                elif (
                    isinstance(payload, ThreadTokenUsageUpdatedNotification)
                    and payload.turn_id == handle.id
                ):
                    usage = payload.token_usage
                elif (
                    isinstance(payload, TurnCompletedNotification)
                    and payload.turn.id == handle.id
                ):
                    completed = payload
    except TimeoutError as exc:
        trace = collector.finish(handle.id, failed=True)
        try:
            async with asyncio.timeout(interrupt_grace_seconds):
                await handle.interrupt()
        except Exception:
            pass
        error = TurnTimeoutError(
            f"Codex turn {handle.id} timed out after {timeout_seconds:g} seconds"
        )
        error.trace = trace
        raise error from exc

    if completed is None:
        trace = collector.finish(handle.id, failed=True)
        error = RuntimeError("turn completed event not received")
        error.trace = trace
        raise error

    turn = completed.turn
    failed = turn.status == TurnStatus.failed
    trace = collector.finish(turn.id, failed=failed)
    if failed:
        message_text = (
            turn.error.message
            if turn.error is not None and turn.error.message
            else f"turn failed with status {turn.status.value}"
        )
        error = RuntimeError(message_text)
        error.trace = trace
        raise error

    return CollectedTurn(
        result=TurnResult(
            id=turn.id,
            status=turn.status,
            error=turn.error,
            started_at=turn.started_at,
            completed_at=turn.completed_at,
            duration_ms=turn.duration_ms,
            final_response=_final_response(items),
            items=items,
            usage=usage,
        ),
        trace=trace,
    )
