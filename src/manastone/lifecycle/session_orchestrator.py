"""
SessionOrchestrator — rate-limiting and cooldown rules for idle tuning.

Prevents over-tuning: min interval, daily max, rollback cooldown.
"""

from __future__ import annotations

import time
from datetime import date
from typing import Tuple


class SessionOrchestrator:
    """Enforces scheduling constraints on idle tuning sessions."""

    def __init__(
        self,
        min_interval_s: float = 60.0,
        max_sessions_per_day: int = 20,
        cooldown_after_rollback_s: float = 300.0,
    ) -> None:
        self._min_interval_s = min_interval_s
        self._max_sessions_per_day = max_sessions_per_day
        self._cooldown_after_rollback_s = cooldown_after_rollback_s

        self._last_tune_time: float = 0.0
        self._last_rollback_time: float = 0.0
        self._daily_count: int = 0
        self._last_count_date: date = date.today()

    def can_tune(self) -> Tuple[bool, str]:
        """Returns (allowed, reason_string)."""
        self._reset_daily_count_if_new_day()
        now = time.time()

        remaining = self._min_interval_s - (now - self._last_tune_time)
        if remaining > 0:
            return False, f"Min interval: {remaining:.0f}s remaining"

        if self._daily_count >= self._max_sessions_per_day:
            return False, f"Daily limit reached ({self._max_sessions_per_day})"

        rollback_remaining = self._cooldown_after_rollback_s - (now - self._last_rollback_time)
        if rollback_remaining > 0:
            return False, f"Post-rollback cooldown: {rollback_remaining:.0f}s remaining"

        return True, "OK"

    def record_tune(self) -> None:
        self._reset_daily_count_if_new_day()
        self._last_tune_time = time.time()
        self._daily_count += 1

    def record_rollback(self) -> None:
        self._last_rollback_time = time.time()

    def _reset_daily_count_if_new_day(self) -> None:
        today = date.today()
        if today != self._last_count_date:
            self._daily_count = 0
            self._last_count_date = today
