"""Sinusoidal input generator."""

from __future__ import annotations

from typing import List

from manastone.profiles.generators.base import BaseGenerator, ExperimentSpec


class SinusoidalGenerator(BaseGenerator):
    """Generates a sinusoidal sweep experiment spec."""

    def __init__(
        self,
        amplitude: float = 0.17,
        frequencies: List[float] = None,
        duration_s: float = 5.0,
        sample_rate_hz: float = 100.0,
    ) -> None:
        self.amplitude = amplitude
        self.frequencies = frequencies if frequencies is not None else [0.5, 1.0, 2.0]
        self.duration_s = duration_s
        self.sample_rate_hz = sample_rate_hz

    def generate(self, joint_name: str, group: str) -> ExperimentSpec:
        return ExperimentSpec(
            setpoint=self.amplitude,
            duration_s=self.duration_s,
            sample_rate_hz=self.sample_rate_hz,
            metadata={
                "type": "sinusoidal",
                "frequencies": self.frequencies,
                "amplitude": self.amplitude,
                "joint_name": joint_name,
                "group": group,
            },
        )
