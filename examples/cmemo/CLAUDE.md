# Project Policy: cmemo (Local LLM optimized)

This file defines the project-level rules and interaction patterns for the local LLM agent.

## Core Rules
- **Memory Discipline**: Always check `MEMORY.md` at the start of a session.
- **Context Management**: Use the `MemoryManager` to process tool outputs > 2KB to avoid context overflow on local models (which often have smaller context windows).
- **Consolidation**: Trigger a "dream" session after every 10 messages or after finishing a significant sub-task.

## Coding Style
- **Python**: Follow PEP 8.
- **Documentation**: Use Google-style docstrings.
- **Performance**: Optimize for memory efficiency.

## Local LLM Specifics
- **Conciseness**: Prefer short, precise responses to save on inference time and context.
- **Verification**: Explicitly verify file system writes using `ls` or `cat` because some local models may hallucinate successful writes.
