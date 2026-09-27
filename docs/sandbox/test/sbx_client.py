from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


@dataclass(frozen=True)
class ExecResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


class SbxClient:
    def __init__(
        self,
        sandbox: str,
        executable: str | Path | None = None,
    ) -> None:
        self.sandbox = sandbox
        self.executable = self._resolve_executable(executable)

    @staticmethod
    def _resolve_executable(executable: str | Path | None) -> str:
        if executable is not None:
            path = Path(executable).expanduser().resolve()
            if path.is_file():
                return str(path)
            raise FileNotFoundError(f"sbx executable not found: {path}")

        from_path = shutil.which("sbx")
        if from_path:
            return from_path

        candidates: list[Path] = []
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            candidates.append(
                Path(local_app_data) / "DockerSandboxes" / "bin" / "sbx.exe"
            )

        program_files = os.environ.get("ProgramFiles")
        if program_files:
            candidates.append(
                Path(program_files) / "DockerSandboxes" / "bin" / "sbx.exe"
            )

        for candidate in candidates:
            if candidate.is_file():
                return str(candidate)

        checked = "\n".join(f"  - {path}" for path in candidates)
        raise FileNotFoundError(
            "Unable to find sbx.exe. Add Docker Sandboxes\\bin to PATH "
            "or pass executable=... to SbxClient. Checked:\n"
            f"{checked}"
        )

    def exec(
        self,
        argv: Sequence[str],
        *,
        stdin: str | None = None,
        workdir: str | None = None,
        timeout: float = 60,
        check: bool = False,
    ) -> ExecResult:
        if not argv:
            raise ValueError("argv cannot be empty")

        command = [self.executable, "exec"]

        if stdin is not None:
            command.append("-i")

        if workdir is not None:
            command.extend(["--workdir", workdir])

        command.extend([self.sandbox, *argv])

        completed = subprocess.run(
            command,
            input=stdin,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout,
            check=False,
        )

        result = ExecResult(
            argv=tuple(argv),
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )

        if check and result.returncode != 0:
            raise RuntimeError(
                f"Sandbox command failed: {result.argv}\n"
                f"exit code: {result.returncode}\n"
                f"stdout:\n{result.stdout}\n"
                f"stderr:\n{result.stderr}"
            )

        return result

    def copy_to(self, local: str | Path, remote: str) -> None:
        subprocess.run(
            [
                self.executable,
                "cp",
                str(Path(local).resolve()),
                f"{self.sandbox}:{remote}",
            ],
            check=True,
        )

    def copy_from(self, remote: str, local: str | Path) -> None:
        subprocess.run(
            [
                self.executable,
                "cp",
                f"{self.sandbox}:{remote}",
                str(Path(local).resolve()),
            ],
            check=True,
        )


@dataclass
class TestReporter:
    passed: int = 0
    failed: int = 0

    @staticmethod
    def _print_field(label: str, value: str | int) -> None:
        text = str(value).strip() or "<empty>"
        lines = text.splitlines()
        print(f"       {label:<9}: {lines[0]}")
        for line in lines[1:]:
            print(f"       {'':11}{line}")

    def record(
        self,
        name: str,
        passed: bool,
        result: ExecResult,
        *,
        expected: str,
        stdout: str | None = None,
    ) -> None:
        status = "PASS" if passed else "FAIL"
        self.passed += int(passed)
        self.failed += int(not passed)

        print(f"[{status}] {name}")
        self._print_field("expected", expected)
        self._print_field("exit code", result.returncode)
        self._print_field("stdout", result.stdout if stdout is None else stdout)
        self._print_field("stderr", result.stderr)
        print()

    def summary(self) -> bool:
        total = self.passed + self.failed
        status = "PASS" if self.failed == 0 else "FAIL"
        print("=" * 64)
        print(
            f"[{status}] Summary: "
            f"{self.passed} passed, {self.failed} failed, {total} total"
        )
        return self.failed == 0


