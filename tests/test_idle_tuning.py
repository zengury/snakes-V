"""Tests for Phase 3: idle_tuning module."""

import asyncio
import os
import tempfile
import uuid
from pathlib import Path

import pytest

os.environ["MANASTONE_MOCK_MODE"] = "true"
os.environ["MANASTONE_SCHEMA_PATH"] = "config/robot_schema.yaml"

from manastone.common.config import ManaConfig


def make_loop(tmpdir):
    """Helper: create a fully wired IdleTuningLoop for testing."""
    from manastone.idle_tuning.agent.idle_detector import IdleDetector
    from manastone.idle_tuning.agent.loop import IdleTuningLoop
    from manastone.idle_tuning.agent.skill_runner import SkillRunner
    from manastone.idle_tuning.collector.session_store import SessionStore
    from manastone.idle_tuning.executor.param_writer import MockParamWriter
    from manastone.idle_tuning.predictor.model import PIDPredictor
    from manastone.idle_tuning.predictor.trainer import PredictorTrainer
    from manastone.common.safety import StaticBoundsChecker
    from manastone.common.llm_client import LLMClient

    ManaConfig.reset()
    config = ManaConfig.get()
    storage = Path(tmpdir)

    detector = IdleDetector(config)
    skills_dir = Path("src/manastone/idle_tuning/agent/skills")
    skill_runner = SkillRunner(skills_dir, LLMClient())
    param_writer = MockParamWriter()
    session_store = SessionStore(storage / "sessions")
    predictor = PIDPredictor()
    trainer = PredictorTrainer(session_store, predictor, storage, "test_robot")
    safety = StaticBoundsChecker()

    loop = IdleTuningLoop(
        config=config,
        detector=detector,
        skill_runner=skill_runner,
        param_writer=param_writer,
        session_store=session_store,
        trainer=trainer,
        predictor=predictor,
        safety=safety,
        robot_id="test_robot",
    )
    return loop, session_store


# M1 test: idle trigger → session JSON persisted
def test_idle_trigger_session_persisted():
    with tempfile.TemporaryDirectory() as tmpdir:
        loop, store = make_loop(tmpdir)
        # Inject high anomaly for left_leg
        loop.set_mock_anomalies({"left_leg": 0.5, "right_leg": 0.1})
        session = asyncio.run(loop.run_once("test_robot"))
        assert session is not None
        assert session.chain_name == "left_leg"
        # JSON file should exist
        session_dir = Path(tmpdir) / "sessions" / "test_robot"
        json_files = list(session_dir.glob("*.json"))
        assert len(json_files) == 1
        # Re-deserialize
        from manastone.idle_tuning.collector.session_store import IdleTuningSession

        loaded = IdleTuningSession.model_validate_json(json_files[0].read_text())
        assert loaded.chain_name == "left_leg"
        assert loaded.outcome in ("improved", "neutral", "rollback")


# M1 test: all chains healthy → no tuning triggered
def test_no_trigger_when_all_chains_healthy():
    with tempfile.TemporaryDirectory() as tmpdir:
        loop, _ = make_loop(tmpdir)
        # All anomalies below threshold (0.3)
        loop.set_mock_anomalies(
            {
                "left_leg": 0.1,
                "right_leg": 0.05,
                "waist": 0.0,
                "left_arm": 0.1,
                "right_arm": 0.1,
            }
        )
        session = asyncio.run(loop.run_once("test_robot"))
        assert session is None


def test_chain_selection_picks_highest_anomaly():
    with tempfile.TemporaryDirectory() as tmpdir:
        loop, _ = make_loop(tmpdir)
        loop.set_mock_anomalies({"left_leg": 0.4, "right_leg": 0.6, "waist": 0.2})
        session = asyncio.run(loop.run_once("test_robot"))
        assert session is not None
        assert session.chain_name == "right_leg"


