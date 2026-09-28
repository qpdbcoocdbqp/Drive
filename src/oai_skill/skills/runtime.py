"""Codex runtime lifecycle, configuration, conversation, and turn execution."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator

from openai_codex import (
    ApprovalMode,
    AsyncCodex,
    CodexConfig,
    InvalidRequestError,
    Sandbox,
    TextInput,
    TurnResult,
)
from openai_codex.generated.v2_all import (
    AgentMessageThreadItem,
    ItemCompletedNotification,
    MessagePhase,
    ThreadTokenUsageUpdatedNotification,
    TurnCompletedNotification,
    TurnStatus,
)

from ..utils.core import (
    AppSettings,
    ConversationBusyError,
    ConversationNotFoundError,
    ServiceNotReadyError,
    TurnTimeoutError,
)
from ..utils.trace import ExecutionTrace, TraceCollector
from .skills import SkillCatalog, SkillInstructionBuilder

LOCAL_API_KEY_ENV = "OAI_SKILL_LOCAL_API_KEY"


def _sdk_sandbox(mode: str) -> Sandbox:
    """Translate the Codex CLI config spelling to the SDK enum spelling."""
    if mode == "danger-full-access":
        return Sandbox.full_access
    return Sandbox(mode)


@dataclass(slots=True)
class MessageExecution:
    conversation_id: str
    turn_id: str
    status: str
    content: str
    model: str
    usage: dict[str, Any] | None
    trace: ExecutionTrace


@dataclass(slots=True)
class CollectedTurn:
    result: TurnResult
    trace: ExecutionTrace


class CodexConfigBuilder:
    """Translate validated settings into an SDK configuration."""

    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings

    @staticmethod
    def _toml_string(value: str) -> str:
        return json.dumps(value, ensure_ascii=False)

    def build(self) -> CodexConfig:
        settings = self.settings
        env = dict(os.environ)
        mcp_launcher = Path(__file__).resolve().parents[1] / "server" / "mcp.py"
        if settings.codex_home is not None:
            env["CODEX_HOME"] = str(settings.codex_home)

        overrides = [
            f"model_provider={self._toml_string(settings.model_provider)}",
            "approval_policy=never",
            f"sandbox_mode={self._toml_string(settings.sandbox_mode)}",
            f"mcp_servers.skill_manager.command={self._toml_string(sys.executable)}",
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
            ),
            "mcp_servers.skill_manager.required=true",
            "mcp_servers.skill_manager.startup_timeout_sec=20",
            "mcp_servers.skill_manager.tool_timeout_sec=60",
        ]

        if settings.llm_mode == "local":
            prefix = f"model_providers.{settings.local_provider_id}"
            overrides.extend([
                f"{prefix}.name={self._toml_string('Local Runtime')}",
                f"{prefix}.base_url={self._toml_string(settings.llm_base_url or '')}",
                f"{prefix}.wire_api={self._toml_string('responses')}",
                f"{prefix}.requires_openai_auth=false",
            ])
            if settings.llm_api_key is not None:
                env[LOCAL_API_KEY_ENV] = settings.llm_api_key.get_secret_value()
                overrides.append(f"{prefix}.env_key={self._toml_string(LOCAL_API_KEY_ENV)}")
        elif settings.llm_api_key is not None:
            env["OPENAI_API_KEY"] = settings.llm_api_key.get_secret_value()

        return CodexConfig(
            cwd=str(settings.workspace.resolve()),
            env=env,
            config_overrides=tuple(overrides),
        )


class ConversationRegistry:
    """Reject overlapping turns and release unused conversation locks."""

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}
        self._guard = asyncio.Lock()

    @asynccontextmanager
    async def acquire(self, conversation_key: str) -> AsyncIterator[None]:
        async with self._guard:
            lock = self._locks.setdefault(conversation_key, asyncio.Lock())
            if lock.locked():
                raise ConversationBusyError(
                    f"conversation {conversation_key!r} already has a turn in progress"
                )
            await lock.acquire()
        try:
            yield
        finally:
            lock.release()
            async with self._guard:
                if self._locks.get(conversation_key) is lock and not lock.locked():
                    self._locks.pop(conversation_key, None)


class CodexTurnRunner:
    """Run one streamed Codex turn with timeout and trace collection."""

    def __init__(self, timeout_seconds: float, interrupt_grace_seconds: float) -> None:
        self.timeout_seconds = timeout_seconds
        self.interrupt_grace_seconds = interrupt_grace_seconds

    @staticmethod
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

    async def run(self, thread, message: str) -> CollectedTurn:
        handle = await thread.turn(TextInput(text=message))
        collector = TraceCollector(message)
        completed: TurnCompletedNotification | None = None
        items: list = []
        usage = None

        try:
            async with asyncio.timeout(self.timeout_seconds):
                async for notification in handle.stream():
                    collector.record(notification)
                    payload = notification.payload
                    if isinstance(payload, ItemCompletedNotification) and payload.turn_id == handle.id:
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
                async with asyncio.timeout(self.interrupt_grace_seconds):
                    await handle.interrupt()
            except Exception:
                pass
            error = TurnTimeoutError(
                f"Codex turn {handle.id} timed out after {self.timeout_seconds:g} seconds"
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
                final_response=self._final_response(items),
                items=items,
                usage=usage,
            ),
            trace=trace,
        )


class CodexRuntime:
    """Long-lived asynchronous Codex runtime shared by REST and Gradio."""

    def __init__(
        self,
        settings: AppSettings,
        *,
        catalog: SkillCatalog | None = None,
        config_builder: CodexConfigBuilder | None = None,
        conversations: ConversationRegistry | None = None,
        turn_runner: CodexTurnRunner | None = None,
    ) -> None:
        self.settings = settings
        self.catalog = catalog or SkillCatalog(settings.resolved_skill_roots)
        self.config_builder = config_builder or CodexConfigBuilder(settings)
        self.turn_runner = turn_runner or CodexTurnRunner(
            settings.turn_timeout_seconds, settings.interrupt_grace_seconds
        )
        self.conversations = conversations or ConversationRegistry()
        self._developer_instructions = SkillInstructionBuilder(self.catalog).build()
        self._codex: AsyncCodex | None = None
        self._semaphore = asyncio.Semaphore(settings.max_concurrency)

    @property
    def ready(self) -> bool:
        return self._codex is not None

    async def start(self) -> None:
        if self._codex is not None:
            return
        codex = AsyncCodex(self.config_builder.build())
        await codex.__aenter__()
        self._codex = codex

    async def close(self) -> None:
        codex, self._codex = self._codex, None
        if codex is not None:
            await codex.__aexit__(None, None, None)

    def list_skills(self, *, refresh: bool = False) -> list[dict[str, str]]:
        skills = self.catalog.discover(refresh=refresh)
        if refresh:
            self._developer_instructions = SkillInstructionBuilder(self.catalog).build()
        return [
            {"name": skill.name, "description": skill.description}
            for skill in sorted(skills.values(), key=lambda item: item.name.lower())
        ]

    async def send_message(
        self, message: str, *, conversation_id: str | None = None
    ) -> MessageExecution:
        if self._codex is None:
            raise ServiceNotReadyError("Codex runtime is not ready")
        text = message.strip()
        if not text:
            raise ValueError("message cannot be empty")

        lock_key = conversation_id or f"new:{uuid.uuid4().hex}"
        async with self._semaphore:
            async with self.conversations.acquire(lock_key):
                if conversation_id:
                    try:
                        thread = await self._codex.thread_resume(
                            conversation_id,
                            approval_mode=ApprovalMode.deny_all,
                            cwd=str(self.settings.workspace.resolve()),
                            developer_instructions=self._developer_instructions,
                            model=self.settings.model,
                            model_provider=self.settings.model_provider,
                            sandbox=_sdk_sandbox(self.settings.sandbox_mode),
                        )
                    except InvalidRequestError as exc:
                        raise ConversationNotFoundError(conversation_id) from exc
                else:
                    thread = await self._codex.thread_start(
                        approval_mode=ApprovalMode.deny_all,
                        cwd=str(self.settings.workspace.resolve()),
                        developer_instructions=self._developer_instructions,
                        ephemeral=False,
                        model=self.settings.model,
                        model_provider=self.settings.model_provider,
                        sandbox=_sdk_sandbox(self.settings.sandbox_mode),
                    )

                collected = await self.turn_runner.run(thread, text)
                result = collected.result
                usage = (
                    result.usage.model_dump(mode="json", by_alias=True)
                    if result.usage is not None else None
                )
                return MessageExecution(
                    conversation_id=thread.id,
                    turn_id=result.id,
                    status=result.status.value,
                    content=result.final_response or "",
                    model=self.settings.model,
                    usage=usage,
                    trace=collected.trace,
                )


CodexService = CodexRuntime

__all__ = [
    "CodexConfigBuilder", "CodexRuntime", "CodexService", "CodexTurnRunner",
    "CollectedTurn", "ConversationRegistry", "LOCAL_API_KEY_ENV", "MessageExecution",
]
