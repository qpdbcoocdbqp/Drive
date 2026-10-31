#!/usr/bin/env python3
"""Run one harness2 role on its own: the solver (executor), the improver, or the judge.

    docker compose run --rm -T harness2 python /opt/tools/roles.py solve --harness DIR [--label solve] [--model local/x] TASK ...
    ... roles.py judge [--session ID] [--label solve] [--model local/x] TASK ...
    ... roles.py improve --harness DIR --rollout [SESSION/]LABEL --dim INV|DOM [--name inv] [--model local/x] TASK
    ... roles.py improve --harness DIR --rollout [SESSION/]LABEL --dim AGG --candidate NAME=HARNESS_DIR@[SESSION/]LABEL ... TASK

TASK is <domain>/<task> under tasks/. DIR is a harness tree inside the container, e.g.
/opt/harness2/runs/<session-id>/harness or the `harness/` an improve run wrote. SESSION defaults to
the current one (each `docker compose run` is a session; prefix MSYS_NO_PATHCONV=1 in Git Bash).

solve    stages TASK from tasks/, runs the executor once (HARNESS2_SANDBOX applies), no judge.
         Output: runs/<session>/jb/dataset/<TASK>/model_output/<label>; harness snapshot in harnesses/<label>.
judge    scores model_output/<label> of SESSION with the current tasks/ RUBRICS.json. An earlier
         verdict is kept as <judge>_judge.json.<UTC stamp> instead of being served from cache.
improve  improver.py: a program-run pipeline for weak models, one structured LLM call per task
         (figures -> checker -> compare -> cause -> rule -> apply; AGG: compare candidates). It never
         sees rubrics. Output under runs/<session>/improve/<name>/: harness/ (feed it to `solve
         --harness`), result.json (every step's structured output), notes.md, check.py and steps/NN_<step>.json
         (each LLM call: prompt, answer, why it was rejected). Exit 2 when a step fails.

--model overrides that role's model for this call only (SOLVER / IMPROVER / JUDGE in harness2.env).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

ROLE_ENV = {"solve": "HARNESS2_SOLVER_MODEL", "improve": "HARNESS2_IMPROVER_MODEL", "judge": "HARNESS2_JUDGE_MODEL"}


def parse(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="role", required=True)
    s = sub.add_parser("solve", help="run the executor once per task")
    s.add_argument("--harness", type=Path, required=True)
    s.add_argument("--label", default="solve")
    j = sub.add_parser("judge", help="score an existing output")
    j.add_argument("--session", default="", help="session id holding the output (default: this one)")
    j.add_argument("--label", default="solve")
    i = sub.add_parser("improve", help="run the improver pipeline once")
    i.add_argument("--harness", type=Path, required=True, help="the harness the rollout ran with")
    i.add_argument("--rollout", required=True, help="[SESSION/]LABEL of the solve run to learn from")
    i.add_argument("--dim", choices=("INV", "DOM", "AGG"), required=True)
    i.add_argument("--candidate", action="append", default=[], metavar="NAME=HARNESS_DIR@[SESSION/]LABEL",
                   help="AGG only: a candidate harness and the solve run of it (repeatable)")
    i.add_argument("--name", default="", help="output folder under improve/ (default: the dim, lowercased)")
    i.add_argument("--retries", type=int, default=2, help="re-asks per step after a rejected answer")
    i.add_argument("--no-think", action="store_true", help="turn the model's reasoning off (faster)")
    i.add_argument("--checker-model", default="", help="model for the two checker scripts only (default: the improver model)")
    for p in (s, j, i):
        p.add_argument("--model", default="", help="override this role's model (e.g. local/fable)")
        p.add_argument("tasks", nargs="+" if p is not i else 1, metavar="TASK")
    a = ap.parse_args(argv)
    if a.role == "improve" and (a.dim == "AGG") != bool(a.candidate):
        ap.error("--candidate is required with --dim AGG and only allowed there")
    return a


A = parse()
if A.model:  # harness2.config reads the models at import time
    os.environ[ROLE_ENV[A.role]] = A.model

sys.path.insert(0, str(Path(__file__).resolve().parent))
import eval_harness as E  # noqa: E402  (stage(), facts(); imports harness2 after the override)
import improver  # noqa: E402
from harness2 import config  # noqa: E402
from harness2.benchmarks.jobbench import JobBenchAdapter  # noqa: E402
from harness2.core import tree as harness_tree  # noqa: E402
from harness2.core.spec import RunView  # noqa: E402


class AnyStoreAdapter(JobBenchAdapter):
    """Reads a rollout's trajectory from the session that holds it, not only the current one."""

    def trajectory(self, run_dir: Path, *, obs_trunc: int) -> str:
        own, self.store = self.store, Path(run_dir).parents[3]  # <store>/<domain>/<task>/model_output/<label>
        try:
            return super().trajectory(run_dir, obs_trunc=obs_trunc)
        finally:
            self.store = own


