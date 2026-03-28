import asyncio
from typing import Optional


class BackgroundObserver:
    """60-second observation loop. Observes, records, generates insights. Does NOT tune."""

    def __init__(self, agent, interval_s: int = 60):
        self._agent = agent
        self._interval = interval_s
        self._task: Optional[asyncio.Task] = None
        self._running = False

    def start(self) -> None:
        if not self._running:
            self._running = True
            self._task = asyncio.create_task(self._loop())

    def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()

    async def _loop(self) -> None:
        while self._running:
            await asyncio.sleep(self._interval)
            try:
                await self._observe()
            except Exception as e:
                self._agent.memory.record_event("background_error", str(e)[:100])

    async def _observe(self) -> None:
        # Check consecutive rollbacks
        rollbacks = self._agent.memory.working.get("consecutive_rollbacks", 0)
        if rollbacks >= 3:
            self._agent.memory.add_insight(
                f"Consecutive {rollbacks} rollbacks — auto-tuning strategy may not suit current state",
                source="agent",
            )
            self._agent.memory.working["consecutive_rollbacks"] = 0

        # Record observation tick
        self._agent.memory.record_event("background_tick", "observation cycle complete")
        self._agent.memory.save()
