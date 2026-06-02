"""The Workspace — the land the government operates on (a git repo).

Each implementation attempt happens on its own branch off ``main``; a successful
review merges that branch (``--no-ff``) so every change is an isolated, revertible
commit — the rollback story the PRD relies on. Per-attempt branches are also what
makes the orchestrator worktree-ready for Phase-3 parallel PRDs.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from .models import Diff


class Workspace:
    """Interface. The orchestrator only depends on these four methods + ``path``."""

    path: Path

    def start_change(self, branch: str) -> None: ...
    def apply(self, diff: Diff) -> None: ...
    def merge(self, message: str) -> str: ...
    def discard(self) -> None: ...


class GitWorkspace(Workspace):
    def __init__(self, path: str | Path, main_branch: str = "main"):
        self.path = Path(path)
        self.main_branch = main_branch
        self._current: str | None = None
        self._ensure_repo()

    # --- git plumbing ---------------------------------------------------
    def _git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args],
            cwd=str(self.path),
            check=check,
            capture_output=True,
            text=True,
        )

    def _ensure_repo(self) -> None:
        self.path.mkdir(parents=True, exist_ok=True)
        if not (self.path / ".git").exists():
            self._git("init", "-b", self.main_branch)
            self._git("config", "user.email", "executive@polis.local")
            self._git("config", "user.name", "Polis Executive")
            # Ignore test/runtime artifacts so running the suite doesn't dirty the
            # tree (a regenerated .pyc must never block a branch switch / merge).
            (self.path / ".gitignore").write_text(
                "__pycache__/\n*.py[cod]\n.pytest_cache/\n", encoding="utf-8"
            )
            (self.path / "README.md").write_text("# Polis workspace\n", encoding="utf-8")
            self._git("add", "-A")
            self._git("commit", "-m", "genesis")

    def head(self) -> str:
        return self._git("rev-parse", "HEAD").stdout.strip()

    # --- Workspace interface -------------------------------------------
    def start_change(self, branch: str) -> None:
        # -f: start every attempt from a clean main, discarding any stray state.
        self._git("checkout", "-f", self.main_branch)
        self._git("checkout", "-B", branch)
        self._current = branch

    def apply(self, diff: Diff) -> None:
        for change in diff.changes:
            dest = self.path / change.path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(change.content, encoding="utf-8")
        self._git("add", "-A")
        # Commit the work-in-progress on the feature branch so there is something to
        # merge (and so a failed attempt can be discarded by deleting the branch).
        self._git("commit", "-m", diff.summary or "wip", check=False)

    def merge(self, message: str) -> str:
        if not self._current:
            raise RuntimeError("merge() called with no active change branch")
        branch = self._current
        self._git("checkout", self.main_branch)
        self._git("merge", "--no-ff", branch, "-m", message)
        sha = self.head()
        self._git("branch", "-D", branch, check=False)
        self._current = None
        return sha

    def discard(self) -> None:
        # We are intentionally abandoning this attempt: force back to main and
        # delete the branch so the next attempt (or escalation) starts clean.
        self._git("checkout", "-f", self.main_branch, check=False)
        if self._current:
            self._git("branch", "-D", self._current, check=False)
        self._current = None
