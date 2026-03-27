"""
Tests for lifecycle/state_machine.py and session_orchestrator.py.

M1 criterion 5: valid transitions pass, invalid transitions raise InvalidTransitionError.
"""
import time

import pytest

from manastone.common.models import LifecyclePhase
from manastone.lifecycle.state_machine import InvalidTransitionError, RobotLifecycle
from manastone.lifecycle.session_orchestrator import SessionOrchestrator


# ---------------------------------------------------------------------------
# RobotLifecycle
# ---------------------------------------------------------------------------


@pytest.fixture
def sm(tmp_path):
    """Fresh RobotLifecycle with isolated state file."""
    return RobotLifecycle(state_file=str(tmp_path / "lifecycle_state.json"))


class TestRobotLifecycle:
    def test_initial_state_is_commissioning(self, sm):
        """Fresh machine starts in COMMISSIONING."""
        assert sm.state == LifecyclePhase.COMMISSIONING

    def test_commissioning_to_runtime(self, sm):
        new_state = sm.transition("export_complete")
        assert new_state == LifecyclePhase.RUNTIME
        assert sm.state == LifecyclePhase.RUNTIME

    def test_runtime_to_idle_tuning(self, sm):
        sm.transition("export_complete")
        sm.transition("idle_detected")
        assert sm.state == LifecyclePhase.IDLE_TUNING

    def test_idle_tuning_to_runtime(self, sm):
        sm.transition("export_complete")
        sm.transition("idle_detected")
        sm.transition("tuning_complete")
        assert sm.state == LifecyclePhase.RUNTIME

    def test_idle_tuning_to_maintenance(self, sm):
        sm.transition("export_complete")
        sm.transition("idle_detected")
        sm.transition("anomaly_detected")
        assert sm.state == LifecyclePhase.MAINTENANCE

    def test_maintenance_to_runtime(self, sm):
        sm.transition("export_complete")
        sm.transition("idle_detected")
        sm.transition("anomaly_detected")
        sm.transition("manual_clear")
        assert sm.state == LifecyclePhase.RUNTIME

    def test_runtime_recommission(self, sm):
        sm.transition("export_complete")
        sm.transition("recommission")
        assert sm.state == LifecyclePhase.COMMISSIONING

    def test_all_valid_transitions_pass(self, sm):
        """M1 criterion 5a: every valid transition succeeds."""
        valid_paths = [
            ("export_complete", LifecyclePhase.RUNTIME),
            ("idle_detected", LifecyclePhase.IDLE_TUNING),
            ("tuning_complete", LifecyclePhase.RUNTIME),
        ]
        for event, expected in valid_paths:
            sm.transition(event)
            assert sm.state == expected

    def test_invalid_transition_raises(self, sm):
        """M1 criterion 5b: invalid transition raises InvalidTransitionError."""
        # COMMISSIONING cannot go idle
        with pytest.raises(InvalidTransitionError):
            sm.transition("idle_detected")

    def test_invalid_from_maintenance(self, sm):
        sm.transition("export_complete")
        sm.transition("idle_detected")
        sm.transition("anomaly_detected")
        with pytest.raises(InvalidTransitionError):
            sm.transition("export_complete")

    def test_unknown_event_raises(self, sm):
        with pytest.raises(InvalidTransitionError):
            sm.transition("nonexistent_event")

    def test_can_transition_returns_bool(self, sm):
        assert sm.can_transition("export_complete") is True
        assert sm.can_transition("idle_detected") is False

    def test_state_persisted_to_eventstore(self, tmp_path):
        """SQLite checkpoint: state survives restart."""
        from manastone.runtime.event_store import EventStore

        db_path = str(tmp_path / "events.db")
        store = EventStore(db_path=db_path)

        sm1 = RobotLifecycle(event_store=store)
        sm1.transition("export_complete")
        assert sm1.state == LifecyclePhase.RUNTIME

        # Simulate restart
        sm2 = RobotLifecycle(event_store=store)
        assert sm2.state == LifecyclePhase.RUNTIME

    def test_active_chain_stored_on_transition(self, tmp_path):
        from manastone.runtime.event_store import EventStore

        db_path = str(tmp_path / "events.db")
        store = EventStore(db_path=db_path)
        sm = RobotLifecycle(event_store=store)
        sm.transition("export_complete")
        sm.transition("idle_detected", active_chain="left_leg")
        assert sm.active_chain == "left_leg"


# ---------------------------------------------------------------------------
# SessionOrchestrator
# ---------------------------------------------------------------------------


class TestSessionOrchestrator:
    def test_can_tune_initially(self):
        orch = SessionOrchestrator(min_interval_s=0.0)
        ok, reason = orch.can_tune()
        assert ok is True
        assert reason == "OK"

    def test_cooldown_blocks_immediate_retry(self):
        orch = SessionOrchestrator(min_interval_s=60.0)
        orch.record_tune()
        ok, reason = orch.can_tune()
        assert ok is False
        assert "interval" in reason.lower() or "Cooldown" in reason

    def test_daily_limit_enforced(self):
        orch = SessionOrchestrator(min_interval_s=0.0, max_sessions_per_day=3)
        for _ in range(3):
            orch.record_tune()
        ok, reason = orch.can_tune()
        assert ok is False
        assert "limit" in reason.lower()

    def test_rollback_cooldown_blocks(self):
        orch = SessionOrchestrator(min_interval_s=0.0, cooldown_after_rollback_s=300.0)
        orch.record_rollback()
        ok, reason = orch.can_tune()
        assert ok is False
        assert "rollback" in reason.lower()

    def test_can_tune_after_cooldown(self):
        orch = SessionOrchestrator(min_interval_s=0.0, cooldown_after_rollback_s=0.0)
        orch.record_rollback()
        ok, _ = orch.can_tune()
        assert ok is True
