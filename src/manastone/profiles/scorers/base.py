"""Base scorer ABC for PID tuning profiles."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Tuple


@dataclass
class ScorerResult:
    score: float  # 0-100
    grade: str  # A/B/C/D/F
    overshoot_pct: float
    rise_time_s: float
    settling_time_s: float
    sse_rad: float
    oscillation_count: int


class BaseScorer(ABC):
    @abstractmethod
    def score(
        self, data: List[Tuple[float, float, float, float]], setpoint: float
    ) -> ScorerResult:
        """Score time-series data [(t, pos, vel, torque), ...] against setpoint."""
        ...
