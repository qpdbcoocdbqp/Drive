"""Application settings, errors, and logging shared across all layers."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PACKAGE_ROOT.parents[1]


class OaiSkillError(RuntimeError):
    """Base exception for expected service failures."""


class ServiceNotReadyError(OaiSkillError):
    """The Codex runtime has not started or is shutting down."""


class ConversationBusyError(OaiSkillError):
    """A conversation already has a turn in progress."""


class ConversationNotFoundError(OaiSkillError):
    """The requested Codex conversation does not exist."""


class TurnTimeoutError(OaiSkillError, TimeoutError):
    """A Codex turn exceeded the configured deadline."""


class AppSettings(BaseSettings):
    """Runtime configuration loaded from ``APP_*`` environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="APP_", env_file_encoding="utf-8", extra="ignore"
    )

    host: str = "0.0.0.0"
    port: int = Field(default=8080, ge=1, le=65535)
    log_level: str = "INFO"
    llm_mode: Literal["openai", "local"] = "local"
    model: str = "sonnet"
    llm_base_url: str | None = "http://host.docker.internal:19001/v1"
    llm_api_key: SecretStr | None = None
    local_provider_id: str = "local-runtime"
    sandbox_mode: Literal[
        "read-only", "workspace-write", "danger-full-access"
    ] = "read-only"
    skill_roots: str = str(REPOSITORY_ROOT / ".agents" / "skills")
    workspace: Path = REPOSITORY_ROOT
    codex_home: Path | None = None
    turn_timeout_seconds: float = Field(default=120.0, gt=0)
    interrupt_grace_seconds: float = Field(default=10.0, gt=0)
    max_concurrency: int = Field(default=4, ge=1)
    queue_max_size: int = Field(default=32, ge=1)
    api_token: SecretStr | None = None
    gradio_username: str | None = None
    gradio_password: SecretStr | None = None
    gradio_path: str = "/ui"

    @field_validator("local_provider_id")
    @classmethod
    def validate_provider_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or any(char.isspace() for char in normalized):
            raise ValueError("local_provider_id must be a non-empty identifier")
        return normalized

    @field_validator("llm_api_key", "api_token", "gradio_password", mode="before")
    @classmethod
    def empty_secret_is_none(cls, value):
        return None if value is None or str(value).strip() == "" else value

    @field_validator("gradio_username", mode="before")
    @classmethod
    def empty_username_is_none(cls, value):
        return None if value is None or str(value).strip() == "" else value

    @field_validator("gradio_path")
    @classmethod
    def validate_gradio_path(cls, value: str) -> str:
        normalized = "/" + value.strip("/")
        if normalized == "/":
            raise ValueError("gradio_path cannot be the application root")
        return normalized

    @model_validator(mode="after")
    def validate_llm(self) -> "AppSettings":
        if self.llm_mode == "local" and not self.llm_base_url:
            raise ValueError("APP_LLM_BASE_URL is required in local mode")
        if bool(self.gradio_username) != bool(self.gradio_password):
            raise ValueError(
                "APP_GRADIO_USERNAME and APP_GRADIO_PASSWORD must be set together"
            )
        return self

    @property
    def resolved_skill_roots(self) -> tuple[Path, ...]:
        return tuple(
            Path(item).expanduser().resolve()
            for item in self.skill_roots.split(os.pathsep)
            if item.strip()
        )

    @property
    def model_provider(self) -> str:
        return "openai" if self.llm_mode == "openai" else self.local_provider_id


Settings = AppSettings


class Logging:
    """Configure process-wide logging at the application boundary."""

    @staticmethod
    def configure(level: str = "INFO") -> None:
        logging.basicConfig(
            level=getattr(logging, level.upper(), logging.INFO),
            format="%(asctime)s %(levelname)s %(name)s %(message)s",
        )


def configure_logging(level: str = "INFO") -> None:
    Logging.configure(level)


__all__ = [
    "AppSettings", "ConversationBusyError", "ConversationNotFoundError",
    "Logging", "OaiSkillError", "PACKAGE_ROOT", "REPOSITORY_ROOT",
    "ServiceNotReadyError", "Settings", "TurnTimeoutError", "configure_logging",
]
