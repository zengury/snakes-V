"""RuntimePredictor — embedded in core_server. Provides ±5% real-time PID nudges."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from manastone.common.models import JointContext, PIDParams
from .model import PIDPredictor


class RuntimePredictor:
    """Embedded in core_server. Provides ±5% real-time PID nudges."""

    def __init__(self, robot_id: str, storage_dir: Path):
        self.robot_id = robot_id
        self.storage_dir = storage_dir
        self._predictor = PIDPredictor()
        self._load_latest()

    def _load_latest(self) -> None:
        predictor_dir = self.storage_dir / "predictors" / self.robot_id
        if not predictor_dir.exists():
            return
        files = sorted(predictor_dir.glob("single_v*.json"), reverse=True)
        if files:
            self._predictor = PIDPredictor.load(files[0])

    async def suggest(self, joint_name: str, ctx: JointContext) -> Optional[PIDParams]:
        """Return adjusted PID if anomaly > 0.3, else None."""
        if ctx.anomaly_score < 0.3:
            return None
        if not self._predictor.is_trained:
            return None
        if ctx.last_params is None:
            return None

        dkp, dki, dkd = self._predictor.predict_delta(ctx)
        # Runtime: 50% decay + max 5% change
        return ctx.last_params.apply_delta(
            dkp * 0.5, dki * 0.5, dkd * 0.5, max_change_pct=0.05
        )

    async def reload(self) -> None:
        self._load_latest()
