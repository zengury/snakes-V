"""Torque tracking scorer — scores based on torque smoothness."""

from __future__ import annotations

import math
from typing import List, Tuple

from manastone.profiles.scorers.base import BaseScorer, ScorerResult


class TorqueScorer(BaseScorer):
    """Scores based on torque smoothness. Low torque std → high score.

    score = 100 - (torque_std / max_torque_nm) * 100, clamped 0-100.
    """

    def __init__(self, max_torque_nm: float = 60.0) -> None:
        self.max_torque_nm = max_torque_nm

    def score(
        self, data: List[Tuple[float, float, float, float]], setpoint: float
    ) -> ScorerResult:
        if not data:
            return ScorerResult(
                score=0.0,
                grade="F",
                overshoot_pct=0.0,
                rise_time_s=0.0,
                settling_time_s=0.0,
                sse_rad=0.0,
                oscillation_count=0,
            )

        torques = [d[3] for d in data]
        mean_t = sum(torques) / len(torques)
        variance = sum((t - mean_t) ** 2 for t in torques) / len(torques)
        torque_std = math.sqrt(variance)

        raw_score = 100.0 - (torque_std / max(self.max_torque_nm, 1e-9)) * 100.0
        final_score = max(0.0, min(100.0, raw_score))

        # Derive grade
        if final_score >= 90:
            grade = "A"
        elif final_score >= 75:
            grade = "B"
        elif final_score >= 60:
            grade = "C"
        elif final_score >= 45:
            grade = "D"
        else:
            grade = "F"

        # Compute basic step metrics for completeness
        positions = [d[1] for d in data]
        max_pos = max(positions)
        overshoot_pct = max(0.0, (max_pos - setpoint) / abs(setpoint) * 100.0) if setpoint else 0.0
        last_pos = positions[-1]
        sse_rad = abs(last_pos - setpoint)

        return ScorerResult(
            score=final_score,
            grade=grade,
            overshoot_pct=overshoot_pct,
            rise_time_s=0.0,
            settling_time_s=0.0,
            sse_rad=sse_rad,
            oscillation_count=0,
        )
