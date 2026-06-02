"""Parallel workspaces via git worktrees (Phase 3).

Each in-flight PRD gets its own worktree — a separate working directory on its own
branch, sharing one underlying repo. Devs edit in isolation; merges back into ``main``
are serialized through a single lock so two runs never update main at once. A merge
that can't apply cleanly (two runs touched the same lines) raises ``MergeConflict``,
which the orchestrator turns into an ESCALATE.
"""

from __future__ import annotations

import subprocess
import threading
from pathlib import Path

from .models import FileChange
from .workspace import GitWorkspace, MergeConflict, Workspace


class WorktreeWorkspace(Workspace):
    def __init__(self, manager: "WorktreeManager", path, run_branch: str,
                 main_branch: str = "main"):
        self.manager = manager
        self.path = Path(path)
        self.run_branch = run_branch
        self.main_branch = main_branch
        self._current: str | None = None

    def _git(self, *args, check=True):
        return subprocess.run(["git", *args], cwd=str(self.path),
                              check=check, capture_output=True, text=True)

    def start_change(self, branch: str) -> None:
        # New attempt branched from the LATEST main (picks up others' merges).
        self._git("checkout", "-B", branch, self.main_branch)
        self._current = branch

    def apply(self, diff) -> None:
        for change in diff.changes:
            dest = self.path / change.path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(change.content, encoding="utf-8")
        self._git("add", "-A")
        self._git("commit", "-m", diff.summary or "wip", check=False)

    def changed_files(self) -> list[FileChange]:
        out = self._git("status", "--porcelain").stdout
        changes: list[FileChange] = []
        for line in out.splitlines():
            if not line.strip():
                continue
            status, path = line[:2], line[3:].strip().strip('"')
            if "D" in status:
                continue
            if " -> " in path:
                path = path.split(" -> ", 1)[1]
            fp = self.path / path
            if not fp.is_file():
                continue
            try:
                content = fp.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            changes.append(FileChange(path.replace("\\", "/"), content))
        return changes

    def merge(self, message: str) -> str:
        if not self._current:
            raise RuntimeError("merge() called with no active change branch")
        sha = self.manager.merge_branch(self._current, message)  # serialized; may raise
        self._current = None
        return sha

    def discard(self) -> None:
        self._git("reset", "--hard", self.main_branch, check=False)
        self._git("clean", "-fd", check=False)
        self._current = None

    def deploy_dir(self) -> Path:
        # Deploy runs against merged main, which lives in the primary repo.
        return self.manager.repo_path


class WorktreeManager:
    def __init__(self, repo_path, work_root=None, main_branch: str = "main"):
        # GitWorkspace ensures the primary repo exists (genesis + .gitignore on main).
        self._primary = GitWorkspace(repo_path, main_branch=main_branch)
        self.repo_path = self._primary.path
        self.main_branch = main_branch
        self.work_root = (Path(work_root).resolve() if work_root
                          else self.repo_path.parent / ".polis-worktrees")
        self.work_root.mkdir(parents=True, exist_ok=True)
        self._merge_lock = threading.Lock()  # only one run updates main at a time
        self._wt_lock = threading.Lock()      # serialize worktree add/remove

    def _git(self, *args, check=True):
        return self._primary._git(*args, check=check)

    def create_worktree(self, run_id: str) -> WorktreeWorkspace:
        branch = f"polis/{run_id}"
        path = self.work_root / run_id
        with self._wt_lock:
            self._git("worktree", "remove", "--force", str(path), check=False)
            self._git("worktree", "add", "-B", branch, str(path), self.main_branch)
        return WorktreeWorkspace(self, path, branch, self.main_branch)

    def merge_branch(self, branch: str, message: str) -> str:
        with self._merge_lock:
            self._git("checkout", self.main_branch, check=False)
            r = self._git("merge", "--no-ff", branch, "-m", message, check=False)
            if r.returncode != 0:
                self._git("merge", "--abort", check=False)
                raise MergeConflict(f"{branch}: {r.stdout.strip()[:200]}")
            return self._git("rev-parse", "HEAD").stdout.strip()

    def remove_worktree(self, ws: WorktreeWorkspace) -> None:
        with self._wt_lock:
            self._git("worktree", "remove", "--force", str(ws.path), check=False)
            self._git("worktree", "prune", check=False)
