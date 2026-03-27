"""
AgentRuntimeStream — JSONL lifecycle event stream for the Agent Runtime.

Events are appended to storage/lifecycle_events.jsonl.
The Agent Runtime tails this file to react to state changes.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional


class AgentRuntimeStream:
    """Write lifecycle events to a JSONL stream file."""

    def __init__(self, stream_path: str = "storage/lifecycle_events.jsonl") -> None:
        self._path = Path(stream_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def emit(
        self,
        event_type: str,
        phase: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        record = {
            "ts": datetime.now().isoformat(),
            "event_type": event_type,
            "phase": phase,
            **(details or {}),
        }
        with self._path.open("a") as f:
            f.write(json.dumps(record) + "\n")

    def emit_transition(self, from_phase: str, to_phase: str, event: str) -> None:
        self.emit(
            "phase_transition",
            to_phase,
            {"from_phase": from_phase, "trigger_event": event},
        )

    def emit_tuning_started(self, chain_name: str, phase: str) -> None:
        self.emit("tuning_started", phase, {"chain_name": chain_name})

    def emit_tuning_complete(self, chain_name: str, score: float) -> None:
        self.emit("tuning_complete", "runtime", {"chain_name": chain_name, "score": score})

    def emit_anomaly(self, joint_name: str, anomaly_score: float) -> None:
        self.emit("anomaly_detected", "maintenance", {
            "joint_name": joint_name,
            "anomaly_score": anomaly_score,
        })

    def tail(self, n: int = 20) -> list[Dict[str, Any]]:
        """Return the last n events (for debugging)."""
        if not self._path.exists():
            return []
        lines = self._path.read_text().strip().splitlines()
        return [json.loads(l) for l in lines[-n:] if l]
