"""Improver for weak models: the program runs the steps, each LLM call does one small task.

Every call is a chat completion with a JSON schema (structured output); its JSON is what the next step
gets. Code, not the model, reads the evidence, runs the checker, compares numbers, edits the rule file and
validates the harness. Rubrics are never read.

One `Run` per improvement. Its steps are methods, run in the order PIPELINES lists; each returns the
structured info it logs and leaves its output on the run for the steps after it.

    figures     (code: CSV and Markdown tables; LLM: numbers in prose)  what the rollout reports: tables by
                their key and numeric columns, plus single figures
    checker     (LLM writes TWO scripts independently, code runs them in the sandbox, LLM fixes on error)  the
                tables and figures recomputed from the inputs; only what both scripts give the same value for
                counts, and when they agree on too few the improvement stops
    compare     (code)  DIFF list, three kinds: a figure that differs, a row the agent has and no script has
                (extra), a row both scripts have and the agent left out (missing)
    facts       (code)  how the agent worked: code run or not, scripts, files read, how outputs were written
    locate      (LLM, quotes checked by code)  the concrete mistake behind the first DIFF: the line of the
                agent's script that differs from the checker, or the trajectory action if it wrote no script
    generalize  (LLM, checked against the facts)  the working habit behind it: a category + one sentence
    read        (code)  the harness's rule file (guardrails.md for INV, LongTermMEMORY.md for DOM) as rules
    match       (LLM, only when a rule mentions the habit's topic)  which rules already cover the habit
    edit        (LLM, checked by code)  the least change: rewrite the covering rule / merge several covering
                rules into one shorter rule / add ONE rule only when none covers it and it repeats none
    measure     (AGG)  each candidate's DIFF count against the same checker values
    choose      (AGG)  candidates with fewer DIFFs than base; when none is better, the ones that tie with base
    pool        (AGG)  base rules + the chosen candidates' new rules, in one list per rule file
    review      (LLM, checked by code)  merge rules that say the same thing, delete rules another rule
                contains; the result is never longer than before. INV/DOM: after the edit. AGG: this is the
                merge of the pooled rules, with exactly the same logic
    apply       (code)  at most one more rule per adopted change; validate the tree
"""

from __future__ import annotations

import csv
import io
import json
import re
import shutil
import tempfile
import textwrap
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import NamedTuple

import eval_harness as E
from harness2 import config, sandbox
from harness2.core import recursion
from harness2.core import tree as harness_tree

TARGET = {"INV": "guardrails.md", "DOM": "LongTermMEMORY.md"}
SYSTEM = "You do exactly one small task. Answer only with JSON that matches the schema."
MAX_TRAJ = 24_000  # chars of trajectory given to the locate step (32k-token slots)
MAX_FILE = 6_000  # chars per input or deliverable file in a prompt
MAX_RULE = 300  # chars of one rule
TOL = 0.011
AGREE_MIN = 0.5  # the two checker scripts must give the same value for this share of their figures
KINDS = ("value", "extra", "missing")  # kinds of difference, most useful to explain first
DUP = 0.5  # word overlap at which two rules count as saying the same thing


class StepFailed(RuntimeError):
    pass


# ---------- small helpers: JSON schemas, guard lists ----------

def obj(**f): return {"type": "object", "additionalProperties": False, "required": list(f), "properties": f}
def string(n=None): return {"type": "string", **({"maxLength": n} if n else {})}
def arr(item, n=None, **kw): return {"type": "array", **({"maxItems": n} if n else {}), "items": item, **kw}
def enum(ids): return {"type": "string", "enum": list(ids)}
NUM, NUM_OR_NULL = {"type": "number"}, {"type": ["number", "null"]}


def first(*pairs):
    """The message of the first (condition, message) pair whose condition holds, else None."""
    return next((msg for bad, msg in pairs if bad), None)


def _clip(text: str, n: int = MAX_FILE) -> str:
    return text if len(text) <= n else text[:n] + f"\n... [{len(text) - n} more characters]"


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


# ---------- LLM: one structured call per task ----------

class LLM:
    """Chat completions on the OpenAI-compatible provider, one JSON-schema answer per call. It also carries
    what every step shares: how often a rejected answer is re-asked, and the names of this task's files."""

    def __init__(self, model: str, log_dir: Path, *, think: bool = True, retries: int = 2, names=()):
        self.model = config.provider_model(model)
        if not (self.model and config.PROVIDER_BASE_URL):
            raise SystemExit(f"improver model {model!r} must be {config.PROVIDER_ID}/<id> on HARNESS2_PROVIDER_BASE_URL")
        self.log_dir, self.think, self.retries, self.names, self.n = log_dir, think, retries, list(names), 0
        log_dir.mkdir(parents=True, exist_ok=True)

    def _post(self, messages: list[dict], name: str, schema: dict, temperature: float) -> dict:
        body = {
            "model": self.model, "messages": messages, "temperature": temperature,
            "max_tokens": 12_000 if self.think else 4_000,
            "response_format": {"type": "json_schema",
                                "json_schema": {"name": name, "strict": True, "schema": schema}},
        }
        if not self.think:
            body["chat_template_kwargs"] = {"enable_thinking": False}
        headers = {"Content-Type": "application/json"}
        if config.PROVIDER_API_KEY:
            headers["Authorization"] = f"Bearer {config.PROVIDER_API_KEY}"
        req = urllib.request.Request(config.PROVIDER_BASE_URL + "/chat/completions",
                                     data=json.dumps(body).encode(), headers=headers)
        with urllib.request.urlopen(req, timeout=1800) as r:
            return json.load(r)

    def ask(self, step: str, prompt: str, schema: dict, *, check=None, temperature: float = 0.2) -> dict:
        """One task: ask, parse, `check(obj)` -> problem or None; a rejected answer is re-asked with the
        reason, up to `self.retries` times. Every call is logged to <log_dir>/NN_step.json."""
        messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}]
        problem = ""
        for attempt in range(self.retries + 1):
            self.n += 1
            start, raw, parsed, reply = time.time(), "", None, {}
            try:
                reply = self._post(messages, step, schema, temperature)
                raw = reply["choices"][0]["message"].get("content") or ""
                parsed = json.loads(raw)
                problem = check(parsed) if check else None
            except (urllib.error.URLError, TimeoutError, KeyError, IndexError) as e:
                problem = f"request failed: {e}"
            except ValueError as e:
                problem = f"the answer is not valid JSON ({e})"
            (self.log_dir / f"{self.n:02d}_{step}.json").write_text(json.dumps({
                "step": step, "attempt": attempt + 1, "seconds": round(time.time() - start, 1),
                "messages": messages, "answer": raw, "parsed": parsed, "rejected": problem,
                "usage": reply.get("usage"),
            }, indent=1, ensure_ascii=False), encoding="utf-8")
            if not problem:
                return parsed
            print(f"    {step}: rejected ({str(problem)[:160]})", flush=True)
            if raw:
                messages = messages[:2] + [{"role": "assistant", "content": raw},
                                           {"role": "user", "content": f"Rejected: {problem}\nAnswer again."}]
        raise StepFailed(f"{step}: {problem}")

    def general(self, text: str) -> str | None:
        """A problem when `text` names something that belongs to this task only (an id, a decimal, a file)."""
        m = E._ID.search(text) or E._DEC.search(text)
        bad = m.group(0) if m else next((n for n in self.names if Path(n).name.lower() in text.lower()), None)
        return f"it names something from this task ({bad}); state it in general terms" if bad else None


