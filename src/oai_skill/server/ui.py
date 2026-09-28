"""Gradio UI adapter backed by an injected Codex runtime."""

from __future__ import annotations

from typing import Any

import gradio as gr

from ..skills.runtime import CodexRuntime
from ..utils.core import AppSettings
from ..utils.trace import TraceRenderer, build_trace_figure


class GradioUI:
    """Own the Gradio layout and its runtime-bound event handlers."""

    def __init__(
        self,
        runtime: CodexRuntime,
        settings: AppSettings,
        *,
        renderer: type[TraceRenderer] = TraceRenderer,
    ) -> None:
        self.runtime = runtime
        self.settings = settings
        self.renderer = renderer

    @staticmethod
    def add_user_message(
        message: str, history: list[dict[str, Any]] | None
    ) -> tuple[list[dict[str, Any]], str, str]:
        text = (message or "").strip()
        current = list(history or [])
        if text:
            current.append({"role": "user", "content": text})
        return current, "", text

    async def add_assistant_message(
        self,
        message: str,
        history: list[dict[str, Any]] | None,
        conversation_id: str | None,
    ) -> tuple[list[dict[str, Any]], str | None, Any]:
        current = list(history or [])
        if not message:
            return current, conversation_id, self.renderer.build(None)
        try:
            execution = await self.runtime.send_message(
                message, conversation_id=conversation_id
            )
        except Exception as exc:
            current.append({"role": "assistant", "content": f"執行失敗：{exc}"})
            return current, conversation_id, self.renderer.build(
                getattr(exc, "trace", None)
            )
        current.append({"role": "assistant", "content": execution.content})
        return current, execution.conversation_id, self.renderer.build(execution.trace)

    def clear_conversation(self) -> tuple[list, None, Any, str, str]:
        return [], None, self.renderer.build(None), "", ""

    def build(self) -> gr.Blocks:
        with gr.Blocks(title="Codex Skill Server") as demo:
            gr.Markdown("# Codex Skill Server")
            conversation_id = gr.State(value=None)
            pending_message = gr.State(value="")

            with gr.Row():
                with gr.Column(scale=1):
                    chatbot = gr.Chatbot(label="Conversation", height=520)
                    message = gr.Textbox(
                        label="User message",
                        placeholder="輸入訊息後按 Enter 或 Send",
                        lines=3,
                    )
                    with gr.Row():
                        send = gr.Button("Send", variant="primary")
                        clear = gr.Button("New chat")
                with gr.Column(scale=1):
                    trace_plot = gr.Plot(
                        value=self.renderer.build(None), label="Execution flow"
                    )

            def wire_submit(trigger):
                staged = trigger(
                    self.add_user_message,
                    inputs=[message, chatbot],
                    outputs=[chatbot, message, pending_message],
                    queue=False,
                )
                staged.then(
                    self.add_assistant_message,
                    inputs=[pending_message, chatbot, conversation_id],
                    outputs=[chatbot, conversation_id, trace_plot],
                )

            wire_submit(send.click)
            wire_submit(message.submit)
            clear.click(
                self.clear_conversation,
                inputs=None,
                outputs=[chatbot, conversation_id, trace_plot, message, pending_message],
                queue=False,
            )

        demo.queue(
            max_size=self.settings.queue_max_size,
            default_concurrency_limit=self.settings.max_concurrency,
            api_open=False,
        )
        return demo


def create_gradio_app(service: CodexRuntime, settings: AppSettings) -> gr.Blocks:
    return GradioUI(service, settings).build()


def add_user_message(message: str, history: list[dict[str, Any]] | None):
    return GradioUI.add_user_message(message, history)


async def add_assistant_message(message, history, conversation_id, service):
    ui = GradioUI(service, service.settings)
    return await ui.add_assistant_message(message, history, conversation_id)


def clear_conversation():
    return [], None, build_trace_figure(None), "", ""
