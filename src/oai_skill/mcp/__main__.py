"""Run the skill-manager MCP server over stdio.

This file is also an absolute-path launcher for Codex. In that mode Python does
not necessarily inherit the API process' ``PYTHONPATH``, so add the package's
``src`` directory before importing the server.
"""

from __future__ import annotations

import sys
from pathlib import Path

if __package__:
    from .skill_server import main
else:  # Executed as ``python /app/src/oai_skill/mcp/__main__.py``.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from oai_skill.mcp.skill_server import main


if __name__ == "__main__":
    main()
