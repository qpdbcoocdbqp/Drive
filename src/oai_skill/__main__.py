"""Run the API server with ``python -m oai_skill``."""

from __future__ import annotations

import os

import uvicorn


def main() -> None:
    uvicorn.run(
        "oai_skill.api.app:app",
        host=os.getenv("APP_HOST", "0.0.0.0"),
        port=int(os.getenv("APP_PORT", "8080")),
        workers=1,
    )


if __name__ == "__main__":
    main()
