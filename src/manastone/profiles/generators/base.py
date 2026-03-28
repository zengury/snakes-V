"""Base experiment generator ABC."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict


@dataclass
class ExperimentSpec:
    setpoint: float
    duration_s: float
    sample_rate_hz: float
    metadata: Dict[str, object] = field(default_factory=dict)


class BaseGenerator(ABC):
    @abstractmethod
    def generate(self, joint_name: str, group: str) -> ExperimentSpec:
        ...
