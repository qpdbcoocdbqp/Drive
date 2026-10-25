# /// script
# requires-python = ">=3.10"
# dependencies = ["gradio>=6", "pandas"]
# ///
"""Browse executor trajectories (model_traj/*.jsonl) of harness2 sessions in a Gradio app.

    uv run tools/traj_viewer.py                    # from docker/; prints the URL (first free port from 7860)
    uv run tools/traj_viewer.py --runs runs --port 7861

Runs on the host, read-only. For each attempt: the conversation (text and tool calls with their
input and output), a timeline, tokens per step against the context size, the deliverables and the
judge's verdict for that label, and every raw event.
"""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from datetime import datetime
from pathlib import Path

import gradio as gr
import pandas as pd

MAX_OUT = 4000  # characters of a tool output shown in the conversation
LANG = {".py": "python", ".md": "markdown", ".csv": "csv", ".json": "json", ".sh": "bash", ".txt": ""}


def _long(p: Path) -> Path:
    """Windows: trajectories sit deeper than MAX_PATH (260), so use the extended-length form."""
    p = p.resolve()
    if os.name == "nt" and not str(p).startswith("\\\\?\\"):
        return Path("\\\\?\\" + str(p))
    return p


def _short(p: Path) -> str:
    return str(p).removeprefix("\\\\?\\")


# ------------------------------------------------------------------ discovery
def find_attempts(root: Path) -> dict[str, Path]:
    """'<session> · <task> · <label> · attempt N' -> file, newest session first."""
    found = []
    for f in _long(root).rglob("model_traj/*/*.jsonl"):
        parts = f.parts
        i = parts.index("model_traj")
        session = parts[len(_long(root).parts)]
        task = "/".join(parts[i - 2 : i])
        label = parts[i + 1]
        attempt = f.stem.rsplit("_attempt", 1)[-1].split("_", 1)[0]
        found.append((session, task, label, attempt, f))
    found.sort(key=lambda x: (x[0], x[1], x[2], x[3]), reverse=True)
    return {f"{s} · {t} · {lab} · attempt {a}": f for s, t, lab, a, f in found}


def load_events(f: Path) -> list[dict]:
    out = []
    for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("{"):
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    return out


# ------------------------------------------------------------------ rendering
def _fence(text: str, lang: str = "") -> str:
    text = text if len(text) <= MAX_OUT else text[:MAX_OUT] + f"\n… ({len(text) - MAX_OUT} more characters)"
    fence = "````" if "```" in text else "```"
    return f"{fence}{lang}\n{text}\n{fence}"


def _ext_lang(path: str) -> str:
    return LANG.get(Path(path).suffix.lower(), "")


def _tool_input(tool: str, inp: dict) -> tuple[str, str]:
    """(one-line summary, markdown body) for a tool call's input."""
    path = str(inp.get("filePath") or inp.get("path") or "")
    if tool == "bash":
        cmd = str(inp.get("command", ""))
        return cmd.splitlines()[0][:120] if cmd else "", _fence(cmd, "bash")
    if tool == "write":
        return path, f"`{path}`\n\n" + _fence(str(inp.get("content", "")), _ext_lang(path))
    if tool == "edit":
        old, new = str(inp.get("oldString", "")), str(inp.get("newString", ""))
        diff = "\n".join(["- " + x for x in old.splitlines()] + ["+ " + x for x in new.splitlines()])
        return path, f"`{path}`\n\n" + _fence(diff, "diff")
    if tool == "read":
        extra = {k: v for k, v in inp.items() if k != "filePath"}
        return path, f"`{path}`" + (f" {extra}" if extra else "")
    body = json.dumps(inp, ensure_ascii=False, indent=2)
    return (path or body.replace("\n", " "))[:120], _fence(body, "json")


def _secs(t: dict | None) -> float | None:
    if t and t.get("start") and t.get("end"):
        return round((t["end"] - t["start"]) / 1000, 1)
    return None


