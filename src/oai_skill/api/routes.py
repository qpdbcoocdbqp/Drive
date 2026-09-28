"""REST routes for messages, skills, and health checks."""

from __future__ import annotations

import hmac

from fastapi import APIRouter, Depends, HTTPException, Request, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..runtime.codex_service import CodexService
from .schemas import HealthResponse, MessageRequest, MessageResponse, SkillSummary


router = APIRouter()
bearer = HTTPBearer(auto_error=False)


def _service(request: Request) -> CodexService:
    return request.app.state.codex_service


def require_api_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Security(bearer),
) -> None:
    configured = request.app.state.settings.api_token
    if configured is None:
        return
    supplied = credentials.credentials if credentials is not None else ""
    if not hmac.compare_digest(configured.get_secret_value(), supplied):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid API token",
            headers={"WWW-Authenticate": "Bearer"},
        )


@router.get("/health/live", response_model=HealthResponse, tags=["health"])
async def live() -> HealthResponse:
    return HealthResponse(status="ok")


@router.get("/health/ready", response_model=HealthResponse, tags=["health"])
async def ready(service: CodexService = Depends(_service)) -> HealthResponse:
    if not service.ready:
        raise HTTPException(status_code=503, detail="Codex runtime is not ready")
    return HealthResponse(status="ready")


@router.post(
    "/v1/messages",
    response_model=MessageResponse,
    dependencies=[Depends(require_api_token)],
    tags=["messages"],
)
async def create_message(
    payload: MessageRequest,
    service: CodexService = Depends(_service),
) -> MessageResponse:
    execution = await service.send_message(
        payload.message,
        conversation_id=payload.conversation_id,
    )
    return MessageResponse.from_execution(execution)


@router.get(
    "/v1/skills",
    response_model=list[SkillSummary],
    dependencies=[Depends(require_api_token)],
    tags=["skills"],
)
async def list_skills(
    refresh: bool = False,
    service: CodexService = Depends(_service),
) -> list[SkillSummary]:
    return [SkillSummary(**item) for item in service.list_skills(refresh=refresh)]
