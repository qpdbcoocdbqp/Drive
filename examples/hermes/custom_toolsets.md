# Custom toolsets


1. Modify `/opt/hermes/toolsets.py`

    ```py
        "browser_mini": {
            "description": "Browser automation for web interaction (navigate, click, type, scroll, iframes, hold-click) with web search for finding URLs",
            "tools": [
                "browser_navigate", "browser_snapshot", "browser_click",
                "browser_scroll", "browser_press", "browser_cdp"
            ],
            "includes": []
        },
    ```
2. Build and reinstall package

    ```bash
    cd /opt/hermes
    uv build
    uv pip install dist/hermes_agent-0.11.0-py3-none-any.whl
    ```
3. Add toolset `browser_mini` to `platform_toolsets.cli` in `/opt/data/config.yaml` 

    ```yaml
    platform_toolsets:
        cli:
        - browser_mini
        - clarify
        - code_execution
        - delegation
        - file
        - memory
        - session_search
        - skills
        - terminal
        - todo
    ```

4. Start chat and check toolset

    ```bash
    hermes chat
    # use verbose mode to check toolsets enable
    /verbose
    # inpu tmessage
    Go to google finance website, what tickers you see?

    # logs
    ...
    > ✅ Enabled toolset 'browser_mini': browser_cdp, browser_click, browser_navigate, browser_press, browser_scroll, browser_snapshot
    ...
    ```
