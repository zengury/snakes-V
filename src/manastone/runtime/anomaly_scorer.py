"""
AnomalyScorer — weighted 0-1 anomaly score per joint.

Rule-based, no ML. Six components:
  temp_normalized, torque_normalized, tracking_error_normalized,
  efficiency_inverted, comm_penalty, event_density
"""

from __future__ import annotations

from typing import Any, Dict, List

from manastone.common.models import JointContext


class AnomalyScorer:
    """Computes a 0-1 anomaly score for a single joint context."""

    WEIGHTS: Dict[str, float] = {
        "temp_normalized":             0.15,
        "torque_normalized":           0.20,
        "tracking_error_normalized":   0.25,
        "efficiency_inverted":         0.15,
        "comm_penalty":                0.10,
        "event_density":               0.15,
    }

    # Reference maxima for normalization
    _TEMP_MAX = 70.0
    _TORQUE_MAX = 60.0
    _TRACKING_MAX = 0.1
    _COMM_MAX = 5.0
    _EVENT_MAX = 20.0

    def score(
        self, joint_ctx: JointContext, recent_events: List[Dict[str, Any]]
    ) -> float:
        components: Dict[str, float] = self._compute_components(joint_ctx, recent_events)
        return sum(self.WEIGHTS[k] * v for k, v in components.items())

    def score_components(
        self, joint_ctx: JointContext, recent_events: List[Dict[str, Any]]
    ) -> Dict[str, float]:
        """Return per-component scores (useful for debugging)."""
        return self._compute_components(joint_ctx, recent_events)

    def _compute_components(
        self, joint_ctx: JointContext, recent_events: List[Dict[str, Any]]
    ) -> Dict[str, float]:
        # C2 fix: clamp torque_efficiency to [0, 1] before inversion.
        # Noisy sensors can return values > 1.0, which would make
        # efficiency_inverted go negative and suppress the anomaly score.
        eff_clamped = max(0.0, min(1.0, joint_ctx.torque_efficiency))
        return {
            "temp_normalized": min(joint_ctx.temp_c / self._TEMP_MAX, 1.0),
            "torque_normalized": min(abs(joint_ctx.torque_nm) / self._TORQUE_MAX, 1.0),
            "tracking_error_normalized": min(
                joint_ctx.tracking_error_mean / self._TRACKING_MAX, 1.0
            ),
            "efficiency_inverted": 1.0 - eff_clamped,  # always in [0, 1]
            "comm_penalty": min(joint_ctx.comm_lost_count / self._COMM_MAX, 1.0),
            "event_density": min(len(recent_events) / self._EVENT_MAX, 1.0),
        }
