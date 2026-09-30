"""Read-only access to Telegram channels joined by a user account.

This module uses Telegram's MTProto API through Telethon.  It deliberately does
not use the Bot API: a bot cannot inherit the channel memberships of a normal
user account.

Install::

    python -m pip install "telethon>=1.45,<2"

Create an application at https://my.telegram.org, then configure::

    TELEGRAM_API_ID=123456
    TELEGRAM_API_HASH=0123456789abcdef0123456789abcdef
    TELEGRAM_SESSION=C:\\path\\outside-the-repository\\reader

First login (interactive OTP/2FA), then use the JSON CLI::

    python src/module/telegram.py login
    python src/module/telegram.py list
    python src/module/telegram.py read @joined_channel --limit 20
    python src/module/telegram.py search @joined_channel "keyword"

The MCP server is intended for an LLM tool client.  Telegram's current Content
Licensing and AI Scraping terms require explicit, informed consent for the
specific content and channel/non-global context.  Consequently the server will
not start without both an explicit acknowledgement and a channel allow-list::

    python src/module/telegram.py serve \
        --ack-ai-consent --allow-channel @consented_channel

The session file is an account credential.  Never commit, upload, log, or send
it to an LLM.  This module never joins channels, sends messages, downloads
media, or exposes the API hash/session through tools.  Marking messages as read
is available only through the explicit CLI ``read --mark-read`` option; MCP
tools remain side-effect free.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import urlparse


MAX_MESSAGES_PER_CALL = 100
MAX_CHANNELS_PER_CALL = 500
AI_CONSENT_ENV_VALUE = "I_HAVE_EXPLICIT_CONSENT"


class TelegramReaderError(RuntimeError):
    """Base error with a safe, user-facing message."""


class ConfigurationError(TelegramReaderError):
    """Required configuration is absent or invalid."""


class AuthenticationRequiredError(TelegramReaderError):
    """The user session has not been authorized interactively yet."""


class ChannelNotJoinedError(TelegramReaderError):
    """The requested target is not a joined broadcast channel."""


class ConsentRequiredError(TelegramReaderError):
    """The LLM-facing server lacks a specific consent acknowledgement."""


def _default_session_path() -> str:
    """Return a user-local path so account credentials stay outside the repo."""
    local_data = os.environ.get("LOCALAPPDATA")
    base = Path(local_data) if local_data else Path.home() / ".local" / "share"
    return str(base / "joined-channel-reader" / "user")


@dataclass(frozen=True)
class TelegramSettings:
    """Credentials and local session location for a Telegram user client."""

    api_id: int
    api_hash: str
    session_path: str

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "TelegramSettings":
        values = os.environ if env is None else env
        raw_api_id = values.get("TELEGRAM_API_ID", "").strip()
        api_hash = values.get("TELEGRAM_API_HASH", "").strip()
        session_path = values.get("TELEGRAM_SESSION", "").strip() or _default_session_path()

        if not raw_api_id or not api_hash:
            raise ConfigurationError(
                "Set TELEGRAM_API_ID and TELEGRAM_API_HASH; obtain them from "
                "the API development tools at https://my.telegram.org."
            )
        try:
            api_id = int(raw_api_id)
        except ValueError as exc:
            raise ConfigurationError("TELEGRAM_API_ID must be an integer.") from exc
        if api_id <= 0:
            raise ConfigurationError("TELEGRAM_API_ID must be a positive integer.")

        return cls(api_id=api_id, api_hash=api_hash, session_path=session_path)


def _load_telethon() -> tuple[Any, Any, Any]:
    """Import optional runtime dependency while keeping ``--help`` usable."""
    try:
        from telethon import TelegramClient, errors, utils
    except ModuleNotFoundError as exc:
        if exc.name != "telethon":
            raise
        raise ConfigurationError(
            'Telethon is not installed; run python -m pip install "telethon>=1.45,<2".'
        ) from exc
    return TelegramClient, errors, utils


def _load_mcp_server_class() -> Any:
    """Support both the MCP Python SDK 2.x and the older 1.x name."""
    try:
        from mcp.server.mcpserver import MCPServer

        return MCPServer
    except (ImportError, ModuleNotFoundError):
        try:
            from mcp.server.fastmcp import FastMCP

            return FastMCP
        except (ImportError, ModuleNotFoundError) as exc:
            raise ConfigurationError(
                "MCP server mode requires the MCP Python SDK; run python -m pip install mcp."
            ) from exc


def _normalise_channel_ref(value: str | int) -> str:
    """Normalise IDs, @usernames and t.me URLs without resolving new channels."""
    text = str(value).strip()
    if not text:
        raise ValueError("channel must not be empty.")

    candidate = text
    if text.lower().startswith(("https://", "http://")):
        parsed = urlparse(text)
        if parsed.netloc.lower() not in {"t.me", "www.t.me", "telegram.me", "www.telegram.me"}:
            raise ValueError("Only t.me/telegram.me channel URLs are accepted.")
        candidate = parsed.path.strip("/").split("/", 1)[0]
    elif text.lower().startswith(("t.me/", "telegram.me/")):
        candidate = text.split("/", 1)[1].split("/", 1)[0]

    return candidate.lstrip("@").strip().casefold()


def parse_allowed_channels(values: Iterable[str]) -> frozenset[str]:
    """Parse an explicit allow-list into the same form used for matching."""
    result: set[str] = set()
    for value in values:
        for item in value.split(","):
            if item.strip():
                result.add(_normalise_channel_ref(item))
    return frozenset(result)


class TelegramChannelReader(AbstractAsyncContextManager["TelegramChannelReader"]):
    """A read-only user client restricted to already-joined broadcast channels."""

    def __init__(
        self,
        settings: TelegramSettings,
        *,
        allowed_channels: Iterable[str] | None = None,
    ) -> None:
        self.settings = settings
        self.allowed_channels = (
            parse_allowed_channels(allowed_channels) if allowed_channels is not None else None
        )
        self._client: Any | None = None
        self._utils: Any | None = None

    async def __aenter__(self) -> "TelegramChannelReader":
        TelegramClient, _, utils = _load_telethon()
        session = Path(self.settings.session_path).expanduser()
        session.parent.mkdir(parents=True, exist_ok=True)
        self._utils = utils
        self._client = TelegramClient(
            str(session),
            self.settings.api_id,
            self.settings.api_hash,
            receive_updates=False,
            flood_sleep_threshold=30,
        )
        await self._client.connect()
        if not await self._client.is_user_authorized():
            await self._client.disconnect()
            self._client = None
            raise AuthenticationRequiredError(
                "This session is not authorized; run "
                "python src/module/telegram.py login。"
            )
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self._client is not None:
            await self._client.disconnect()
        self._client = None

    @property
    def client(self) -> Any:
        if self._client is None:
            raise RuntimeError("TelegramChannelReader must be used within async with.")
        return self._client

    def _channel_keys(self, entity: Any) -> frozenset[str]:
        keys = {str(entity.id).casefold(), f"-100{entity.id}".casefold()}
        username = getattr(entity, "username", None)
        if username:
            keys.add(str(username).casefold())
        if self._utils is not None:
            try:
                keys.add(str(self._utils.get_peer_id(entity)).casefold())
            except (TypeError, ValueError):
                pass
        return frozenset(keys)

    def _is_allowed(self, entity: Any) -> bool:
        if self.allowed_channels is None:
            return True
        return bool(self._channel_keys(entity) & self.allowed_channels)

    @staticmethod
    def _is_broadcast_channel(dialog: Any) -> bool:
        entity = getattr(dialog, "entity", None)
        return bool(
            getattr(dialog, "is_channel", False)
            and entity is not None
            and getattr(entity, "broadcast", False)
        )

    async def _joined_channels(self) -> list[Any]:
        dialogs: list[Any] = []
        async for dialog in self.client.iter_dialogs(ignore_migrated=True):
            if self._is_broadcast_channel(dialog) and self._is_allowed(dialog.entity):
                dialogs.append(dialog)
        return dialogs

    async def _resolve_joined_channel(self, channel: str | int) -> tuple[Any, Any]:
        target = _normalise_channel_ref(channel)
        dialogs = await self._joined_channels()
        key_matches = [d for d in dialogs if target in self._channel_keys(d.entity)]
        if len(key_matches) == 1:
            return key_matches[0].entity, key_matches[0]

        # Exact title is a convenience fallback, but ambiguity is rejected.
        title_matches = [d for d in dialogs if str(d.name).strip().casefold() == target]
        if len(title_matches) == 1:
            return title_matches[0].entity, title_matches[0]
        if len(title_matches) > 1:
            raise ChannelNotJoinedError(
                "Multiple joined channels have the same title; use @username or numeric ID."
            )

        if self.allowed_channels is not None and target not in self.allowed_channels:
            raise ChannelNotJoinedError("This channel is not in the MCP consent allow-list.")
        raise ChannelNotJoinedError(
            "No joined broadcast channel was found. The tool will not automatically join "
            "public or private channels."
        )

    def _channel_dict(self, dialog: Any) -> dict[str, Any]:
        entity = dialog.entity
        username = getattr(entity, "username", None)
        return {
            "id": self._utils.get_peer_id(entity) if self._utils else entity.id,
            "title": str(dialog.name),
            "username": username,
            "url": f"https://t.me/{username}" if username else None,
            "unread_count": int(getattr(dialog, "unread_count", 0) or 0),
            "unread_mentions_count": int(
                getattr(dialog, "unread_mentions_count", 0) or 0
            ),
            "participants_count": getattr(entity, "participants_count", None),
            "verified": bool(getattr(entity, "verified", False)),
            "restricted": bool(getattr(entity, "restricted", False)),
        }

    @staticmethod
    def _message_dict(message: Any, channel_username: str | None) -> dict[str, Any]:
        date = getattr(message, "date", None)
        edit_date = getattr(message, "edit_date", None)
        media = getattr(message, "media", None)
        message_id = int(message.id)
        return {
            "id": message_id,
            "date": date.isoformat() if date else None,
            "edit_date": edit_date.isoformat() if edit_date else None,
            "text": getattr(message, "raw_text", None) or "",
            "sender_id": getattr(message, "sender_id", None),
            "post_author": getattr(message, "post_author", None),
            "views": getattr(message, "views", None),
            "forwards": getattr(message, "forwards", None),
            "reply_to_message_id": getattr(message, "reply_to_msg_id", None),
            "grouped_id": getattr(message, "grouped_id", None),
            "media_type": type(media).__name__ if media is not None else None,
            "url": (
                f"https://t.me/{channel_username}/{message_id}"
                if channel_username
                else None
            ),
        }

    async def list_joined_channels(self, limit: int = 100) -> dict[str, Any]:
        """List joined broadcast channels, filtered by the optional allow-list."""
        if not 1 <= limit <= MAX_CHANNELS_PER_CALL:
            raise ValueError(f"limit must be between 1 and {MAX_CHANNELS_PER_CALL}.")
        dialogs = await self._joined_channels()
        channels = [self._channel_dict(dialog) for dialog in dialogs[:limit]]
        return {"count": len(channels), "channels": channels}

    async def read_messages(
        self,
        channel: str | int,
        *,
        limit: int = 20,
        before_id: int | None = None,
        after_id: int | None = None,
        query: str | None = None,
        mark_read: bool = False,
    ) -> dict[str, Any]:
        """Read newest-first messages from one joined broadcast channel.

        ``before_id`` paginates toward older posts. ``after_id`` is useful for
        incremental reads. When ``query`` is present Telegram performs the
        server-side message search.
        """
        if not 1 <= limit <= MAX_MESSAGES_PER_CALL:
            raise ValueError(f"limit must be between 1 and {MAX_MESSAGES_PER_CALL}.")
        if before_id is not None and before_id <= 0:
            raise ValueError("before_id must be a positive integer.")
        if after_id is not None and after_id <= 0:
            raise ValueError("after_id must be a positive integer.")
        if mark_read and before_id is not None:
            raise ValueError(
                "--mark-read cannot be used with --before-id; reading an older page "
                "should not clear newer unread indicators."
            )
        if query is not None:
            query = query.strip()
            if not query:
                raise ValueError("query must not be empty.")

        entity, dialog = await self._resolve_joined_channel(channel)
        username = getattr(entity, "username", None)
        kwargs: dict[str, Any] = {
            "limit": limit,
            "offset_id": before_id or 0,
            "min_id": after_id or 0,
        }
        if query is not None:
            kwargs["search"] = query

        messages: list[dict[str, Any]] = []
        async for message in self.client.iter_messages(entity, **kwargs):
            messages.append(self._message_dict(message, username))

        ids = [item["id"] for item in messages]
        unread_before = int(getattr(dialog, "unread_count", 0) or 0)
        latest_id = max(ids) if ids else None
        marked_read = False
        if mark_read and latest_id is not None:
            # This is the only state-changing Telegram call in the module.  It
            # is deliberately unavailable to MCP tools and only happens after
            # the requested messages were fetched successfully.
            await self.client.send_read_acknowledge(
                entity,
                max_id=latest_id,
                clear_mentions=True,
            )
            marked_read = True

        return {
            "channel": self._channel_dict(dialog),
            "order": "newest_first",
            "count": len(messages),
            "query": query,
            "latest_id": latest_id,
            "next_before_id": min(ids) if ids else None,
            "read_state": {
                "marked_read": marked_read,
                "marked_through_id": latest_id if marked_read else None,
                "unread_count_before": unread_before,
                "unread_count_after": 0 if marked_read else unread_before,
            },
            "messages": messages,
        }


async def login(settings: TelegramSettings, phone: str | None = None) -> dict[str, Any]:
    """Perform the only interactive operation and persist a local user session."""
    TelegramClient, _, _ = _load_telethon()
    session = Path(settings.session_path).expanduser()
    session.parent.mkdir(parents=True, exist_ok=True)
    client = TelegramClient(str(session), settings.api_id, settings.api_hash)
    try:
        # Passing ``phone=None`` explicitly disables Telethon's default
        # interactive prompt and raises "No phone number or bot token
        # provided".  Omit the argument entirely when the CLI flag was not
        # supplied so Telethon can ask for the phone number, OTP and 2FA.
        if phone is None:
            await client.start()
        else:
            await client.start(phone=phone)
        me = await client.get_me()
        return {
            "authorized": True,
            "user_id": getattr(me, "id", None),
            "username": getattr(me, "username", None),
            "session_path": str(session) + ("" if str(session).endswith(".session") else ".session"),
        }
    finally:
        await client.disconnect()


def _assert_ai_consent(acknowledged: bool, allowed_channels: Sequence[str]) -> frozenset[str]:
    env_ack = os.environ.get("TELEGRAM_AI_CONSENT", "") == AI_CONSENT_ENV_VALUE
    if not (acknowledged or env_ack):
        raise ConsentRequiredError(
            "The MCP server exposes Telegram content to an LLM. First obtain explicit, "
            "ongoing consent from all relevant users for that specific content/channel, "
            "then pass --ack-ai-consent or set "
            f"TELEGRAM_AI_CONSENT={AI_CONSENT_ENV_VALUE}。"
        )
    allowed = parse_allowed_channels(allowed_channels)
    if not allowed:
        raise ConsentRequiredError(
            "MCP server requires --allow-channel to specify channels with consent; "
            "it cannot expose the entire account."
        )
    return allowed


def build_mcp_server(
    settings: TelegramSettings,
    allowed_channels: Iterable[str],
) -> Any:
    """Build an MCP stdio server exposing three bounded, read-only tools."""
    Server = _load_mcp_server_class()
    allow_list = tuple(allowed_channels)
    server = Server(
        name="joined-channel-reader",
        instructions=(
            "Read-only access to explicitly allowed Telegram broadcast channels already joined "
            "by the authenticated user. Never request channels outside the consent allow-list."
        ),
    )

    @server.tool(
        name="list_joined_channels",
        description="List consent-approved broadcast channels already joined by the user.",
    )
    async def list_joined_channels(limit: int = 100) -> dict[str, Any]:
        async with TelegramChannelReader(
            settings, allowed_channels=allow_list
        ) as reader:
            return await reader.list_joined_channels(limit=limit)

    @server.tool(
        name="read_channel_messages",
        description=(
            "Read newest-first posts from one consent-approved channel. Use next_before_id as "
            "before_id for the next older page, or latest_id as after_id for an incremental poll."
        ),
    )
    async def read_channel_messages(
        channel: str,
        limit: int = 20,
        before_id: int | None = None,
        after_id: int | None = None,
    ) -> dict[str, Any]:
        async with TelegramChannelReader(
            settings, allowed_channels=allow_list
        ) as reader:
            return await reader.read_messages(
                channel,
                limit=limit,
                before_id=before_id,
                after_id=after_id,
            )

    @server.tool(
        name="search_channel_messages",
        description="Search posts within one consent-approved, already-joined broadcast channel.",
    )
    async def search_channel_messages(
        channel: str,
        query: str,
        limit: int = 20,
        before_id: int | None = None,
    ) -> dict[str, Any]:
        async with TelegramChannelReader(
            settings, allowed_channels=allow_list
        ) as reader:
            return await reader.read_messages(
                channel,
                limit=limit,
                before_id=before_id,
                query=query,
            )

    return server


def _json_print(value: Any, *, stream: Any = sys.stdout) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str), file=stream)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read joined Telegram broadcast channels through a user account."
    )
    subparsers = parser.add_subparsers(dest="command")

    login_parser = subparsers.add_parser("login", help="Interactively log in with OTP/2FA and create a session")
    login_parser.add_argument("--phone", help="Phone number with country code; Telethon prompts if omitted")

    list_parser = subparsers.add_parser("list", help="List joined broadcast channels")
    list_parser.add_argument("--limit", type=int, default=100)

    read_parser = subparsers.add_parser("read", help="Read messages from a joined channel")
    read_parser.add_argument("channel", help="@username, t.me URL, numeric ID, or exact title")
    read_parser.add_argument("--limit", type=int, default=20)
    read_parser.add_argument("--before-id", type=int, help="Read older messages before this message ID")
    read_parser.add_argument("--after-id", type=int, help="Read newer messages after this message ID")
    read_parser.add_argument(
        "--mark-read",
        action="store_true",
        help="Clear the channel's unread indicator in Telegram after reading the latest page",
    )

    search_parser = subparsers.add_parser("search", help="Search messages in a joined channel")
    search_parser.add_argument("channel")
    search_parser.add_argument("query")
    search_parser.add_argument("--limit", type=int, default=20)
    search_parser.add_argument("--before-id", type=int)

    serve_parser = subparsers.add_parser("serve", help="Start an MCP stdio server for LLM access")
    serve_parser.add_argument(
        "--ack-ai-consent",
        action="store_true",
        help="Acknowledge the specific, ongoing explicit consent required by Telegram's terms",
    )
    serve_parser.add_argument(
        "--allow-channel",
        action="append",
        default=[],
        help="Allowed @username/numeric ID; may be specified multiple times",
    )

    return parser


async def _run_async_command(args: argparse.Namespace, settings: TelegramSettings) -> Any:
    if args.command == "login":
        return await login(settings, phone=args.phone)
    if args.command == "list":
        async with TelegramChannelReader(settings) as reader:
            return await reader.list_joined_channels(limit=args.limit)
    if args.command == "read":
        async with TelegramChannelReader(settings) as reader:
            return await reader.read_messages(
                args.channel,
                limit=args.limit,
                before_id=args.before_id,
                after_id=args.after_id,
                mark_read=args.mark_read,
            )
    if args.command == "search":
        async with TelegramChannelReader(settings) as reader:
            return await reader.read_messages(
                args.channel,
                limit=args.limit,
                before_id=args.before_id,
                query=args.query,
            )
    raise ValueError(f"Unsupported command: {args.command}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0

    try:
        settings = TelegramSettings.from_env()
        if args.command == "serve":
            env_allowed = os.environ.get("TELEGRAM_ALLOWED_CHANNELS", "")
            raw_allowed = [*args.allow_channel]
            if env_allowed:
                raw_allowed.append(env_allowed)
            allowed = _assert_ai_consent(args.ack_ai_consent, raw_allowed)
            server = build_mcp_server(settings, allowed)
            server.run(transport="stdio")
            return 0

        result = asyncio.run(_run_async_command(args, settings))
        _json_print(result)
        return 0
    except (TelegramReaderError, ValueError) as exc:
        _json_print({"error": type(exc).__name__, "message": str(exc)}, stream=sys.stderr)
        return 2
    except KeyboardInterrupt:
        _json_print({"error": "Interrupted", "message": "Operation interrupted by the user."}, stream=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
