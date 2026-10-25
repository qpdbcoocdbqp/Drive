#!/usr/bin/env python3
"""Score one harness on local tasks, no improvement: every task N times, every run judged.

    docker compose run --rm -T harness2 python /opt/tools/eval_harness.py [--reps 3] [--harness DIR] [TASK ...]
    ... python /opt/tools/eval_harness.py --rescore runs/<session-id>     # recompute `facts` only

TASK is <domain>/<task> under tasks/ (default: every task in tasks/local_eval). Writes into the
session (runs/<session-id>/): harness/ (the snapshot that ran), eval.json, and the usual jb/ tree.

Two scores per run: `score` is the harness2 judge (a small model; rubrics are all-or-nothing), and
`facts` is judge-free: the share of rubric criteria whose ids, months and decimals all appear in
the deliverables (on one line when the criterion reads like a table row). This file is never
visible to the agent with HARNESS2_SANDBOX=docker; with `local` it would be.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path

from harness2 import config
from harness2.benchmarks.jobbench import OC_BASE_HARNESS, JobBenchAdapter

TASKS = config.ROOT / "tasks"
DATASET = config.jb_repo() / "dataset" / "main"


def stage(task: str) -> None:
    """Refresh the dataset copy, as README `cp -r tasks/* .../dataset/main/` does."""
    dst = DATASET / task
    shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(TASKS / task, dst)


def trace_stats(task: str, label: str) -> dict:
    """Tool calls by name, and bash calls that failed before Python even ran."""
    tools: dict[str, int] = {}
    shell_errors = 0
    for f in (config.RUNS / "jb" / "dataset" / task / "model_traj" / label).glob("*.jsonl"):
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.startswith("{"):
                continue
            try:
                e = json.loads(line)
            except ValueError:
                continue
            p = e.get("part") or {}
            if e.get("type") != "tool_use":
                continue
            tools[p.get("tool", "?")] = tools.get(p.get("tool", "?"), 0) + 1
            if p.get("tool") == "bash" and "syntax error" in str((p.get("state") or {}).get("output", "")):
                shell_errors += 1
    return {"tools": tools, "shell_errors": shell_errors}


_ID = re.compile(r"\b[A-Z]{1,4}-?\d+\b")
_MONTH = re.compile(r"\b\d{4}-\d{2}(?:-\d{2})?\b")
_DEC = re.compile(r"-?\$?\d[\d,]*\.\d+")
_WORD = re.compile(r"\b[A-Z][a-z]+\b")


def _num(s: str) -> str | None:
    try:
        return f"{Decimal(s.replace('$', '').replace(',', '')):.2f}"
    except InvalidOperation:
        return None


def _tokens(line: str, is_csv: bool) -> set[str]:
    """What a line of output can be matched on: ids, months/dates, words, numbers at 2 places.
    CSV lines split on commas; prose first drops thousands separators (32,438.27)."""
    if not is_csv:
        line = re.sub(r"(?<=\d),(?=\d{3}(?:\.\d+)?\b)", "", line)
    toks = set()
    for piece in re.split(r"[,\s|:;()=]+", line):
        p = piece.strip("$*`'\".").removesuffix("USD")
        if not p:
            continue
        if _ID.fullmatch(p) or _MONTH.fullmatch(p) or _WORD.fullmatch(p):
            toks.add(p)
        if re.fullmatch(r"-?\$?\d+(?:\.\d+)?", p) and (n := _num(p)) is not None:
            toks.add(n)
    return toks


def facts(task: str, out_dir: Path) -> tuple[float | None, list[str]]:
    """(share of checkable criteria found, the ones missing). Negative criteria are skipped.
    Row-like criteria must sit on one line of a CSV deliverable when there is one."""
    rub = json.loads((TASKS / task / "RUBRICS.json").read_text(encoding="utf-8"))
    files = [f for f in out_dir.rglob("*") if f.is_file()] if out_dir.is_dir() else []
    lines, csv_lines = [], []
    for f in files:
        is_csv = f.suffix.lower() == ".csv"
        # some scripts write a literal backslash-n: those are still separate lines
        for line in f.read_text(encoding="utf-8", errors="replace").replace("\\n", "\n").splitlines():
            (csv_lines if is_csv else lines).append(_tokens(line, is_csv))
    whole = set().union(*lines, *csv_lines)
    rows = csv_lines or lines  # for table-row criteria (an id or month plus figures)
    lines = lines + csv_lines
    ok, missing = 0, []
    for r in rub["rubrics"]:
        for c in r["criterion"]:
            if "NOT " in c or c.startswith("No other"):
                continue
            want = set(_ID.findall(c)) | set(_MONTH.findall(c))
            decs = {n for m in _DEC.findall(c) if (n := _num(m)) is not None and "within" not in c}
            if not want and not decs:
                continue  # qualitative: left to the judge
            words = (set(_WORD.findall(c)) & whole) - {"Total", "Sum", "Names", "Lists"}
            if decs and want:  # a table row: id or month plus figures
                hit = any(want | decs | words <= ln for ln in rows)
            elif decs and words:  # a labelled figure: "North with 13939.91"
                hit = any(decs | words <= ln for ln in lines)
            else:
                hit = (want | decs) <= whole
            ok += hit
            if not hit:
                missing.append(c)
    n = ok + len(missing)
    return (round(ok / n, 4) if n else None), missing


def failed_criteria(task: str, label: str) -> list[str]:
    out = []
    for f in (config.RUNS / "jb" / "dataset" / task / "eval_result" / f"eval_{label}").glob("*_judge.json"):
        d = json.loads(f.read_text(encoding="utf-8"))
        for r in d.get("rubrics", []):
            for c in (r.get("result") or {}).get("criteria_results", []):
                if not c.get("passed"):
                    out.append(f"{c.get('criterion')} | {c.get('evidence', '')[:120]}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tasks", nargs="*")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--harness", type=Path, default=OC_BASE_HARNESS)
    ap.add_argument("--rescore", type=Path, metavar="SESSION_DIR", help="recompute facts in an old session")
    a = ap.parse_args()
    if a.rescore:
        return rescore(a.rescore)
    tasks = a.tasks or sorted(f"local_eval/{p.name}" for p in (TASKS / "local_eval").iterdir() if p.is_dir())

    snap = config.RUNS / "harness"
    shutil.rmtree(snap, ignore_errors=True)
    shutil.copytree(a.harness, snap)
    for t in tasks:
        stage(t)

    jb = JobBenchAdapter()
    out = config.RUNS / "eval.json"
    rows = json.loads(out.read_text(encoding="utf-8")) if out.is_file() else []
    done = {(r["task"], r["rep"]) for r in rows}
    for rep in range(1, a.reps + 1):  # rep-major, so every task has a result early
        for t in tasks:
            if (t, rep) in done:
                continue
            label, start = f"eval-r{rep}", time.time()
            try:
                score = jb.score(jb.execute(t, snap, label), t) or {}
                err = ""
            except Exception as e:  # keep going; one broken run is a result too
                score, err = {}, repr(e)
            label = label.replace("/", "__")
            fact, missing = facts(t, config.RUNS / "jb" / "dataset" / t / "model_output" / label)
            row = {
                "task": t,
                "rep": rep,
                "score": round(score["n_passed"] / score["n_criteria"], 4) if score.get("n_criteria") else None,
                "points": f"{score.get('n_passed', 0):g}/{score.get('n_criteria', 0):g}",
                "facts": fact,
                "facts_missing": missing,
                "seconds": round(time.time() - start),
                **trace_stats(t, label),
                "failed": failed_criteria(t, label),
                "error": err,
            }
            rows.append(row)
            out.write_text(json.dumps(rows, indent=1, ensure_ascii=False), encoding="utf-8")
            print(f"RESULT {t} r{rep} judge {row['points']} facts {fact or 0:.0%} "
                  f"shell_errors={row['shell_errors']} {row['tools']}", flush=True)
    summary(rows)
    return 0


def summary(rows: list[dict]) -> None:
    print("\ntask                    judge   facts   runs (judge/facts)")
    for t in sorted({r["task"] for r in rows}):
        mine = [r for r in rows if r["task"] == t]
        j = [r["score"] or 0 for r in mine]
        f = [r.get("facts") or 0 for r in mine]
        runs = " ".join(f"{a:.0%}/{b:.0%}" for a, b in zip(j, f))
        print(f"{t:22s} {sum(j) / len(j):6.0%} {sum(f) / len(f):7.0%}   {runs}")


def rescore(session: Path) -> int:
    session = session if session.is_absolute() else config.ROOT / session
    out = session / "eval.json"
    rows = json.loads(out.read_text(encoding="utf-8"))
    for r in rows:
        label = f"eval-r{r['rep']}"
        r["facts"], r["facts_missing"] = facts(r["task"], session / "jb" / "dataset" / r["task"] / "model_output" / label)
    out.write_text(json.dumps(rows, indent=1, ensure_ascii=False), encoding="utf-8")
    summary(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
