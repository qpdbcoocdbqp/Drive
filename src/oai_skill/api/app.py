"""FastAPI application factory with a mounted Gradio UI."""

from __future__ import annotations

from contextlib import asynccontextmanager
import warnings

try:
    from starlette.exceptions import StarletteDeprecationWarning
except ImportError:  # Starlette versions before this warning class existed.
    StarletteDeprecationWarning = UserWarning

# Gradio 6.14 accesses Starlette's deprecated HTTP 422 alias from its queue
# route. Suppress only that known upstream warning; all other deprecations stay
# visible. Remove this filter after upgrading to a Gradio release with #13548.
warnings.filterwarnings(
    "ignore",
    message=r"'HTTP_422_UNPROCESSABLE_ENTITY' is deprecated\.",
    category=StarletteDeprecationWarning,
    module=r"gradio\.routes",
)

import gradio as gr
from fastapi import FastAPI
from fastapi.responses import RedirectResponse

from ..core.logging import configure_logging
from ..core.settings import Settings
from ..runtime.codex_service import CodexService
from ..ui.gradio_app import create_gradio_app
from .exception_handlers import register_exception_handlers
from .routes import router


def create_app(settings: Settings | None = None) -> FastAPI:
    selected = settings or Settings()
    configure_logging(selected.log_level)
    service = CodexService(selected)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        await service.start()
        try:
            yield
        finally:
            await service.close()

    base_app = FastAPI(
        title="Codex Skill Server",
        version="0.1.0",
        lifespan=lifespan,
    )
    base_app.state.settings = selected
    base_app.state.codex_service = service
    base_app.include_router(router)
    register_exception_handlers(base_app)

    @base_app.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        return RedirectResponse(url=f"{selected.gradio_path}/")

    credentials = None
    if selected.gradio_username and selected.gradio_password:
        credentials = (
            selected.gradio_username,
            selected.gradio_password.get_secret_value(),
        )

    demo = create_gradio_app(service, selected)
    return gr.mount_gradio_app(
        base_app,
        demo,
        path=selected.gradio_path,
        auth=credentials,
        footer_links=[],
    )


app = create_app()
