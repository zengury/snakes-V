"""PIDWorkspace — git-backed per-joint experiment workspace."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional

import yaml

from manastone.common.models import PIDParams
from manastone.profiles.scorers.base import ScorerResult

_TSV_HEADER = "exp_num\tstatus\tkeep\tkp\tki\tkd\tscore\tgrade\tovershoot_pct\trise_time_s\tsettling_time_s\tsse_rad\toscillation_count\thypothesis\n"


class PIDWorkspace:
    """Git-backed workspace for one joint's PID experiments.

    Directory layout: storage/pid_workspace/{robot_id}/{joint_name}/
    Files: program.md, params.yaml, results.tsv, EXPERIMENT_IN_PROGRESS (sentinel)
    """

    def __init__(self, robot_id: str, joint_name: str, storage_dir: Path) -> None:
        self.joint_name = joint_name
        self.robot_id = robot_id
        self.root = storage_dir / "pid_workspace" / robot_id / joint_name
        self.root.mkdir(parents=True, exist_ok=True)
        self._git_available = bool(shutil.which("git"))
        self._init_workspace()

    # ---------------------------------------------------------------- init

    def _init_workspace(self) -> None:
        """A4: detect sentinel on startup → git reset --hard HEAD."""
        sentinel = self.root / "EXPERIMENT_IN_PROGRESS"
        if sentinel.exists() and self._git_available:
            subprocess.run(
                ["git", "reset", "--hard", "HEAD"],
                cwd=self.root,
                check=False,
                capture_output=True,
            )
            sentinel.unlink(missing_ok=True)
        elif sentinel.exists():
            sentinel.unlink(missing_ok=True)

        git_dir = self.root / ".git"
        if not git_dir.exists():
            if self._git_available:
                subprocess.run(["git", "init"], cwd=self.root, check=True, capture_output=True)
                self._write_initial_files()
                subprocess.run(["git", "add", "."], cwd=self.root, check=True, capture_output=True)
                subprocess.run(
                    ["git", "-c", "user.email=manastone@robot", "-c", "user.name=Manastone",
                     "commit", "-m", "init"],
                    cwd=self.root, check=True, capture_output=True,
                )
            else:
                self._write_initial_files()

    def _write_initial_files(self) -> None:
        params_path = self.root / "params.yaml"
        if not params_path.exists():
            default_pid = PIDParams(kp=5.0, ki=0.1, kd=0.5)
            params_path.write_text(self._pid_to_yaml(default_pid, "# initial params"))

        results_path = self.root / "results.tsv"
        if not results_path.exists():
            results_path.write_text(_TSV_HEADER)

        program_path = self.root / "program.md"
        if not program_path.exists():
            program_path.write_text(
                f"# PID Workspace: {self.joint_name}\n\nAutoResearch workspace.\n"
            )

    # ---------------------------------------------------------------- params

    def _pid_to_yaml(self, pid: PIDParams, hypothesis: str = "") -> str:
        lines = []
        if hypothesis:
            lines.append(f"# hypothesis: {hypothesis}")
        lines.append(f"kp: {pid.kp}")
        lines.append(f"ki: {pid.ki}")
        lines.append(f"kd: {pid.kd}")
        return "\n".join(lines) + "\n"

    def write_params(self, pid: PIDParams, hypothesis: str = "") -> None:
        params_path = self.root / "params.yaml"
        params_path.write_text(self._pid_to_yaml(pid, hypothesis))

    def read_params(self) -> PIDParams:
        params_path = self.root / "params.yaml"
        if not params_path.exists():
            return PIDParams(kp=5.0, ki=0.1, kd=0.5)
        raw = yaml.safe_load(params_path.read_text())
        if not isinstance(raw, dict):
            return PIDParams(kp=5.0, ki=0.1, kd=0.5)
        return PIDParams(
            kp=float(raw.get("kp", 5.0)),
            ki=float(raw.get("ki", 0.1)),
            kd=float(raw.get("kd", 0.5)),
        )

    # ---------------------------------------------------------------- results

    def get_results_tsv_tail(self, n: int = 15) -> str:
        results_path = self.root / "results.tsv"
        if not results_path.exists():
            return ""
        lines = results_path.read_text().strip().split("\n")
        # Header + last n data rows
        header = lines[0] if lines else ""
        data_lines = lines[1:] if len(lines) > 1 else []
        tail = data_lines[-n:]
        return "\n".join([header] + tail)

    # ---------------------------------------------------------------- commit

    def commit_experiment(
        self,
        exp_num: int,
        hypothesis: str,
        result: ScorerResult,
        pid: PIDParams,
        status: str,
        keep: bool,
    ) -> None:
        """A4: write sentinel → commit → remove sentinel."""
        sentinel = self.root / "EXPERIMENT_IN_PROGRESS"
        sentinel.write_text("in_progress")
        try:
            # Append to results.tsv
            results_path = self.root / "results.tsv"
            # Ensure header exists
            if not results_path.exists() or results_path.read_text().strip() == "":
                results_path.write_text(_TSV_HEADER)

            hyp_clean = hypothesis.replace("\t", " ").replace("\n", " ")
            row = (
                f"{exp_num}\t{status}\t{int(keep)}\t"
                f"{pid.kp:.4f}\t{pid.ki:.4f}\t{pid.kd:.4f}\t"
                f"{result.score:.2f}\t{result.grade}\t"
                f"{result.overshoot_pct:.2f}\t{result.rise_time_s:.4f}\t"
                f"{result.settling_time_s:.4f}\t{result.sse_rad:.6f}\t"
                f"{result.oscillation_count}\t{hyp_clean}\n"
            )
            with open(results_path, "a") as f:
                f.write(row)

            if not keep:
                # Rollback params.yaml to last committed state
                self._git_checkout_params()

            if self._git_available:
                subprocess.run(["git", "add", "."], cwd=self.root, check=False, capture_output=True)
                msg = f"exp {exp_num}: score={result.score:.1f} grade={result.grade} keep={keep}"
                subprocess.run(
                    ["git", "-c", "user.email=manastone@robot", "-c", "user.name=Manastone",
                     "commit", "-m", msg],
                    cwd=self.root, check=False, capture_output=True,
                )
                if keep:
                    self._git_tag(f"best_{exp_num}")
        finally:
            sentinel.unlink(missing_ok=True)

    def _git_checkout_params(self) -> None:
        """Rollback params.yaml to last committed version."""
        if self._git_available:
            subprocess.run(
                ["git", "checkout", "HEAD", "--", "params.yaml"],
                cwd=self.root, check=False, capture_output=True,
            )

    def _git_tag(self, tag_name: str) -> None:
        if self._git_available:
            # Delete existing tag if any (ignore error)
            subprocess.run(
                ["git", "tag", "-d", tag_name],
                cwd=self.root, check=False, capture_output=True,
            )
            subprocess.run(
                ["git", "tag", tag_name],
                cwd=self.root, check=False, capture_output=True,
            )

    def rollback_params(self) -> None:
        self._git_checkout_params()

    def tag_chain_start(self, chain_name: str) -> None:
        self._git_tag(f"chain_{chain_name}_start")

    def tag_chain_best(self, chain_name: str, n: int) -> None:
        self._git_tag(f"chain_{chain_name}_best_{n}")

    # ---------------------------------------------------------------- helpers

    def get_experiment_count(self) -> int:
        results_path = self.root / "results.tsv"
        if not results_path.exists():
            return 0
        lines = results_path.read_text().strip().split("\n")
        return max(0, len(lines) - 1)  # subtract header

    def get_best_params(self) -> Optional[PIDParams]:
        """Return the PID params from the highest-scoring kept experiment."""
        results_path = self.root / "results.tsv"
        if not results_path.exists():
            return None
        lines = results_path.read_text().strip().split("\n")
        if len(lines) < 2:
            return None

        best_score = -1.0
        best_pid = None
        for line in lines[1:]:  # skip header
            parts = line.split("\t")
            if len(parts) < 7:
                continue
            try:
                keep = int(parts[2])
                if keep == 0:
                    continue
                score = float(parts[6])
                kp = float(parts[3])
                ki = float(parts[4])
                kd = float(parts[5])
                if score > best_score:
                    best_score = score
                    best_pid = PIDParams(kp=kp, ki=ki, kd=kd)
            except (ValueError, IndexError):
                continue
        return best_pid
