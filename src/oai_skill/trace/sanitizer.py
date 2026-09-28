"""Redact secrets and bound trace payload size before exposing it."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any


SENSITIVE_PARTS = (
    "api_key",
    "apikey",
    "authorization",
    "bearer",
    "password",
    "secret",
    "token",
)


def _is_sensitive(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in SENSITIVE_PARTS)


def redact(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): "[REDACTED]" if _is_sensitive(str(key)) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [redact(item) for item in value]
    return value


def summarize(value: Any, *, limit: int = 800) -> str | None:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json", by_alias=True)
    cleaned = redact(value)
    if isinstance(cleaned, str):
        text = cleaned
    else:
        try:
            text = json.dumps(cleaned, ensure_ascii=False, default=str)
        except TypeError:
            text = str(cleaned)
    text = text.strip()
    if len(text) > limit:
        return text[: limit - 1] + "…"
    return text or None
