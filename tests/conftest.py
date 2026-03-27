"""
Shared pytest fixtures for Phase 1 tests.
All tests run with MANASTONE_MOCK_MODE=true.
"""
import os
import tempfile
from pathlib import Path

import pytest

# Set mock mode before any imports touch config
os.environ["MANASTONE_MOCK_MODE"] = "true"
os.environ["MANASTONE_SCHEMA_PATH"] = str(
    Path(__file__).parent.parent / "config" / "robot_schema.yaml"
)


@pytest.fixture(autouse=True)
def reset_config():
    """Reset ManaConfig singleton between tests."""
    from manastone.common.config import ManaConfig
    ManaConfig.reset()
    yield
    ManaConfig.reset()


@pytest.fixture
def tmp_storage(tmp_path, monkeypatch):
    """Redirect all storage paths to a temp directory."""
    monkeypatch.setenv("MANASTONE_STORAGE_DIR", str(tmp_path / "storage"))
    return tmp_path


@pytest.fixture
def sample_pid():
    from manastone.common.models import PIDParams
    return PIDParams(kp=10.0, ki=0.5, kd=2.0)


@pytest.fixture
def healthy_joint_ctx():
    from manastone.common.models import JointContext, PIDParams
    return JointContext(
        joint_name="left_knee",
        joint_id=3,
        group="leg",
        temp_c=28.0,
        torque_nm=5.0,
        velocity_rad_s=1.0,
        tracking_error_mean=0.01,
        torque_efficiency=0.85,
        anomaly_score=0.05,
        last_params=PIDParams(kp=10.0, ki=0.5, kd=2.0),
    )


@pytest.fixture
def anomalous_joint_ctx():
    from manastone.common.models import JointContext, PIDParams
    return JointContext(
        joint_name="left_knee",
        joint_id=3,
        group="leg",
        temp_c=72.0,
        torque_nm=65.0,
        velocity_rad_s=18.0,
        tracking_error_mean=0.12,
        torque_efficiency=0.2,
        anomaly_score=0.9,
        comm_lost_count=3,
        last_params=PIDParams(kp=10.0, ki=0.5, kd=2.0),
    )
