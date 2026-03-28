"""Energy efficiency scorer — scores based on energy consumption."""

from __future__ import annotations

from typing import List, Tuple

from manastone.profiles.scorers.base import BaseScorer, ScorerResult


class EnergyScorer(BaseScorer):
    """Scores based on energy efficiency.

    score = max(0, 100 - sum(|torque*vel|*dt) / energy_budget * 100)
    """

    def __init__(self, energy_budget_j: float = 10.0) -> None:
        self.energy_budget_j = energy_budget_j

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

        # Compute energy = integral of |torque * velocity| * dt
        total_energy = 0.0
        for i in range(1, len(data)):
            dt = data[i][0] - data[i - 1][0]
            torque = data[i][3]
            velocity = data[i][2]
            total_energy += abs(torque * velocity) * dt

        final_score = max(0.0, 100.0 - total_energy / max(self.energy_budget_j, 1e-9) * 100.0)
        final_score = min(100.0, final_score)

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

        positions = [d[1] for d in data]
        max_pos = max(positions)
        overshoot_pct = max(0.0, (max_pos - setpoint) / abs(setpoint) * 100.0) if setpoint else 0.0
        sse_rad = abs(positions[-1] - setpoint)

        return ScorerResult(
            score=final_score,
            grade=grade,
            overshoot_pct=overshoot_pct,
            rise_time_s=0.0,
            settling_time_s=0.0,
            sse_rad=sse_rad,
            oscillation_count=0,
        )
