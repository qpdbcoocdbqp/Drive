# oai_skill

Standalone Python implementation of Hermes' core skill pattern: discover `SKILL.md` files, put only a compact index into the stable system prompt, and expose `skill_view` to the model for progressive loading.

```powershell
$env:OPENAI_API_KEY = "..."
python -m oai_skill --skills-dir .\skills --model gpt-4.1-mini "Review this project and propose tests"
python -m oai_skill --skills-dir .\skills --skill code-review "Review the current diff"
```

Each skill is a directory containing `SKILL.md`:

```markdown
---
name: code-review
description: Review source changes with the team's conventions.
---

# Code review
Read the diff first, then report only actionable findings.
```

Use `--skill` for an explicit `/skill-name`-style load. Without it, the model sees the skill index and may call the supplied `skill_view` function when a skill is relevant. The runner uses `from openai import OpenAI` and `client.chat.completions.create`; install the SDK with `pip install openai` and set `OPENAI_API_KEY`.

---

## Hermes Skill Execution Flow

The following documents how the original Hermes agent (`source/hermes/`) discovers, surfaces, and executes skills. This implementation (`skill_runner.py`) is a portable subset of that design.

### Architecture overview

```
┌─────────────────────────────────────────────────────────────────┐
│  Session start                                                  │
│    build_skills_system_prompt()  →  ## Skills + <available_skills> │
│    injected into system prompt (stable, byte-identical)         │
└────────────────────────┬────────────────────────────────────────┘
                         │
          ┌──────────────┴──────────────┐
          │ User types a message        │
          │                             │
          ▼                             ▼
   /skill-name [instruction]     plain message
   (explicit invocation)         (auto-discovery)
          │                             │
          ▼                             ▼
  build_skill_invocation_message()   model reads <available_skills>
  embeds full body + scaffolding     decides to call skill_view()
  in user message turn               as a tool call
          │                             │
          └──────────────┬──────────────┘
                         ▼
              agent tool-call loop
              model calls skill_view(name)  →  full body returned
              model calls skill_view(name, file_path=…)  →  supporting file
              model produces final text response
```

---

### 1. Skill discovery

**Primary files:** `agent/skill_commands.py` → `scan_skill_commands()`, `tools/skills_tool.py` → `_find_all_skills()`, `agent/skill_utils.py`

Skills are `SKILL.md` files discovered by walking directory trees in strict precedence order:

1. **Trusted project-local dirs** — `.hermes/skills/` and `.agents/skills/` at the nearest `.git` root, if listed in `skills.trusted_project_dirs` in `config.yaml`. Found via `find_project_root()` (walks up from `TERMINAL_CWD` or `cwd()`). Each file passes through a quarantine chokepoint (`iter_project_skill_files`) before being yielded.
2. **Profile-local skills** — `~/.hermes/skills/` (the `SKILLS_DIR` constant).
3. **External dirs** — paths from `skills.external_dirs` in `config.yaml`, resolved relative to `HERMES_HOME` if not absolute.

Earlier tiers win when two skills share the same frontmatter name. Symlinks that resolve outside the trusted root are silently skipped.

**Excluded paths:**
- Hidden directories: `.git`, `.github`, `.hub`, `.archive`, etc.
- Skill-package support directories (`references/`, `templates/`, `assets/`, `scripts/`) when they sit directly inside a directory that already contains a `SKILL.md`. These are Tier 3 content, not independent skills.
- Org mirror directory (`_org/`) is only activated when `_org/.active_org` marker is present (written by the sync client after token verification).

**Discovery cache** (`tools/skills_tool.py`):
- A short-lived in-process TTL cache (30 s) keyed on a mtime/size signature of scanned directories plus the current disabled-skill set.
- A persistent disk snapshot at `~/.hermes/.skills_prompt_snapshot.json`, validated by a full mtime/size manifest of all `SKILL.md` files. Survives process restarts and avoids cold-path filesystem scans on every session start.

---

### 2. Frontmatter parsing

**Primary file:** `agent/skill_utils.py` → `parse_frontmatter()`

```markdown
---
name: code-review
description: Review source changes with the team's conventions.
version: 1.0.0
platforms: [macos, linux]
metadata:
  hermes:
    tags: [git, quality]
    fallback_for_tools: [gh]
    config:
      - key: wiki.path
        description: Path to the wiki knowledge base
        default: "~/wiki"
---
```

