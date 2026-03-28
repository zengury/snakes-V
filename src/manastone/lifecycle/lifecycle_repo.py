"""
LifecycleRepo — per-robot per-profile Git repository management.

Each robot+profile pair gets its own branch in the workspace repo.
Every tuning experiment is a Git commit. Rollback = git reset --hard.

Crash recovery: EXPERIMENT_IN_PROGRESS sentinel file is written before
each commit and removed after. On init, stale sentinel triggers rollback.

LifecycleRepository (Phase 5) — subprocess-based variant that does not
require the gitpython library; works even when git is unavailable.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from manastone.common.models import PIDParams


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


# ---------------------------------------------------------------------------
# Phase-5: LifecycleRepository
# ---------------------------------------------------------------------------


class LifecycleRepository:
    """Per-robot Git repo with per-profile branches (subprocess-based)."""

    def __init__(self, robot_id: str, base_dir: Path = Path("storage/workspaces")):
        self.robot_id = robot_id
        self.repo_path = Path(base_dir) / robot_id
        self._git_available = bool(shutil.which("git"))

    def init(self) -> None:
        self.repo_path.mkdir(parents=True, exist_ok=True)
        if not self._git_available:
            return
        if not (self.repo_path / ".git").exists():
            subprocess.run(["git", "init"], cwd=self.repo_path, check=True, capture_output=True)
            subprocess.run(
                ["git", "commit", "--allow-empty", "-m", "init"],
                cwd=self.repo_path, check=True, capture_output=True,
            )

    def create_profile_branch(self, profile_id: str) -> Path:
        """Create branch {robot_id}/{profile_id} and return profile work dir."""
        profile_dir = self.repo_path / profile_id
        profile_dir.mkdir(parents=True, exist_ok=True)

        if self._git_available and (self.repo_path / ".git").exists():
            branch = f"{self.robot_id}/{profile_id}"
            result = subprocess.run(
                ["git", "branch", "--list", branch],
                cwd=self.repo_path, capture_output=True, text=True,
            )
            if not result.stdout.strip():
                subprocess.run(
                    ["git", "checkout", "-b", branch],
                    cwd=self.repo_path, capture_output=True,
                )
            else:
                subprocess.run(
                    ["git", "checkout", branch],
                    cwd=self.repo_path, capture_output=True,
                )
        return profile_dir

    def switch_profile(self, profile_id: str) -> Path:
        branch = f"{self.robot_id}/{profile_id}"
        if self._git_available and (self.repo_path / ".git").exists():
            subprocess.run(["git", "checkout", branch], cwd=self.repo_path, capture_output=True)
        return self.repo_path / profile_id

    def get_best_params(self, profile_id: str) -> Optional[Dict[str, PIDParams]]:
        """Read best_params.yaml from profile dir."""
        path = self.repo_path / profile_id / "best_params.yaml"
        if path.exists():
            raw = yaml.safe_load(path.read_text())
            if isinstance(raw, dict):
                return {k: PIDParams(**v) for k, v in raw.items() if isinstance(v, dict)}
        return None

    def write_best_params(self, profile_id: str, params: Dict[str, PIDParams]) -> None:
        profile_dir = self.repo_path / profile_id
        profile_dir.mkdir(parents=True, exist_ok=True)
        path = profile_dir / "best_params.yaml"
        path.write_text(yaml.dump({k: v.model_dump() for k, v in params.items()}))

    def tag_version(self, profile_id: str, version: str, label: str = "stable") -> None:
        if self._git_available and (self.repo_path / ".git").exists():
            tag = f"{profile_id}/v{version}-{label}"
            subprocess.run(["git", "tag", tag], cwd=self.repo_path, capture_output=True)

    def list_profiles(self) -> List[str]:
        if not self._git_available or not (self.repo_path / ".git").exists():
            # Fallback: list directories
            if not self.repo_path.exists():
                return []
            return [d.name for d in self.repo_path.iterdir() if d.is_dir() and not d.name.startswith(".")]
        result = subprocess.run(
            ["git", "branch", "--list", f"{self.robot_id}/*"],
            cwd=self.repo_path, capture_output=True, text=True,
        )
        branches = []
        for b in result.stdout.strip().split("\n"):
            b = b.strip().lstrip("* ")
            if "/" in b:
                branches.append(b.split("/", 1)[-1])
        return [b for b in branches if b]
