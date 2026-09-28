"""Long-lived asynchronous Codex service shared by REST and Gradio."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from typing import Any

from openai_codex import ApprovalMode, AsyncCodex, InvalidRequestError, Sandbox

from ..core.exceptions import ConversationNotFoundError, ServiceNotReadyError
from ..core.settings import Settings
from ..skills.catalog import SkillCatalog
from ..skills.prompt import build_skills_developer_instructions
from ..trace.models import ExecutionTrace
from .config_factory import build_codex_config
from .conversation_manager import ConversationManager
from .turn_runner import run_turn


@dataclass(slots=True)
class MessageExecution:
    conversation_id: str
    turn_id: str
    status: str
    content: str
    model: str
    usage: dict[str, Any] | None
    trace: ExecutionTrace


class CodexService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.catalog = SkillCatalog(settings.resolved_skill_roots)
        self._developer_instructions = build_skills_developer_instructions(
            self.catalog
        )
        self._codex: AsyncCodex | None = None
        self._semaphore = asyncio.Semaphore(settings.max_concurrency)
        self._conversations = ConversationManager()

    @property
    def ready(self) -> bool:
        return self._codex is not None

    async def start(self) -> None:
        if self._codex is not None:
            return
        codex = AsyncCodex(build_codex_config(self.settings))
        await codex.__aenter__()
        self._codex = codex

    async def close(self) -> None:
        codex, self._codex = self._codex, None
        if codex is not None:
            await codex.__aexit__(None, None, None)

    def list_skills(self, *, refresh: bool = False) -> list[dict[str, str]]:
        skills = self.catalog.discover(refresh=refresh)
        return [
            {"name": skill.name, "description": skill.description}
            for skill in sorted(skills.values(), key=lambda item: item.name.lower())
        ]

    async def send_message(
        self,
        message: str,
        *,
        conversation_id: str | None = None,
    ) -> MessageExecution:
        if self._codex is None:
            raise ServiceNotReadyError("Codex runtime is not ready")
        text = message.strip()
        if not text:
            raise ValueError("message cannot be empty")

        lock_key = conversation_id or f"new:{uuid.uuid4().hex}"
        async with self._semaphore:
            async with self._conversations.acquire(lock_key):
                if conversation_id:
                    try:
                        thread = await self._codex.thread_resume(
                            conversation_id,
                            approval_mode=ApprovalMode.deny_all,
                            cwd=str(self.settings.workspace.resolve()),
                            developer_instructions=self._developer_instructions,
                            model=self.settings.model,
                            model_provider=self.settings.model_provider,
                            sandbox=Sandbox.read_only,
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
                        sandbox=Sandbox.read_only,
                    )

                collected = await run_turn(
                    thread,
                    text,
                    timeout_seconds=self.settings.turn_timeout_seconds,
                    interrupt_grace_seconds=self.settings.interrupt_grace_seconds,
                )
                result = collected.result
                usage = (
                    result.usage.model_dump(mode="json", by_alias=True)
                    if result.usage is not None
                    else None
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
