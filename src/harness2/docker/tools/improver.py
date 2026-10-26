"""Improver for weak models: the program runs the steps, each LLM call does one small task.

Every call is a plain chat completion with a JSON schema (structured output); its JSON is the only
thing handed to the next step. Code, not the model, reads the evidence, runs the checker, compares
numbers, applies the rule and validates the harness. Rubrics are never read.

The steps are fixed and always run in this order:

    figures     (code: CSV and Markdown tables; LLM: numbers in prose)  what the rollout reports
    checker     (LLM writes, code runs in the sandbox, LLM fixes on error)  the same figures from the inputs
    compare     (code)  DIFF list
    facts       (code)  how the agent worked: code run or not, scripts, files read, how outputs were written
    locate      (LLM, quoted lines checked by code)  the concrete mistake behind the first DIFF: the line of
                the agent's script that differs from the checker, or the trajectory action if it wrote no script
    generalize  (LLM, checked against the facts)  the working habit behind it, as a category + one sentence
                with nothing task-specific
    read        (code)  the rule file of the harness (guardrails.md for INV, LongTermMEMORY.md for DOM), split into
                numbered rules; every later step sees these rules
    match       (LLM, only when a rule mentions the habit's topic)  which existing rules already cover the habit
    edit        (LLM, checked by code)  the least change: rewrite the covering rule (the agent had it and still did
                this) / merge several covering rules into one shorter rule / add ONE rule only when none covers it
                and it repeats none
    review      (LLM, checked by code)  after every change: merge rules that say the same thing, delete rules
                another rule contains; the result may never be longer than before the review
    apply       (code)  validate the tree; at most one more rule than the harness had
AGG stops after compare: it takes the rules the candidates added or rewrote (similar to an existing rule ->
replaces it instead of adding), from the candidates whose rollouts have fewer DIFFs, and runs `review`.
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

import eval_harness as E
from harness2 import config, sandbox
from harness2.core import recursion
from harness2.core import tree as harness_tree

TARGET = {"INV": "guardrails.md", "DOM": "LongTermMEMORY.md"}
SYSTEM = "You do exactly one small task. Answer only with JSON that matches the schema."
MAX_TRAJ = 24_000  # chars of trajectory given to the cause step (32k-token slots)
MAX_FILE = 6_000  # chars per input or deliverable file in a prompt
TOL = 0.011


class StepFailed(RuntimeError):
    pass


# ---------- LLM: one structured call per task ----------

class LLM:
    """Chat completions on the OpenAI-compatible provider, one JSON-schema answer per call."""

    def __init__(self, model: str, log_dir: Path, *, think: bool = True):
        self.model = config.provider_model(model)
        if not (self.model and config.PROVIDER_BASE_URL):
            raise SystemExit(f"improver model {model!r} must be {config.PROVIDER_ID}/<id> on HARNESS2_PROVIDER_BASE_URL")
        self.log_dir, self.think, self.n = log_dir, think, 0
        log_dir.mkdir(parents=True, exist_ok=True)

    def _post(self, messages: list[dict], name: str, schema: dict) -> dict:
        body = {
            "model": self.model, "messages": messages, "temperature": 0.2,
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

    def ask(self, step: str, prompt: str, schema: dict, *, check=None, retries: int = 2) -> dict:
        """One task: ask, parse, `check(obj)` -> problem or None; a rejected answer is re-asked
        with the reason, up to `retries` times. Every call is logged to <log_dir>/NN_step.json."""
        messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}]
        problem = ""
        for attempt in range(retries + 1):
            self.n += 1
            start, raw, obj, reply = time.time(), "", None, {}
            try:
                reply = self._post(messages, step, schema)
                raw = reply["choices"][0]["message"].get("content") or ""
                obj = json.loads(raw)
                problem = check(obj) if check else None
            except (urllib.error.URLError, TimeoutError, KeyError, IndexError) as e:
                problem = f"request failed: {e}"
            except ValueError as e:
                problem = f"the answer is not valid JSON ({e})"
            (self.log_dir / f"{self.n:02d}_{step}.json").write_text(json.dumps({
                "step": step, "attempt": attempt + 1, "seconds": round(time.time() - start, 1),
                "messages": messages, "answer": raw, "parsed": obj, "rejected": problem,
                "usage": reply.get("usage"),
            }, indent=1, ensure_ascii=False), encoding="utf-8")
            if not problem:
                return obj
            print(f"    {step}: rejected ({str(problem)[:160]})", flush=True)
            if raw:
                messages = messages[:2] + [{"role": "assistant", "content": raw},
                                           {"role": "user", "content": f"Rejected: {problem}\nAnswer again."}]
        raise StepFailed(f"{step}: {problem}")


# ---------- evidence (code) ----------

def _clip(text: str, n: int = MAX_FILE) -> str:
    return text if len(text) <= n else text[:n] + f"\n... [{len(text) - n} more characters]"


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


def table_figures(name: str, rows: list[list[str]]) -> dict[str, float]:
    """Every numeric cell of a table (first row = header), named `file | key cells | column`."""
    if len(rows) < 2:
        return {}
    head, out = rows[0], {}
    for row in rows[1:]:
        key = " ".join(c.strip().strip("*`") for c in row if c.strip() and _num(c) is None)
        for col, cell in zip(head, row):
            v = _num(cell)
            if v is not None:
                out[f"{name} | {key} | {col.strip().strip('*`')}"] = v
    return out


def csv_figures(name: str, text: str) -> dict[str, float]:
    return table_figures(name, list(csv.reader(io.StringIO(text))))


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


def figures(llm: LLM, files: dict[str, str], names: list[str] | None = None) -> dict[str, float | None]:
    """What a rollout reports. CSV and Markdown table cells are read by code; numbers in the
    remaining prose by one LLM call that either names them (names=None) or fills in given names."""
    out: dict[str, float | None] = {}
    other = {}
    for n, t in files.items():
        if n.lower().endswith(".csv"):
            out.update(csv_figures(n, t))
            continue
        tables, prose = md_tables(t)
        for rows in tables:
            out.update(table_figures(n, rows))
        if re.search(r"\d", "\n".join(ln for ln in prose.splitlines() if not ln.lstrip().startswith("#"))):
            other[n] = prose
    if names is not None:
        out = {k: out.get(k) for k in names if " | " in k}
        names = [k for k in names if " | " not in k]
        if not (names and other):
            return {**out, **{k: None for k in names or []}}
        got = llm.ask("figures", (
            "Below are report files. For each name in the list, give the number the files report for "
            "it, or null if they do not report it.\n\nNAMES\n" + "\n".join(f"- {k}" for k in names)
            + "\n\n" + _files_block(other)),
            {"type": "object", "additionalProperties": False, "required": ["values"], "properties": {
                "values": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                           "required": ["name", "value"], "properties": {
                               "name": {"type": "string", "enum": names},
                               "value": {"type": ["number", "null"]}}}}}})
        found = {v["name"]: v["value"] for v in got["values"]}
        return {**out, **{k: found.get(k) for k in names}}
    if other:
        got = llm.ask("figures", (
            "Below are report files an agent wrote. List every number they report as a result "
            "(totals, counts, amounts, rates). Give each a short unique name that says what it is, "
            "starting with the file name, e.g. 'summary.md: total revenue USD'. Skip dates, years, ids "
            "and list numbering.\n\n" + _files_block(other)),
            {"type": "object", "additionalProperties": False, "required": ["figures"], "properties": {
                "figures": {"type": "array", "maxItems": 60, "items": {
                    "type": "object", "additionalProperties": False, "required": ["name", "value"],
                    "properties": {"name": {"type": "string"}, "value": {"type": "number"}}}}}},
            check=lambda o: None if o["figures"] or out else "no figure listed")
        for f in got["figures"]:
            out.setdefault(f["name"].replace(" | ", " / "), f["value"])
    return out


# ---------- checker: the same figures recomputed from the inputs ----------

def _run_checker(code: str, folder: Path, work: Path) -> tuple[dict | None, str]:
    """Run code on a copy of the task inputs (in the sandbox when HARNESS2_SANDBOX=docker);
    (the printed JSON object, the output)."""
    ws = Path(tempfile.mkdtemp(prefix="h2check-"))
    try:
        for p in folder.rglob("*"):
            if p.is_file():
                (ws / p.relative_to(folder)).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, ws / p.relative_to(folder))
        (ws / "check.py").write_text(code, encoding="utf-8")
        log = work / "check_output.txt"
        with log.open("w", encoding="utf-8") as out:
            rc = sandbox.run(["python3", "check.py"], workspace=ws, cwd=ws,
                             env={"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(ws)},
                             stdout=out, stderr=out, timeout=120)
        text = log.read_text(encoding="utf-8", errors="replace")
    finally:
        shutil.rmtree(ws, ignore_errors=True)
    if rc != 0:
        return None, f"exit {rc}\n{text[-2500:]}"
    for line in reversed(text.strip().splitlines()):
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict):
            return obj, text
    return None, f"no JSON object line in the output\n{text[-2500:]}"


def _error_snippet(code: str, output: str) -> str:
    """The lines around the last `line N` the traceback names, numbered, to point the fix at them."""
    nums = [int(n) for n in re.findall(r"line (\d+)", output)]
    if not nums:
        return ""
    lines, n = code.splitlines(), nums[-1]
    return "\n".join(f"{i:>4}| {lines[i - 1]}" for i in range(max(1, n - 3), min(len(lines), n + 3) + 1))


def checker(llm: LLM, instructions: str, previews: dict[str, str], folder: Path, names: list[str],
            work: Path, *, retries: int) -> dict[str, float]:
    """LLM writes the script, code runs it. Figures get short keys f1..fN (code maps them back).
    Rounds alternate write / fix / write / fix so a weak model is not stuck patching a broken
    script. Returns {figure: recomputed value}."""
    keys = {f"f{i}": n for i, n in enumerate(names, 1)}
    context = (f"TASK INSTRUCTIONS\n{instructions}\n\nINPUT FILES (previews; the script reads the full "
               f"files from the current directory)\n{_files_block(previews)}\n\nFIGURES (key: what it is)\n"
               + "\n".join(f"{k}: {n}" for k, n in keys.items()))
    rules = ("Rules for the script:\n- Python 3, standard library only; read the input files by name from "
             "the current directory\n- write each instruction you apply as a short comment, then its code\n"
             "- do NOT write any file; at most 80 lines\n- keep full precision; the last line printed is ONE "
             'JSON object with every key below, e.g. {"f1": 123.45, "f2": 6, "f3": null}\n'
             "- use null for a figure the instructions do not let you compute; never put 0 as a placeholder")
    schema = {"type": "object", "additionalProperties": False, "required": ["code"],
              "properties": {"code": {"type": "string"}}}
    write = (f"Write a Python script that computes the figures below from the input files, following "
             f"the task instructions exactly.\n{rules}\n\n{context}")
    code, output = "", ""
    for attempt in range(retries + 2):
        fixing = attempt % 2 == 1
        prompt = write if not fixing else (
            f"This script was written for the task below and failed. Return the whole corrected script."
            f"\n{rules}\n\nWHAT WENT WRONG\n{output}\n"
            + (f"\nTHE LINES IT POINTS AT\n{_error_snippet(code, output)}\n" if _error_snippet(code, output) else "")
            + f"\nSCRIPT\n{code}\n\n{context}")
        code = llm.ask("fix_checker" if fixing else "checker", prompt, schema,
                       check=lambda o: None if o["code"].strip() else "empty code")["code"]
        (work / "check.py").write_text(code, encoding="utf-8")
        got, output = _run_checker(code, folder, work)
        if got is not None:  # null = not computable from the instructions: left out of the comparison
            vals = {keys[k]: float(v) for k, v in got.items() if k in keys and isinstance(v, (int, float))}
            if vals and len(vals) + sum(got.get(k, 0) is None for k in keys) >= max(1, len(names) // 2):
                return vals
            output = ("the JSON object lacks these keys or their values are not numbers: "
                      + ", ".join(k for k, n in keys.items() if n not in vals))
        print(f"    checker run {attempt + 1}: failed ({output.splitlines()[0][:120]})", flush=True)
    raise StepFailed("checker: no working script")


def compare(reported: dict, expected: dict) -> list[dict]:
    """Figures the checker left out (null) are not compared."""
    diffs = []  # in the order the deliverables report them, so "the first DIFF" is stable
    for n in [k for k in reported if k in expected] + [k for k in expected if k not in reported]:
        want, got = expected[n], reported.get(n)
        if got is None or abs(got - want) > max(TOL, 1e-6 * abs(want)):
            diffs.append({"name": n, "reported": got, "recomputed": round(want, 6)})
    return diffs


# ---------- facts: how the agent worked (code) ----------

def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def _tool_events(run_dir: Path) -> list[dict]:
    """The tool calls of the newest attempt, as `part` dicts, from the rollout's trajectory."""
    traj = Path(run_dir).parents[1] / "model_traj" / Path(run_dir).name
    files = sorted(traj.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
    out = []
    for line in (files[-1].read_text(encoding="utf-8", errors="replace").splitlines() if files else []):
        if not line.startswith("{"):
            continue
        try:
            p = json.loads(line).get("part") or {}
        except ValueError:
            continue
        if p.get("type") == "tool":
            out.append(p)
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
        if tool in ("write", "edit") and "/output/" in path and not path.endswith(".py"):
            typed.append(Path(path).name)
        if tool == "bash" and re.search(r"\bpython3?\b", str(i.get("command", ""))):
            runs += 1
    scripts = sorted(Path(k).name for k in agent_scripts(run_dir))
    return {
        "ran_code": runs > 0, "code_runs": runs, "scripts_written": scripts,
        "outputs_typed_with_write_tool": sorted(set(typed)),
        "inputs_not_read": [n for n in inputs if Path(n).name not in read],
        "tool_errors": errors, "subagent_calls": tools.get("task", 0), "tool_calls": tools,
    }


def facts_text(f: dict) -> str:
    lines = [f"- it ran code {f['code_runs']} time(s)" if f["ran_code"]
             else "- it ran NO code: no python command was executed",
             f"- scripts it wrote: {', '.join(f['scripts_written']) or 'none'}"]
    if f["outputs_typed_with_write_tool"]:
        lines.append(f"- it typed these deliverables itself with the write tool: {', '.join(f['outputs_typed_with_write_tool'])}")
    if f["inputs_not_read"]:
        lines.append(f"- input files it never opened: {', '.join(f['inputs_not_read'])}")
    if f["tool_errors"]:
        lines.append(f"- {f['tool_errors']} tool call(s) failed")
    if f["subagent_calls"]:
        lines.append(f"- it handed work to a subagent {f['subagent_calls']} time(s)")
    return "\n".join(lines)


# ---------- locate, generalize, rule ----------


def _has_line(code: str, line: str) -> bool:
    want = _norm(line)
    return bool(want) and any(want in _norm(ln) for ln in code.splitlines())


def locate_in_scripts(llm: LLM, agent_code: str, check_code: str, diff: dict, *, retries: int) -> dict:
    """ONE task: two scripts give different values for one figure; name the line that differs."""
    got = llm.ask("locate", (
        f"Script A was written by an agent; script B is a checked script for the same task. For the figure "
        f"below, A gives a wrong value and B gives the correct one.\n\nFIGURE: {diff['name']}: A gives "
        f"{diff['reported']}, B gives {diff['recomputed']}\n\nFind the ONE line in A that computes it "
        f"differently from B. Answer with agent_line = that line copied exactly from A, checker_line = the "
        f"matching line copied exactly from B, difference = one sentence on what A does wrong.\n\n"
        f"SCRIPT A\n{_clip(agent_code, 12_000)}\n\nSCRIPT B\n{_clip(check_code, 8_000)}"),
        {"type": "object", "additionalProperties": False, "required": ["agent_line", "checker_line", "difference"],
         "properties": {"agent_line": {"type": "string", "maxLength": 400},
                        "checker_line": {"type": "string", "maxLength": 400},
                        "difference": {"type": "string", "maxLength": 400}}},
        check=lambda o: (None if _has_line(agent_code, o["agent_line"]) and _has_line(check_code, o["checker_line"])
                         else "agent_line must be copied exactly from script A and checker_line from script B"),
        retries=retries)
    return {"quote": got["agent_line"], "mistake": got["difference"], "checker_line": got["checker_line"],
            "from": "scripts"}


def locate_in_trajectory(llm: LLM, instructions: str, traj: str, diff: dict | None, *, retries: int) -> dict:
    """ONE task: quote the action that produced the wrong number (or the riskiest one)."""
    if len(traj) > MAX_TRAJ:
        traj = traj[:6000] + "\n... [middle of the trajectory cut] ...\n" + traj[-(MAX_TRAJ - 6000):]
    if diff:
        head = (f"An agent did the task below and reported a wrong number. Find the ONE action in its "
                f"trajectory that produced it.\n\nWRONG NUMBER: {diff['name']}: reported {diff['reported']}, "
                f"correct {diff['recomputed']}")
    else:
        head = ("An agent did the task below and every number it reported is correct. Find the ONE action "
                "in its trajectory most likely to go wrong on a similar task.")
    norm = _norm(traj)
    got = llm.ask("locate", (
        f"{head}\n\nAnswer with quote = the exact text of that action as it appears in the trajectory "
        f"(at most 300 characters), and mistake = one sentence on what that action did wrong.\n\n"
        f"TASK INSTRUCTIONS\n{_clip(instructions)}\n\nTRAJECTORY\n{traj}"),
        {"type": "object", "additionalProperties": False, "required": ["quote", "mistake"], "properties": {
            "quote": {"type": "string", "maxLength": 400}, "mistake": {"type": "string", "maxLength": 400}}},
        check=lambda o: None if _norm(o["quote"])[:80] and _norm(o["quote"])[:80] in norm
        else "the quote is not text from the trajectory; copy it exactly", retries=retries)
    return {**got, "from": "trajectory"}


CATEGORIES = {
    "no_code": "computed or wrote results without running code",
    "lookup_key": "matched or looked up records on a wrong or incomplete key",
    "filter": "kept or dropped the wrong records",
    "aggregation": "grouped, summed or counted wrongly",
    "rounding": "rounded too early or wrongly",
    "input_missed": "did not read or use an input or a rule from the instructions",
    "output_check": "did not check the written result against the computation",
    "other": "none of the above",
}


# What a rule against each habit must spell out, and words code requires in it (any one of them).
RULE_HINT = {
    "no_code": ("name the method: compute every number in a script that reads the input files, and copy "
                "numbers into deliverables only from what the script printed", ("script", "code", "python")),
    "lookup_key": ("say to look records up on the full key the instructions define (every field, e.g. both "
                   "parts of a pair), never on a partial key", ("key",)),
    "filter": ("say to apply every inclusion and exclusion condition from the instructions in code, and print "
               "how many records each condition kept or removed", ("condition", "filter", "include", "exclude")),
    "aggregation": ("say to group by exactly the requested keys, then print each group's count and total "
                    "before writing", ("group", "sum", "total", "count")),
    "rounding": ("say to keep full precision and round only the values written out", ("round",)),
    "input_missed": ("say to read every input file and every rule in the instructions before computing, and "
                     "list them first", ("read", "every", "all")),
    "output_check": ("say to reopen every written file and compare each figure with the script's output",
                     ("compare", "check", "verify", "reopen")),
    "other": ("say exactly which step to do differently and how to check it was done", ()),
}


def _specific(text: str, names: list[str]) -> str | None:
    """What in `text` belongs to this task only: an id, a decimal number or a file name."""
    if m := (E._ID.search(text) or E._DEC.search(text)):
        return m.group(0)
    low = text.lower()
    return next((n for n in names if Path(n).name.lower() in low), None)


def generalize(llm: LLM, found: dict, facts: dict, task_names: list[str], *, retries: int) -> dict:
    """ONE task: name the working habit behind the concrete mistake, without anything task-specific.
    Code checks the category against the facts (no code run -> no_code)."""
    def check(o):
        if not facts["ran_code"] and o["category"] != "no_code":
            return "the facts show the agent ran no code, so the category is no_code"
        if len(o["problem"]) > 250:
            return "problem is too long: one sentence, at most 250 characters"
        if bad := _specific(o["problem"], task_names):
            return f"problem names something from this task ({bad}); describe the habit in general terms"
        return None

    got = llm.ask("generalize", (
        "Below is one concrete mistake an AI agent made on a data task, and facts about how it worked. "
        "Name the GENERAL working habit behind it: the habit that would cause the same kind of mistake on "
        "other tasks.\nAnswer with category = one of the categories, and problem = one sentence about the "
        "habit, with no names, numbers, ids or file names from this task.\n\nCATEGORIES\n"
        + "\n".join(f"- {k}: {v}" for k, v in CATEGORIES.items())
        + f"\n\nMISTAKE: {found['mistake']}\nWHERE: {found['quote']}\n\nHOW THE AGENT WORKED\n{facts_text(facts)}"),
        {"type": "object", "additionalProperties": False, "required": ["category", "problem"], "properties": {
            "category": {"type": "string", "enum": list(CATEGORIES)},
            "problem": {"type": "string", "maxLength": 400}}},
        check=check, retries=retries)
    return got


# ---------- the rule file: read it first, then change as little as possible ----------

MAX_RULE = 300
DUP = 0.5  # word overlap at which two rules count as saying the same thing
_ITEM = re.compile(r"^(\d+\.|-)\s+(\S.*)$")
_STOP = set("a an the to of and or in on for with from by at is are be it its that this each every any as "
            "not never only then when before after into than so if all".split())


def _words(text: str) -> set[str]:
    return {w.rstrip("s") for w in re.findall(r"[a-z]{3,}", text.lower()) if w not in _STOP}


def similarity(a: str, b: str) -> float:
    """Overlap coefficient of the content words: 1.0 when the shorter text is inside the longer one."""
    wa, wb = _words(a), _words(b)
    return len(wa & wb) / min(len(wa), len(wb)) if wa and wb else 0.0


class Rules:
    """A rules file read as rule items (`1. text` / `- text`, with their indented lines) plus the
    rest (headings, comments), which is kept as it is. A rule nobody touched keeps its own lines."""

    def __init__(self, text: str):
        self.segs: list = []
        self.style = None
        cur = None
        for line in text.splitlines():
            if m := _ITEM.match(line):
                cur = {"raw": [line], "text": m.group(2).strip(), "new": None}
                self.segs.append(cur)
                self.style = self.style or ("num" if m.group(1)[0].isdigit() else "bullet")
            elif cur is not None and line.strip() and line[:1] in " \t":
                cur["raw"].append(line)
                cur["text"] += " " + line.strip()
            else:
                cur = None
                self.segs.append(line)

    @staticmethod
    def text_of(rule: dict) -> str:
        return rule["text"] if rule["new"] is None else rule["new"]

    @property
    def rules(self) -> list[dict]:
        return [s for s in self.segs if isinstance(s, dict)]

    def labelled(self) -> list[tuple[str, dict]]:
        return [(f"r{i}", r) for i, r in enumerate(self.rules, 1)]

    def texts(self) -> dict[str, str]:
        return {i: self.text_of(r) for i, r in self.labelled()}

    @property
    def chars(self) -> int:
        return sum(len(self.text_of(r)) for r in self.rules)

    def listing(self, ids: list[str] | None = None) -> str:
        return "\n".join(f"{i}: {t}" for i, t in self.texts().items() if ids is None or i in ids) or "(none yet)"

    def candidates(self, category: str) -> list[str]:
        words = CAT_WORDS.get(category, ())
        return [i for i, t in self.texts().items() if any(w in t.lower() for w in words)]

    def _rule(self, rid: str) -> dict:
        return dict(self.labelled())[rid]

    def rewrite(self, rid: str, text: str) -> None:
        self._rule(rid)["new"] = text

    def delete(self, rid: str) -> None:
        self.segs.remove(self._rule(rid))

    def add(self, text: str) -> None:
        last = [i for i, s in enumerate(self.segs) if isinstance(s, dict)]
        self.segs.insert(last[-1] + 1 if last else len(self.segs), {"raw": None, "text": text, "new": text})

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


# words that show a rule is about a habit category (used to find the rules that may already cover it)
CAT_WORDS = {
    "no_code": ("script", "python", "code"),
    "lookup_key": ("key", "look up", "lookup", "match"),
    "filter": ("filter", "exclude", "include", "condition"),
    "aggregation": ("group", "sum", "total", "count"),
    "rounding": ("round", "precision"),
    "input_missed": ("every input", "each input", "all input", "every file", "read every"),
    "output_check": ("reopen", "read back", "compare", "verify", "check"),
}


def _rule_check(task_names: list[str], hint: str, words: tuple, limit: int = MAX_RULE):
    """The checks every rule text passes, whether it is new, rewritten or merged."""
    def check(r: str):
        if not r:
            return "empty rule"
        if len(r) > limit or r.count("\n") > 1:
            return f"too long: at most two lines and {limit} characters"
        if bad := _specific(r, task_names):
            return f"it names something from this task ({bad}); state the general procedure"
        if words and not any(w in r.lower() for w in words):
            return f"too vague: the rule must {hint}"
        return None
    return check


def match_rules(llm: LLM, general: dict, rules: Rules, cand: list[str], *, retries: int) -> list[str]:
    """ONE task: which existing rules already tell the agent not to do this."""
    got = llm.ask("match", (
        f"HABIT ({general['category']}): {general['problem']}\n\nRULES\n{rules.listing(cand)}\n\n"
        f"Which of these rules already tell an AI agent not to do this habit? Answer with their ids, or an "
        f"empty list if none of them does."),
        {"type": "object", "additionalProperties": False, "required": ["covering"], "properties": {
            "covering": {"type": "array", "maxItems": 3, "uniqueItems": True,
                         "items": {"type": "string", "enum": cand}}}},
        retries=retries)
    return got["covering"]


def edit_rule(llm: LLM, dim: str, rules: Rules, general: dict, covering: list[str], facts: dict,
              task_names: list[str], *, retries: int) -> dict:
    """ONE task: the text of ONE rule. A rule that already covers the habit is rewritten (the agent had it
    and still did this); several are merged into one that is shorter than they were together; only when
    none covers it is a new rule written, and one that repeats an existing rule becomes a rewrite of it."""
    kind = ("how the agent must work: what to compute in code, what to check, in which order" if dim == "INV"
            else "what an expert in this kind of work always checks or applies")
    hint, words = RULE_HINT[general["category"]]
    texts = rules.texts()
    action = "add" if not covering else "rewrite" if len(covering) == 1 else "merge"
    habit = f"HABIT ({general['category']}: {CATEGORIES[general['category']]}): {general['problem']}"
    common = _rule_check(task_names, hint, words)
    dup: dict = {}

    def check(o):
        r = o["rule"].strip()
        if problem := common(r):
            return problem
        if action == "add":
            for i, t in texts.items():
                if similarity(r, t) >= DUP:
                    dup.setdefault("id", i)
                    return f"it repeats rule {i} ({t[:80]}); do not add what a rule already says"
        elif _norm(r) in {_norm(texts[i]) for i in covering}:
            return "the rule is unchanged"
        elif action == "merge" and len(r) >= sum(len(texts[i]) for i in covering):
            return "the merged rule must be shorter than those rules together"
        return None

    if action == "add":
        prompt = (f"Write ONE rule for an AI agent's standing instructions that stops the working habit below, "
                  f"on any task. The rule is about {kind}.\nThe rule must {hint}.\nConstraints: one or two "
                  f"imperative sentences, at most {MAX_RULE} characters. None of the existing rules covers this "
                  f"habit.\n\n{habit}\n\nEXISTING RULES\n{_clip(rules.listing())}")
    elif action == "rewrite":
        prompt = (f"An AI agent's harness already has the rule below about the working habit below, and the "
                  f"agent STILL did it:\n{facts_text(facts)}\nRewrite that rule so the agent cannot misread "
                  f"it. It must {hint}. Keep every requirement it already has, add nothing on other topics, at "
                  f"most {MAX_RULE} characters, one or two imperative sentences.\n\n{habit}\n\n"
                  f"RULE\n{rules.listing(covering)}")
    else:
        prompt = (f"These rules of an AI agent's harness all address the working habit below, and the agent "
                  f"STILL did it:\n{facts_text(facts)}\nWrite ONE rule that replaces all of them: it must "
                  f"{hint}, keep every requirement they have, drop the repetition, at most {MAX_RULE} "
                  f"characters, shorter than they are together.\n\n{habit}\n\nRULES\n{rules.listing(covering)}")
    try:
        got = llm.ask("edit_" + action, prompt, {
            "type": "object", "additionalProperties": False, "required": ["rule"],
            "properties": {"rule": {"type": "string", "maxLength": 400}}}, check=check, retries=retries)
    except StepFailed:
        if action == "add" and dup:  # the model keeps restating a rule that is there: improve that rule
            return edit_rule(llm, dim, rules, general, [dup["id"]], facts, task_names, retries=retries)
        raise
    text = re.sub(r"^\s*(\d+[.)]|[-*])\s*", "", got["rule"].strip())
    return {"action": action, "ids": covering, "before": [texts[i] for i in covering], "text": text}


def apply_edit(rules: Rules, edit: dict) -> None:
    if edit["action"] == "add":
        rules.add(edit["text"])
        return
    first, *rest = edit["ids"]
    rules.rewrite(first, edit["text"])
    for rid in rest:
        rules.delete(rid)


def _near_pairs(texts: dict[str, str]) -> list[tuple[str, str]]:
    ids = list(texts)
    return [(a, b) for k, a in enumerate(ids) for b in ids[k + 1:] if similarity(texts[a], texts[b]) >= DUP]


def review_rules(llm: LLM, rules: Rules, task_names: list[str], *, retries: int) -> dict:
    """ONE task: shorten the list. Rules saying the same thing become one shorter rule; a rule another one
    already contains is deleted. Code checks every op (shorter, covered, no near-duplicate left)."""
    texts = rules.texts()
    ids = list(texts)
    if len(ids) < 2:
        return {"merge": [], "delete": []}
    pairs = _near_pairs(texts)
    common = _rule_check(task_names, "", ())

    def check(o):
        used: list[str] = []
        for m in o["merge"]:
            used += m["ids"]
            if problem := common(m["text"].strip()):
                return f"merge of {', '.join(m['ids'])}: {problem}"
            if len(m["text"].strip()) >= sum(len(texts[i]) for i in m["ids"]):
                return f"merge of {', '.join(m['ids'])} is not shorter than those rules together"
        used += o["delete"]
        if len(used) != len(set(used)):
            return "a rule id appears in more than one operation"
        gone = set(o["delete"]) | {i for m in o["merge"] for i in m["ids"][1:]}
        for d in o["delete"]:
            keepers = [i for i in ids if i not in gone]
            if not any(similarity(texts[d], texts[i]) >= 0.4 for i in keepers):
                return f"{d} is not covered by another rule, do not delete it"
        for a, b in pairs:
            same = any(a in m["ids"] and b in m["ids"] for m in o["merge"])
            if not (same or a in o["delete"] or b in o["delete"]):
                return f"{a} and {b} say almost the same thing: merge them into one rule or delete one"
        return None

    return llm.ask("review", (
        f"These are an AI agent's standing rules.\n\nRULES\n{rules.listing()}\n\n"
        + (f"These pairs overlap a lot: {', '.join(f'{a}+{b}' for a, b in pairs)}.\n" if pairs else "")
        + f"Make the list shorter without losing any requirement. Rules that say the same thing: merge them "
        f"into ONE shorter rule (merge: the ids and the new text, at most {MAX_RULE} characters). A rule that "
        f"another rule already contains: delete it (delete: its id). Leave different rules alone. If every "
        f"rule is needed, answer with two empty lists."),
        {"type": "object", "additionalProperties": False, "required": ["merge", "delete"], "properties": {
            "merge": {"type": "array", "maxItems": 4, "items": {
                "type": "object", "additionalProperties": False, "required": ["ids", "text"], "properties": {
                    "ids": {"type": "array", "minItems": 2, "maxItems": 4, "uniqueItems": True,
                            "items": {"type": "string", "enum": ids}},
                    "text": {"type": "string", "maxLength": 400}}}},
            "delete": {"type": "array", "maxItems": 4, "uniqueItems": True,
                       "items": {"type": "string", "enum": ids}}}},
        check=check, retries=retries)


def refine(llm: LLM, text: str, task_names: list[str], *, retries: int) -> tuple[str, dict]:
    """Review the rule file after a change: (the shorter text, what was done). When the model's answer is
    rejected for good, near-duplicates are resolved by code: the longer wording stays, at the earlier place."""
    rules = Rules(text)
    before = (len(rules.rules), rules.chars)
    if before[0] < 2:  # nothing to merge or delete, no model call
        return text, {"merge": [], "delete": [], "by": "skipped", "rules": f"{before[0]}->{before[0]}",
                      "chars": f"{before[1]}->{before[1]}"}
    try:
        ops, how = review_rules(llm, rules, task_names, retries=retries), "llm"
    except StepFailed:
        texts = rules.texts()
        pairs = _near_pairs(texts)
        ops, how = {"merge": [], "delete": []}, "code" if pairs else "none"
        for a, b in pairs:
            ops["merge"].append({"ids": [a, b], "text": max(texts[a], texts[b], key=len)})
        ops["merge"] = ops["merge"][:1]  # one pair per pass; the final check reports what is left
    for m in ops["merge"]:
        first, *rest = m["ids"]
        rules.rewrite(first, m["text"].strip())
        for rid in rest:
            rules.delete(rid)
    for rid in ops["delete"]:
        rules.delete(rid)
    after = (len(rules.rules), rules.chars)
    if after[0] > before[0] or after[1] > before[1]:
        raise StepFailed(f"review made the rules longer: {before} -> {after}")
    changed = bool(ops["merge"] or ops["delete"])
    return (rules.render() if changed else text), {
        **ops, "by": how, "rules": f"{before[0]}->{after[0]}", "chars": f"{before[1]}->{after[1]}"}


def adopt_candidates(llm: LLM, base_tree: dict, cand_trees: list[dict], task_names: list[str], *,
                     retries: int) -> tuple[dict, dict]:
    """AGG: the rules the candidates added or rewrote go into the base rule files, a rule similar to an
    existing one replaces it instead of being added; then each file is reviewed."""
    tree, info = dict(base_tree), {}
    for k in TARGET.values():
        base_text = base_tree.get(k, "")
        base_norm = {_norm(t) for t in Rules(base_text).texts().values()}
        rules, taken = Rules(base_text), []
        for ct in cand_trees:
            for t in Rules(ct.get(k, "")).texts().values():
                if _norm(t) in base_norm or _norm(t) in taken:
                    continue
                taken.append(_norm(t))
                near = [i for i, old in rules.texts().items() if similarity(t, old) >= DUP]
                if near:
                    rules.rewrite(near[0], max(t, rules.texts()[near[0]], key=len))
                else:
                    rules.add(t)
        if not taken:
            continue
        text, review = refine(llm, rules.render(), task_names, retries=retries)
        tree[k] = text
        info[k] = {"taken": len(taken), "review": review}
    return tree, info


# ---------- the pipeline ----------

def run(*, jb, task: str, dim: str, base_tree: dict, rollout, candidates: list, work: Path,
        model: str, think: bool, retries: int) -> dict:
    """INV / DOM: change the rule file by at most one rule from the first DIFF. AGG: adopt the rules of the
    candidates with fewer DIFFs. Returns the structured result; `result["tree"]` is the new harness."""
    llm = LLM(model, work / "steps", think=think)
    res: dict = {"dim": dim, "task": task, "model": llm.model, "rollout": rollout.label, "steps": []}
    t0 = time.time()

    def step(name, **info):
        res["steps"].append({"step": name, "seconds": round(time.time() - t0), **info})
        print(f"  [{round(time.time() - t0):>4}s] {name}: {json.dumps(info, ensure_ascii=False)[:200]}", flush=True)

    instructions, previews, folder = task_inputs(jb, task)
    task_names = list(previews) + list(jb.deliverable_files(rollout.run_dir))
    reported = figures(llm, jb.deliverable_files(rollout.run_dir))
    step("figures", count=len(reported))
    if not reported:
        raise StepFailed("figures: the rollout reports no number to check")
    expected = checker(llm, instructions, previews, folder, list(reported), work, retries=retries)
    step("checker", computed=len(expected))
    diffs = compare(reported, expected)
    res["diffs"] = diffs
    step("compare", diffs=len(diffs), first=diffs[0] if diffs else None)
    surface = jb.substrate.surface()

    if dim == "AGG":
        scores = {}
        for label, hdir, view in candidates:
            got = figures(llm, jb.deliverable_files(view.run_dir), names=list(expected))
            scores[label] = len(compare(got, expected))
        step("candidates", base=len(diffs), **scores)
        better = [c for c in candidates if scores[c[0]] < len(diffs)]
        chosen = better or [c for c in candidates if scores[c[0]] == len(diffs)]
        tree, info = adopt_candidates(llm, base_tree, [harness_tree.read_tree(surface, c[1]) for c in chosen],
                                      task_names, retries=retries)
        step("adopt", **{k: v for k, v in info.items()})
        res.update(scores=scores, adopted=[c[0] for c in chosen], adopt=info)
    else:
        target = TARGET[dim]
        facts = process_facts(rollout.run_dir, list(previews))
        step("facts", **{k: v for k, v in facts.items() if k != "tool_calls"})
        scripts = agent_scripts(rollout.run_dir)
        if diffs and scripts:  # compare code with code: the easiest form of "find the mistake"
            agent_code = "\n\n".join(f"# ---- {p}\n{c}" for p, c in scripts.items())
            found = locate_in_scripts(llm, agent_code, (work / "check.py").read_text(encoding="utf-8"),
                                      diffs[0], retries=retries)
        else:
            found = locate_in_trajectory(llm, instructions, jb.trajectory(rollout.run_dir, obs_trunc=400),
                                         diffs[0] if diffs else None, retries=retries)
        step("locate", **found)
        general = generalize(llm, found, facts, task_names, retries=retries)
        step("generalize", **general)

        base_text = base_tree.get(target, "")
        rules = Rules(base_text)  # read the current harness before changing it
        step("read", file=target, rules=len(rules.rules), chars=rules.chars, rule_list=rules.texts())
        cand = rules.candidates(general["category"])
        covering = match_rules(llm, general, rules, cand, retries=retries) if cand else []
        step("match", candidates=cand, covering=covering)
        edit = edit_rule(llm, dim, rules, general, covering, facts, task_names, retries=retries)
        step("edit", **edit)
        apply_edit(rules, edit)
        text, review = refine(llm, rules.render(), task_names, retries=retries)
        step("review", **review)

        before, after = Rules(base_text), Rules(text)
        if len(after.rules) > len(before.rules) + 1 or after.chars > before.chars + MAX_RULE:
            raise StepFailed(f"minimal change violated: {len(before.rules)} rules/{before.chars} chars -> "
                             f"{len(after.rules)}/{after.chars}")
        tree = dict(base_tree)
        tree[target] = text
        res.update(facts=facts, locate=found, general=general, edit=edit, review=review, target=target,
                   rules={"before": before.texts(), "after": after.texts()})

    _, err = recursion._accept_tree(surface, jb, tree, base_tree, f"improve-{dim.lower()}")
    if err:
        raise StepFailed(f"validate: {err}")
    res["changed"] = sorted(k for k in set(tree) | set(base_tree) if tree.get(k) != base_tree.get(k))
    res["tree"] = tree
    step("apply", changed=res["changed"])
    return res
