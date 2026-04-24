"""
Tests for runtime/dds_bridge.py, ring_buffer.py, event_store.py,
semantic_engine.py, and anomaly_scorer.py.

M1 criterion 1: mock mode emits joint data at ~50Hz.
"""
import asyncio
import time
from pathlib import Path

import pytest

from manastone.common.models import JointContext
from manastone.runtime.anomaly_scorer import AnomalyScorer
from manastone.runtime.dds_bridge import MockDDSBridge
from manastone.runtime.event_store import EventStore
from manastone.runtime.ring_buffer import JointRingBuffer, RingBufferManager
from manastone.runtime.semantic_engine import SemanticEngine


# ---------------------------------------------------------------------------
# MockDDSBridge
# ---------------------------------------------------------------------------


class TestMockDDSBridge:
    @pytest.mark.asyncio
    async def test_mock_emits_joint_data(self):
        """Mock bridge emits /joint_states messages for all joints."""
        bridge = MockDDSBridge()
        received = []

        await bridge.connect()
        await bridge.subscribe("/joint_states", "sensor_msgs/JointState", received.append)

        await asyncio.sleep(0.1)  # ~5 ticks at 50Hz
        await bridge.disconnect()

        assert len(received) >= 3
        msg = received[0]
        assert "name" in msg and "position" in msg and "velocity" in msg and "effort" in msg

    @pytest.mark.asyncio
    async def test_mock_emits_at_50hz(self):
        """Verify roughly 50 messages per second."""
        bridge = MockDDSBridge()
        received = []
        await bridge.connect()
        await bridge.subscribe("/joint_states", "sensor_msgs/JointState", received.append)

        t0 = time.monotonic()
        await asyncio.sleep(0.2)
        elapsed = time.monotonic() - t0
        await bridge.disconnect()

        hz = len(received) / elapsed
        # Allow 40-60 Hz range
        assert 35 <= hz <= 70, f"Expected ~50Hz, got {hz:.1f}Hz"

    @pytest.mark.asyncio
    async def test_mock_call_service_returns_success(self):
        bridge = MockDDSBridge()
        await bridge.connect()
        result = await bridge.call_service("/set_param", {"name": "kp", "value": 10.0})
        await bridge.disconnect()
        assert result.get("success") is True

    @pytest.mark.asyncio
    async def test_disconnect_cancels_loop(self):
        bridge = MockDDSBridge()
        await bridge.connect()
        assert bridge._task is not None
        await bridge.disconnect()
        assert bridge._task is not None  # Task exists but is cancelled
        assert bridge._task.done()


# ---------------------------------------------------------------------------
# JointRingBuffer
# ---------------------------------------------------------------------------




def test_real_call_service_raises_if_disconnected():
    """Real bridge should fail fast if call_service is used before connect()."""
    from manastone.runtime.dds_bridge import DDSConnectionLostError, RealDDSBridge

    bridge = RealDDSBridge()

    async def _run() -> None:
        with pytest.raises(DDSConnectionLostError):
            await bridge.call_service("/set_param", {"name": "kp", "value": 10.0})

    asyncio.run(_run())


class TestJointRingBuffer:
    def test_append_and_get_latest(self):
        buf = JointRingBuffer("left_knee", max_duration_s=30.0, sample_rate=50.0)
        buf.append(1.0, 0.1, 0.2, 0.3)
        latest = buf.get_latest()
        assert latest is not None
        assert latest[1] == pytest.approx(0.1)

    def test_max_capacity_enforced(self):
        buf = JointRingBuffer("left_knee", max_duration_s=1.0, sample_rate=10.0)
        # max_samples = 10
        for i in range(20):
            buf.append(float(i), 0.0, 0.0, 0.0)
        assert len(buf) == 10

    def test_get_window_returns_recent_samples(self):
        buf = JointRingBuffer("left_knee", max_duration_s=30.0, sample_rate=50.0)
        now = time.time()
        for i in range(100):
            buf.append(now - (100 - i) * 0.02, float(i), 0.0, 0.0)
        window = buf.get_window(1.0)
        # 1s window at 50Hz = ~50 samples
        assert 40 <= len(window) <= 60

    def test_get_window_empty_buffer(self):
        buf = JointRingBuffer("left_knee")
        assert buf.get_window(5.0) == []

    def test_old_samples_dropped(self):
        buf = JointRingBuffer("left_knee", max_duration_s=30.0, sample_rate=50.0)
        now = time.time()
        buf.append(now - 100, 0.0, 0.0, 0.0)  # very old
        buf.append(now, 1.0, 0.0, 0.0)  # fresh
        window = buf.get_window(5.0)
        assert len(window) == 1


# ---------------------------------------------------------------------------
# RingBufferManager
# ---------------------------------------------------------------------------


class TestRingBufferManager:
    def test_on_joint_state_creates_buffers(self):
        mgr = RingBufferManager()
        msg = {
            "name": ["left_knee", "right_knee"],
            "position": [0.1, 0.2],
            "velocity": [0.3, 0.4],
            "effort": [0.5, 0.6],
        }
        mgr.on_joint_state(msg)
        assert "left_knee" in mgr.buffers
        assert "right_knee" in mgr.buffers

    def test_on_joint_state_appends_samples(self):
        mgr = RingBufferManager()
        for i in range(5):
            mgr.on_joint_state({
                "name": ["left_knee"],
                "position": [float(i)],
                "velocity": [0.0],
                "effort": [0.0],
            })
        assert len(mgr.buffers["left_knee"]) == 5


