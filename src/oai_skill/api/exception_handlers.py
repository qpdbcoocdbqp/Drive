"""Map expected runtime failures to stable HTTP errors."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ..core.exceptions import (
    ConversationBusyError,
    ConversationNotFoundError,
    ServiceNotReadyError,
    TurnTimeoutError,
)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(ConversationBusyError)
    async def conversation_busy(
        _request: Request, exc: ConversationBusyError
    ) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(ConversationNotFoundError)
    async def conversation_missing(
        _request: Request, exc: ConversationNotFoundError
    ) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(TurnTimeoutError)
    async def turn_timeout(
        _request: Request, exc: TurnTimeoutError
    ) -> JSONResponse:
        content = {"detail": str(exc)}
        trace = getattr(exc, "trace", None)
        if trace is not None:
            content["trace"] = trace.model_dump(mode="json")
        return JSONResponse(status_code=504, content=content)

    @app.exception_handler(ServiceNotReadyError)
    async def service_not_ready(
        _request: Request, exc: ServiceNotReadyError
    ) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": str(exc)})