def render(events: list[dict]):
    """-> (chat messages, timeline rows, token rows, summary facts)."""
    chat, rows, tokens = [], [], []
    tools, errors = Counter(), 0
    t0 = events[0].get("timestamp", 0) if events else 0
    step = 0
    for i, e in enumerate(events):
        kind, part = e.get("type"), e.get("part") or {}
        rel = round((e.get("timestamp", t0) - t0) / 1000, 1)
        if kind == "step_start":
            step += 1
        elif kind == "text":
            text = part.get("text", "")
            chat.append({"role": "assistant", "content": text or "(empty)"})
            rows.append([i, rel, step, "text", "", text.replace("\n", " ")[:120], "", _secs(part.get("time"))])
        elif kind == "reasoning":
            chat.append({"role": "assistant", "content": part.get("text", ""),
                         "metadata": {"title": "💭 reasoning", "status": "done"}})
            rows.append([i, rel, step, "reasoning", "", part.get("text", "")[:120], "", None])
        elif kind == "tool_use":
            tool = part.get("tool", "?")
            state = part.get("state") or {}
            status = state.get("status", "")
            out = str(state.get("output", "") or state.get("error", ""))
            summary, body = _tool_input(tool, state.get("input") or {})
            secs = _secs(state.get("time"))
            tools[tool] += 1
            bad = status not in ("completed", "") or "Traceback" in out or "syntax error" in out
            icon = "❌" if bad else "🔧"
            title = f"{icon} {tool} · {summary[:80]}" + (f" · {secs}s" if secs is not None else "")
            chat.append({"role": "assistant",
                         "content": f"**Input**\n\n{body}\n\n**Output** ({len(out)} chars)\n\n{_fence(out)}",
                         "metadata": {"title": title, "status": "done"}})
            rows.append([i, rel, step, tool, status, summary, len(out), secs])
        elif kind == "step_finish":
            tk = part.get("tokens") or {}
            tokens.append({"step": step, "input": tk.get("input", 0), "output": tk.get("output", 0),
                           "reasoning": tk.get("reasoning", 0), "reason": part.get("reason", "")})
        elif kind == "error":
            errors += 1
            err = e.get("error") or {}
            msg = (err.get("data") or {}).get("message") or json.dumps(err, ensure_ascii=False)
            chat.append({"role": "assistant", "content": _fence(msg),
                         "metadata": {"title": f"❌ error · {err.get('name', '')}", "status": "done"}})
            rows.append([i, rel, step, "error", err.get("name", ""), msg[:120], "", None])
    duration = round((events[-1].get("timestamp", t0) - t0) / 1000) if events else 0
    facts = {"steps": step, "duration_s": duration, "tools": dict(tools), "errors": errors,
             "input_max": max((t["input"] for t in tokens), default=0),
             "output_sum": sum(t["output"] for t in tokens),
             "start": datetime.fromtimestamp(t0 / 1000).strftime("%Y-%m-%d %H:%M:%S") if t0 else ""}
    return chat, rows, tokens, facts


def task_dir_of(f: Path) -> tuple[Path, str]:
    """(…/<domain>/<task>/, label) for a model_traj/<label>/<file>.jsonl."""
    return f.parent.parent.parent, f.parent.name


def judge_md(task_dir: Path, label: str) -> str:
    files = sorted((task_dir / "eval_result" / f"eval_{label}").glob("*_judge.json"))
    if not files:
        return "_No judge result for this label._"
    d = json.loads(files[0].read_text(encoding="utf-8"))
    out = [f"### Judge `{d.get('judge_model')}`: {d.get('total_score')}/{d.get('max_score')}"
           f" ({d.get('passed_count')}/{d.get('total_count')} rubrics)\n"]
    for r in d.get("rubrics", []):
        res = r.get("result") or {}
        out.append(f"**{'✅' if res.get('passed') else '❌'} (w{r.get('weight')}) {r.get('rubric')}**\n")
        for c in res.get("criteria_results", []):
            mark = "✅" if c.get("passed") else "❌"
            out.append(f"- {mark} {c.get('criterion')}  \n  <small>{c.get('reasoning', '')}</small>")
        out.append("")
    return "\n".join(out)


def outputs_of(task_dir: Path, label: str) -> dict[str, Path]:
    d = task_dir / "model_output" / label
    return {p.relative_to(d).as_posix(): p for p in sorted(d.rglob("*")) if p.is_file()} if d.is_dir() else {}