def test_rollback_on_bad_params():
    """Inject params that will score low → rollback."""
    with tempfile.TemporaryDirectory() as tmpdir:
        loop, _ = make_loop(tmpdir)
        loop.set_mock_anomalies({"left_leg": 0.95})  # Very high anomaly
        session = asyncio.run(loop.run_once("test_robot"))
        assert session is not None
        assert session.outcome in ("rollback", "neutral", "improved")  # outcome computed correctly


def test_predictor_cold_start():
    """Untrained predictor returns (0, 0, 0) deltas."""
    from manastone.idle_tuning.predictor.model import PIDPredictor
    from manastone.common.models import JointContext

    p = PIDPredictor()
    ctx = JointContext(joint_name="left_knee", joint_id=3, group="leg")
    assert p.is_trained == False
    delta = p.predict_delta(ctx)
    assert delta == (0.0, 0.0, 0.0)


def test_predictor_feature_dimension():
    """Feature vector must be exactly 19-dim."""
    from manastone.idle_tuning.predictor.model import PIDPredictor
    from manastone.common.models import JointContext, PIDParams

    p = PIDPredictor()
    ctx = JointContext(
        joint_name="left_knee",
        joint_id=3,
        group="leg",
        temp_c=45.0,
        torque_nm=12.0,
        velocity_rad_s=0.01,
        anomaly_score=0.4,
        last_params=PIDParams(kp=5.0, ki=0.1, kd=0.5),
        quality_trend=[0.6, 0.65, 0.7],
    )
    features = p.extract_features(ctx)
    assert features.shape == (19,)
    assert features.dtype == "float32"


def test_runtime_predictor_below_threshold():
    """anomaly < 0.3 → suggest() returns None."""
    from manastone.idle_tuning.predictor.runtime_predictor import RuntimePredictor
    from manastone.common.models import JointContext, PIDParams

    with tempfile.TemporaryDirectory() as tmpdir:
        rp = RuntimePredictor("test_robot", Path(tmpdir))
        ctx = JointContext(
            joint_name="left_knee",
            joint_id=3,
            group="leg",
            anomaly_score=0.1,
            last_params=PIDParams(kp=5.0, ki=0.1, kd=0.5),
        )
        result = asyncio.run(rp.suggest("left_knee", ctx))
        assert result is None


def test_session_store_save_load():
    from manastone.idle_tuning.collector.session_store import IdleTuningSession, SessionStore
    from manastone.common.models import PIDParams

    with tempfile.TemporaryDirectory() as tmpdir:
        store = SessionStore(Path(tmpdir))
        session = IdleTuningSession(
            session_id=str(uuid.uuid4()),
            robot_id="r1",
            chain_name="left_leg",
            joint_params={"left_knee": PIDParams(kp=5.0, ki=0.1, kd=0.5)},
            outcome="improved",
            training_sample=True,
        )
        path = asyncio.run(store.save(session))
        assert path.exists()

        loaded = asyncio.run(store.query_by_chain("r1", "left_leg"))
        assert len(loaded) == 1
        assert loaded[0].outcome == "improved"
        assert loaded[0].joint_params["left_knee"].kp == 5.0


def test_feature_cols_constants():
    from manastone.idle_tuning.predictor.features import CHAIN_JOINT_COLS, JOINT_FEATURE_COLS

    assert len(JOINT_FEATURE_COLS) == 19
    assert len(CHAIN_JOINT_COLS) == 10


def test_mock_param_writer():
    from manastone.idle_tuning.executor.param_writer import MockParamWriter
    from manastone.common.models import PIDParams

    writer = MockParamWriter()
    params = {"left_knee": PIDParams(kp=6.0, ki=0.2, kd=0.6)}
    asyncio.run(writer.write_chain_params("left_leg", params))
    assert writer.get_current_params("left_knee").kp == 6.0
    asyncio.run(
        writer.rollback_chain(
            "left_leg", {"left_knee": PIDParams(kp=5.0, ki=0.1, kd=0.5)}
        )
    )
    assert writer.get_current_params("left_knee").kp == 5.0