def main() -> int:
    sbx = SbxClient("python-agent")
    reporter = TestReporter()

    print("Docker Sandbox interaction tests")
    print(f"Sandbox: {sbx.sandbox}")
    print("=" * 64)

    # 1. 基本命令與 stdout
    version = sbx.exec(["python3", "--version"])
    reporter.record(
        "Python runtime is available",
        version.returncode == 0 and version.stdout.startswith("Python 3."),
        version,
        expected="exit code 0 and stdout starts with 'Python 3.'",
    )

    # 2. 取得結構化 JSON
    information = sbx.exec(
        [
            "python3",
            "-c",
            (
                "import json, os, platform; "
                "print(json.dumps({"
                "'python': platform.python_version(), "
                "'system': platform.system(), "
                "'machine': platform.machine(), "
                "'cwd': os.getcwd()"
                "}))"
            ),
        ],
    )
    try:
        payload = json.loads(information.stdout)
        formatted_information = json.dumps(payload, indent=2)
        information_ok = (
            information.returncode == 0
            and payload.get("system") == "Linux"
            and payload.get("cwd") == "/home/agent/workspace"
        )
    except (json.JSONDecodeError, AttributeError):
        formatted_information = information.stdout
        information_ok = False

    reporter.record(
        "Sandbox environment is Linux",
        information_ok,
        information,
        expected="Linux with cwd /home/agent/workspace",
        stdout=formatted_information,
    )

    # 3. 傳送 stdin
    upper = sbx.exec(
        [
            "python3",
            "-c",
            "import sys; print(sys.stdin.read().upper(), end='')",
        ],
        stdin="hello from Windows Python client\n",
    )
    reporter.record(
        "Standard input round-trip",
        upper.returncode == 0
        and upper.stdout == "HELLO FROM WINDOWS PYTHON CLIENT\n",
        upper,
        expected="uppercase copy of the supplied stdin",
    )

    # 4. 測試 stderr 與非零 exit code
    failure = sbx.exec(
        [
            "python3",
            "-c",
            (
                "import sys; "
                "print('normal output'); "
                "print('diagnostic output', file=sys.stderr); "
                "sys.exit(7)"
            ),
        ],
    )

    reporter.record(
        "Non-zero exit and stderr propagation",
        failure.returncode == 7
        and failure.stdout.strip() == "normal output"
        and failure.stderr.strip() == "diagnostic output",
        failure,
        expected="exit code 7 with separate stdout and stderr",
    )

    # 5. 測試 sandbox 內的持久狀態
    write = sbx.exec(
        [
            "python3",
            "-c",
            (
                "from pathlib import Path; "
                "Path('/home/agent/workspace/client-test.txt')"
                ".write_text('persistent sandbox data\\n')"
            ),
        ],
    )
    reporter.record(
        "Persistent file can be written",
        write.returncode == 0,
        write,
        expected="exit code 0",
    )

    read = sbx.exec(
        [
            "python3",
            "-c",
            (
                "from pathlib import Path; "
                "print(Path('/home/agent/workspace/client-test.txt').read_text(), "
                "end='')"
            ),
        ],
    )
    reporter.record(
        "Persistent file survives between exec calls",
        read.returncode == 0
        and read.stdout == "persistent sandbox data\n",
        read,
        expected="stdout equals 'persistent sandbox data'",
    )

    # 宿主 Windows 路徑不應存在
    host_file = sbx.exec([
        "python3",
        "-c",
        (
            "from pathlib import Path; "
            "paths = ["
            "'/mnt/c/Users/siao/iloveit/Drive/sbx_cli.py',"
            "'/run/desktop/mnt/host/c/Users/siao/iloveit/Drive/sbx_cli.py',"
            "'/host/C/Users/siao/iloveit/Drive/sbx_cli.py'"
            "]; "
            "found = [p for p in paths if Path(p).exists()]; "
            "print(found); "
            "raise SystemExit(bool(found))"
        ),
    ])

    reporter.record(
        "Host filesystem is hidden",
        host_file.returncode == 0,
        host_file,
        expected="exit code 0 and no discovered host paths",
    )


    # 敏感 host device 不應存在
    devices = sbx.exec([
        "python3",
        "-c",
        (
            "from pathlib import Path; "
            "paths = ['/dev/mem', '/dev/kvm']; "
            "found = [p for p in paths if Path(p).exists()]; "
            "print(found); "
            "raise SystemExit(bool(found))"
        ),
    ])

    reporter.record(
        "Sensitive devices are hidden",
        devices.returncode == 0,
        devices,
        expected="exit code 0 and neither /dev/mem nor /dev/kvm exists",
    )


    # 已允許的網路目的地應成功
    allowed_network = sbx.exec([
        "curl",
        "--fail",
        "--silent",
        "--show-error",
        "--max-time", "15",
        "https://pypi.org/",
        "-o", "/dev/null",
    ])

    reporter.record(
        "Allowed network destination",
        allowed_network.returncode == 0,
        allowed_network,
        expected="pypi.org request exits with code 0",
    )


    # 未允許的目的地應失敗
    blocked_network = sbx.exec([
        "curl",
        "--fail",
        "--silent",
        "--show-error",
        "--max-time", "15",
        "https://example.com/",
        "-o", "/dev/null",
    ])

    reporter.record(
        "Unlisted network destination is blocked",
        blocked_network.returncode != 0,
        blocked_network,
        expected="example.com request is rejected with a non-zero exit code",
    )

    return 0 if reporter.summary() else 1

if __name__ == "__main__":
    raise SystemExit(main())