Key details:
- A leading UTF-8 BOM (`\ufeff`) is stripped before parsing — Windows editors (Notepad, PowerShell `>`) prepend it, and leaving it in silently discards the entire frontmatter block.
- Parsed with `yaml.load(CSafeLoader)`, with a fallback to simple `key: value` line splitting for malformed YAML.
- Only `name` and `description` are strictly required. All other fields are optional and additive.

---

### 3. Filtering: platform, environment, and conditional activation

**Primary file:** `agent/skill_utils.py`, `tools/skills_tool.py`

Before a skill appears in the index or a slash command, three independent filters are applied:

**Platform gate** (`skill_matches_platform`):
The `platforms:` frontmatter field restricts a skill to specific operating systems (`macos`, `linux`, `windows`). Absent or empty means all platforms. Termux/Android is treated as Linux regardless of `sys.platform`.

**Environment gate** (`skill_matches_environment`):
Skills can be tagged for specific runtime environments (e.g. kanban, docker, s6 container). A skill tagged for an inactive environment is hidden from the offer-time index but still loadable via an explicit `skill_view()` call.

**Conditional activation** (`extract_skill_conditions`, `_skill_should_show`):
Declared via `metadata.hermes.*` in frontmatter:
- `fallback_for_toolsets` / `fallback_for_tools` — hide when the primary tool/toolset IS available (the skill is a fallback).
- `requires_toolsets` / `requires_tools` — hide when a required tool/toolset is NOT available.
- `session_platforms` — hide on all gateway channels except the listed ones (e.g. `[msteams]`).

**Disabled list** — `skills.disabled` and `skills.platform_disabled` in `config.yaml` suppress skills by name globally or per-platform.

---

### 4. System prompt — Tier 1 (compact index)

**Primary file:** `agent/prompt_builder.py` → `build_skills_system_prompt()` / `_build_skills_system_prompt_inner()`

The compact index is injected into the system prompt once per session, inside a `## Skills` block:

```
## Skills
Before replying, scan the skills below. If a skill matches or is even partially
relevant to your task, you MUST load it with skill_view(name) and follow its
instructions. Err on the side of loading — it is always better to have context
you don't need than to miss critical steps, pitfalls, or established workflows.
...
<available_skills>
  category:
    - skill-name: Brief description (≤57 chars)
    - other-skill: ...
  org:acme:
    - [org-shared: by alice] acme-deploy: Deploy to the Acme cluster
</available_skills>
Only proceed without loading a skill if genuinely none are relevant to the task.
```

Important properties of this block:
- **No skill bodies** are included — only name and description. This keeps the system prompt short and stable (critical for Anthropic prompt-cache hits).
- Skills are grouped by top-level category directory. Org-shared skills get a `[org-shared: by author]` tag.
- **Name collisions** between a personal skill and an org skill of the same name are surfaced explicitly with `[name collision]` flags; neither silently wins.
- **Project-local skills** get a `[project]` prefix and shadow same-named profile-local skills (intentional override feature).
- **`compact_categories`** (set by the coding posture) demote entire categories to a names-only line, dropping descriptions to reduce noise while keeping every skill name visible and callable.
- The result is cached in an LRU dict keyed by `(skills_dir, external_dirs, project_dirs, available_tools, available_toolsets, session_platform, disabled_set, compact_categories)`. Same inputs always produce the same bytes.

---

### 5. Explicit invocation — the `/skill-name` path

**Primary file:** `agent/skill_commands.py` → `build_skill_invocation_message()`

When the user types `/code-review fix the auth bug`, the CLI or gateway dispatcher:

1. Calls `resolve_skill_command_key("code-review")` — normalises `_` → `-`, looks up in the slash-command map built by `scan_skill_commands()`.
2. Calls `_load_skill_payload(skill_dir, task_id)` which calls `skill_view()` internally with `preprocess=False`.
3. Builds the activation note and calls `_build_skill_message()`.
4. The entire expanded message replaces the user turn for that conversation step.

The user message is assembled by `_build_skill_message()` in this order:

```
[IMPORTANT: The user has invoked the "code-review" skill, indicating they want
you to follow its instructions. The full skill content is loaded below.]

<full SKILL.md body after preprocessing>

[Skill directory: /absolute/path/to/skills/code-review]
Resolve any relative paths in this skill against that directory, then run them
with the terminal tool using the absolute path.

[Skill config (from ~/.hermes/config.yaml):
  wiki.path = ~/wiki
]

[This skill has supporting files (paths relative to the skill directory above):]
- references/api.md
- templates/report.md

Load any of these with skill_view(name="code-review", file_path="<path>"), ...

The user has provided the following instruction alongside the skill invocation: fix the auth bug
```

The text up to (but not including) the user instruction is the **stable prefix** — registered with `register_stable_prefix()` so the Anthropic cache planner puts a breakpoint there rather than caching the whole message as one block.

**Scaffolding markers** are byte-identical constants shared across the codebase:
- `_SKILL_INVOCATION_PREFIX = "[IMPORTANT: The user has invoked the "`
- `_SINGLE_SKILL_MARKER = "The full skill content is loaded below.]"`
- `_SINGLE_SKILL_INSTRUCTION = "The user has provided the following instruction alongside the skill invocation: "`

Memory providers (mem0, openviking, hindsight, etc.) use `extract_user_instruction_from_skill_message()` to strip the skill body and store only the user's actual instruction. This function keys off these exact marker strings.

**Stacked invocations** (`/skill-a /skill-b do XYZ`): `split_stacked_skill_commands()` greedily consumes up to `_MAX_STACKED_SKILLS = 5` leading `/tokens` that resolve to installed skills. `build_stacked_skill_invocation_message()` loads each in order and prepends a bundle header containing `" skill bundle,"` — the bundle-format marker reused by `extract_user_instruction_from_skill_message()` without any new marker plumbing.

**Preloaded skills** (`hermes -s code-review` / `HERMES_TUI_SKILLS` env var): `build_preloaded_skills_prompt()` uses the same `_load_skill_payload` / `_build_skill_message` path with a different activation note: `"...launched this CLI session with the skill preloaded. Treat its instructions as active guidance for the duration of this session..."`. Injected at session start, before the first user turn.

---

### 6. Skill preprocessing

**Primary file:** `agent/skill_preprocessing.py` → `preprocess_skill_content()`

Applied to the skill body before it is embedded in the message:

**Template variable substitution** (`skills.template_vars: true` in config, default on):
- `${HERMES_SKILL_DIR}` → absolute path to the skill's directory.
- `${HERMES_SESSION_ID}` → current session/task ID.
- Unresolved tokens are left in place so the author can spot them.