# ------------------------------------------------------------------ app
def build(root: Path) -> gr.Blocks:
    index: dict[str, Path] = {}

    def refresh():
        index.clear()
        index.update(find_attempts(root))
        keys = list(index)
        return gr.update(choices=keys, value=keys[0] if keys else None)

    def show(key):
        empty = ([], pd.DataFrame(), pd.DataFrame(columns=["step", "tokens", "kind"]), "", "",
                 gr.update(choices=[], value=None), "", [], gr.update(maximum=0, value=0), {})
        if not key or key not in index:
            return empty
        f = index[key]
        events = load_events(f)
        chat, rows, tokens, facts = render(events)
        task_dir, label = task_dir_of(f)
        outs = outputs_of(task_dir, label)
        tools = ", ".join(f"{k} {v}" for k, v in sorted(facts["tools"].items(), key=lambda kv: -kv[1]))
        summary = (
            f"**{key}**  \n`{_short(f)}`\n\n"
            f"| start | duration | steps | tool calls | errors | max input tokens | output tokens |\n"
            f"|---|---|---|---|---|---|---|\n"
            f"| {facts['start']} | {facts['duration_s']} s | {facts['steps']} | {tools or '-'} | "
            f"{facts['errors']} | {facts['input_max']:,} | {facts['output_sum']:,} |"
        )
        timeline = pd.DataFrame(rows, columns=["#", "t (s)", "step", "type", "status", "summary", "out chars", "secs"])
        long = [{"step": t["step"], "tokens": t[k], "kind": k} for t in tokens for k in ("input", "output")]
        tok = pd.DataFrame(long or [], columns=["step", "tokens", "kind"])
        first = next(iter(outs), None)
        return (chat, timeline, tok, summary, judge_md(task_dir, label),
                gr.update(choices=list(outs), value=first), show_output(key, first),
                events, gr.update(maximum=max(len(events) - 1, 0), value=0), events[0] if events else {})

    def show_output(key, name):
        if not key or key not in index or not name:
            return ""
        task_dir, label = task_dir_of(index[key])
        p = outputs_of(task_dir, label).get(name)
        if p is None:
            return ""
        text = p.read_text(encoding="utf-8", errors="replace")
        return text if p.suffix.lower() == ".md" else _fence(text, _ext_lang(name))

    with gr.Blocks(title="harness2 trajectories") as app:
        gr.Markdown(f"# harness2 trajectories\nRuns root: `{_short(_long(root))}`")
        with gr.Row():
            pick = gr.Dropdown(label="Attempt (session · task · label · attempt)", filterable=True, scale=8)
            again = gr.Button("Refresh", scale=1)
        summary = gr.Markdown()
        events_state = gr.State([])
        with gr.Tabs():
            with gr.Tab("Conversation"):
                chat = gr.Chatbot(height=720, label="Conversation")
            with gr.Tab("Timeline"):
                timeline = gr.Dataframe(wrap=True)
            with gr.Tab("Tokens"):
                gr.Markdown("Input tokens per step: the whole context sent to the model. "
                            "Compare with the server's context size (llm config.yaml `--ctx-size`).")
                tok = gr.LinePlot(x="step", y="tokens", color="kind", height=360)
            with gr.Tab("Deliverables"):
                out_pick = gr.Dropdown(label="File in model_output/<label>/")
                out_view = gr.Markdown()
            with gr.Tab("Judge"):
                judge = gr.Markdown()
            with gr.Tab("Raw events"):
                idx = gr.Number(value=0, precision=0, minimum=0, label="Event # (from 0)")
                raw = gr.JSON()

        outputs = [chat, timeline, tok, summary, judge, out_pick, out_view, events_state, idx, raw]
        app.load(refresh, None, pick).then(show, pick, outputs)
        again.click(refresh, None, pick)
        pick.change(show, pick, outputs)
        out_pick.change(show_output, [pick, out_pick], out_view)
        idx.change(lambda evs, i: evs[min(max(int(i or 0), 0), len(evs) - 1)] if evs else {},
                   [events_state, idx], raw)
    return app


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs", type=Path, default=Path(__file__).resolve().parent.parent / "runs")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=None, help="default: the first free port from 7860")
    a = ap.parse_args()
    build(a.runs).launch(server_name=a.host, server_port=a.port)  # None lets Gradio try 7860, 7861, ...


if __name__ == "__main__":
    main()
