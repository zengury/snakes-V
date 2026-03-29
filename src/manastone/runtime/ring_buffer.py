"""
JointRingBuffer — sliding window of raw joint sensor data.

RingBufferManager manages one buffer per joint and routes
rosbridge /joint_states messages to the correct buffer.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# (timestamp_s, position_rad, velocity_rad_s, effort_nm)
Sample = Tuple[float, float, float, float]


class JointRingBuffer:
    """Fixed-duration sliding window for one joint."""

    def __init__(
        self,
        joint_name: str,
        max_duration_s: float = 30.0,
        sample_rate: float = 50.0,
    ) -> None:
        self.joint_name = joint_name
        self.max_samples = int(max_duration_s * sample_rate)
        self._buf: deque[Sample] = deque(maxlen=self.max_samples)

    def append(
        self, timestamp: float, position: float, velocity: float, effort: float
    ) -> None:
        self._buf.append((timestamp, position, velocity, effort))

    def get_window(self, duration_s: float) -> List[Sample]:
        """Return samples within the most recent duration_s seconds."""
        if not self._buf:
            return []
        cutoff = self._buf[-1][0] - duration_s
        return [(t, p, v, e) for t, p, v, e in self._buf if t >= cutoff]

    def get_latest(self) -> Optional[Sample]:
        return self._buf[-1] if self._buf else None

    def __len__(self) -> int:
        return len(self._buf)

    @property
    def max_capacity(self) -> int:
        return self.max_samples


class RingBufferManager:
    """Singleton-style manager for all joint ring buffers."""

    def __init__(self) -> None:
        self.buffers: Dict[str, JointRingBuffer] = {}
        self._max_duration_s: float = 30.0
        self._sample_rate: float = 50.0

    def configure(self, max_duration_s: float, sample_rate: float) -> None:
        self._max_duration_s = max_duration_s
        self._sample_rate = sample_rate

    def on_joint_state(self, msg: dict) -> None:
        """Route a rosbridge /joint_states message to per-joint buffers.

        H6 fix: if the position/velocity/effort arrays are shorter than the
        name array (malformed rosbridge message), skip the mismatched joints
        instead of silently filling them with 0.0.  Silently zeroing masks
        hardware sensor failures and can corrupt the anomaly scorer.
        """
        names: List[str] = msg.get("name", [])
        positions: List[float] = msg.get("position", [])
        velocities: List[float] = msg.get("velocity", [])
        efforts: List[float] = msg.get("effort", [])
        ts = time.time()

        n_pos = len(positions)
        n_vel = len(velocities)
        n_eff = len(efforts)
        array_len_ok = (n_pos == len(names) and n_vel == len(names) and n_eff == len(names))
        if not array_len_ok:
            logger.warning(
                "Malformed /joint_states: names=%d pos=%d vel=%d eff=%d — "
                "skipping mismatched joints to avoid silent data loss",
                len(names), n_pos, n_vel, n_eff,
            )

        for i, name in enumerate(names):
            # H6: skip joints whose sensor arrays are missing entries.
            if i >= n_pos or i >= n_vel or i >= n_eff:
                continue
            if name not in self.buffers:
                self.buffers[name] = JointRingBuffer(
                    name, self._max_duration_s, self._sample_rate
                )
            self.buffers[name].append(ts, positions[i], velocities[i], efforts[i])

    def get_buffer(self, joint_name: str) -> Optional[JointRingBuffer]:
        return self.buffers.get(joint_name)


# Module-level singleton used by context_bridge and other consumers.
ring_buffer_manager = RingBufferManager()
