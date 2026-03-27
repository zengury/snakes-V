"""
ProfileSwitchingStrategy — logic for switching between TuningProfiles.

Strategies:
  manual:     only switch on explicit user command
  score_based: switch if current profile score drops below threshold
  anomaly_based: switch if anomaly score exceeds threshold
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional


@dataclass
class SwitchDecision:
    should_switch: bool
    reason: str
    target_profile: Optional[str] = None


class ProfileSwitchingStrategy:
    """Determines when to switch the active TuningProfile."""

    def __init__(
        self,
        strategy: Literal["manual", "score_based", "anomaly_based"] = "manual",
        score_threshold: float = 60.0,
        anomaly_threshold: float = 0.8,
    ) -> None:
        self._strategy = strategy
        self._score_threshold = score_threshold
        self._anomaly_threshold = anomaly_threshold

    def evaluate(
        self,
        current_profile: str,
        available_profiles: list[str],
        latest_score: Optional[float] = None,
        chain_anomaly_score: Optional[float] = None,
    ) -> SwitchDecision:
        if self._strategy == "manual":
            return SwitchDecision(False, "Manual strategy: no auto-switch")

        if self._strategy == "score_based":
            if latest_score is not None and latest_score < self._score_threshold:
                candidates = [p for p in available_profiles if p != current_profile]
                if candidates:
                    return SwitchDecision(
                        True,
                        f"Score {latest_score:.1f} < threshold {self._score_threshold}",
                        target_profile=candidates[0],
                    )

        if self._strategy == "anomaly_based":
            if chain_anomaly_score is not None and chain_anomaly_score > self._anomaly_threshold:
                candidates = [p for p in available_profiles if p != current_profile]
                if "collision_safe" in candidates:
                    return SwitchDecision(
                        True,
                        f"Anomaly {chain_anomaly_score:.2f} > threshold {self._anomaly_threshold}",
                        target_profile="collision_safe",
                    )

        return SwitchDecision(False, "No switch criteria met")