# ---------------------------------------------------------------------------
# EventStore
# ---------------------------------------------------------------------------


class TestEventStore:
    def test_append_and_query(self, tmp_path):
        store = EventStore(db_path=str(tmp_path / "test.db"))
        store.append("torque_spike", "left_knee", "warning", value=45.0, threshold=40.0)
        events = store.query_recent(joint_name="left_knee", hours=1.0)
        assert len(events) == 1
        assert events[0]["event_type"] == "torque_spike"

    def test_query_by_event_type(self, tmp_path):
        store = EventStore(db_path=str(tmp_path / "test.db"))
        store.append("torque_spike", "left_knee", "warning")
        store.append("joint_temp_warning", "left_knee", "warning")
        events = store.query_recent(event_type="torque_spike", hours=1.0)
        assert all(e["event_type"] == "torque_spike" for e in events)

    def test_lifecycle_state_roundtrip(self, tmp_path):
        store = EventStore(db_path=str(tmp_path / "test.db"))
        store.save_lifecycle_state("runtime", "left_leg")
        row = store.load_lifecycle_state()
        assert row is not None
        assert row["phase"] == "runtime"
        assert row["active_chain"] == "left_leg"

    def test_lifecycle_state_none_when_empty(self, tmp_path):
        store = EventStore(db_path=str(tmp_path / "test.db"))
        assert store.load_lifecycle_state() is None

    def test_wal_mode_enabled(self, tmp_path):
        import sqlite3
        store = EventStore(db_path=str(tmp_path / "test.db"))
        conn = sqlite3.connect(str(tmp_path / "test.db"))
        row = conn.execute("PRAGMA journal_mode").fetchone()
        conn.close()
        assert row[0] == "wal"


# ---------------------------------------------------------------------------
# SemanticEngine
# ---------------------------------------------------------------------------


class TestSemanticEngine:
    def test_healthy_joint_no_events(self, healthy_joint_ctx):
        engine = SemanticEngine()
        events = engine.evaluate(healthy_joint_ctx)
        assert events == []

    def test_high_temp_triggers_warning(self):
        engine = SemanticEngine()
        jc = JointContext(
            joint_name="left_knee", joint_id=3, group="leg",
            temp_c=55.0, torque_nm=5.0
        )
        events = engine.evaluate(jc)
        types = [e["event_type"] for e in events]
        assert "joint_temp_warning" in types

    def test_critical_temp_triggers_critical(self):
        engine = SemanticEngine()
        jc = JointContext(
            joint_name="left_knee", joint_id=3, group="leg",
            temp_c=75.0, torque_nm=5.0
        )
        events = engine.evaluate(jc)
        types = [e["event_type"] for e in events]
        assert "joint_temp_critical" in types

    def test_high_torque_triggers_spike(self, anomalous_joint_ctx):
        engine = SemanticEngine()
        events = engine.evaluate(anomalous_joint_ctx)
        types = [e["event_type"] for e in events]
        assert "torque_spike" in types or "torque_critical" in types

    def test_evaluate_all_aggregates(self, healthy_joint_ctx, anomalous_joint_ctx):
        engine = SemanticEngine()
        events = engine.evaluate_all([healthy_joint_ctx, anomalous_joint_ctx])
        # Healthy produces 0, anomalous produces several
        assert len(events) > 0


# ---------------------------------------------------------------------------
# AnomalyScorer
# ---------------------------------------------------------------------------


class TestAnomalyScorer:
    def test_healthy_joint_score_low(self, healthy_joint_ctx):
        scorer = AnomalyScorer()
        score = scorer.score(healthy_joint_ctx, recent_events=[])
        assert score < 0.2, f"Expected healthy joint score < 0.2, got {score:.3f}"

    def test_anomalous_joint_score_high(self, anomalous_joint_ctx):
        scorer = AnomalyScorer()
        events = [{"event_type": "torque_critical"} for _ in range(15)]
        score = scorer.score(anomalous_joint_ctx, recent_events=events)
        assert score > 0.6, f"Expected anomalous joint score > 0.6, got {score:.3f}"

    def test_score_bounded_0_to_1(self, healthy_joint_ctx):
        scorer = AnomalyScorer()
        score = scorer.score(healthy_joint_ctx, recent_events=[])
        assert 0.0 <= score <= 1.0

    def test_score_components_sum_to_total(self, healthy_joint_ctx):
        scorer = AnomalyScorer()
        components = scorer.score_components(healthy_joint_ctx, recent_events=[])
        manual_total = sum(AnomalyScorer.WEIGHTS[k] * v for k, v in components.items())
        direct_total = scorer.score(healthy_joint_ctx, recent_events=[])
        assert abs(manual_total - direct_total) < 1e-10

    def test_comm_lost_increases_score(self):
        scorer = AnomalyScorer()
        jc_clean = JointContext(joint_name="x", joint_id=0, group="leg", comm_lost_count=0)
        jc_comm = JointContext(joint_name="x", joint_id=0, group="leg", comm_lost_count=5)
        assert scorer.score(jc_comm, []) > scorer.score(jc_clean, [])
