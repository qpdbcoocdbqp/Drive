"""FastAPI application, REST schemas, routes, and exception mapping."""

from __future__ import annotations

from contextlib import asynccontextmanager
import hmac
import os
import warnings
from typing import Any

try:
    from starlette.exceptions import StarletteDeprecationWarning
except ImportError:  # pragma: no cover
    StarletteDeprecationWarning = UserWarning

warnings.filterwarnings(
    "ignore",
    message=r"'HTTP_422_UNPROCESSABLE_ENTITY' is deprecated\.",
    category=StarletteDeprecationWarning,
    module=r"gradio\.routes",
)

os.environ.setdefault("GRADIO_ANALYTICS_ENABLED", "False")

import gradio as gr
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Security, status
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, field_validator

from ..skills.runtime import CodexRuntime, MessageExecution
from ..utils.core import (
    AppSettings,
    ConversationBusyError,
    ConversationNotFoundError,
    ServiceNotReadyError,
    TurnTimeoutError,
    configure_logging,
)
from ..utils.trace import ExecutionTrace
from .ui import GradioUI


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
        return cls(**{
            "conversation_id": execution.conversation_id,
            "turn_id": execution.turn_id,
            "status": execution.status,
            "content": execution.content,
            "model": execution.model,
            "usage": execution.usage,
            "trace": execution.trace,
        })


class HealthResponse(BaseModel):
    status: str


class SkillSummary(BaseModel):
    name: str
    description: str


class ApiServer:
    """Compose the HTTP API and UI around an injected runtime."""

    bearer = HTTPBearer(auto_error=False)

    def __init__(
        self,
        settings: AppSettings | None = None,
        *,
        runtime: CodexRuntime | None = None,
        ui: GradioUI | None = None,
    ) -> None:
        self.settings = settings or AppSettings()
        configure_logging(self.settings.log_level)
        self.runtime = runtime or CodexRuntime(self.settings)
        self.ui = ui or GradioUI(self.runtime, self.settings)
        self.app = self._build()

    @asynccontextmanager
    async def lifespan(self, _app: FastAPI):
        await self.runtime.start()
        try:
            yield
        finally:
            await self.runtime.close()

    def _build(self) -> FastAPI:
        app = FastAPI(
            title="Codex Skill Server", version="0.1.0", lifespan=self.lifespan
        )
        app.state.settings = self.settings
        app.state.codex_service = self.runtime
        app.include_router(self._router())
        self._register_exception_handlers(app)

        @app.get("/", include_in_schema=False)
        async def root() -> RedirectResponse:
            return RedirectResponse(url=f"{self.settings.gradio_path}/")

        credentials = None
        if self.settings.gradio_username and self.settings.gradio_password:
            credentials = (
                self.settings.gradio_username,
                self.settings.gradio_password.get_secret_value(),
            )
        return gr.mount_gradio_app(
            app,
            self.ui.build(),
            path=self.settings.gradio_path,
            auth=credentials,
            footer_links=[],
        )

    def _router(self) -> APIRouter:
        router = APIRouter()
        router.add_api_route(
            "/health/live", self.live, methods=["GET"],
            response_model=HealthResponse, tags=["health"],
        )
        router.add_api_route(
            "/health/ready", self.ready, methods=["GET"],
            response_model=HealthResponse, tags=["health"],
        )
        router.add_api_route(
            "/v1/messages", self.create_message, methods=["POST"],
            response_model=MessageResponse, dependencies=[Depends(self.require_api_token)],
            tags=["messages"],
        )
        router.add_api_route(
            "/v1/skills", self.list_skills, methods=["GET"],
            response_model=list[SkillSummary], dependencies=[Depends(self.require_api_token)],
            tags=["skills"],
        )
        return router

    def require_api_token(
        self,
        credentials: HTTPAuthorizationCredentials | None = Security(bearer),
    ) -> None:
        configured = self.settings.api_token
        if configured is None:
            return
        supplied = credentials.credentials if credentials is not None else ""
        if not hmac.compare_digest(configured.get_secret_value(), supplied):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid API token",
                headers={"WWW-Authenticate": "Bearer"},
            )

    async def live(self) -> HealthResponse:
        return HealthResponse(status="ok")

    async def ready(self) -> HealthResponse:
        if not self.runtime.ready:
            raise HTTPException(status_code=503, detail="Codex runtime is not ready")
        return HealthResponse(status="ready")

    async def create_message(self, payload: MessageRequest) -> MessageResponse:
        execution = await self.runtime.send_message(
            payload.message, conversation_id=payload.conversation_id
        )
        return MessageResponse.from_execution(execution)

    async def list_skills(self, refresh: bool = False) -> list[SkillSummary]:
        return [
            SkillSummary(**item) for item in self.runtime.list_skills(refresh=refresh)
        ]

    @staticmethod
    def _register_exception_handlers(app: FastAPI) -> None:
        mappings = {
            ConversationBusyError: 409,
            ConversationNotFoundError: 404,
            ServiceNotReadyError: 503,
        }
        for error_type, status_code in mappings.items():
            async def handler(_request: Request, exc: Exception, code=status_code):
                return JSONResponse(status_code=code, content={"detail": str(exc)})
            app.add_exception_handler(error_type, handler)

        async def turn_timeout(_request: Request, exc: TurnTimeoutError):
            content: dict[str, Any] = {"detail": str(exc)}
            trace = getattr(exc, "trace", None)
            if trace is not None:
                content["trace"] = trace.model_dump(mode="json")
            return JSONResponse(status_code=504, content=content)

        app.add_exception_handler(TurnTimeoutError, turn_timeout)


def create_app(
    settings: AppSettings | None = None, *, runtime: CodexRuntime | None = None
) -> FastAPI:
    return ApiServer(settings, runtime=runtime).app


default_server = ApiServer()
app = default_server.app
router = default_server._router()
bearer = default_server.bearer
live = default_server.live
ready = default_server.ready
create_message = default_server.create_message
list_skills = default_server.list_skills
require_api_token = default_server.require_api_token


def register_exception_handlers(target: FastAPI) -> None:
    ApiServer._register_exception_handlers(target)
