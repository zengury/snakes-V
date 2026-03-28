"""Tests for profiles/ module."""

import os
from pathlib import Path

import pytest

# Ensure env is set (conftest.py also sets these, but set here for safety)
os.environ.setdefault("MANASTONE_MOCK_MODE", "true")
os.environ.setdefault(
    "MANASTONE_SCHEMA_PATH",
    str(Path(__file__).parent.parent / "config" / "robot_schema.yaml"),
)


def test_classic_precision_loads():
    from manastone.profiles.registry import ProfileRegistry

    registry = ProfileRegistry()
    profile = registry.get("classic_precision")
    assert profile.profile_id == "classic_precision"
    assert profile.scorer is not None
    assert profile.generator is not None


def test_all_builtin_profiles_load():
    from manastone.profiles.registry import ProfileRegistry

    registry = ProfileRegistry()
    for profile_id in ["classic_precision", "rl_fidelity", "energy_saver", "high_speed", "collision_safe"]:
        p = registry.get(profile_id)
        assert p.profile_id == profile_id


def test_profile_not_found_raises():
    from manastone.profiles.registry import ProfileRegistry, ProfileNotFoundError

    registry = ProfileRegistry()
    with pytest.raises(ProfileNotFoundError):
        registry.get("nonexistent_profile")


def test_step_response_scorer():
    import math
    from manastone.profiles.scorers.step_response import StepResponseScorer

    scorer = StepResponseScorer()
    setpoint = 0.3

    # Generate ideal step response data (exponential approach)
    dt = 0.01
    data = []
    for i in range(200):
        t = i * dt
        tau = 0.3
        pos = setpoint * (1 - math.exp(-t / tau))
        vel = setpoint / tau * math.exp(-t / tau)
        torque = pos * 10.0
        data.append((t, pos, vel, torque))

    result = scorer.score(data, setpoint)
    assert 0 <= result.score <= 100
    assert result.grade in ["A", "B", "C", "D", "F"]
    assert result.overshoot_pct >= 0.0
    assert result.rise_time_s >= 0.0


def test_step_response_scorer_empty_data():
    from manastone.profiles.scorers.step_response import StepResponseScorer

    scorer = StepResponseScorer()
    result = scorer.score([], 0.3)
    assert result.score == 0.0
    assert result.grade == "F"


def test_torque_scorer():
    from manastone.profiles.scorers.torque_tracking import TorqueScorer

    scorer = TorqueScorer(max_torque_nm=60.0)
    # Smooth torque data
    data = [(i * 0.01, 0.3, 0.0, 5.0 + 0.1 * (i % 3)) for i in range(100)]
    result = scorer.score(data, 0.3)
    assert 0 <= result.score <= 100
    assert result.grade in ["A", "B", "C", "D", "F"]


def test_energy_scorer():
    from manastone.profiles.scorers.energy import EnergyScorer

    scorer = EnergyScorer(energy_budget_j=10.0)
    data = [(i * 0.01, 0.3, 0.1, 2.0) for i in range(100)]
    result = scorer.score(data, 0.3)
    assert 0 <= result.score <= 100


def test_step_generator():
    from manastone.profiles.generators.step import StepGenerator

    gen = StepGenerator(setpoint=0.5, duration_s=3.0, sample_rate_hz=50.0)
    spec = gen.generate("left_knee", "leg")
    assert spec.setpoint == 0.5
    assert spec.duration_s == 3.0
    assert spec.sample_rate_hz == 50.0
    assert spec.metadata["type"] == "step"
    assert spec.metadata["joint_name"] == "left_knee"


def test_sinusoidal_generator():
    from manastone.profiles.generators.sinusoidal import SinusoidalGenerator

    gen = SinusoidalGenerator(amplitude=0.17, frequencies=[0.5, 1.0], duration_s=5.0)
    spec = gen.generate("left_ankle_pitch", "leg")
    assert spec.setpoint == 0.17
    assert spec.duration_s == 5.0
    assert spec.metadata["frequencies"] == [0.5, 1.0]


def test_list_compatible_all():
    from manastone.profiles.registry import ProfileRegistry

    registry = ProfileRegistry()
    all_profiles = registry.list_compatible()
    assert len(all_profiles) >= 5


def test_list_compatible_leg_group():
    from manastone.profiles.registry import ProfileRegistry

    registry = ProfileRegistry()
    # classic_precision has empty compatible_joint_groups → matches any group
    all_leg = registry.list_compatible(joint_group="leg")
    assert "classic_precision" in all_leg


def test_profile_render_prompt():
    from manastone.profiles.registry import ProfileRegistry

    registry = ProfileRegistry()
    profile = registry.get("classic_precision")
    safety_bounds = {"kp_range": [1.0, 50.0], "ki_range": [0.0, 10.0], "kd_range": [0.0, 20.0]}
    prompt = profile.render_prompt(
        joint_name="left_knee",
        group="leg",
        safety_bounds=safety_bounds,
        recent_results_tsv="exp\tscore\n0\t65.0",
        chain_context={"left_hip_yaw": {"best_score": 72.0}},
    )
    assert "left_knee" in prompt
    assert "leg" in prompt
    assert "50.0" in prompt