def store_of(session: str) -> Path:
    """jb/dataset of a session; "" is the current one."""
    root = config.ROOT / "runs" / session if session else config.RUNS
    if not root.is_dir():
        raise SystemExit(f"no session folder {root}")
    return root / "jb" / "dataset"


def run_view(ref: str, task: str) -> RunView:
    """`[SESSION/]LABEL` -> the RunView of that solve run."""
    session, _, label = ref.rpartition("/")
    jb = JobBenchAdapter(store_root=store_of(session))
    run_dir = jb.store / task / "model_output" / label
    if not (jb.store / task / "model_traj" / label).is_dir():
        raise SystemExit(f"no rollout {label!r} for {task} in {jb.store.parent.parent}")
    return RunView(label=label, run_id=label, run_dir=run_dir, ok=True, delivered=jb.delivered(run_dir))


def solve(a: argparse.Namespace) -> int:
    jb = JobBenchAdapter()
    snap = config.RUNS / "harnesses" / a.label
    if snap.exists():
        raise SystemExit(f"{snap} exists; pick another --label")
    shutil.copytree(a.harness, snap)
    for t in a.tasks:
        E.stage(t)
        start = time.time()
        try:
            out, err = jb.execute(t, snap, a.label), ""
        except Exception as e:  # one broken run is still a result
            out, err = jb.store / t / "model_output" / a.label, repr(e)
        files = sorted(p.name for p in out.rglob("*") if p.is_file()) if out.is_dir() else []
        print(f"SOLVE {t} {a.label} {round(time.time() - start)}s files={files} {err}".rstrip(), flush=True)
    print(f"\nnext: roles.py judge --session {config.RUNS.name} --label {a.label} {' '.join(a.tasks)}")
    return 0


def judge(a: argparse.Namespace) -> int:
    jb = JobBenchAdapter(store_root=store_of(a.session))
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    for t in a.tasks:
        E.stage(t)  # the judge reads RUBRICS.json from the dataset copy
        run_dir = jb.store / t / "model_output" / a.label
        _, details = jb._details_candidates(t, a.label)
        if details.is_file():
            details.rename(details.with_name(f"{details.name}.{stamp}"))
        s = jb.score(run_dir, t) or {}
        fact, missing = E.facts(t, run_dir)
        print(f"JUDGE {t} {a.label} {s.get('n_passed', 0):g}/{s.get('n_criteria', 0):g} facts {fact or 0:.0%}", flush=True)
        if details.is_file():
            for r in json.loads(details.read_text(encoding="utf-8")).get("rubrics", []):
                for c in (r.get("result") or {}).get("criteria_results", []):
                    if not c.get("passed"):
                        print(f"  x {c.get('criterion')} | {str(c.get('reasoning', ''))[:200]}")
        for m in missing:
            print(f"  facts missing: {m}")
    return 0


