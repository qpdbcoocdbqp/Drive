"""Render a normalized execution trace as an interactive Plotly flow."""

from __future__ import annotations

from collections import defaultdict
from textwrap import wrap

import plotly.graph_objects as go

from .models import ExecutionTrace, TraceEventType


LANES = {
    TraceEventType.user: 3,
    TraceEventType.assistant: 2,
    TraceEventType.skill_call: 1,
    TraceEventType.tool_call: 0,
}

COLORS = {
    TraceEventType.user: "#2563eb",
    TraceEventType.assistant: "#7c3aed",
    TraceEventType.skill_call: "#059669",
    TraceEventType.tool_call: "#d97706",
}


def _format_hover(value: str | None, *, limit: int = 96, width: int = 40) -> str:
    """Return compact text with explicit Plotly line breaks."""
    text = (value or "—").strip()
    if len(text) > limit:
        text = text[: limit - 1] + "…"

    # Plotly displays HTML entities from customdata literally. Replace the
    # characters that could form markup while leaving JSON quotes readable.
    text = text.translate(str.maketrans({"&": "＆", "<": "‹", ">": "›"}))

    lines: list[str] = []
    for source_line in text.splitlines() or [""]:
        lines.extend(wrap(source_line, width=width) or [""])
    return "<br>".join(lines)


def build_trace_figure(trace: ExecutionTrace | None) -> go.Figure:
    figure = go.Figure()
    events = [] if trace is None else sorted(trace.events, key=lambda item: item.sequence)
    if not events:
        figure.add_annotation(
            text="送出訊息後，Codex 執行流程會顯示在這裡。",
            x=0.5,
            y=0.5,
            xref="paper",
            yref="paper",
            showarrow=False,
        )
        return _style(figure)

    positions = {
        event.id: (event.sequence, LANES[event.event_type]) for event in events
    }
    edge_x: list[float | None] = []
    edge_y: list[float | None] = []
    for event in events:
        if event.parent_id not in positions:
            continue
        parent_x, parent_y = positions[event.parent_id]
        child_x, child_y = positions[event.id]
        edge_x.extend([parent_x, child_x, None])
        edge_y.extend([parent_y, child_y, None])
    figure.add_trace(
        go.Scatter(
            x=edge_x,
            y=edge_y,
            mode="lines",
            line={"color": "#94a3b8", "width": 1.5},
            hoverinfo="skip",
            showlegend=False,
        )
    )

    grouped: defaultdict[TraceEventType, list] = defaultdict(list)
    for event in events:
        grouped[event.event_type].append(event)
    for event_type, items in grouped.items():
        figure.add_trace(
            go.Scatter(
                x=[item.sequence for item in items],
                y=[LANES[event_type] for _ in items],
                mode="markers+text",
                name=event_type.value.replace("_", " ").title(),
                text=[item.name for item in items],
                textposition="top center",
                marker={
                    "size": 18,
                    "color": COLORS[event_type],
                    "symbol": [
                        "x" if item.status.value == "failed" else "circle"
                        for item in items
                    ],
                },
                customdata=[
                    [
                        item.status.value,
                        item.duration_ms,
                        _format_hover(item.input_summary),
                        _format_hover(item.output_summary),
                    ]
                    for item in items
                ],
                hovertemplate=(
                    "<b>%{text}</b><br>status=%{customdata[0]}"
                    "<br>duration=%{customdata[1]} ms"
                    "<br>input=%{customdata[2]}"
                    "<br>output=%{customdata[3]}<extra></extra>"
                ),
            )
        )
    return _style(figure)


def _style(figure: go.Figure) -> go.Figure:
    figure.update_layout(
        title="Codex execution flow",
        template="plotly_white",
        height=440,
        margin={"l": 30, "r": 30, "t": 60, "b": 40},
        legend={"orientation": "h", "y": 1.12, "x": 0},
        xaxis={"title": "Execution order", "showgrid": False, "zeroline": False},
        yaxis={
            "tickmode": "array",
            "tickvals": [0, 1, 2, 3],
            "ticktext": ["Tool", "Skill", "Assistant", "User"],
            "range": [-0.5, 3.5],
            "showgrid": True,
            "zeroline": False,
        },
        hoverlabel={"align": "left"},
    )
    return figure
