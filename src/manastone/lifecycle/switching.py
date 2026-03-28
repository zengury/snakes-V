"""
ProfileSwitchingStrategy — logic for switching between TuningProfiles.

Strategies:
  manual:     only switch on explicit user command
  score_based: switch if current profile score drops below threshold
  anomaly_based: switch if anomaly score exceeds threshold

Phase-5 additions:
  should_switch() — async rule-based decision
  execute_switch() — async execution with stream event + param loading
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, List, Optional


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
        available_profiles: List[str],
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

    # ------------------------------------------------------------------
    # Phase-5: async rule-based API
    # ------------------------------------------------------------------

    async def should_switch(
        self,
        robot_id: str,
        current_profile: str,
        upcoming_context: dict,
    ) -> Optional[str]:
        """Return None to keep current, or new profile_id to switch."""

        # Rule 1: Explicit required_profile
        required = upcoming_context.get("required_profile")
        if required and required != current_profile:
            return required

        # Rule 2: Long idle -> energy_saver
        if upcoming_context.get("idle_duration_s", 0) > 300:
            if current_profile != "energy_saver":
                return "energy_saver"

        # Rule 3: Poor recent quality -> try compatible alternative
        if upcoming_context.get("recent_quality_score", 100) < 60:
            try:
                from manastone.profiles.registry import ProfileRegistry
                registry = ProfileRegistry()
                alternatives = registry.list_compatible(
                    joint_group=upcoming_context.get("joint_group"),
                    task_type=upcoming_context.get("task_type"),
                )
                alternatives = [a for a in alternatives if a != current_profile]
                if alternatives:
                    return alternatives[0]
            except Exception:
                pass

        return None

    async def execute_switch(
        self,
        robot_id: str,
        new_profile: str,
        reason: str,
        stream=None,
        param_writer=None,
        lifecycle_repo=None,
    ) -> dict:
        """Execute profile switch: git branch checkout + load params + emit stream event."""

        if lifecycle_repo:
            lifecycle_repo.switch_profile(new_profile)
            best_params = lifecycle_repo.get_best_params(new_profile)
            if best_params and param_writer:
                await param_writer.write_chain_params("all", best_params)

        if stream:
            from manastone.lifecycle.stream import StreamEvent
            stream.process_event(StreamEvent(
                robot_id=robot_id,
                profile_id=new_profile,
                event_type="profile_switched",
                payload={"reason": reason},
            ))

        return {"switched_to": new_profile, "reason": reason}
