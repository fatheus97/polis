"""The Sandbox — where the project's tests run.

In Phase 0 execution is local (``LocalSandbox``); the seam exists so Phase 1 can
drop in a ``DockerSandbox`` without touching the orchestrator. Running arbitrary
code is a safety boundary, which is exactly why it is isolated behind this
interface from day one.
"""

from __future__ import annotations

import subprocess
import sys

from .models import TestResult


class Sandbox:
    """Interface: run the project's tests against the current workspace state."""

    def run_tests(self, workspace) -> TestResult: ...


class LocalSandbox(Sandbox):
    """Runs the test command as a subprocess in the workspace directory.

    Default command discovers stdlib ``unittest`` tests (``test_*.py``), so the
    Phase-0 stub dev's bundled tests run with no project configuration.
    """

    def __init__(self, test_command: list[str] | None = None, timeout: int = 120):
        self.test_command = test_command
        self.timeout = timeout

    def run_tests(self, workspace) -> TestResult:
        cmd = self.test_command or [
            sys.executable, "-m", "unittest", "discover", "-p", "test_*.py"
        ]
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(workspace.path),
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
        except subprocess.TimeoutExpired:
            return TestResult(ran=True, passed=False, summary="timeout",
                              details=f"test command exceeded {self.timeout}s")
        passed = proc.returncode == 0
        output = (proc.stdout or "") + (proc.stderr or "")
        return TestResult(
            ran=True,
            passed=passed,
            summary="tests passed" if passed else f"tests failed (exit {proc.returncode})",
            details=output[-2000:],
        )


class FixedSandbox(Sandbox):
    """Always returns the same verdict — handy for dry runs and demos."""

    def __init__(self, passed: bool = True):
        self._passed = passed

    def run_tests(self, workspace) -> TestResult:
        return TestResult(ran=True, passed=self._passed,
                          summary="fixed:" + ("pass" if self._passed else "fail"))
