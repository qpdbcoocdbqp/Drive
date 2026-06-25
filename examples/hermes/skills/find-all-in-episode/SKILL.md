---
name: find-all-in-episode
description: "Locates and returns the title and episode number of the most recent episodes of major podcasts by executing a targeted extraction script on the episodes archive."
version: 1.1.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [Podcast, Latest, Episode, Audio, Search]
    related_skills: [web_extract, web_search]
---

# 🎧 Podcast Episode Finder

This skill is designed to quickly locate the latest episode title and associated information for major podcasts (e.g., All-In). It uses a specialized extraction script that queries official archives or RSS feeds to bypass manual browsing.

## Usage

Run `find-all-in-episode` to automatically retrieve the most recent podcast metadata.

### Example Workflow

1. **Discovery:** Identify the podcast name (e.g., "All-In").
2. **Extraction:** Execute the `./scripts/extract_allin_latest.py` script, passing the podcast name.
3. **Output:** Receive the latest title, episode number, and brief summary.

## Limitations

*   **Scope:** Currently optimized for major, well-indexed podcasts (e.g., those with public RSS feeds or dedicated data endpoints).
*   **Accuracy:** Accuracy depends on the script successfully parsing the target podcast's data structure.
*   **Tool Reliance:** Requires `terminal` access to execute the specialized extraction script.

## Available Tools

*   `terminal`: Used to execute the specialized extraction script.

---
