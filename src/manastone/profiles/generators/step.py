"""Step input generator."""

from __future__ import annotations

from manastone.profiles.generators.base import BaseGenerator, ExperimentSpec


class StepGenerator(BaseGenerator):
    """Generates a step response experiment spec."""

    def __init__(
        self,
        setpoint: float = 0.3,
        duration_s: float = 2.0,
        sample_rate_hz: float = 100.0,
    ) -> None:
        self.setpoint = setpoint
        self.duration_s = duration_s
        self.sample_rate_hz = sample_rate_hz

    def generate(self, joint_name: str, group: str) -> ExperimentSpec:
        return ExperimentSpec(
            setpoint=self.setpoint,
            duration_s=self.duration_s,
            sample_rate_hz=self.sample_rate_hz,
            metadata={"type": "step", "joint_name": joint_name, "group": group},
        )
