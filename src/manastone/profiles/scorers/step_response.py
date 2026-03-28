"""Step response scorer — evaluates PID performance via step response metrics."""

from __future__ import annotations

from typing import List, Tuple

from manastone.profiles.scorers.base import BaseScorer, ScorerResult


class StepResponseScorer(BaseScorer):
    """Scores a step response based on overshoot, rise time, settling time, SSE, oscillations.

    Score formula from DD-C04:
        score  = 100.0
        score -= max(0, overshoot_pct - 5.0) * 1.0
        score -= max(0, rise_time_s - 0.5)  * 20.0
        score -= max(0, settling_time_s - 1.0) * 25.0
        score -= max(0, sse_rad - 0.02) * 100.0
        score -= oscillation_count * 3.0
        score  = clamp(score, 0, 100)
    """

    def score(
        self, data: List[Tuple[float, float, float, float]], setpoint: float
    ) -> ScorerResult:
        if not data or setpoint == 0.0:
            return ScorerResult(
                score=0.0,
                grade="F",
                overshoot_pct=0.0,
                rise_time_s=99.0,
                settling_time_s=99.0,
                sse_rad=99.0,
                oscillation_count=0,
            )

        times = [d[0] for d in data]
        positions = [d[1] for d in data]
        velocities = [d[2] for d in data]

        # Overshoot
        max_pos = max(positions)
        overshoot_pct = max(0.0, (max_pos - setpoint) / abs(setpoint) * 100.0)

        # Rise time: first time from 10% to 90% of setpoint
        t10 = None
        t90 = None
        p10 = 0.10 * setpoint
        p90 = 0.90 * setpoint
        for t, pos in zip(times, positions):
            if t10 is None and pos >= p10:
                t10 = t
            if t90 is None and pos >= p90:
                t90 = t
                break
        if t10 is not None and t90 is not None and t90 > t10:
            rise_time_s = t90 - t10
        elif t90 is not None and t10 is None:
            rise_time_s = t90 - times[0]
        else:
            rise_time_s = 99.0

        # Settling time: last time position exits ±2% band around setpoint
        band = 0.02 * abs(setpoint)
        settling_time_s = times[-1]  # default to end if never settles
        last_outside = None
        for t, pos in zip(times, positions):
            if abs(pos - setpoint) > band:
                last_outside = t
        if last_outside is not None:
            settling_time_s = last_outside
        else:
            settling_time_s = 0.0  # settled from the start

        # SSE: mean of last 200ms positions
        dt = times[1] - times[0] if len(times) > 1 else 0.01
        n_last = max(1, int(0.2 / dt))
        last_positions = positions[-n_last:]
        sse_rad = abs(sum(last_positions) / len(last_positions) - setpoint)

        # Oscillation count: zero crossings in velocity / 2
        zero_crossings = 0
        for i in range(1, len(velocities)):
            if velocities[i - 1] * velocities[i] < 0:
                zero_crossings += 1
        oscillation_count = zero_crossings // 2

        # Score formula DD-C04
        score = 100.0
        score -= max(0.0, overshoot_pct - 5.0) * 1.0
        score -= max(0.0, rise_time_s - 0.5) * 20.0
        score -= max(0.0, settling_time_s - 1.0) * 25.0
        score -= max(0.0, sse_rad - 0.02) * 100.0
        score -= oscillation_count * 3.0
        score = max(0.0, min(100.0, score))

        # Grade
        if score >= 90:
            grade = "A"
        elif score >= 75:
            grade = "B"
        elif score >= 60:
            grade = "C"
        elif score >= 45:
            grade = "D"
        else:
            grade = "F"

        return ScorerResult(
            score=score,
            grade=grade,
            overshoot_pct=overshoot_pct,
            rise_time_s=rise_time_s,
            settling_time_s=settling_time_s,
            sse_rad=sse_rad,
            oscillation_count=oscillation_count,
        )
