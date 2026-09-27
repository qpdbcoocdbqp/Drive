from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from typing import Any, Sequence

from openai import APIConnectionError, APIError, OpenAI

from docs.sandbox.test.sbx_client import ExecResult, SbxClient


DEFAULT_BASE_URL = "http://localhost:8000/v1"
DEFAULT_SANDBOX = "python-agent"
MAX_TOOL_ROUNDS = 8
MAX_OUTPUT_CHARS = 12_000


SANDBOX_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "run_in_sandbox",
            "description": (
                "Run one command inside the isolated Linux Docker Sandbox. "
                "Pass the executable and every argument as separate argv items. "
                "Use python3, not python. This tool cannot access the Windows host "
                "unless a host path was explicitly mounted into the sandbox."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "argv": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                        "maxItems": 64,
                        "description": (
                            "Command argument vector, for example "
                            "['python3', '-c', 'print(2 + 2)']."
                        ),
                    },
                    "stdin": {
                        "type": ["string", "null"],
                        "description": "Optional text sent to the command's stdin.",
                    },
                    "timeout_seconds": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 120,
                        "description": "Maximum time to wait for the command.",
                    },
                },
                "required": ["argv", "stdin", "timeout_seconds"],
                "additionalProperties": False,
            },
            "strict": True,
        },
    }
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Let an OpenAI-compatible model execute tools in Docker Sandbox."
    )
    parser.add_argument(
        "prompt",
        nargs="*",
        help="Prompt sent to the model.",
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("OPENAI_BASE_URL", DEFAULT_BASE_URL),
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("OPENAI_API_KEY", "local"),
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("OPENAI_MODEL"),
        help="Model ID. If omitted, use the first model returned by /models.",
    )
    parser.add_argument(
        "--sandbox",
        default=os.environ.get("SBX_SANDBOX", DEFAULT_SANDBOX),
    )
    parser.add_argument(
        "--max-tool-rounds",
        type=int,
        default=MAX_TOOL_ROUNDS,
    )
    return parser.parse_args()


def resolve_model(client: OpenAI, requested_model: str | None) -> str:
    if requested_model:
        return requested_model

    models = list(client.models.list().data)
    if not models:
        raise RuntimeError(
            "The endpoint returned no models. Pass --model or set OPENAI_MODEL."
        )
    return models[0].id


def truncate_output(value: str) -> tuple[str, bool]:
    if len(value) <= MAX_OUTPUT_CHARS:
        return value, False
    omitted = len(value) - MAX_OUTPUT_CHARS
    return value[:MAX_OUTPUT_CHARS] + f"\n...[truncated {omitted} chars]", True


def validate_tool_arguments(arguments: Any) -> tuple[list[str], str | None, int]:
    if not isinstance(arguments, dict):
        raise ValueError("tool arguments must be a JSON object")

    unknown = set(arguments) - {"argv", "stdin", "timeout_seconds"}
    if unknown:
        raise ValueError(f"unknown tool arguments: {sorted(unknown)}")

    argv = arguments.get("argv")
    if (
        not isinstance(argv, list)
        or not 1 <= len(argv) <= 64
        or any(not isinstance(item, str) or not item for item in argv)
    ):
        raise ValueError("argv must contain 1 to 64 non-empty strings")

    stdin = arguments.get("stdin")
    if stdin is not None and not isinstance(stdin, str):
        raise ValueError("stdin must be a string or null")
    if stdin is not None and len(stdin) > 100_000:
        raise ValueError("stdin exceeds the 100,000 character limit")

    timeout = arguments.get("timeout_seconds")
    if isinstance(timeout, bool) or not isinstance(timeout, int):
        raise ValueError("timeout_seconds must be an integer")
    if not 1 <= timeout <= 120:
        raise ValueError("timeout_seconds must be between 1 and 120")

    return argv, stdin, timeout


