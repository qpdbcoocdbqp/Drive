from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.module.skills import SkillsRunner, load_skills

BASE_URL = os.environ.get("SKILLS_RUNNER_BASE_URL", "http://localhost:19001/v1")
MODEL = os.environ.get("SKILLS_RUNNER_MODEL", "sonnet")

CHECKSUM_SKILL = """---
name: checksum-report
description: Compute the SHA-256 checksum of a local file with the bundled
  script and write a report file. Use when asked for a file checksum report.
---

# Checksum report

1. Run `python <skill dir>/scripts/checksum.py <file>`; it prints `sha256=<hex>`.
2. Write `report.txt` in the working directory with exactly one line:
   `FILE=<file name> SHA256=<hex>`
3. Reply with the hex digest.
"""

CHECKSUM_SCRIPT = """import hashlib, sys
print("sha256=" + hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest())
"""


@pytest.fixture
def skills_dir(tmp_path: Path) -> Path:
    skill = tmp_path / "skills" / "checksum-report"
    (skill / "scripts").mkdir(parents=True)
    (skill / "SKILL.md").write_text(CHECKSUM_SKILL, encoding="utf-8")
    (skill / "scripts" / "checksum.py").write_text(CHECKSUM_SCRIPT, encoding="utf-8")
    other = tmp_path / "skills" / "unrelated" / "SKILL.md"
    other.parent.mkdir()
    other.write_text("---\nname: unrelated\ndescription: Does something else.\n---\nbody", encoding="utf-8")
    return tmp_path / "skills"


def test_load_skills(skills_dir: Path):
    skills = load_skills(skills_dir)
    assert set(skills) == {"checksum-report", "unrelated"}
    assert "SHA-256" in skills["checksum-report"].description
    assert skills["checksum-report"].body.startswith("# Checksum report")
    assert set(load_skills(skills_dir / "checksum-report")) == {"checksum-report"}
    with pytest.raises(FileNotFoundError):
        load_skills(skills_dir / "missing")


def _reply(content=None, calls=()):
    tool_calls = [
        SimpleNamespace(id=f"c{i}", function=SimpleNamespace(name=n, arguments=a))
        for i, (n, a) in enumerate(calls)
    ] or None
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=tool_calls))])


class FakeClient:
    def __init__(self, replies):
        self.replies = list(replies)
        self.seen = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.seen.append(json.loads(json.dumps(kwargs["messages"])))
        return self.replies.pop(0)


@pytest.mark.parametrize("prompt", ["checksum please", "/checksum-report checksum please"])
def test_lifecycle_with_fake_client(skills_dir: Path, tmp_path: Path, prompt: str):
    data = tmp_path / "data.bin"
    data.write_bytes(b"hello")
    digest = hashlib.sha256(b"hello").hexdigest()
    script = skills_dir / "checksum-report" / "scripts" / "checksum.py"
    client = FakeClient([
        _reply(calls=[("load_skill", '{"name": "checksum-report"}')]),
        _reply(calls=[("run_command", json.dumps({"command": f'"{sys.executable}" "{script}" "{data}"'}))]),
        _reply(calls=[("nope", "{}"), ("read_file", "not json")]),  # errors must not crash the loop
        _reply(content=digest),
    ])
    if prompt.startswith("/"):
        client.replies.pop(0)  # The runner preloads the skill before the first LLM call.
    runner = SkillsRunner(skills_dir, client=client, workdir=tmp_path)
    result = runner.run(prompt)
    assert result.finished and result.content == digest
    assert [s["name"] for s in result.steps] == ["load_skill", "run_command", "nope", "read_file"]
    assert digest in result.steps[1]["result"]
    assert result.steps[2]["result"].startswith("error: unknown tool")
    assert result.steps[3]["result"].startswith("error: invalid tool arguments")
    assert "checksum-report:" in client.seen[0][0]["content"]