# ---------- evidence (code) ----------

def _num(cell: str) -> float | None:
    s = cell.strip().strip("*`").replace(",", "").replace("$", "").rstrip("%").strip()
    try:
        return float(s) if re.fullmatch(r"-?\d+(\.\d+)?", s) else None
    except ValueError:
        return None


def task_inputs(jb, task: str) -> tuple[str, dict[str, str], Path]:
    """(instructions, {input file: preview}, task_folder) from the dataset copy the agent got."""
    folder = jb._dataset_task(task) / "task_folder"
    instructions = (folder / "TASK_INSTRUCTIONS.txt").read_text(encoding="utf-8")
    previews = {}
    for p in sorted(folder.rglob("*")):
        if not p.is_file() or p.name == "TASK_INSTRUCTIONS.txt":
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        if p.suffix.lower() == ".csv":
            lines = text.splitlines()
            text = "\n".join(lines[:8]) + (f"\n... ({len(lines) - 1} data rows in total)" if len(lines) > 8 else "")
        previews[p.relative_to(folder).as_posix()] = _clip(text)
    return instructions, previews, folder


def norm_key(cells) -> tuple:
    return tuple(_norm(str(c).strip().strip("*`")) for c in cells)


_TOTAL = re.compile(r"^(grand |sub)?total|^overall|^sum$")


def to_table(rows: list[list[str]]) -> tuple[dict | None, dict[str, float]]:
    """(the table, its total rows). A table is {key: [key columns], val: [numeric columns], rows: {key: {col:
    number}}}: a column is numeric when every cell is. A row labelled total/sum/overall is not a row but the
    figures `<label> | <column>`."""
    if len(rows) < 2:
        return None, {}
    head = [c.strip().strip("*`") for c in rows[0]]
    body = [r + [""] * (len(head) - len(r)) for r in rows[1:] if any(c.strip() for c in r)]
    val = [j for j in range(len(head)) if body and all(_num(r[j]) is not None for r in body)]
    key = [j for j in range(len(head)) if j not in val]
    if not (key and val):
        return None, {}
    table, totals = {"key": [head[j] for j in key], "val": [head[j] for j in val], "rows": {}}, {}
    for r in body:
        k = norm_key(r[j] for j in key)
        if _TOTAL.match(" ".join(k)):
            totals.update({f"{' '.join(k)} | {head[j]}": _num(r[j]) for j in val})
        else:
            table["rows"][k] = {head[j]: _num(r[j]) for j in val}
    return table, totals


_SEP_CELL = re.compile(r"^:?-{2,}:?$")


def md_tables(text: str) -> tuple[list[list[list[str]]], str]:
    """(the Markdown tables as rows of cells, the text outside them)."""
    tables, rest, cur = [], [], []
    for line in text.splitlines() + [""]:
        if line.strip().startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if not all(_SEP_CELL.match(c) for c in cells if c):
                cur.append(cells)
            continue
        if cur:
            tables.append(cur)
            cur = []
        rest.append(line)
    return tables, "\n".join(rest)


def _files_block(files: dict[str, str]) -> str:
    return "\n\n".join(f"=== {n} ===\n{_clip(t)}" for n, t in files.items())


def read_report(llm: LLM, files: dict[str, str], scalar_names: list[str] | None = None) -> dict:
    """What a rollout reports: {"tables": {id: table}, "scalars": {name: number}}. CSV and Markdown tables (and
    their total rows) are read by code; numbers in the remaining prose by one LLM call that either names them
    (scalar_names=None) or fills in the given names."""
    tables, scalars, prose = {}, {}, {}
    for n, t in files.items():
        raw, rest = ([list(csv.reader(io.StringIO(t)))], "") if n.lower().endswith(".csv") else md_tables(t)
        for i, rows in enumerate(raw, 1):
            tid = n if len(raw) == 1 else f"{n}#{i}"
            table, totals = to_table(rows)
            if table:
                tables[tid] = table
            scalars.update({f"{tid} | {k}": v for k, v in totals.items()})
        if re.search(r"\d", "\n".join(ln for ln in rest.splitlines() if not ln.lstrip().startswith("#"))):
            prose[n] = rest
    if scalar_names is not None:
        want = [k for k in scalar_names if k not in scalars]
        if want and prose:
            got = llm.ask("figures", (
                "Below are report files. For each name in the list, give the number the files report for it, "
                "or null if they do not report it.\n\nNAMES\n" + "\n".join(f"- {k}" for k in want)
                + "\n\n" + _files_block(prose)), obj(values=arr(obj(name=enum(want), value=NUM_OR_NULL))))
            scalars.update({v["name"]: v["value"] for v in got["values"] if v["value"] is not None})
    elif prose:
        got = llm.ask("figures", (
            "Below are report files an agent wrote. List every number they report as a result (totals, counts, "
            "amounts, rates). Give each a short unique name that says what it is, starting with the file name, "
            "e.g. 'summary.md: total revenue USD'. Skip dates, years, ids and list numbering.\n\n"
            + _files_block(prose)), obj(figures=arr(obj(name=string(), value=NUM), 60)),
            check=lambda o: None if o["figures"] or tables or scalars else "no figure listed")
        scalars.update({f["name"].replace(" | ", " / "): f["value"] for f in got["figures"]})
    return {"tables": tables, "scalars": scalars}


def report_size(rep: dict) -> int:
    return sum(len(v) for t in rep["tables"].values() for v in t["rows"].values()) + len(rep["scalars"])


# ---------- checker: the same figures recomputed from the inputs ----------

