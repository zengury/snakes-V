"""
LifecycleRepo — per-robot per-profile Git repository management.

Each robot+profile pair gets its own branch in the workspace repo.
Every tuning experiment is a Git commit. Rollback = git reset --hard.

Crash recovery: EXPERIMENT_IN_PROGRESS sentinel file is written before
each commit and removed after. On init, stale sentinel triggers rollback.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional


_SENTINEL = "EXPERIMENT_IN_PROGRESS"


class LifecycleRepo:
    """Git-backed workspace for one robot + one profile."""

    def __init__(
        self,
        robot_id: str,
        profile_name: str,
        base_dir: str = "storage/repos",
    ) -> None:
        self.robot_id = robot_id
        self.profile_name = profile_name
        self._repo_dir = Path(base_dir) / robot_id
        self._branch = f"profile/{profile_name}"
        self._repo: Optional[Any] = None

    # ---------------------------------------------------------------- setup

    def init(self) -> None:
        """Initialise the Git repo and branch, recover from crash if needed."""
        import git

        self._repo_dir.mkdir(parents=True, exist_ok=True)
        if not (self._repo_dir / ".git").exists():
            repo = git.Repo.init(self._repo_dir)
            # Initial commit so branch operations work
            readme = self._repo_dir / "README.md"
            readme.write_text(f"# Robot: {self.robot_id}\n")
            repo.index.add(["README.md"])
            repo.index.commit(f"init: robot {self.robot_id}")
            self._repo = repo
        else:
            self._repo = git.Repo(self._repo_dir)

        # Ensure the profile branch exists
        if self._branch not in [b.name for b in self._repo.branches]:
            self._repo.git.checkout("-b", self._branch)
        else:
            self._repo.git.checkout(self._branch)

        # Crash recovery
        sentinel = self._repo_dir / _SENTINEL
        if sentinel.exists():
            self._repo.git.reset("--hard", "HEAD")
            sentinel.unlink()

    # --------------------------------------------------------------- commit

    def write_and_commit(
        self, filename: str, content: Dict[str, Any], message: str
    ) -> str:
        """Write content as JSON and commit. Returns commit sha."""
        assert self._repo is not None, "Call init() first"
        sentinel = self._repo_dir / _SENTINEL
        sentinel.write_text(datetime.now().isoformat())
        try:
            target = self._repo_dir / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(content, indent=2, default=str))
            self._repo.index.add([filename])
            commit = self._repo.index.commit(message)
            sentinel.unlink()
            return commit.hexsha[:8]
        except Exception:
            sentinel.unlink(missing_ok=True)
            raise

    def rollback(self, commits: int = 1) -> None:
        """Roll back N commits on the profile branch."""
        assert self._repo is not None
        self._repo.git.reset("--hard", f"HEAD~{commits}")

    def get_log(self, n: int = 10) -> list:
        assert self._repo is not None
        return [
            {"sha": c.hexsha[:8], "message": c.message.strip(), "ts": str(c.committed_datetime)}
            for c in list(self._repo.iter_commits(self._branch, max_count=n))
        ]

    @property
    def path(self) -> Path:
        return self._repo_dir