@pytest.mark.parametrize("prompt, skill", [
    ("/checksum-report checksum please", None),
    ("/checksum-report\nchecksum please", None),
    (" \t/checksum-report\tchecksum please  \n", None),
    ("/checksum-report checksum please", "checksum-report"),
    ("checksum please", "checksum-report"),
])
def test_skill_preload(skills_dir: Path, tmp_path: Path, prompt: str, skill: str | None):
    client = FakeClient([_reply(content="done")])
    runner = SkillsRunner(skills_dir, client=client, workdir=tmp_path)
    result = runner.run(prompt, skill=skill)
    assert result.finished and result.content == "done"
    assert len(result.steps) == 1
    assert result.steps[0]["name"] == "load_skill"
    assert result.steps[0]["arguments"] == {"name": "checksum-report"}
    messages = client.seen[0]
    assert messages[1] == {"role": "user", "content": "checksum please"}
    assert messages[2]["tool_calls"][0]["function"]["name"] == "load_skill"
    assert messages[3]["tool_call_id"] == messages[2]["tool_calls"][0]["id"]
    assert "# Checksum report" in messages[3]["content"]


@pytest.mark.parametrize("prompt, skill, error", [
    ("/missing task", None, "unknown skill 'missing'. Available: checksum-report, unrelated"),
    ("/checksum task", None, "unknown skill 'checksum'"),
    ("/checksum-report task", "unrelated", "conflicts with /checksum-report"),
    ("/checksum-report", None, "provide a task after /checksum-report"),
    ("/checksum-report \n\t", None, "provide a task after /checksum-report"),
])
def test_invalid_skill_command(skills_dir: Path, tmp_path: Path, prompt: str, skill: str | None, error: str):
    client = FakeClient([])
    runner = SkillsRunner(skills_dir, client=client, workdir=tmp_path)
    with pytest.raises(ValueError) as exc:
        runner.run(prompt, skill=skill)
    assert error in str(exc.value)
    assert client.seen == []


@pytest.mark.parametrize("prompt", ["checksum please", "  checksum please  ", "Explain /checksum-report"])
def test_plain_prompt_unchanged(skills_dir: Path, tmp_path: Path, prompt: str):
    client = FakeClient([_reply(content="done")])
    result = SkillsRunner(skills_dir, client=client, workdir=tmp_path).run(prompt)
    assert result.finished and result.steps == []
    assert client.seen[0][1] == {"role": "user", "content": prompt}


def test_max_turns(skills_dir: Path, tmp_path: Path):
    client = FakeClient([_reply(calls=[("read_file", '{"path": "x"}')])] * 3)
    result = SkillsRunner(skills_dir, client=client, workdir=tmp_path, max_turns=3).run("loop")
    assert not result.finished and result.turns == 3


# ---- acceptance: real server ------------------------------------------------

def _server_up() -> bool:
    try:
        from openai import OpenAI

        return any(m.id == MODEL for m in OpenAI(base_url=BASE_URL, api_key="x", timeout=5).models.list())
    except Exception:
        return False


live = pytest.mark.skipif(not _server_up(), reason=f"{BASE_URL} model={MODEL} unavailable")


@live
@pytest.mark.parametrize("attempt", range(3))  # "stable": must pass every time
def test_acceptance_checksum_goal(skills_dir: Path, tmp_path: Path, attempt: int):
    work = tmp_path / f"work{attempt}"
    work.mkdir()
    payload = f"acceptance-{attempt}-payload".encode()
    (work / "input.txt").write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()

    runner = SkillsRunner(skills_dir, base_url=BASE_URL, model=MODEL, workdir=work, max_turns=12)
    prompt = "Give me a checksum report for input.txt."
    result = runner.run(f"/checksum-report {prompt}" if attempt == 0 else prompt)

    print(f"\n=== attempt {attempt} | model={MODEL} | skills={list(runner.skills)} ===")
    for i, step in enumerate(result.steps, 1):
        print(f"[step {i}] {step['name']} {json.dumps(step['arguments'], ensure_ascii=False)}")
        print("    -> " + step["result"][:300].replace("\n", "\n       "))
    print(f"[final answer] {result.content}")

    assert result.finished, result.steps
    names = [s["name"] for s in result.steps]
    assert "load_skill" in names and "run_command" in names
    assert (work / "report.txt").read_text(encoding="utf-8").strip() == f"FILE=input.txt SHA256={digest}"
    assert digest in result.content


def test_run_command_decoding(tmp_path: Path):
    runner = SkillsRunner({}, client=object(), workdir=tmp_path)
    out = runner._tool_run_command(f'"{sys.executable}" -c "print(\'中文 ok\')"')
    assert "中文 ok" in out
    bad = runner._tool_run_command("nonexistent_cmd_xyz_123")
    assert "exit_code: 0" not in bad and "\ufffd" not in bad and "b'" not in bad