**Inline shell expansion** (`skills.inline_shell: false` in config, default off):
- Replaces `` !`command` `` snippets by running them under `bash -c` with the skill directory as `cwd`.
- Output is capped at 4000 characters. A configurable timeout (default 10 s) produces a `[inline-shell timeout: ...]` marker instead of raising.
- Useful for injecting dynamic context (today's date, git branch, environment state) into skill instructions at load time.

Both transformations happen before the skill body is assembled into the message, so setup notes and supporting-file hints see the expanded content.

---

### 7. `skill_view` — Tier 2 and Tier 3

**Primary file:** `tools/skills_tool.py` → `skill_view(name, file_path=None)`

The tool the model calls when it decides a skill is relevant. Returns a JSON payload.

**Name resolution** (three strategies, tried in order):

1. **Direct path join** — `skills_dir / name`. Handles bare names (`axolotl`) and categorised paths (`mlops/axolotl`).
2. **Recursive scan** — walks all `SKILL.md` files; matches `parent.name == name` or frontmatter `name:` field. Catches deeply nested skills and frontmatter-alias lookups.
3. **Legacy flat `.md` files** — `<name>.md` anywhere under the dir, excluding support subdirectories.

Each strategy collects candidates across all search roots (project → local → external). If more than one candidate survives cross-tier collision resolution (project-tier candidates take precedence over local/external), the tool refuses with an explicit collision error rather than silently guessing.

**Tier 2 response** (no `file_path`):
```json
{
  "success": true,
  "name": "code-review",
  "description": "...",
  "content": "<preprocessed SKILL.md body>",
  "skill_dir": "/absolute/path/to/skills/code-review",
  "linked_files": {
    "references": ["references/api.md"],
    "templates": ["templates/report.md"]
  },
  "required_environment_variables": [...],
  "setup_needed": false,
  "readiness_status": "available"
}
```

**Tier 3 response** (`file_path="references/api.md"`):
```json
{
  "success": true,
  "name": "code-review",
  "file": "references/api.md",
  "content": "<file content>",
  "file_type": ".md"
}
```
Path traversal (`..`) is rejected. The target must resolve within the skill directory.

**Environment variable setup**: if the skill's frontmatter declares `required_environment_variables` (or the legacy `prerequisites.env_vars`), `skill_view` checks which are present, invokes secret-capture callbacks for missing ones, and returns `setup_needed: true` plus a human-readable `setup_note` if any remain unset. Available env vars are registered for passthrough into sandboxed execution environments (Docker, Modal).

---

### 8. Tool-call loop

**Primary file:** `agent/skill_commands.py` (loop mechanics implied by the tool registry in `tools/skills_tool.py`)

The agent's main loop follows standard OpenAI/Anthropic tool-call semantics:

```
send messages  →  model responds
                        │
            ┌───────────┴───────────┐
            │ tool_calls present    │ no tool_calls
            ▼                       ▼
  dispatch each call           return content
  append tool result           (done)
  to messages
            │
            └──────────────────────┐
                                   ▼
                         send messages again  →  ...
```

- `skill_view` and `skills_list` are registered via the tool registry (`tools/registry.py`). All registered tools are available in the loop; there is no round-trip limit specific to skill loading.
- The model may call `skill_view` multiple times in one session (once per skill, or once per supporting file).
- `skill_view` usage is tracked via `tools/skill_usage.py` → `bump_use(skill_name)` for Curator lifecycle management (auto-archiving unused skills).

---

### 9. What `skill_runner.py` implements vs. what Hermes adds

| Concern | `skill_runner.py` | Hermes |
|---|---|---|
| **Discovery roots** | One or more explicit roots passed to `SkillCatalog` | Project → local `~/.hermes/skills/` → `skills.external_dirs` with trust gating, quarantine, org mirrors |
| **Frontmatter parsing** | Hand-rolled: `name` and `description` only, BOM stripping | Full YAML (`CSafeLoader`), all fields including `platforms`, `metadata.hermes.*`, `required_environment_variables` |
| **Platform / env filtering** | None | `skill_matches_platform()`, `skill_matches_environment()`, conditional activation via `fallback_for_tools`, `requires_tools` |
| **Discovery cache** | In-process `dict` per `SkillCatalog` instance | TTL + mtime signature cache + persistent disk snapshot |
| **System prompt** | `## Skills` block with categorised index | Same structure + compact-category demotion, org labels, name-collision flags, project-skill `[project]` tags, two-layer cache |
| **Scaffolding markers** | Byte-identical to Hermes | Byte-identical; `extract_user_instruction_from_skill_message` and memory providers key off these |
| **Tier 3 supporting files** | `view(name, file_path=…)` with traversal guard | Same, plus binary-file detection, `skill_view(name, file_path=…)` API with linked-file catalogue in Tier 2 response |
| **Preprocessing** | None | `${HERMES_SKILL_DIR}` / `${HERMES_SESSION_ID}` substitution; optional inline shell (`` !`cmd` ``) |
| **Skill config injection** | None | `metadata.hermes.config` entries resolved from `config.yaml` and appended as `[Skill config: …]` block |
| **Environment variable setup** | None | Missing env vars detected, secret-capture callbacks invoked, sandbox passthrough registered |
| **Stacked invocations** | Not implemented | `/skill-a /skill-b instruction` loads up to 5 skills |
| **Preloaded skills** | Pass `skills=[…]` to `run()` | `hermes -s skill` / `HERMES_TUI_SKILLS`; distinct activation note for session-wide guidance |
| **Prompt cache boundary** | Not implemented | `register_stable_prefix()` marks scaffold/instruction split for Anthropic cache planner |
| **Usage tracking** | None | `bump_use()` for Curator lifecycle management |
| **Tool loop limit** | `max_tool_rounds=8` | No limit; general agent loop handles all tools |
| **Security** | Symlink-exit guard in `discover()`; path-traversal guard in `view()` | Same + `_skill_lookup_path_error` (Windows drive letter, absolute paths), `validate_within_dir`, injection-pattern scan, quarantine for project skills |
