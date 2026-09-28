"""In-process serialization for Codex conversations."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import AsyncIterator

from ..core.exceptions import ConversationBusyError


class ConversationManager:
    """Prevent overlapping turns for the same conversation in one worker."""

    def __init__(self) -> None:
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    @asynccontextmanager
    async def acquire(self, conversation_key: str) -> AsyncIterator[None]:
        lock = self._locks[conversation_key]
        if lock.locked():
            raise ConversationBusyError(
                f"conversation {conversation_key!r} already has a turn in progress"
            )
        await lock.acquire()
        try:
            yield
        finally:
            lock.release()
