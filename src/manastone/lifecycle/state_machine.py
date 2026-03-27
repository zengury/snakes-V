"""
RobotLifecycle — state machine with SQLite-backed durability.

State is checkpointed to EventStore on every transition.
On startup, last known state is restored from the database.

Valid transitions:
  COMMISSIONING --export_complete--> RUNTIME
  RUNTIME       --idle_detected---->  IDLE_TUNING
  IDLE_TUNING   --tuning_complete--> RUNTIME
  IDLE_TUNING   --anomaly_detected-> MAINTENANCE
  MAINTENANCE   --manual_clear----->  RUNTIME
  RUNTIME       --recommission-----> COMMISSIONING
"""

from __future__ import annotations

from typing import Dict, Optional, Tuple

from manastone.common.models import LifecyclePhase


class InvalidTransitionError(Exception):
    """Raised when a requested state transition is not allowed."""


# (from_phase, event) -> to_phase
_VALID_TRANSITIONS: Dict[Tuple[LifecyclePhase, str], LifecyclePhase] = {
    (LifecyclePhase.COMMISSIONING, "export_complete"): LifecyclePhase.RUNTIME,
    (LifecyclePhase.RUNTIME,       "idle_detected"):   LifecyclePhase.IDLE_TUNING,
    (LifecyclePhase.IDLE_TUNING,   "tuning_complete"): LifecyclePhase.RUNTIME,
    (LifecyclePhase.IDLE_TUNING,   "anomaly_detected"):LifecyclePhase.MAINTENANCE,
    (LifecyclePhase.MAINTENANCE,   "manual_clear"):    LifecyclePhase.RUNTIME,
    (LifecyclePhase.RUNTIME,       "recommission"):    LifecyclePhase.COMMISSIONING,
}


class RobotLifecycle:
    """Lifecycle state machine.

    Uses EventStore for crash-safe state persistence.
    Falls back to file-based JSON if EventStore is unavailable.
    """

    def __init__(
        self,
        event_store: Optional[object] = None,
        state_file: Optional[str] = None,
    ) -> None:
        self._event_store = event_store
        self._state_file = state_file  # None = use default path
        self._active_chain: Optional[str] = None
        self._state = self._load_state()

    # ------------------------------------------------------------------ API

    @property
    def state(self) -> LifecyclePhase:
        return self._state

    @property
    def active_chain(self) -> Optional[str]:
        return self._active_chain

    def transition(self, event: str, active_chain: Optional[str] = None) -> LifecyclePhase:
        key = (self._state, event)
        if key not in _VALID_TRANSITIONS:
            raise InvalidTransitionError(
                f"Cannot transition from '{self._state.value}' via event '{event}'"
            )
        self._state = _VALID_TRANSITIONS[key]
        self._active_chain = active_chain
        self._save_state()
        return self._state

    def can_transition(self, event: str) -> bool:
        return (self._state, event) in _VALID_TRANSITIONS

    # --------------------------------------------------------------- persist

    def _load_state(self) -> LifecyclePhase:
        if self._event_store is not None:
            row = self._event_store.load_lifecycle_state()  # type: ignore[union-attr]
            if row:
                self._active_chain = row.get("active_chain")
                return LifecyclePhase(row["phase"])
            return LifecyclePhase.COMMISSIONING
        # Fallback: file-based
        import json
        from pathlib import Path
        path = Path(self._state_file) if self._state_file else Path("storage/lifecycle_state.json")
        if path.exists():
            try:
                data = json.loads(path.read_text())
                self._active_chain = data.get("active_chain")
                return LifecyclePhase(data["phase"])
            except Exception:
                pass
        return LifecyclePhase.COMMISSIONING

    def _save_state(self) -> None:
        if self._event_store is not None:
            self._event_store.save_lifecycle_state(  # type: ignore[union-attr]
                self._state.value, self._active_chain
            )
            return
        # Fallback: file-based
        import json
        from datetime import datetime
        from pathlib import Path
        path = Path(self._state_file) if self._state_file else Path("storage/lifecycle_state.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "phase": self._state.value,
                    "active_chain": self._active_chain,
                    "updated_at": datetime.now().isoformat(),
                }
            )
        )
