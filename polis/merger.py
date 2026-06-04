"""Merge strategy — how a finished attempt branch gets into main.

Two strategies, injected via OrchestratorConfig so the orchestrator's flow is unchanged:
  * LocalMerger (default): the historical ``git merge --no-ff`` into the LOCAL main.
  * PullRequestMerger: push the branch, open a GitHub PR, wait for the required CI checks to
    go green, then squash-merge the PR. It never pushes to main directly and never rewrites
    local main under a human — Polis behaves like a careful contributor.

Both return the merged commit sha and raise ``MergeConflict`` on any failure (CI red, timeout,
no remote, push/merge error), which the orchestrator already turns into an ESCALATE.

Stdlib only (subprocess), so it follows the same pattern as GitWorkspace._git and is unit
testable by patching the single ``_run`` seam.
"""

from __future__ import annotations

import json
import subprocess
import time

from .workspace import MergeConflict, Workspace


class Merger:
    """Strategy for getting an attempt branch into main. Returns the merged commit sha."""

    def merge(self, workspace: Workspace, branch: str, message: str) -> str:
        raise NotImplementedError


class LocalMerger(Merger):
    """The historical behavior: a local ``git merge --no-ff`` into main (no remote)."""

    def merge(self, workspace: Workspace, branch: str, message: str) -> str:
        return workspace.merge(message)


class PullRequestMerger(Merger):
    """Push -> open PR -> wait for required CI -> squash-merge -> sync local main.

    Requires the workspace repo to have an ``origin`` remote on GitHub, the ``gh`` CLI
    authenticated, and CI configured. Never pushes to main directly.
    """

    _FAIL = ("fail", "cancel")
    _OK = ("pass", "skipping")

    def __init__(self, main_branch: str = "main", gh: str = "gh",
                 poll_interval: float = 10.0, timeout: float = 1800.0):
        self.main_branch = main_branch
        self.gh = gh
        self.poll_interval = poll_interval
        self.timeout = timeout

    # Single subprocess seam (tests patch this).
    def _run(self, cwd, *args, timeout: float = 120):
        return subprocess.run(list(args), cwd=str(cwd),
                              capture_output=True, text=True, timeout=timeout)

    def merge(self, workspace: Workspace, branch: str, message: str) -> str:
        cwd = workspace.path
        main = self.main_branch
        try:
            if self._run(cwd, "git", "remote", "get-url", "origin").returncode != 0:
                raise MergeConflict("merge_via_pr needs an 'origin' remote on the target repo")

            r = self._run(cwd, "git", "push", "-u", "origin", branch, timeout=300)
            if r.returncode != 0:
                raise MergeConflict(f"push failed: {(r.stderr or r.stdout or '')[:200]}")

            title = (message.splitlines()[0][:200] if message else branch)
            r = self._run(cwd, self.gh, "pr", "create", "--base", main, "--head", branch,
                          "--title", title, "--body", f"{message}\n\nOpened by Polis (automated).",
                          timeout=120)
            if r.returncode != 0:
                raise MergeConflict(f"gh pr create failed: {(r.stderr or r.stdout or '')[:200]}")
            pr = self._pr_ref(r.stdout, branch)

            self._await_checks(cwd, pr)

            r = self._run(cwd, self.gh, "pr", "merge", pr, "--squash", "--delete-branch",
                          timeout=180)
            if r.returncode != 0:
                raise MergeConflict(f"gh pr merge failed: {(r.stderr or r.stdout or '')[:200]}")

            # Sync local main so the next run branches from the merge; never push main.
            self._run(cwd, "git", "fetch", "origin", timeout=120)
            self._run(cwd, "git", "checkout", main, timeout=60)
            self._run(cwd, "git", "reset", "--hard", f"origin/{main}", timeout=60)
            self._run(cwd, "git", "branch", "-D", branch)  # best-effort cleanup
            head = self._run(cwd, "git", "rev-parse", "HEAD")
            return (head.stdout or "").strip() or pr
        except subprocess.TimeoutExpired as e:
            raise MergeConflict(f"git/gh timed out: {e}")

    @staticmethod
    def _pr_ref(stdout: str, branch: str) -> str:
        # `gh pr create` prints the PR URL on the last line; fall back to the branch.
        lines = [ln for ln in (stdout or "").splitlines() if ln.strip()]
        return lines[-1].strip() if lines else branch

    def _await_checks(self, cwd, pr: str) -> None:
        deadline = time.time() + self.timeout
        while True:
            r = self._run(cwd, self.gh, "pr", "checks", pr, "--required",
                          "--json", "name,bucket", timeout=60)
            try:
                checks = json.loads(r.stdout or "[]")
            except json.JSONDecodeError:
                checks = []
            buckets = [str(c.get("bucket", "")).lower() for c in checks]
            if any(b in self._FAIL for b in buckets):
                bad = [c.get("name") for c, b in zip(checks, buckets) if b in self._FAIL]
                raise MergeConflict(f"CI failed: {bad}")
            # Non-empty AND every required check is terminal-good -> green. Empty means the
            # checks haven't registered yet (or none required) -> keep waiting until timeout.
            if checks and all(b in self._OK for b in buckets):
                return
            if time.time() >= deadline:
                raise MergeConflict(f"CI not green within {int(self.timeout)}s")
            time.sleep(self.poll_interval)