def notes_md(res: dict) -> str:
    lines = [f"# {res['name']} ({res['dim']}) for {res['task']}", "",
             f"model: {res.get('model')}  rollout: {res.get('rollout')}  status: {res['status']}"]
    if res.get("error"):
        lines.append(f"error: {res['error']}")
    lines += ["", "## steps", ""] + [f"- {s['seconds']:>4}s {s['step']}: "
                                     + json.dumps({k: v for k, v in s.items() if k not in ('step', 'seconds')},
                                                  ensure_ascii=False)[:400] for s in res.get("steps", [])]
    diffs = res.get("diffs")
    if diffs is not None:
        c = res.get("checker") or {}
        lines += ["", f"## checker: two scripts agree on {c.get('agreed')} of {max(c.get('a', 0), c.get('b', 0))} figures", ""]
        lines += [f"- not verified, left out of the comparison: {n}" for n in c.get("unverified", [])[:10]]
        lines += ["", f"## DIFF ({len(diffs)})", ""] + [
            f"- [{d['kind']}] {d['name']}: reported {d['reported']}, recomputed {d['recomputed']}" for d in diffs[:30]]
    if res.get("facts"):
        lines += ["", "## how the agent worked (facts, from code)", "", improver.facts_text(res["facts"])]
    if res.get("locate"):
        c = res["locate"]
        lines += ["", f"## concrete mistake (from {c.get('from')})", "", f"agent: {c['quote']}"] + (
            [f"checker: {c['checker_line']}"] if c.get("checker_line") else []) + [f"mistake: {c['mistake']}"]
    if res.get("general"):
        lines += ["", "## general problem", "", f"{res['general']['category']}: {res['general']['problem']}"]
    if res.get("edit"):
        e = res["edit"]
        lines += ["", f"## change to {res['target']}", "", f"action: {e['action']} {e['ids'] or ''}".rstrip()]
        lines += [f"was: {b}" for b in e["before"]] + [f"now: {e['text']}"]
    if res.get("scores"):
        lines += ["", "## candidates (DIFF count)", "", json.dumps(res["scores"]), f"adopted: {res.get('adopted')}"]
    for f, rv in (res.get("reviews") or {}).items():
        lines += ["", f"## review of {f} ({rv.get('by')}): rules {rv.get('rules')}, chars {rv.get('chars')}", ""]
        lines += [f"- merged {m['ids']}: {m['text']}" for m in rv.get("merge", [])]
        lines += [f"- deleted {d}" for d in rv.get("delete", [])]
    for f, rr in (res.get("rules") or {}).items():
        lines += ["", f"## rules in {f}: {len(rr['before'])} -> {len(rr['after'])}", ""]
        lines += [f"{i}: {t}" for i, t in rr["after"].items()]
    return "\n".join(lines) + "\n"


def improve(a: argparse.Namespace) -> int:
    t = a.tasks[0]
    E.stage(t)
    jb = AnyStoreAdapter()
    surface = jb.substrate.surface()
    name = a.name or a.dim.lower()
    work = config.RUNS / "improve" / name
    if work.exists():
        raise SystemExit(f"{work} exists; pick another --name")
    work.mkdir(parents=True)
    base_tree = harness_tree.read_tree(surface, a.harness)
    if not base_tree:
        raise SystemExit(f"{a.harness} is not a harness tree")
    candidates = []
    for c in a.candidate:
        label, _, rest = c.partition("=")
        hdir, _, ref = rest.partition("@")
        if not (label and hdir and ref):
            raise SystemExit(f"bad --candidate {c!r}; expected NAME=HARNESS_DIR@[SESSION/]LABEL")
        candidates.append((label, Path(hdir), run_view(ref, t)))
    print(f"improver model: {config.IMPROVER_MODEL}  dim: {a.dim}  think: {not a.no_think}", flush=True)
    res = {"name": name, "dim": a.dim, "task": t}
    try:
        res.update(improver.run(jb=jb, task=t, dim=a.dim, base_tree=base_tree, rollout=run_view(a.rollout, t),
                                candidates=candidates, work=work, model=config.IMPROVER_MODEL,
                                think=not a.no_think, retries=a.retries, checker_model=a.checker_model))
        res["status"] = "changed" if res["changed"] else "unchanged"
    except improver.StepFailed as e:
        res.update(status="failed", error=str(e))
    tree = res.pop("tree", None)
    if tree:
        harness_tree.write_tree(surface, tree, work / "harness")
    (work / "result.json").write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")
    (work / "notes.md").write_text(notes_md(res), encoding="utf-8")
    print(f"IMPROVE {t} {a.dim} {res['status']} {res.get('changed', [])} {res.get('error', '')}".rstrip())
    print(f"result: {work / 'result.json'}\nnotes:  {work / 'notes.md'}")
    if res["status"] != "changed":
        return 2
    print(f"\nnext: roles.py solve --harness {work / 'harness'} --label {name} {t}")
    return 0


if __name__ == "__main__":
    raise SystemExit({"solve": solve, "judge": judge, "improve": improve}[A.role](A))