def _run_checker(code: str, folder: Path, work: Path, tag: str = "") -> tuple[dict | None, str]:
    """Run code on a copy of the task inputs (in the sandbox when HARNESS2_SANDBOX=docker);
    (the printed JSON object, the output)."""
    ws = Path(tempfile.mkdtemp(prefix="h2check-"))
    try:
        for p in folder.rglob("*"):
            if p.is_file():
                (ws / p.relative_to(folder)).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, ws / p.relative_to(folder))
        (ws / "check.py").write_text(code, encoding="utf-8")
        log = work / f"check_output{tag}.txt"
        with log.open("w", encoding="utf-8") as out:
            rc = sandbox.run(["python3", "check.py"], workspace=ws, cwd=ws,
                             env={"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(ws), "PYTHONWARNINGS": "ignore"},
                             stdout=out, stderr=out, timeout=120)
        text = log.read_text(encoding="utf-8", errors="replace")
    finally:
        shutil.rmtree(ws, ignore_errors=True)
    if rc != 0:
        return None, f"exit {rc}\n{text[-2500:]}"
    for line in reversed(text.strip().splitlines()):
        try:
            parsed = json.loads(line)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            return parsed, text
    return None, f"no JSON object line in the output\n{text[-2500:]}"


def _error_snippet(code: str, output: str) -> str:
    """The lines around the last `line N` the traceback names, numbered, to point the fix at them."""
    nums = [int(n) for n in re.findall(r"line (\d+)", output)]
    if not nums:
        return ""
    lines, n = code.splitlines(), nums[-1]
    return "\n".join(f"{i:>4}| {lines[i - 1]}" for i in range(max(1, n - 3), min(len(lines), n + 3) + 1))


# How each of the two checker scripts is told to go about it: (with pandas, with the standard library only)
STYLES = (("build ONE DataFrame with a row per input record that counts and the value to total, then groupby "
           "the key columns and sum", "loop over the input rows once and accumulate the figures in dictionaries"),
          ("write plain top-level code, step by step, with no functions and one named DataFrame per step; merge "
           "to look values up, groupby or pivot_table to total", "first load every input file into a list of row "
           "dictionaries, then compute the figures one after the other in plain top-level code, with no functions"))
PANDAS = ("pandas is installed: read the input files with pandas.read_csv and compute every table with pandas "
          "(boolean masks to filter, merge to look values up, groupby(...).sum() to total); never total by "
          "assigning into a dict or by a loop with a counter; assign a column with df['col'] = ... on a .copy(), "
          "never df.loc[:, 'col'] = ... (it keeps the old dtype); to parse a date column that mixes formats use "
          "pd.to_datetime(col, format='mixed', dayfirst=<True when the instructions say day-first>), never "
          "errors='coerce' on the whole column (it turns every value in the other format into NaT); create the "
          "column you merge on (such as the month) on the orders side BEFORE the merge, in the exact format the "
          "lookup file already uses; never rewrite the lookup file's own key values; after a merge filter and total the merged "
          "DataFrame itself, never index one DataFrame with a boolean mask made from another; when rows may be "
          "repeated, df = df.drop_duplicates() first and compute everything from that DataFrame")


def has_pandas(folder: Path, work: Path) -> bool:
    """Whether the checker's python (the sandbox's, or this container's when HARNESS2_SANDBOX=local) has pandas."""
    got, _ = _run_checker("import pandas\nprint('{}')", folder, work, "_probe")
    return got is not None


def parse_expected(got: dict, tables: dict, skeys: dict[str, str]) -> dict:
    """A checker's JSON as {"tables": {id: {key: {column: number}}}, "scalars": {name: number}}."""
    out: dict = {"tables": {}, "scalars": {}}
    for tid, rows in (got.get("tables") or {}).items():
        t = tables.get(tid)
        if t and isinstance(rows, list):
            out["tables"][tid] = {}
            for r in rows:
                if isinstance(r, dict) and all(c in r for c in t["key"]):
                    vals = {c: float(r[c]) for c in t["val"] if isinstance(r.get(c), (int, float))}
                    if vals:
                        out["tables"][tid][norm_key(r[c] for c in t["key"])] = vals
    for k, v in (got.get("scalars") or {}).items():
        if k in skeys and isinstance(v, (int, float)):
            out["scalars"][skeys[k]] = float(v)
    return out


def write_checker(llm: LLM, instructions: str, previews: dict[str, str], folder: Path, report: dict, work: Path,
                  tag: str, style: tuple, temperature: float, pandas: bool) -> dict:
    """LLM writes a script that recomputes the report from the inputs, code runs it. A table is asked for as
    rows with the deliverable's own columns (code joins them on the key columns); the other figures get short
    keys s1..sN. Rounds alternate write / fix / write / fix so a weak model is not stuck patching a script."""
    skeys = {f"s{i}": n for i, n in enumerate(report["scalars"], 1)}
    spec = "\n".join(f"- table {tid}: key columns {t['key']}, value columns {t['val']}"
                     for tid, t in report["tables"].items()) or "(none)"
    context = (f"TASK INSTRUCTIONS\n{instructions}\n\nINPUT FILES (previews; the script reads the full files "
               f"from the current directory)\n{_files_block(previews)}\n\nTABLES TO PRODUCE (rows with exactly "
               f"these column names; key cells as text, value cells as numbers)\n{spec}\n\nSCALARS TO PRODUCE "
               f"(key: what it is)\n" + ("\n".join(f"{k}: {n}" for k, n in skeys.items()) or "(none)"))
    rules = (f"Rules for the script:\n- Python 3, {PANDAS if pandas else 'standard library only'}; read the input "
             f"files by name from the current directory\n- write each instruction you apply as a short comment, "
             f"then its code; {style[0] if pandas else style[1]}\n"
             "- do NOT write any file; at most 100 lines; keep full precision; print with json.dumps(obj, "
             "default=float)\n- the last line printed is ONE JSON "
             'object: {"tables": {"<table>": [{"<column>": value, ...}, ...]}, "scalars": {"s1": number, ...}}\n'
             "- start with the imports (import pandas as pd, import json); every scalar is ONE number (a count, not "
             "a list)\n"
             "- put every multi-line condition inside parentheses, never end a line with & or |; after parsing "
             "dates or numbers, assert that no value became missing\n"
             "- give EVERY row a correct deliverable would contain, not only some of them\n"
             "- use null for a scalar the instructions do not let you compute; never put 0 as a placeholder")
    write = (f"Write a Python script that computes the tables and scalars below from the input files, "
             f"following the task instructions exactly. First fill plan: one short line for EACH rule of the task "
             f"instructions (what the script does for it), then write the code that does all of them.\n"
             f"{rules}\n\n{context}")
    code, output, seen = "", "", []  # seen: the last line of each failure so far
    for attempt in range(llm.retries + 4):
        fixing = attempt % 2 == 1
        snippet = _error_snippet(code, output) if fixing else ""
        prompt = (f"This script was written for the task below and failed. Return the whole corrected script."
                  f"\n{rules}\n\nWHAT WENT WRONG\n{output}\n" + (f"\nTHE LINES IT POINTS AT\n{snippet}\n" if snippet else "")
                  + (f"\nERRORS MET BY EARLIER VERSIONS (do not bring them back)\n" + "\n".join(seen[:-1]) + "\n"
                     if len(seen) > 1 else "")
                  + f"\nSCRIPT\n{code}\n\n{context}") if fixing else write
        code = llm.ask("fix_checker" if fixing else "checker", prompt, obj(plan=arr(string(300)), code=string()),
                       temperature=temperature, check=lambda o: None if o["code"].strip() else "empty code")["code"]
        (work / f"check{tag}.py").write_text(code, encoding="utf-8")
        got, output = _run_checker(code, folder, work, tag)
        if got is not None:
            expected = parse_expected(got, report["tables"], skeys)
            if flat(expected):
                return expected
            output = "the JSON object has no usable table rows or scalars (check the table ids, column names and keys)"
        seen.append(next((ln.strip()[:200] for ln in reversed(output.splitlines()) if ln.strip()), ""))
        print(f"    checker {tag.strip('_')} run {attempt + 1}: failed ({output.splitlines()[0][:120]})", flush=True)
    raise StepFailed(f"checker {tag.strip('_')}: no working script")


def write_checkers(llm: LLM, instructions: str, previews: dict[str, str], folder: Path, report: dict,
                   work: Path) -> tuple[dict, dict]:
    """Two scripts written independently (different style, different sampling); only the figures both compute
    with the same value are the expected ones. When they agree on too few, the evidence is not reliable and the
    improvement stops instead of writing a rule from it."""
    pandas = has_pandas(folder, work)
    a = write_checker(llm, instructions, previews, folder, report, work, "_a", STYLES[0], 0.2, pandas)
    b = write_checker(llm, instructions, previews, folder, report, work, "_b", STYLES[1], 0.3, pandas)
    shutil.copy(work / "check_a.py", work / "check.py")  # the script `locate` compares the agent's code with
    expected, info = agree(a, b)
    info["pandas"] = pandas
    if info["agreed"] < 1 or info["agreed"] < AGREE_MIN * max(info["a"], info["b"]):
        raise StepFailed(f"checker: the two scripts agree on only {info['agreed']} of {max(info['a'], info['b'])} "
                         f"figures, too few to trust; not changing the harness from this evidence")
    return expected, info


def same(x: float, y: float) -> bool:
    return abs(x - y) <= max(TOL, 1e-6 * abs(y))


def flat(e: dict) -> dict:
    """{("t", table, key, column) or ("s", name): number} of an expected structure."""
    f = {("t", tid, key, c): v for tid, rows in e["tables"].items() for key, vals in rows.items()
         for c, v in vals.items()}
    return {**f, **{("s", n): v for n, v in e["scalars"].items()}}


def cell_name(k: tuple) -> str:
    return k[1] if k[0] == "s" else f"{k[1]} | {' '.join(k[2])} | {k[3]}"


def agree(a: dict, b: dict) -> tuple[dict, dict]:
    """The figures both scripts computed with the same value are the expected ones. Each table also keeps the
    rows the scripts gave (`seen`), to tell an extra row from a missing one; only for tables both gave rows for."""
    fa, fb = flat(a), flat(b)
    ok = {k: fa[k] for k in fa.keys() & fb.keys() if same(fa[k], fb[k])}
    out: dict = {"tables": {}, "scalars": {}, "seen": {}}
    for k, v in ok.items():
        if k[0] == "s":
            out["scalars"][k[1]] = v
        else:
            out["tables"].setdefault(k[1], {}).setdefault(k[2], {})[k[3]] = v
    for tid in a["tables"].keys() & b["tables"].keys():
        if a["tables"][tid] and b["tables"][tid]:
            out["seen"][tid] = set(a["tables"][tid]) | set(b["tables"][tid])
    return out, {"agreed": len(ok), "a": len(fa), "b": len(fb),
                 "unverified": sorted(map(cell_name, (fa.keys() | fb.keys()) - ok.keys()))[:30]}


def diff_report(rep: dict, exp: dict) -> list[dict]:
    """Three kinds of difference: `value` (a figure differs), `extra` (a row of the agent's that neither script
    has) and `missing` (something both scripts have that the agent left out). Figures the scripts did not agree
    on are not compared."""
    out = []
    for tid, t in rep["tables"].items():
        want = exp["tables"].get(tid, {})
        for key, vals in t["rows"].items():
            for c, v in vals.items():
                if c in want.get(key, {}) and not same(v, want[key][c]):
                    out.append({"kind": "value", "name": cell_name(("t", tid, key, c)), "reported": v,
                                "recomputed": round(want[key][c], 6)})
            if tid in exp["seen"] and key not in exp["seen"][tid]:
                out.append({"kind": "extra", "name": f"{tid} | {' '.join(key)}", "reported": vals, "recomputed": None})
    for n, want in exp["scalars"].items():
        got = rep["scalars"].get(n)
        if got is None:
            out.append({"kind": "missing", "name": n, "reported": None, "recomputed": round(want, 6)})
        elif not same(got, want):
            out.append({"kind": "value", "name": n, "reported": got, "recomputed": round(want, 6)})
    for tid, rows in exp["tables"].items():
        for key, vals in rows.items():
            if key not in rep["tables"].get(tid, {"rows": {}})["rows"]:
                out.append({"kind": "missing", "name": f"{tid} | {' '.join(key)}", "reported": None, "recomputed": vals})
    return sorted(out, key=lambda d: KINDS.index(d["kind"]))


def diff_text(d: dict) -> str:
    if d["kind"] == "extra":
        return f"the agent reported a row that should not exist: {d['name']} = {d['reported']}"
    if d["kind"] == "missing":
        return f"the agent left out something that should exist: {d['name']} = {d['recomputed']}"
    return f"{d['name']}: the agent reported {d['reported']}, the correct value is {d['recomputed']}"


# ---------- facts: how the agent worked (code) ----------

def _tool_events(run_dir: Path) -> list[dict]:
    """The tool calls of the newest attempt, as `part` dicts, from the rollout's trajectory."""
    traj = Path(run_dir).parents[1] / "model_traj" / Path(run_dir).name
    files = sorted(traj.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
    out = []
    for line in (files[-1].read_text(encoding="utf-8", errors="replace").splitlines() if files else []):
        try:
            part = json.loads(line).get("part") or {} if line.startswith("{") else {}
        except ValueError:
            continue
        if part.get("type") == "tool":
            out.append(part)
    return out


def agent_scripts(run_dir: Path) -> dict[str, str]:
    """{path: content} of the last version of every .py file the agent wrote."""
    out: dict[str, str] = {}
    for p in _tool_events(run_dir):
        i = (p.get("state") or {}).get("input") or {}
        if p.get("tool") == "write" and str(i.get("filePath", "")).endswith(".py") and i.get("content"):
            out[i["filePath"]] = i["content"]
    return out


def process_facts(run_dir: Path, inputs: list[str]) -> dict:
    """What the trajectory shows about HOW the agent worked, read by code, never by the model."""
    tools: dict[str, int] = {}
    read, typed, errors, runs = set(), [], 0, 0
    for p in _tool_events(run_dir):
        tool, s = p.get("tool", "?"), p.get("state") or {}
        i = s.get("input") or {}
        tools[tool] = tools.get(tool, 0) + 1
        errors += s.get("status") == "error"
        path = str(i.get("filePath", ""))
        if tool == "read":
            read.add(Path(path).name)
        cmd = str(i.get("command", ""))
        python = tool == "bash" and re.search(r"\bpython3?\b", cmd)
        runs += bool(python)
        if tool in ("write", "edit") and "/output/" in path and not path.endswith(".py"):
            typed.append(Path(path).name)
        elif tool == "bash" and not python and (m := re.search(r">\s*\S*/output/(\S+)", cmd)):
            typed.append(m.group(1))  # a deliverable typed into a shell heredoc counts as typed by hand
    return {
        "ran_code": runs > 0, "code_runs": runs, "scripts_written": sorted(Path(k).name for k in agent_scripts(run_dir)),
        "outputs_typed_with_write_tool": sorted(set(typed)),
        "inputs_not_read": [n for n in inputs if Path(n).name not in read],
        "tool_errors": errors, "subagent_calls": tools.get("task", 0), "tool_calls": tools,
    }


def facts_text(f: dict) -> str:
    lines = [f"- it ran code {f['code_runs']} time(s)" if f["ran_code"] else "- it ran NO code: no python command was executed",
             f"- scripts it wrote: {', '.join(f['scripts_written']) or 'none'}"]
    if f["outputs_typed_with_write_tool"]:
        lines.append(f"- it typed these deliverables itself (write tool or shell heredoc): {', '.join(f['outputs_typed_with_write_tool'])}")
    if f["inputs_not_read"]:
        lines.append(f"- input files it never opened: {', '.join(f['inputs_not_read'])}")
    if f["tool_errors"]:
        lines.append(f"- {f['tool_errors']} tool call(s) failed")
    if f["subagent_calls"]:
        lines.append(f"- it handed work to a subagent {f['subagent_calls']} time(s)")
    return "\n".join(lines)


# ---------- locate and generalize ----------

def _has_line(code: str, line: str) -> bool:
    want = _norm(line)
    return bool(want) and any(want in _norm(ln) for ln in code.splitlines())


def locate_in_scripts(llm: LLM, agent_code: str, check_code: str, diff: dict) -> dict:
    """ONE task: two scripts disagree on one finding; name the line of the agent's that makes it differ."""
    got = llm.ask("locate", (
        f"Script A was written by an agent; script B is a checked script for the same task. They disagree on "
        f"the finding below: A's result is wrong and B's is right.\n\nFINDING: {diff_text(diff)}\n\nFind the "
        f"ONE line in A that makes it differ from B. Answer with agent_line = that line copied exactly from A, "
        f"checker_line = the matching line copied exactly from B, difference = one sentence on what A does "
        f"wrong.\n\nSCRIPT A\n{_clip(agent_code, 12_000)}\n\nSCRIPT B\n{_clip(check_code, 8_000)}"),
        obj(agent_line=string(400), checker_line=string(400), difference=string(400)),
        check=lambda o: None if _has_line(agent_code, o["agent_line"]) and _has_line(check_code, o["checker_line"])
        else "agent_line must be copied exactly from script A and checker_line from script B")
    return {"quote": got["agent_line"], "mistake": got["difference"], "checker_line": got["checker_line"],
            "from": "scripts"}


def locate_in_trajectory(llm: LLM, instructions: str, traj: str, diff: dict | None) -> dict:
    """ONE task: quote the action that produced the wrong result (or the riskiest one)."""
    if len(traj) > MAX_TRAJ:
        traj = traj[:6000] + "\n... [middle of the trajectory cut] ...\n" + traj[-(MAX_TRAJ - 6000):]
    head = (f"An agent did the task below and got something wrong. Find the ONE action in its trajectory that "
            f"produced it.\n\nFINDING: {diff_text(diff)}" if diff else
            "An agent did the task below and every number it reported is correct. Find the ONE action in its "
            "trajectory most likely to go wrong on a similar task.")
    norm = _norm(traj)
    got = llm.ask("locate", (
        f"{head}\n\nAnswer with quote = the exact text of that action as it appears in the trajectory "
        f"(at most 300 characters), and mistake = one sentence on what that action did wrong.\n\n"
        f"TASK INSTRUCTIONS\n{_clip(instructions)}\n\nTRAJECTORY\n{traj}"),
        obj(quote=string(400), mistake=string(400)),
        check=lambda o: None if _norm(o["quote"])[:80] and _norm(o["quote"])[:80] in norm
        else "the quote is not text from the trajectory; copy it exactly")
    return {**got, "from": "trajectory"}


class Habit(NamedTuple):
    desc: str  # what the habit is
    hint: str  # what a rule against it must spell out
    must: tuple  # words a rule against it must contain (any one)
    topics: tuple  # words that show an existing rule is about it


HABITS = {
    "no_code": Habit("computed or wrote results without running code",
                     "name the method: compute every number in a script that reads the input files, and copy "
                     "numbers into deliverables only from what the script printed",
                     ("script", "code", "python"), ("script", "python", "code")),
    "lookup_key": Habit("matched or looked up records on a wrong or incomplete key",
                        "say to look records up on the full key the instructions define (every field, e.g. both "
                        "parts of a pair), never on a partial key", ("key",), ("key", "look up", "lookup", "match")),
    "filter": Habit("kept or dropped the wrong records",
                    "say to apply every inclusion and exclusion condition from the instructions in code, and "
                    "print how many records each condition kept or removed",
                    ("condition", "filter", "include", "exclude"), ("filter", "exclude", "include", "condition")),
    "aggregation": Habit("grouped, summed or counted wrongly",
                         "say to group by exactly the requested keys, then print each group's count and total "
                         "before writing", ("group", "sum", "total", "count"), ("group", "sum", "total", "count")),
    "rounding": Habit("rounded too early or wrongly",
                      "say to keep full precision and round only the values written out", ("round",),
                      ("round", "precision")),
    "input_missed": Habit("did not read or use an input or a rule from the instructions",
                          "say to read every input file and every rule in the instructions before computing, "
                          "and list them first", ("read", "every", "all"),
                          ("every input", "each input", "all input", "every file", "read every")),
    "output_check": Habit("did not check the written result against the computation",
                          "say to reopen every written file and compare each figure with the script's output",
                          ("compare", "check", "verify", "reopen"), ("reopen", "read back", "compare", "verify", "check")),
    "other": Habit("none of the above", "say exactly which step to do differently and how to check it was done",
                   (), ()),
}


def generalize(llm: LLM, found: dict, how: dict) -> dict:
    """ONE task: name the working habit behind the concrete mistake, with nothing task-specific. Code checks
    the category against the facts (no code run -> no_code)."""
    return llm.ask("generalize", (
        "Below is one concrete mistake an AI agent made on a data task, and facts about how it worked. Name "
        "the GENERAL working habit behind it: the habit that would cause the same kind of mistake on other "
        "tasks.\nAnswer with category = one of the categories, and problem = one sentence about the habit, "
        "with no names, numbers, ids or file names from this task.\n\nCATEGORIES\n"
        + "\n".join(f"- {k}: {h.desc}" for k, h in HABITS.items())
        + f"\n\nMISTAKE: {found['mistake']}\nWHERE: {found['quote']}\n\nHOW THE AGENT WORKED\n{facts_text(how)}"),
        obj(category=enum(HABITS), problem=string(400)),
        check=lambda o: first((not how["ran_code"] and o["category"] != "no_code",
                               "the facts show the agent ran no code, so the category is no_code"),
                              (len(o["problem"]) > 250, "problem is too long: one sentence, at most 250 characters"))
        or llm.general(o["problem"]))


# ---------- the rule file: read it first, change as little as possible ----------

_ITEM = re.compile(r"^(\d+\.|-)\s+(\S.*)$")
_STOP = set("a an the to of and or in on for with from by at is are be it its that this each every any as "
            "not never only then when before after into than so if all".split())


def _words(text: str) -> set[str]:
    return {w.rstrip("s") for w in re.findall(r"[a-z]{3,}", text.lower()) if w not in _STOP}


def similarity(a: str, b: str) -> float:
    """Overlap coefficient of the content words: 1.0 when the shorter text is inside the longer one."""
    wa, wb = _words(a), _words(b)
    return len(wa & wb) / min(len(wa), len(wb)) if wa and wb else 0.0


def _near_pairs(texts: dict[str, str]) -> list[tuple[str, str]]:
    ids = list(texts)
    return [(a, b) for k, a in enumerate(ids) for b in ids[k + 1:] if similarity(texts[a], texts[b]) >= DUP]


class Rules:
    """A rules file read as rule items (`1. text` / `- text`, with their indented lines) plus the rest
    (headings, comments), which is kept as it is. A rule nobody touched keeps its own lines. Every rule has
    an id (r1, r2, ...) that never changes while the file is edited, so several edits can name rules freely."""

    def __init__(self, text: str):
        self.segs: list = []
        self.style, self._n = None, 0
        cur = None
        for line in text.splitlines():
            if m := _ITEM.match(line):
                cur = self._new(m.group(2).strip(), raw=[line])
                self.segs.append(cur)
                self.style = self.style or ("num" if m.group(1)[0].isdigit() else "bullet")
            elif cur is not None and line.strip() and line[:1] in " \t":
                cur["raw"].append(line)
                cur["text"] += " " + line.strip()
            else:
                cur = None
                self.segs.append(line)

    def _new(self, text: str, raw=None, new=None) -> dict:
        self._n += 1
        return {"id": f"r{self._n}", "raw": raw, "text": text, "new": new}

    @property
    def rules(self) -> list[dict]:
        return [s for s in self.segs if isinstance(s, dict)]

    def texts(self) -> dict[str, str]:
        return {r["id"]: r["text"] if r["new"] is None else r["new"] for r in self.rules}

    @property
    def chars(self) -> int:
        return sum(map(len, self.texts().values()))

    def listing(self, ids: list[str] | None = None) -> str:
        return "\n".join(f"{i}: {t}" for i, t in self.texts().items() if ids is None or i in ids) or "(none yet)"

    def candidates(self, category: str) -> list[str]:
        topics = HABITS[category].topics
        return [i for i, t in self.texts().items() if any(w in t.lower() for w in topics)]

    def _rule(self, rid: str) -> dict:
        return next(r for r in self.rules if r["id"] == rid)

    def rewrite(self, rid: str, text: str) -> None:
        self._rule(rid)["new"] = text

    def delete(self, rid: str) -> None:
        self.segs.remove(self._rule(rid))

    def merge(self, ids: list[str], text: str) -> None:
        """The first rule becomes `text`, the others go (one id = a rewrite)."""
        head, *rest = ids
        self.rewrite(head, text)
        for rid in rest:
            self.delete(rid)

    def add(self, text: str) -> None:
        last = [i for i, s in enumerate(self.segs) if isinstance(s, dict)]
        self.segs.insert(last[-1] + 1 if last else len(self.segs), self._new(text, new=text))

    def render(self) -> str:
        out, n = [], 0
        for s in self.segs:
            if not isinstance(s, dict):
                out.append(s)
                continue
            n += 1
            lead = f"{n}. " if self.style == "num" else "- "
            if s["new"] is None:
                out.append(lead + _ITEM.match(s["raw"][0]).group(2))
                out.extend(s["raw"][1:])
            else:
                out.extend(textwrap.fill(s["new"], 100, initial_indent=lead,
                                         subsequent_indent=" " * len(lead)).splitlines())
        return "\n".join(out).rstrip("\n") + "\n"


def rule_problem(llm: LLM, r: str, h: Habit | None = None) -> str | None:
    """What is wrong with the text of a rule (new, rewritten or merged), or None."""
    if not r:
        return "empty rule"
    if len(r) > MAX_RULE or r.count("\n") > 1:
        return f"too long: at most two lines and {MAX_RULE} characters"
    if problem := llm.general(r):
        return problem
    if h and h.must and not any(w in r.lower() for w in h.must):
        return f"too vague: the rule must {h.hint}"
    return None


def match_rules(llm: LLM, habit: dict, rules: Rules, cand: list[str]) -> list[str]:
    """ONE task: which existing rules already tell the agent not to do this."""
    return llm.ask("match", (
        f"HABIT ({habit['category']}): {habit['problem']}\n\nRULES\n{rules.listing(cand)}\n\n"
        f"Which of these rules already tell an AI agent not to do this habit? Answer with their ids, or an "
        f"empty list if none of them does."), obj(covering=arr(enum(cand), 3, uniqueItems=True)))["covering"]


def edit_rule(llm: LLM, dim: str, rules: Rules, habit: dict, covering: list[str], how: dict) -> dict:
    """ONE task: the text of ONE rule. A rule that already covers the habit is rewritten (the agent had it and
    still did this); several are merged into one that is shorter than they were together; only when none
    covers it is a new rule written, and one that repeats an existing rule becomes a rewrite of it."""
    h, texts = HABITS[habit["category"]], rules.texts()
    action = "add" if not covering else "rewrite" if len(covering) == 1 else "merge"
    kind = ("how the agent must work: what to compute in code, what to check, in which order" if dim == "INV"
            else "what an expert in this kind of work always checks or applies")
    line = f"HABIT ({habit['category']}: {h.desc}): {habit['problem']}"
    dup: dict = {}

    def check(o):
        r = o["rule"].strip()
        if problem := rule_problem(llm, r, h):
            return problem
        if action == "add":
            for i, t in texts.items():
                if similarity(r, t) >= DUP:
                    dup.setdefault("id", i)
                    return f"it repeats rule {i} ({t[:80]}); do not add what a rule already says"
        return first((_norm(r) in {_norm(texts[i]) for i in covering}, "the rule is unchanged"),
                     (action == "merge" and len(r) >= sum(len(texts[i]) for i in covering),
                      "the merged rule must be shorter than those rules together"))

    if action == "add":
        prompt = (f"Write ONE rule for an AI agent's standing instructions that stops the working habit below, "
                  f"on any task. The rule is about {kind}.\nThe rule must {h.hint}.\nConstraints: one or two "
                  f"imperative sentences, at most {MAX_RULE} characters. None of the existing rules covers this "
                  f"habit.\n\n{line}\n\nEXISTING RULES\n{_clip(rules.listing())}")
    elif action == "rewrite":
        prompt = (f"An AI agent's harness already has the rule below about the working habit below, and the "
                  f"agent STILL did it:\n{facts_text(how)}\nRewrite that rule so the agent cannot misread it. "
                  f"It must {h.hint}. Keep every requirement it already has, add nothing on other topics, at "
                  f"most {MAX_RULE} characters, one or two imperative sentences.\n\n{line}\n\n"
                  f"RULE\n{rules.listing(covering)}")
    else:
        prompt = (f"These rules of an AI agent's harness all address the working habit below, and the agent "
                  f"STILL did it:\n{facts_text(how)}\nWrite ONE rule that replaces all of them: it must "
                  f"{h.hint}, keep every requirement they have, drop the repetition, at most {MAX_RULE} "
                  f"characters, shorter than they are together.\n\n{line}\n\nRULES\n{rules.listing(covering)}")
    try:
        got = llm.ask("edit_" + action, prompt, obj(rule=string(400)), check=check)
    except StepFailed:
        if action == "add" and dup:  # the model keeps restating a rule that is there: improve that rule
            return edit_rule(llm, dim, rules, habit, [dup["id"]], how)
        raise
    return {"action": action, "ids": covering, "before": [texts[i] for i in covering],
            "text": re.sub(r"^\s*(\d+[.)]|[-*])\s*", "", got["rule"].strip())}


def apply_change(rules: Rules, change: dict) -> None:
    rules.add(change["text"]) if change["action"] == "add" else rules.merge(change["ids"], change["text"])


def review_rules(llm: LLM, rules: Rules) -> dict:
    """ONE task: shorten the list. Rules saying the same thing become one shorter rule; a rule another one
    already contains is deleted. Code checks every op (shorter, covered, no near-duplicate left). This is
    also the merge of AGG: pooled rules go through exactly this."""
    texts = rules.texts()
    ids = list(texts)
    if len(ids) < 2:
        return {"merge": [], "delete": []}
    pairs = _near_pairs(texts)

    def check(o):
        used = [i for m in o["merge"] for i in m["ids"]] + o["delete"]
        if len(used) != len(set(used)):
            return "a rule id appears in more than one operation"
        for m in o["merge"]:
            t, src = m["text"].strip(), ", ".join(m["ids"])
            if problem := rule_problem(llm, t):
                return f"merge of {src}: {problem}"
            if len(t) >= sum(len(texts[i]) for i in m["ids"]):
                return f"merge of {src} is not shorter than those rules together"
        gone = set(o["delete"]) | {i for m in o["merge"] for i in m["ids"][1:]}
        for d in o["delete"]:
            if not any(similarity(texts[d], texts[i]) >= 0.4 for i in ids if i not in gone):
                return f"{d} is not covered by another rule, do not delete it"
        for a, b in pairs:
            if not (any(a in m["ids"] and b in m["ids"] for m in o["merge"]) or a in o["delete"] or b in o["delete"]):
                return f"{a} and {b} say almost the same thing: merge them into one rule or delete one"

    return llm.ask("review", (
        f"These are an AI agent's standing rules.\n\nRULES\n{rules.listing()}\n\n"
        + (f"These pairs overlap a lot: {', '.join(f'{a}+{b}' for a, b in pairs)}.\n" if pairs else "")
        + f"Make the list shorter without losing any requirement. Rules that say the same thing: merge them "
        f"into ONE shorter rule (merge: the ids and the new text, at most {MAX_RULE} characters). A rule that "
        f"another rule already contains: delete it (delete: its id). Leave different rules alone. If every "
        f"rule is needed, answer with two empty lists."),
        obj(merge=arr(obj(ids=arr(enum(ids), 4, minItems=2, uniqueItems=True), text=string(400)), 4),
            delete=arr(enum(ids), 4, uniqueItems=True)), check=check)


def refine(llm: LLM, text: str) -> tuple[str, dict]:
    """Review a rule file (up to 3 rounds, until no near-duplicates are left): (the shorter text, what was
    done). When the model's answer is rejected for good, near-duplicates are resolved by code: the longer
    wording stays, at the earlier place. The result is never longer than the input."""
    rules = Rules(text)
    before = (len(rules.rules), rules.chars)
    done, by, llm_ok = {"merge": [], "delete": []}, "skipped", True
    for _ in range(3):
        if len(rules.rules) < 2:
            break
        texts = rules.texts()
        pairs = _near_pairs(texts)
        try:
            if not llm_ok:
                raise StepFailed("review: not asked again after a rejected answer")
            ops, how = review_rules(llm, rules), "llm"
        except StepFailed:
            llm_ok = False
            ops, how = {"merge": [{"ids": list(p), "text": max((texts[i] for i in p), key=len)} for p in pairs[:1]],
                        "delete": []}, "code"
        if not (ops["merge"] or ops["delete"]):
            by = how if by == "skipped" else by  # the model was asked and found nothing to shorten
            break
        for m in ops["merge"]:
            rules.merge(m["ids"], m["text"].strip())
        for rid in ops["delete"]:
            rules.delete(rid)
        done["merge"] += ops["merge"]
        done["delete"] += ops["delete"]
        by = how if by in ("skipped", how) else "llm+code"
        if not _near_pairs(rules.texts()):
            break
    after = (len(rules.rules), rules.chars)
    if after[0] > before[0] or after[1] > before[1]:
        raise StepFailed(f"review made the rules longer: {before} -> {after}")
    return (rules.render() if done["merge"] or done["delete"] else text), {
        **done, "by": by, "rules": f"{before[0]}->{after[0]}", "chars": f"{before[1]}->{after[1]}"}


def pool_rules(base_text: str, cand_texts: list[str]) -> Rules:
    """The base rules plus every rule of the candidates that the base does not already have (exact text)."""
    rules = Rules(base_text)
    have = {_norm(t) for t in rules.texts().values()}
    for ct in cand_texts:
        for t in Rules(ct).texts().values():
            if _norm(t) not in have:
                rules.add(t)
                have.add(_norm(t))
    return rules


# ---------- the run ----------

class Run:
    """One improvement. Its steps are methods, run in the order PIPELINES[dim] lists; each returns the
    structured info it logs and keeps its output on the run for the steps after it."""

    def __init__(self, *, jb, task, dim, base_tree, rollout, candidates, work, model, think, retries,
                 checker_model=""):
        self.jb, self.task, self.dim, self.base_tree = jb, task, dim, base_tree
        self.rollout, self.candidates, self.work = rollout, candidates, work
        self.surface = jb.substrate.surface()
        self.instructions, self.previews, self.folder = task_inputs(jb, task)
        self.deliverables = jb.deliverable_files(rollout.run_dir)
        self.llm = LLM(model, work / "steps", think=think, retries=retries,
                       names=[*self.previews, *self.deliverables])
        self.checker_model = config.provider_model(checker_model) if checker_model else ""  # "" = the improver model
        self.tree = dict(base_tree)  # the harness being built
        self.files: dict[str, Rules] = {}  # rule files to review, by file name
        self.before: dict[str, Rules] = {}  # the same files as the base harness has them
        self.allow = 1  # rules the change may add per file
        self.res = {"dim": dim, "task": task, "model": self.llm.model, "rollout": rollout.label, "steps": []}

    def run(self) -> dict:
        t0 = time.time()
        for name in PIPELINES[self.dim]:
            info = getattr(self, name)() or {}
            self.res["steps"].append({"step": name, "seconds": round(time.time() - t0), **info})
            print(f"  [{round(time.time() - t0):>4}s] {name}: {json.dumps(info, ensure_ascii=False)[:200]}", flush=True)
        return self.res

    # -- INV, DOM and AGG: measure the rollout --

    def figures(self):
        self.reported = read_report(self.llm, self.deliverables)
        if not report_size(self.reported):
            raise StepFailed("figures: the rollout reports no number to check")
        return {"tables": {t: len(v["rows"]) for t, v in self.reported["tables"].items()},
                "scalars": len(self.reported["scalars"])}

    def checker(self):
        main = self.llm.model
        self.llm.model = self.checker_model or main  # only the two checker scripts use the checker model
        try:
            self.expected, info = write_checkers(self.llm, self.instructions, self.previews, self.folder,
                                                 self.reported, self.work)
        finally:
            self.llm.model = main
        info["model"] = self.checker_model or main
        self.res["checker"] = info
        return {k: info[k] for k in ("agreed", "a", "b", "pandas")}

    def compare(self):
        self.diffs = self.res["diffs"] = diff_report(self.reported, self.expected)
        return {"diffs": len(self.diffs), **{k: sum(d["kind"] == k for d in self.diffs) for k in KINDS},
                "first": self.diffs[0] if self.diffs else None}

    # -- INV and DOM: one change from the first DIFF --

    def facts(self):
        self.how = self.res["facts"] = process_facts(self.rollout.run_dir, list(self.previews))
        return {k: v for k, v in self.how.items() if k != "tool_calls"}

    def locate(self):
        diff = self.diffs[0] if self.diffs else None
        scripts = agent_scripts(self.rollout.run_dir)
        if diff and scripts:  # compare code with code: the easiest form of "find the mistake"
            code = "\n\n".join(f"# ---- {p}\n{c}" for p, c in scripts.items())
            self.found = locate_in_scripts(self.llm, code, (self.work / "check.py").read_text(encoding="utf-8"), diff)
        else:
            self.found = locate_in_trajectory(self.llm, self.instructions,
                                              self.jb.trajectory(self.rollout.run_dir, obs_trunc=400), diff)
        self.res["locate"] = self.found
        return self.found

    def generalize(self):
        self.habit = self.res["general"] = generalize(self.llm, self.found, self.how)
        return self.habit

    def read(self):
        target = self.res["target"] = TARGET[self.dim]
        self.rules = Rules(self.base_tree.get(target, ""))  # the current harness, read before changing it
        self.files, self.before = {target: self.rules}, {target: Rules(self.base_tree.get(target, ""))}
        return {"file": target, "rules": len(self.rules.rules), "chars": self.rules.chars,
                "rule_list": self.rules.texts()}

    def match(self):
        cand = self.rules.candidates(self.habit["category"])
        self.covering = match_rules(self.llm, self.habit, self.rules, cand) if cand else []
        return {"candidates": cand, "covering": self.covering}

    def edit(self):
        change = self.res["edit"] = edit_rule(self.llm, self.dim, self.rules, self.habit, self.covering, self.how)
        apply_change(self.rules, change)
        return change

    # -- AGG: adopt what the candidates did better, merged with base --

    def measure(self):
        names = list(self.expected["scalars"])
        self.scores = {label: len(diff_report(read_report(self.llm, self.jb.deliverable_files(view.run_dir), names),
                                              self.expected))
                       for label, _, view in self.candidates}
        return {"base": len(self.diffs), **self.scores}

    def choose(self):
        better = [c for c in self.candidates if self.scores[c[0]] < len(self.diffs)]
        self.chosen = better or [c for c in self.candidates if self.scores[c[0]] == len(self.diffs)]
        self.allow = max(1, len(self.chosen))
        self.res.update(scores=self.scores, adopted=[c[0] for c in self.chosen])
        return {"chosen": [c[0] for c in self.chosen],
                "why": "fewer DIFFs than base" if better else "tie with base: merged with base"}

    def pool(self):
        trees = [harness_tree.read_tree(self.surface, c[1]) for c in self.chosen]
        taken = {}
        for f in TARGET.values():
            base_text = self.base_tree.get(f, "")
            rules = pool_rules(base_text, [t.get(f, "") for t in trees])
            if taken.setdefault(f, len(rules.rules) - len(Rules(base_text).rules)):
                self.files[f], self.before[f] = rules, Rules(base_text)
        return {"new_rules": taken}

    # -- all: shorten, then check --

    def review(self):
        reviews = self.res["reviews"] = {}
        for f, rules in self.files.items():
            self.tree[f], reviews[f] = refine(self.llm, rules.render())
        return reviews

    def apply(self):
        for f, before in self.before.items():
            after = Rules(self.tree[f])
            if len(after.rules) > len(before.rules) + self.allow or after.chars > before.chars + MAX_RULE * self.allow:
                raise StepFailed(f"minimal change violated in {f}: {len(before.rules)} rules/{before.chars} chars "
                                 f"-> {len(after.rules)}/{after.chars}")
        _, err = recursion._accept_tree(self.surface, self.jb, self.tree, self.base_tree, f"improve-{self.dim.lower()}")
        if err:
            raise StepFailed(f"validate: {err}")
        self.res["changed"] = sorted(k for k in set(self.tree) | set(self.base_tree) if self.tree.get(k) != self.base_tree.get(k))
        self.res["rules"] = {f: {"before": b.texts(), "after": Rules(self.tree[f]).texts()} for f, b in self.before.items()}
        self.res["tree"] = self.tree
        return {"changed": self.res["changed"]}


MEASURE = ("figures", "checker", "compare")
PIPELINES = {
    "INV": MEASURE + ("facts", "locate", "generalize", "read", "match", "edit", "review", "apply"),
    "DOM": MEASURE + ("facts", "locate", "generalize", "read", "match", "edit", "review", "apply"),
    "AGG": MEASURE + ("measure", "choose", "pool", "review", "apply"),
}


def run(**kw) -> dict:
    """Improve once. INV / DOM: change the rule file by at most one rule from the first DIFF. AGG: merge the
    base with the candidates that did better (or tied). Returns the structured result; `result["tree"]` is
    the new harness."""
    return Run(**kw).run()
