from datetime import datetime, date
from typing import List, Dict


class LLMBudgetExceededError(Exception):
    """Raised when token budget is exhausted."""
    pass


class TokenBudget:
    def __init__(self, daily_budget: int = 100_000):
        self.daily_budget = daily_budget
        self._daily_used: int = 0
        self._reset_date: date = date.today()
        self._call_log: List[Dict] = []  # last 100 calls

    def _check_reset(self):
        today = date.today()
        if today != self._reset_date:
            self._daily_used = 0
            self._reset_date = today
            self._call_log = []

    def can_afford(self, estimated_tokens: int) -> bool:
        self._check_reset()
        return self._daily_used + estimated_tokens <= self.daily_budget

    def spend(self, tokens: int, caller: str = "unknown") -> None:
        self._check_reset()
        self._daily_used += tokens
        self._call_log.append({
            "timestamp": datetime.now().isoformat(),
            "tokens": tokens,
            "caller": caller,
        })
        if len(self._call_log) > 100:
            self._call_log = self._call_log[-100:]

    @property
    def daily_used(self) -> int:
        self._check_reset()
        return self._daily_used

    @property
    def remaining(self) -> int:
        return max(0, self.daily_budget - self.daily_used)

    def get_usage_summary(self) -> dict:
        self._check_reset()
        today_str = date.today().isoformat()
        return {
            "daily_budget": self.daily_budget,
            "daily_used": self._daily_used,
            "remaining": self.remaining,
            "utilization_pct": round(self._daily_used / self.daily_budget * 100, 1),
            "calls_today": len([c for c in self._call_log if c["timestamp"][:10] == today_str]),
        }
