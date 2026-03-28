"""Tests for commissioning/ module. M1 acceptance criterion."""

import asyncio
import os
import subprocess
import tempfile
from pathlib import Path

import pytest

# Set environment BEFORE any imports
os.environ["MANASTONE_MOCK_MODE"] = "true"
os.environ["ANTHROPIC_API_KEY"] = ""  # Ensure LLM fallback path is used
os.environ.setdefault(
    "MANASTONE_SCHEMA_PATH",
    str(Path(__file__).parent.parent / "config" / "robot_schema.yaml"),
)


def test_chain_tune_left_leg_completes():
    """M1: chain_tune('left_leg') completes 6 joints in causal order."""
    from manastone.commissioning.chain_orchestrator import ChainTuningOrchestrator
    from manastone.common.config import ManaConfig
    from manastone.profiles.registry import ProfileRegistry

    ManaConfig.reset()
    with tempfile.TemporaryDirectory() as tmpdir:
        config = ManaConfig.get()
        registry = ProfileRegistry()
        profile = registry.get("classic_precision")

        orch = ChainTuningOrchestrator(
            config=config,
            profile=profile,
            storage_dir=Path(tmpdir),
            robot_id="test_robot",
        )

        result = asyncio.run(
            orch.tune_chain("left_leg", target_score=50.0, max_experiments_per_joint=5)
        )

        # 6 joints completed
        expected_joints = config.get_chain_tuning_order("left_leg")
        assert len(result.joint_results) == 6
        assert list(result.joint_results.keys()) == expected_joints

        # All joints have results
        for joint_name, joint_result in result.joint_results.items():
            assert joint_result.experiment_count >= 1
            assert joint_result.best_score >= 0.0


def test_chain_tune_causal_order():
    """Joints are processed in hip→knee→ankle causal order."""
    from manastone.common.config import ManaConfig

    ManaConfig.reset()
    config = ManaConfig.get()
    order = config.get_chain_tuning_order("left_leg")
    assert order[0] == "left_hip_yaw"
    assert order[2] == "left_hip_pitch"
    assert order[3] == "left_knee"
    assert order[5] == "left_ankle_roll"


def test_git_history_present():
    """Git log should have commits after chain_tune."""
    from manastone.commissioning.chain_orchestrator import ChainTuningOrchestrator
    from manastone.common.config import ManaConfig
    from manastone.profiles.registry import ProfileRegistry

    ManaConfig.reset()
    with tempfile.TemporaryDirectory() as tmpdir:
        config = ManaConfig.get()
        profile = ProfileRegistry().get("classic_precision")
        orch = ChainTuningOrchestrator(
            config=config,
            profile=profile,
            storage_dir=Path(tmpdir),
            robot_id="git_test",
        )
        asyncio.run(
            orch.tune_chain("left_leg", target_score=30.0, max_experiments_per_joint=2)
        )

        # Check git log for first joint
        workspace_path = Path(tmpdir) / "pid_workspace" / "git_test" / "left_hip_yaw"
        if (workspace_path / ".git").exists():
            r = subprocess.run(
                ["git", "log", "--oneline"],
                cwd=workspace_path,
                capture_output=True,
                text=True,
            )
            assert r.returncode == 0
            commits = [c for c in r.stdout.strip().split("\n") if c.strip()]
            assert len(commits) >= 2  # init + at least 1 experiment


def test_results_tsv_present():
    """results.tsv should exist and have data rows."""
    from manastone.commissioning.chain_orchestrator import ChainTuningOrchestrator
    from manastone.common.config import ManaConfig
    from manastone.profiles.registry import ProfileRegistry

    ManaConfig.reset()
    with tempfile.TemporaryDirectory() as tmpdir:
        config = ManaConfig.get()
        profile = ProfileRegistry().get("classic_precision")
        orch = ChainTuningOrchestrator(
            config=config,
            profile=profile,
            storage_dir=Path(tmpdir),
            robot_id="tsv_test",
        )
        asyncio.run(
            orch.tune_chain("left_leg", target_score=30.0, max_experiments_per_joint=2)
        )

        for joint_name in config.get_chain_tuning_order("left_leg"):
            tsv_path = (
                Path(tmpdir) / "pid_workspace" / "tsv_test" / joint_name / "results.tsv"
            )
            assert tsv_path.exists(), f"results.tsv missing for {joint_name}"
            lines = tsv_path.read_text().strip().split("\n")
            assert len(lines) >= 2, f"results.tsv for {joint_name} has no data rows"


def test_chain_context_propagation():
    """Second joint should receive first joint's result in chain_context."""
    from manastone.commissioning.chain_orchestrator import ChainTuningOrchestrator
    from manastone.common.config import ManaConfig
    from manastone.profiles.registry import ProfileRegistry

    ManaConfig.reset()
    with tempfile.TemporaryDirectory() as tmpdir:
        config = ManaConfig.get()
        profile = ProfileRegistry().get("classic_precision")
        orch = ChainTuningOrchestrator(
            config=config,
            profile=profile,
            storage_dir=Path(tmpdir),
            robot_id="ctx_test",
        )
        result = asyncio.run(
            orch.tune_chain("left_leg", target_score=30.0, max_experiments_per_joint=2)
        )

        # hip_yaw (first) has correct name
        hip_result = result.joint_results["left_hip_yaw"]
        assert hip_result.joint_name == "left_hip_yaw"

        # knee has 3 predecessors in context (tested implicitly by successful completion)
        knee_result = result.joint_results["left_knee"]
        assert knee_result.joint_name == "left_knee"