def result_payload(result: ExecResult) -> dict[str, Any]:
    stdout, stdout_truncated = truncate_output(result.stdout)
    stderr, stderr_truncated = truncate_output(result.stderr)
    return {
        "ok": result.returncode == 0,
        "exit_code": result.returncode,
        "stdout": stdout,
        "stderr": stderr,
        "truncated": stdout_truncated or stderr_truncated,
    }


def execute_tool(
    sbx: SbxClient,
    name: str,
    raw_arguments: str,
) -> str:
    if name != "run_in_sandbox":
        return json.dumps(
            {"ok": False, "error": f"unknown tool: {name}"},
            ensure_ascii=False,
        )

    try:
        arguments = json.loads(raw_arguments)
        argv, stdin, timeout = validate_tool_arguments(arguments)
        print(f"\n[tool] run_in_sandbox argv={argv!r}")
        result = sbx.exec(argv, stdin=stdin, timeout=timeout)
        payload = result_payload(result)
    except json.JSONDecodeError as error:
        payload = {"ok": False, "error": f"invalid tool JSON: {error}"}
    except ValueError as error:
        payload = {"ok": False, "error": str(error)}
    except subprocess.TimeoutExpired:
        payload = {"ok": False, "error": "sandbox command timed out"}
    except Exception as error:
        payload = {
            "ok": False,
            "error": f"sandbox execution error: {type(error).__name__}: {error}",
        }

    print("[tool result]", json.dumps(payload, ensure_ascii=False))
    return json.dumps(payload, ensure_ascii=False)


def run_agent(
    client: OpenAI,
    sbx: SbxClient,
    model: str,
    prompt: str,
    max_tool_rounds: int,
) -> str:
    messages: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": (
                "You are a local coding assistant. Use run_in_sandbox whenever "
                "the request requires executing or verifying code. Never claim a "
                "command succeeded without checking its tool result. The sandbox "
                "is Linux and provides python3. Summarize the verified result for "
                "the user after tool execution."
            ),
        },
        {"role": "user", "content": prompt},
    ]

    for round_number in range(1, max_tool_rounds + 1):
        completion = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=SANDBOX_TOOLS,
            tool_choice="auto",
        )
        message = completion.choices[0].message
        messages.append(message.model_dump(exclude_none=True))

        tool_calls = message.tool_calls or []
        if not tool_calls:
            return message.content or ""

        print(f"[round {round_number}] model requested {len(tool_calls)} tool call(s)")
        for tool_call in tool_calls:
            tool_output = execute_tool(
                sbx,
                tool_call.function.name,
                tool_call.function.arguments,
            )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": tool_output,
                }
            )

    raise RuntimeError(
        f"The model exceeded the limit of {max_tool_rounds} tool-calling rounds."
    )


def main() -> int:
    args = parse_args()
    if args.max_tool_rounds < 1:
        raise SystemExit("--max-tool-rounds must be at least 1")

    prompt = " ".join(args.prompt).strip() or (
        "Please use sandbox Python 3 to calculate the first 20 prime numbers, "
        "actually run the program to verify the result, "
        "and report the result in Traditional Chinese."
    )

    client = OpenAI(
        base_url=args.base_url,
        api_key=args.api_key,
        timeout=60.0,
        max_retries=0,
    )
    try:
        model = resolve_model(client, args.model)
        sbx = SbxClient(args.sandbox)

        print(f"Endpoint: {args.base_url}")
        print(f"Model: {model}")
        print(f"Sandbox: {args.sandbox}")
        print(f"Prompt: {prompt}\n")

        answer = run_agent(
            client,
            sbx,
            model,
            prompt,
            args.max_tool_rounds,
        )
    except APIConnectionError:
        print(
            f"Cannot connect to the OpenAI-compatible endpoint: {args.base_url}",
            file=sys.stderr,
        )
        return 2
    except APIError as error:
        print(f"OpenAI-compatible API error: {error}", file=sys.stderr)
        return 2

    print("\n[assistant]")
    print(answer)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
