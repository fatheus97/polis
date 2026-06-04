"""LLM backend — how officials think.

There is no API key or SDK in this environment, but the ``claude`` CLI is installed
and authenticated, so we drive it in headless JSON mode (``-p --output-format json``)
as the model backend for every branch. The prompt is fed on stdin (no argv length
limit, no stdin-wait warning); the response carries the text plus *actual* USD cost,
which the orchestrator debits from the Treasury.

A FakeLLM is provided so the whole suite runs hermetically — no model calls, no cost.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass, field


class LLMError(RuntimeError):
    pass


@dataclass
class LLMResponse:
    text: str
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    is_error: bool = False
    raw: dict | None = None


class LLMBackend:
    def complete(self, prompt: str, *, system: str | None = None, model: str | None = None,
                 cwd: str | None = None, permission_mode: str = "default",
                 extra_args: list[str] | None = None) -> LLMResponse:  # pragma: no cover
        raise NotImplementedError


class ClaudeCliBackend(LLMBackend):
    # API statuses worth retrying (rate limit / transient server overload).
    _TRANSIENT_STATUS = {429, 500, 502, 503, 504, 529}
    _TRANSIENT_TEXT = ("overloaded", "rate limit", "try again", "timeout", "529", "503")

    # Env vars that would route billing to an API key instead of the logged-in
    # (e.g. Max) subscription. Scrubbed by default so we can NEVER spend someone
    # else's key just because it happens to be in the environment.
    _BILLING_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")

    def __init__(self, binary: str = "claude", default_model: str = "sonnet",
                 timeout: int = 300, default_cwd: str | None = None,
                 max_retries: int = 3, retry_base: float = 2.0,
                 use_subscription: bool = True):
        self.binary = binary
        self.default_model = default_model
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_base = retry_base
        # Force the CLI to use its logged-in subscription, not an ambient API key.
        self.use_subscription = use_subscription
        # A neutral, throwaway directory so pure-reasoning agents (architect/reviewer)
        # never run in — and so cannot read or edit — the Polis repo itself.
        self.default_cwd = default_cwd or tempfile.mkdtemp(prefix="polis-llm-cwd-")

    def _child_env(self) -> dict:
        """Environment for the claude subprocess. By default the API-key/billing vars
        are removed so the CLI authenticates with the logged-in subscription."""
        env = os.environ.copy()
        if self.use_subscription:
            for key in self._BILLING_ENV:
                env.pop(key, None)
        return env

    @classmethod
    def _is_transient(cls, data: dict) -> bool:
        status = data.get("api_error_status")
        if status in cls._TRANSIENT_STATUS:
            return True
        text = (data.get("result") or "").lower()
        return any(token in text for token in cls._TRANSIENT_TEXT)

    def complete(self, prompt, *, system=None, model=None, cwd=None,
                 permission_mode="default", extra_args=None, timeout=None) -> LLMResponse:
        to = timeout or self.timeout   # per-call override (the agentic dev needs longer)
        cmd = [
            self.binary, "-p",
            "--output-format", "json",
            "--model", model or self.default_model,
            "--permission-mode", permission_mode,
        ]
        if system:
            cmd += ["--append-system-prompt", system]
        if extra_args:
            cmd += extra_args

        last_err = "claude CLI failed"
        for attempt in range(self.max_retries + 1):
            try:
                proc = subprocess.run(
                    cmd, input=prompt, cwd=cwd or self.default_cwd,
                    capture_output=True, text=True, encoding="utf-8", timeout=to,
                    env=self._child_env(),
                )
            except subprocess.TimeoutExpired:
                # A timeout is not a transient API blip; retrying the same long call just wastes
                # minutes. Fail fast so the run escalates after one window, not 4 attempts.
                raise LLMError(f"claude CLI timed out after {to}s")

            data = None
            if proc is not None and proc.stdout.strip():
                try:
                    data = json.loads(proc.stdout.strip())
                except json.JSONDecodeError:
                    last_err = f"could not parse claude JSON: {proc.stdout[:200]!r}"
            elif proc is not None:
                last_err = f"claude CLI exit {proc.returncode}: {proc.stderr.strip()[:300]}"

            if data is not None and not data.get("is_error"):
                usage = data.get("usage") or {}
                return LLMResponse(
                    text=data.get("result", "") or "",
                    cost_usd=float(data.get("total_cost_usd", 0.0) or 0.0),
                    input_tokens=int(usage.get("input_tokens", 0) or 0),
                    output_tokens=int(usage.get("output_tokens", 0) or 0),
                    is_error=False, raw=data,
                )

            if data is not None and data.get("is_error"):
                last_err = f"claude API error: {(data.get('result') or '')[:200]}"
                if not self._is_transient(data):
                    break  # non-transient (e.g. auth) — fail fast

            if attempt < self.max_retries:
                time.sleep(self.retry_base * (2 ** attempt))  # 2s, 4s, 8s

        raise LLMError(last_err)


class FakeLLM(LLMBackend):
    """Returns scripted responses; records calls. For hermetic tests only."""

    def __init__(self, responses):
        # responses: text, LLMResponse, or an Exception (to simulate a failure).
        # The last entry repeats.
        self._responses = [
            r if isinstance(r, (LLMResponse, Exception)) else LLMResponse(text=r, cost_usd=0.01)
            for r in responses
        ]
        self.calls: list[dict] = []
        self._i = 0

    def complete(self, prompt, *, system=None, model=None, cwd=None,
                 permission_mode="default", extra_args=None, timeout=None) -> LLMResponse:
        self.calls.append({"prompt": prompt, "system": system, "model": model,
                           "cwd": cwd, "permission_mode": permission_mode, "timeout": timeout})
        r = self._responses[min(self._i, len(self._responses) - 1)]
        self._i += 1
        if isinstance(r, Exception):
            raise r
        return r


_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def extract_json(text: str) -> dict:
    """Best-effort parse of a JSON object from a model response.

    Handles bare JSON, ```json fenced blocks, and prose with an embedded object.
    Raises LLMError if nothing parseable is found.
    """
    candidates = []
    m = _FENCE.search(text)
    if m:
        candidates.append(m.group(1))
    candidates.append(text)
    # last resort: slice from first { to last }
    if "{" in text and "}" in text:
        candidates.append(text[text.index("{"): text.rindex("}") + 1])
    for c in candidates:
        try:
            obj = json.loads(c.strip())
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            continue
    raise LLMError(f"no JSON object found in model response: {text[:200]!r}")