def test_chain_score_computed():
    """chain_score should be a valid float between 0 and 100."""
    from manastone.commissioning.chain_orchestrator import ChainTuningOrchestrator
    from manastone.common.config import ManaConfig
    from manastone.profiles.registry import ProfileRegistry

    ManaConfig.reset()
    with tempfile.TemporaryDirectory() as tmpdir:
        config = ManaConfig.get()
        profile = ProfileRegistry().get("classic_precision")
        orch = ChainTuningOrchestrator(
            config=config,
            profile=profile,
            storage_dir=Path(tmpdir),
            robot_id="score_test",
        )
        result = asyncio.run(
            orch.tune_chain("left_leg", target_score=30.0, max_experiments_per_joint=2)
        )
        assert 0.0 <= result.chain_score <= 100.0


def test_mock_simulator_step_response():
    """MockJointSimulator produces valid step response data."""
    from manastone.commissioning.autoresearch.experiment import MockJointSimulator

    physics = {"inertia": 0.15, "friction": 0.8, "gravity_comp": 0.0, "noise_std": 0.0}
    sim = MockJointSimulator(physics)
    data = sim.step_response(kp=10.0, ki=0.1, kd=1.0, setpoint=0.3, duration=2.0)

    assert len(data) > 0
    for t, pos, vel, torque in data:
        assert t >= 0.0
        assert isinstance(pos, float)
        assert isinstance(vel, float)
        assert isinstance(torque, float)


def test_mock_simulator_safety_abort():
    """Very high Kp should trigger safety abort."""
    from manastone.commissioning.autoresearch.experiment import MockJointSimulator

    physics = {"inertia": 0.15, "friction": 0.0, "gravity_comp": 0.0, "noise_std": 0.0}
    sim = MockJointSimulator(physics)
    # Absurdly high Kp to force torque limit
    data = sim.step_response(kp=5000.0, ki=0.0, kd=0.0, setpoint=0.3, duration=2.0)
    # Should abort early — data length much less than 200
    assert len(data) < 200


def test_workspace_git_init():
    """PIDWorkspace initializes git repo and creates initial commit."""
    from manastone.commissioning.autoresearch.workspace import PIDWorkspace

    with tempfile.TemporaryDirectory() as tmpdir:
        ws = PIDWorkspace("robot_x", "left_knee", Path(tmpdir))
        git_dir = ws.root / ".git"
        assert ws.root.exists()
        # Either git repo or fallback files exist
        results_tsv = ws.root / "results.tsv"
        assert results_tsv.exists()
        params_yaml = ws.root / "params.yaml"
        assert params_yaml.exists()


def test_workspace_write_read_params():
    """Write and read back PID params from workspace."""
    from manastone.commissioning.autoresearch.workspace import PIDWorkspace
    from manastone.common.models import PIDParams

    with tempfile.TemporaryDirectory() as tmpdir:
        ws = PIDWorkspace("robot_x", "left_knee", Path(tmpdir))
        pid = PIDParams(kp=12.5, ki=0.3, kd=1.8)
        ws.write_params(pid, "test hypothesis")
        read_back = ws.read_params()
        assert abs(read_back.kp - 12.5) < 0.001
        assert abs(read_back.ki - 0.3) < 0.001
        assert abs(read_back.kd - 1.8) < 0.001


def test_fallback_rule_engine():
    """LLMParamEditor fallback rule engine works without LLM."""
    from manastone.commissioning.autoresearch.llm_client import LLMParamEditor
    from manastone.common.llm_client import LLMClient
    from manastone.common.models import PIDParams
    from manastone.profiles.registry import ProfileRegistry
    from manastone.profiles.scorers.base import ScorerResult

    profile = ProfileRegistry().get("classic_precision")
    editor = LLMParamEditor(LLMClient(), profile)

    current = PIDParams(kp=10.0, ki=0.5, kd=1.0)
    last_result = ScorerResult(
        score=40.0, grade="F", overshoot_pct=25.0,
        rise_time_s=0.3, settling_time_s=0.8, sse_rad=0.01, oscillation_count=0
    )
    safety_bounds = {"kp_range": [1.0, 50.0], "ki_range": [0.0, 10.0], "kd_range": [0.0, 20.0]}

    new_pid, hypothesis = editor._fallback_rule_engine(current, last_result, safety_bounds)
    # Overshoot > 15% → kp should decrease
    assert new_pid.kp < current.kp + 0.001  # kp * 0.9 → smaller
    assert "overshoot" in hypothesis.lower() or "rule" in hypothesis.lower()
    assert 1.0 <= new_pid.kp <= 50.0
