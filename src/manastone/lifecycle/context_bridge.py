"""
ContextBridge — builds JointContext and ChainContext from runtime data.

export_from_commissioning: called after commissioning completes.
load_for_runtime: loads saved InitialContext on startup.
build_tuning_context: assembles 19-field JointContext for idle tuning.
build_chain_context: assembles ChainContext from joint contexts.
"""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import yaml

from manastone.common.config import load_robot_schema
from manastone.common.models import (
    ChainContext,
    CommissioningResult,
    InitialContext,
    JointContext,
    PIDParams,
)
from manastone.runtime.anomaly_scorer import AnomalyScorer
from manastone.runtime.event_store import event_store
from manastone.runtime.ring_buffer import ring_buffer_manager


class ContextBridge:
    CONTEXT_DIR = Path("storage/contexts")
    PID_WORKSPACE_DIR = Path("storage/pid_workspace")

    # --------------------------------------------------------- commissioning

    def export_from_commissioning(self, robot_id: str) -> InitialContext:
        """Scan pid_workspace and build + persist InitialContext."""
        ctx = InitialContext(
            robot_id=robot_id,
            commissioning_date=datetime.now(),
        )
        if self.PID_WORKSPACE_DIR.exists():
            for joint_dir in sorted(self.PID_WORKSPACE_DIR.iterdir()):
                if not joint_dir.is_dir() or joint_dir.name.startswith("_chain"):
                    continue
                best_path = joint_dir / "best_params.yaml"
                if not best_path.exists():
                    continue
                best = yaml.safe_load(best_path.read_text())
                results_path = joint_dir / "results.tsv"
                history = self._load_results_tsv(results_path) if results_path.exists() else []
                ctx.joints[joint_dir.name] = CommissioningResult(
                    joint_name=joint_dir.name,
                    base_pid=PIDParams(**best["pid"]),
                    best_score=best.get("score", 0.0),
                    experiment_count=len(history),
                    research_log=self._get_git_log(joint_dir),
                    variance_allowance=0.15,
                    thermal_time_constant=45.0,
                )
        path = self._context_path(robot_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(ctx.model_dump_json(indent=2))
        return ctx

    def load_for_runtime(self, robot_id: str) -> Optional[InitialContext]:
        path = self._context_path(robot_id)
        if not path.exists():
            return None
        return InitialContext.model_validate_json(path.read_text())

    # ------------------------------------------------------------- contexts

    def build_tuning_context(self, robot_id: str, joint_name: str) -> JointContext:
        """Build a 19-field JointContext for a single joint."""
        cfg = load_robot_schema()
        joint_id = cfg.get_motor_index_map().get(joint_name, 0)
        group = cfg.get_joint_group(joint_name)

        # Runtime ring buffer data
        buf = ring_buffer_manager.get_buffer(joint_name)
        if buf:
            latest = buf.get_latest()
            window = buf.get_window(5.0)
        else:
            latest = None
            window = []

        position = latest[1] if latest else 0.0
        velocity = latest[2] if latest else 0.0
        torque = latest[3] if latest else 0.0

        # Event log
        recent_events = event_store.query_recent(joint_name=joint_name, hours=24)

        # Temperature from events
        temp_c = self._extract_temp(recent_events)

        # Anomaly score (pass a partial JointContext)
        partial = JointContext(
            joint_name=joint_name,
            joint_id=joint_id,
            group=group,
            temp_c=temp_c,
            torque_nm=torque,
            velocity_rad_s=velocity,
        )
        anomaly = AnomalyScorer().score(partial, recent_events)

        # InitialContext + tuning history
        initial = self.load_for_runtime(robot_id)
        initial_joint = initial.joints.get(joint_name) if initial else None

        last_params: Optional[PIDParams] = None
        hours_since_last_tune = 0.0
        tune_count = 0
        quality_trend: List[float] = []

        if initial_joint:
            last_params = initial_joint.base_pid
            if initial:
                hours_since_last_tune = (
                    datetime.now() - initial.commissioning_date
                ).total_seconds() / 3600.0

        hours_since_comm = (
            (datetime.now() - initial.commissioning_date).total_seconds() / 3600.0
            if initial
            else 0.0
        )

        tracking_error = self._compute_tracking_error(window, last_params, position)
        tracking_max = self._compute_max_tracking_error(window, last_params)
        efficiency = self._compute_efficiency(window)

        return JointContext(
            joint_name=joint_name,
            joint_id=joint_id,
            group=group,
            temp_c=temp_c,
            temp_trend=self._compute_temp_trend(recent_events),
            current_a=0.0,
            torque_nm=torque,
            velocity_rad_s=velocity,
            tracking_error_mean=tracking_error,
            tracking_error_max=tracking_max,
            torque_efficiency=efficiency,
            anomaly_score=anomaly,
            comm_lost_count=len(
                [e for e in recent_events if e.get("event_type") == "comm_lost"]
            ),
            hours_since_commissioning=hours_since_comm,
            hours_since_last_tune=hours_since_last_tune,
            tune_count=tune_count,
            last_params=last_params,
            quality_trend=quality_trend,
        )

    def build_chain_context(self, robot_id: str, chain_name: str) -> ChainContext:
        cfg = load_robot_schema()
        joint_names = cfg.get_chain_tuning_order(chain_name)
        joint_contexts = [self.build_tuning_context(robot_id, j) for j in joint_names]
        return ChainContext(
            chain_name=chain_name,
            joints=joint_contexts,
            chain_anomaly_score=max((jc.anomaly_score for jc in joint_contexts), default=0.0),
        )

    # ------------------------------------------------------------- helpers

    def _context_path(self, robot_id: str) -> Path:
        return self.CONTEXT_DIR / robot_id / "initial_context.json"

    def _load_results_tsv(self, path: Path) -> list:
        lines = path.read_text().strip().splitlines()
        return [l for l in lines if l and not l.startswith("#")]

    def _get_git_log(self, joint_dir: Path) -> List[str]:
        try:
            import subprocess
            result = subprocess.run(
                ["git", "log", "--oneline", "-10"],
                cwd=joint_dir,
                capture_output=True,
                text=True,
                timeout=5,
            )
            return result.stdout.strip().splitlines()
        except Exception:
            return []

    def _extract_temp(self, events: list) -> float:
        for e in events:
            if e.get("event_type") in ("joint_temp_warning", "joint_temp_critical"):
                return float(e.get("value", 25.0))
        return 25.0

    def _compute_temp_trend(self, events: list) -> float:
        # Simplified: count recent temp events per hour
        temp_events = [e for e in events if "temp" in e.get("event_type", "")]
        return float(len(temp_events)) * 0.1

    def _compute_tracking_error(
        self, window: list, last_params: Optional[PIDParams], position: float
    ) -> float:
        if not window or last_params is None:
            return 0.0
        errors = [abs(p - position) for _, p, _, _ in window]
        return sum(errors) / len(errors)

    def _compute_max_tracking_error(
        self, window: list, last_params: Optional[PIDParams]
    ) -> float:
        if not window:
            return 0.0
        return max(abs(v) for _, _, v, _ in window) if window else 0.0

    def _compute_efficiency(self, window: list) -> float:
        if not window:
            return 1.0
        products = [abs(v * e) for _, _, v, e in window if abs(v) > 1e-6]
        if not products:
            return 1.0
        avg = sum(products) / len(products)
        return min(1.0, 1.0 / (avg + 1e-6))
