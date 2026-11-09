---
name: find-all-in-episode
description: "Locates and returns the latest episode title, date, summary and URL for the All-In podcast using the Hermes browser_exec tool."
version: 1.3.0
author: User
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [Podcast, Latest, Episode, Audio, Search]
    related_tools:
      - browser_exec
constraints:
  - "Use ONLY the browser_exec tool (v0.23.0+ removed browser_navigate / browser_snapshot / browser_cdp / browser_tools)."
  - "Do NOT use web_extract or web search: they depend on Firecrawl and may hang for minutes."
  - "Do NOT import extra modules or write helper files; the browser helpers are pre-imported inside browser_exec."
  - "Only retrieve the most recent episode information."
  - "Ignore older episodes and unrelated page content."
---

# 🎧 Podcast Episode Finder

This skill retrieves the latest episode metadata from the All-In Podcast episodes archive.

## Usage

Run `find-all-in-episode` to automatically fetch:

- Latest episode title
- Release date
- Episode number (only if the page shows one — currently it does not)
- Short summary / description
- Episode URL

## Workflow

Make **one** `browser_exec` call with the code below. The helpers (`new_tab`,
`wait_for_load`, `js`, ...) are already imported. Copy the code below
**verbatim** (only the stdlib `import` line shown is allowed); do NOT import
`agent_helpers` or any other module (that raises `ModuleNotFoundError`).

```python
# Reading latest All-In episode
import json, re, time
new_tab('https://allin.com/episodes')
wait_for_load()
time.sleep(3)  # the list is rendered client-side
text = js("document.body.innerText")
links = json.loads(js("JSON.stringify([...document.querySelectorAll('a[href]')].map(a => a.href))"))
lines = [l.strip() for l in text.split("\n") if l.strip()]
i = next((k for k, l in enumerate(lines) if re.fullmatch(r"\[\s*\d{1,2}/\d{1,2}/\d{4}\s*\]", l)), None)
yt = next((h for h in links if re.search(r"youtube\.com/(v/|watch\?v=)", h)), None)
vid = re.search(r"(?:/v/|v=)([\w-]{11})", yt) if yt else None
if i is None:
    print(json.dumps({"error": "date marker not found", "head": lines[:12]}))
else:
    print(json.dumps({
        "date": re.sub(r"[\[\]\s]", "", lines[i]),
        "title": lines[i + 1],
        "summary": lines[i + 2],
        "url": "https://www.youtube.com/watch?v=" + vid.group(1) if vid else None,
    }, ensure_ascii=False))
```

The first `[ M/D/YYYY ]` entry on the page is the most recent episode: the next
line is its title, the line after is its description. Do not read further.

If the output is an `error`, wait a few seconds and run the same call once more
before giving up.

### Output Format

Return results in this structure (`episode_number` is `null` unless the page
itself shows one — never invent it):

```json
{
  "podcast": "All-In",
  "episode_number": null,
  "date": "M/D/YYYY",
  "title": "Episode title",
  "summary": "Brief episode summary",
  "url": "https://..."
}
```

If extraction fails, return:

```json
{
  "error": "Unable to locate latest episode information."
}
```
