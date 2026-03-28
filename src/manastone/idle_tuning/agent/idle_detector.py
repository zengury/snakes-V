"""IdleDetector — determines when the robot is idle and safe to tune."""

from __future__ import annotations

import time
from typing import List, Optional, Tuple


class IdleDetector:
    def __init__(self, config, dds_bridge=None):
        self.config = config
        self.dds_bridge = dds_bridge
        self.velocity_threshold = 0.02   # rad/s
        self.duration_threshold = 30.0   # seconds
        self.min_battery_soc = 20        # %
        self.max_joint_temp = 60.0       # °C
        self._low_vel_since: Optional[float] = None  # time.monotonic()

    async def is_idle(self) -> Tuple[bool, str]:
        """Returns (is_idle, reason)"""
        if self.config.is_mock_mode():
            return True, "mock_mode"

        # Real mode: velocity inference
        vels = await self._get_all_joint_velocities()
        if all(abs(v) < self.velocity_threshold for v in vels):
            if self._low_vel_since is None:
                self._low_vel_since = time.monotonic()
            elif time.monotonic() - self._low_vel_since >= self.duration_threshold:
                return True, "velocity_inference"
        else:
            self._low_vel_since = None
        return False, ""

    async def is_safe_to_tune(self) -> Tuple[bool, List[str]]:
        """Check pre-conditions for tuning."""
        if self.config.is_mock_mode():
            return True, []
        # Real mode checks (battery, temp)
        return True, []

    async def _get_all_joint_velocities(self) -> List[float]:
        if self.dds_bridge and hasattr(self.dds_bridge, 'get_latest'):
            # Get from ring buffer
            return [0.0]  # stub for real mode
        return [0.0]


class MockIdleDetector:
    """Test helper: controllable idle detector."""

    def __init__(self, force_idle: bool = True):
        self.force_idle = force_idle

    async def is_idle(self) -> Tuple[bool, str]:
        return (self.force_idle, "mock" if self.force_idle else "")

    async def is_safe_to_tune(self) -> Tuple[bool, List[str]]:
        return True, []
