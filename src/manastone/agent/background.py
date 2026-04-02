import asyncio
from datetime import datetime, timezone
from typing import Optional

from manastone.agent.memdir import ensure_robot_identity_memory, ensure_safety_gotcha_memory
from manastone.agent.memory_extractor import MemoryTurnContext
from manastone.idle_tuning.agent.idle_detector import IdleDetector


class BackgroundObserver:
    """Observation loop.

    Responsibilities:
    - Lightweight monitoring (insights, housekeeping)
    - Detect robot "cycles" using the IdleDetector and trigger *one* memdir
      extraction per cycle.

    Definition of a cycle:
    - A cycle is the active period BETWEEN two idle windows.
      i.e., transition idle=True → idle=False starts a cycle,
            transition idle=False → idle=True ends a cycle.

    This aligns memory consolidation with the robot's real operational rhythm.
    """

    def __init__(self, agent, interval_s: int = 60):
        self._agent = agent
        self._interval = interval_s
        self._task: Optional[asyncio.Task] = None
        self._running = False

        # Idle/cycle tracking
        self._idle_detector = IdleDetector(agent.config)
        self._last_idle: Optional[bool] = None
        self._cycle_start_event_idx: Optional[int] = None
        self._cycle_started_at: Optional[str] = None
        self._cycle_counter: int = 0

        # Mock-mode cycle simulation (dev/testing)
        # In mock mode, IdleDetector is always idle=True, so no cycles occur.
        # Enable simulated idle↔active transitions by setting:
        #   MANASTONE_MOCK_CYCLE_TICKS=<N>
        # Meaning: switch state every N observer ticks (interval_s each).
        import os

        try:
            self._mock_cycle_ticks: int = int(os.getenv("MANASTONE_MOCK_CYCLE_TICKS", "0"))
        except Exception:
            self._mock_cycle_ticks = 0
        self._mock_tick_counter: int = 0

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
        # 0) Cycle boundary detection via idle state
        idle, idle_reason = await self._idle_detector.is_idle()

        # Mock-mode cycle simulation
        if self._agent.config.is_mock_mode() and self._mock_cycle_ticks > 0:
            self._mock_tick_counter += 1
            # Start in idle, then alternate every N ticks.
            phase = (self._mock_tick_counter // self._mock_cycle_ticks) % 2
            idle = phase == 0
            idle_reason = "mock_cycle"

        if self._last_idle is None:
            # Initialize state without triggering a cycle event.
            self._last_idle = idle
            if not idle:
                self._cycle_started_at = datetime.now(timezone.utc).isoformat()
                self._cycle_start_event_idx = len(self._agent.memory.episodic)
            return

        # idle -> active: start a new cycle
        if self._last_idle and not idle:
            self._cycle_counter += 1
            self._cycle_started_at = datetime.now(timezone.utc).isoformat()
            self._cycle_start_event_idx = len(self._agent.memory.episodic)
            self._agent.memory.record_event(
                "cycle_started",
                f"cycle={self._cycle_counter} start (idle_reason_prev=true)",
            )

        # active -> idle: end cycle and consolidate memories once
        if (not self._last_idle) and idle:
            ended_at = datetime.now(timezone.utc).isoformat()
            start_idx = self._cycle_start_event_idx or 0
            events = self._agent.memory.episodic[start_idx:]

            # Build a compact cycle transcript (bounded)
            lines = []
            for e in events[-40:]:
                ts = str(e.get("timestamp", ""))[:16]
                et = str(e.get("type", ""))
                summ = str(e.get("summary", ""))[:120]
                lines.append(f"- [{ts}] {et}: {summ}")

            cycle_summary = (
                f"Cycle {self._cycle_counter} summary (active -> idle).\n"
                f"started_at={self._cycle_started_at}\n"
                f"ended_at={ended_at}\n"
                f"idle_reason={idle_reason}\n\n"
                "Recent events (last 40):\n" + "\n".join(lines)
            )

            # Refresh identity (robot_fact) and safety baseline, then consolidate file memories
            try:
                ensure_robot_identity_memory(
                    self._agent._storage_dir, self._agent.robot_id, config=self._agent.config
                )
                ensure_safety_gotcha_memory(self._agent._storage_dir, self._agent.robot_id)
            except Exception:
                pass

            try:
                r = await self._agent.mem_extractor.extract_and_apply(
                    MemoryTurnContext(
                        robot_id=self._agent.robot_id,
                        user_text="cycle_consolidation",
                        result_summary=cycle_summary[:3500],
                        action="cycle",
                        success=True,
                    )
                )
                if isinstance(r, dict) and r.get("applied"):
                    self._agent.memory.record_event(
                        "cycle_consolidated",
                        f"cycle={self._cycle_counter} consolidated to memdir",
                    )
                else:
                    reason = r.get("reason") if isinstance(r, dict) else "unknown"
                    self._agent.memory.record_event(
                        "cycle_consolidation_skipped",
                        f"cycle={self._cycle_counter} skipped (reason={reason})",
                    )
            except Exception as e:
                self._agent.memory.record_event(
                    "cycle_consolidation_error", str(e)[:120]
                )

            # Reset cycle markers
            self._cycle_started_at = None
            self._cycle_start_event_idx = None

        self._last_idle = idle

        # 1) Existing lightweight monitoring
        rollbacks = self._agent.memory.working.get("consecutive_rollbacks", 0)
        if rollbacks >= 3:
            self._agent.memory.add_insight(
                f"Consecutive {rollbacks} rollbacks — auto-tuning strategy may not suit current state",
                source="agent",
            )
            self._agent.memory.working["consecutive_rollbacks"] = 0

        # 2) Record observation tick
        self._agent.memory.record_event("background_tick", "observation cycle complete")
        self._agent.memory.save()
