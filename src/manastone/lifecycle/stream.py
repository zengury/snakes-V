"""
AgentRuntimeStream — JSONL lifecycle event stream for the Agent Runtime.

Events are appended to a per-robot JSONL file.
The Agent Runtime tails this file to react to state changes.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# StreamEvent model (Phase 5)
# ---------------------------------------------------------------------------


class StreamEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = Field(default_factory=datetime.now)
    robot_id: str
    profile_id: str = "classic_precision"
    event_type: Literal[
        "profile_switched", "commissioning_started", "commissioning_completed",
        "task_started", "task_completed", "anomaly_detected", "idle_detected",
        "tuning_started", "tuning_completed", "params_applied", "model_exported",
        "template_inherited", "checkpoint_created",
    ]
    payload: Dict = Field(default_factory=dict)
    git_commit: Optional[str] = None


# ---------------------------------------------------------------------------
# AgentRuntimeStream
# ---------------------------------------------------------------------------


class AgentRuntimeStream:
    """Lifecycle event stream — appends to per-robot JSONL file.

    Supports two usage modes:

    1. Legacy (Phase 1-4): construct with a file path string and call
       ``emit(event_type, phase, details)``.

    2. Phase-5: construct with ``robot_id`` + ``base_dir`` and call
       ``emit(StreamEvent)`` / ``process_event`` / ``query`` / ``checkpoint``.
    """

    def __init__(
        self,
        robot_id_or_path: str = "storage/lifecycle_events.jsonl",
        base_dir: Optional[Path] = None,
    ) -> None:
        if base_dir is not None:
            # Phase-5 mode: per-robot directory
            self._robot_id: Optional[str] = robot_id_or_path
            self._path = Path(base_dir) / robot_id_or_path / "stream.jsonl"
        else:
            # Legacy mode: flat file path
            self._robot_id = None
            self._path = Path(robot_id_or_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------------------- Phase-5 API

    def emit(self, event: "StreamEvent | str", phase: Optional[str] = None, details: Optional[Dict[str, Any]] = None) -> None:  # type: ignore[override]
        """Emit a StreamEvent (Phase-5) or a legacy raw dict (Phase 1-4)."""
        if isinstance(event, StreamEvent):
            with self._path.open("a") as f:
                f.write(event.model_dump_json() + "\n")
        else:
            # Legacy dict-based emit
            record = {
                "ts": datetime.now().isoformat(),
                "event_type": event,
                "phase": phase,
                **(details or {}),
            }
            with self._path.open("a") as f:
                f.write(json.dumps(record) + "\n")

    def process_event(self, event: StreamEvent) -> None:
        """Alias for emit (matches SPEC naming)."""
        self.emit(event)

    def query(
        self,
        event_type: Optional[str] = None,
        profile_id: Optional[str] = None,
        limit: int = 50,
    ) -> List[StreamEvent]:
        if not self._path.exists():
            return []
        events = []
        for line in self._path.read_text().strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                e = StreamEvent.model_validate_json(line)
                if event_type and e.event_type != event_type:
                    continue
                if profile_id and e.profile_id != profile_id:
                    continue
                events.append(e)
            except Exception:
                continue
        return events[-limit:]

    def checkpoint(self, label: str, profile_id: str = "classic_precision") -> StreamEvent:
        e = StreamEvent(
            robot_id=self._robot_id or "unknown",
            profile_id=profile_id,
            event_type="checkpoint_created",
            payload={"label": label},
        )
        self.emit(e)
        return e

    # ---------------------------------------------------------------- Legacy API

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

    def tail(self, n: int = 20) -> list:
        """Return the last n raw event dicts (for debugging)."""
        if not self._path.exists():
            return []
        lines = self._path.read_text().strip().splitlines()
        result = []
        for line in lines[-n:]:
            if line:
                try:
                    result.append(json.loads(line))
                except Exception:
                    pass
        return result
