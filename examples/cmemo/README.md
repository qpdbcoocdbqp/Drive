# cmemo: 7-Layer Defensive Memory Pyramid

A high-performance memory management system for LLM agents, following the architecture used by Claude Code.

## Architecture

The `module/` directory implements a tiered defensive pyramid to maintain an optimized context window:

- **L1: Tool Result Storage** (`tool_result_store.py`) - Large strings stay on disk; 2KB preview in context.
- **L2: Micro-compaction** (`context_consolidator.py`) - Lightweight pre-API cleanup of whitespace and duplicates.
- **L3: Session Memory** (`session_memory.py`) - Structured notes (Current State, Task Specs, Patterns).
- **L4: Persistence** (`memory_store.py`) - Long-term repository knowledge stored in `.cmemo/memory/`.
- **L5: Memory Indexing** (`memory_index.py`) - Maintenance of `MEMORY.md` as a searchable directory.
- **L6: AutoDream** (`context_consolidator.py`) - Automated "REM sleep" for consolidation and summary.
- **L7: Policy** - Project-specific instructions and global rules loaded from `CLAUDE.md`.

## Analysis Source

[How Claude Code Manages Memory: A Deep Technical Analysis (by @troyhua)](https://x.com/troyhua/status/2039052328070734102)

## Resources

* [learn-claude-code](https://github.com/shareAI-lab/learn-claude-code)
* [claude-code-prompts](https://github.com/repowise-dev/claude-code-prompts/tree/master)
