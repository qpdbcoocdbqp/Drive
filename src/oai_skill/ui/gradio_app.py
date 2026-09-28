"""Gradio chat UI and Codex execution-flow view."""

from __future__ import annotations

from functools import partial

import gradio as gr

from ..core.settings import Settings
from ..runtime.codex_service import CodexService
from ..trace.plot import build_trace_figure
from .handlers import add_assistant_message, add_user_message, clear_conversation


def create_gradio_app(service: CodexService, settings: Settings) -> gr.Blocks:
    respond = partial(add_assistant_message, service=service)

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
                    value=build_trace_figure(None),
                    label="Execution flow",
                )

        def wire_submit(trigger):
            staged = trigger(
                add_user_message,
                inputs=[message, chatbot],
                outputs=[chatbot, message, pending_message],
                queue=False,
            )
            staged.then(
                respond,
                inputs=[pending_message, chatbot, conversation_id],
                outputs=[chatbot, conversation_id, trace_plot],
            )

        wire_submit(send.click)
        wire_submit(message.submit)
        clear.click(
            clear_conversation,
            inputs=None,
            outputs=[
                chatbot,
                conversation_id,
                trace_plot,
                message,
                pending_message,
            ],
            queue=False,
        )

    demo.queue(
        max_size=settings.queue_max_size,
        default_concurrency_limit=settings.max_concurrency,
        api_open=False,
    )
    return demo
