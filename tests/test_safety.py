"""
Tests for common/safety.py.

M1 criterion 4: kp=999 → StaticBoundsChecker blocks it.
"""
import pytest

from manastone.common.models import ChainContext, JointContext, PIDParams
from manastone.common.safety import RuntimeMonitor, SafetyGuard, SafetyResult, StaticBoundsChecker


# ---------------------------------------------------------------------------
# StaticBoundsChecker
# ---------------------------------------------------------------------------


class TestStaticBoundsChecker:
    def test_valid_params_passes(self, sample_pid):
        checker = StaticBoundsChecker()
        result = checker.check("left_knee", sample_pid)
        assert result.safe is True
        assert result.severity == "ok"
        assert result.issues == []

    def test_kp_999_blocked(self):
        """M1 criterion 4."""
        checker = StaticBoundsChecker()
        bad = PIDParams(kp=999.0, ki=0.5, kd=2.0)
        result = checker.check("left_knee", bad)
        assert result.safe is False
        assert result.severity == "critical"
        assert any("kp" in issue for issue in result.issues)

    def test_ki_out_of_range_blocked(self):
        checker = StaticBoundsChecker()
        bad = PIDParams(kp=10.0, ki=50.0, kd=2.0)
        result = checker.check("left_knee", bad)
        assert result.safe is False
        assert any("ki" in issue for issue in result.issues)

    def test_kd_out_of_range_blocked(self):
        checker = StaticBoundsChecker()
        bad = PIDParams(kp=10.0, ki=0.5, kd=100.0)
        result = checker.check("left_knee", bad)
        assert result.safe is False
        assert any("kd" in issue for issue in result.issues)

    def test_boundary_values_pass(self):
        """Params exactly at bounds should pass."""
        checker = StaticBoundsChecker()
        # kp_range: [1.0, 50.0], ki_range: [0.0, 10.0], kd_range: [0.0, 20.0]
        at_max = PIDParams(kp=50.0, ki=10.0, kd=20.0)
        result = checker.check("left_knee", at_max)
        assert result.safe is True

        at_min = PIDParams(kp=1.0, ki=0.0, kd=0.0)
        result = checker.check("left_knee", at_min)
        assert result.safe is True

    def test_all_params_out_of_range(self):
        checker = StaticBoundsChecker()
        bad = PIDParams(kp=999.0, ki=999.0, kd=999.0)
        result = checker.check("left_knee", bad)
        assert result.safe is False
        assert len(result.issues) == 3


# ---------------------------------------------------------------------------
# RuntimeMonitor
# ---------------------------------------------------------------------------


class TestRuntimeMonitor:
    def test_normal_sample_ok(self):
        monitor = RuntimeMonitor()
        result = monitor.check_sample(torque=10.0, velocity=2.0, temp_current=30.0, temp_start=28.0)
        assert result.safe is True
        assert result.severity == "ok"

    def test_torque_over_limit_emergency(self):
        monitor = RuntimeMonitor()
        result = monitor.check_sample(torque=70.0, velocity=2.0, temp_current=30.0, temp_start=28.0)
        assert result.safe is False
        assert result.severity == "emergency"
        assert any("torque" in i for i in result.issues)

    def test_velocity_over_limit(self):
        monitor = RuntimeMonitor()
        result = monitor.check_sample(torque=5.0, velocity=25.0, temp_current=30.0, temp_start=28.0)
        assert result.safe is False
        assert result.severity == "emergency"

    def test_temp_rise_over_limit(self):
        monitor = RuntimeMonitor()
        result = monitor.check_sample(torque=5.0, velocity=2.0, temp_current=36.0, temp_start=28.0)
        assert result.safe is False
        assert result.severity == "emergency"
        assert any("temp" in i for i in result.issues)

    def test_negative_torque_also_checked(self):
        monitor = RuntimeMonitor()
        result = monitor.check_sample(torque=-70.0, velocity=2.0, temp_current=30.0, temp_start=28.0)
        assert result.safe is False


# ---------------------------------------------------------------------------
# SafetyGuard
# ---------------------------------------------------------------------------


class TestSafetyGuard:
    @pytest.mark.asyncio
    async def test_pre_experiment_mock_returns_ok(self):
        guard = SafetyGuard()
        result = await guard.check_pre_experiment("left_knee")
        assert result.safe is True

    def test_check_params_delegates_to_static(self, sample_pid):
        guard = SafetyGuard()
        result = guard.check_params("left_knee", sample_pid)
        assert result.safe is True

    def test_apply_chain_constraints_clamps_large_delta(self):
        """Change > 15% should be clamped."""
        guard = SafetyGuard()
        from manastone.common.models import ChainContext, JointContext

        base = PIDParams(kp=10.0, ki=1.0, kd=2.0)
        jc = JointContext(
            joint_name="left_knee",
            joint_id=3,
            group="leg",
            last_params=base,
            anomaly_score=0.1,
        )
        chain = ChainContext(chain_name="left_leg", joints=[jc], chain_anomaly_score=0.1)

        # Suggest a 50% increase — should be clamped to 15%
        suggested = {"left_knee": PIDParams(kp=15.0, ki=1.5, kd=3.0)}
        result = guard.apply_chain_constraints(suggested, chain, max_change_pct=0.15)

        assert "left_knee" in result
        constrained = result["left_knee"]
        # Max allowed: 10.0 * 1.15 = 11.5
        assert constrained.kp <= 11.5 + 1e-6

    def test_apply_chain_constraints_anomaly_guard(self):
        """When anomaly_score > 0.7, params should only decrease."""
        guard = SafetyGuard()
        from manastone.common.models import ChainContext, JointContext

        base = PIDParams(kp=10.0, ki=1.0, kd=2.0)
        jc = JointContext(
            joint_name="left_knee",
            joint_id=3,
            group="leg",
            last_params=base,
            anomaly_score=0.9,  # high anomaly
        )
        chain = ChainContext(chain_name="left_leg", joints=[jc], chain_anomaly_score=0.9)

        # Suggest an increase — should be blocked down to current value
        suggested = {"left_knee": PIDParams(kp=11.0, ki=1.1, kd=2.2)}
        result = guard.apply_chain_constraints(suggested, chain)
        constrained = result["left_knee"]
        assert constrained.kp <= base.kp + 1e-6
        assert constrained.ki <= base.ki + 1e-6
        assert constrained.kd <= base.kd + 1e-6


# ---------------------------------------------------------------------------
# PIDParams.apply_delta
# ---------------------------------------------------------------------------


class TestPIDParamsApplyDelta:
    def test_positive_delta_applied(self):
        p = PIDParams(kp=10.0, ki=1.0, kd=2.0)
        p2 = p.apply_delta(0.1, 0.1, 0.1)
        assert abs(p2.kp - 11.0) < 1e-5
        assert abs(p2.ki - 1.1) < 1e-5
        assert abs(p2.kd - 2.2) < 1e-5

    def test_delta_clamped_to_max_change_pct(self):
        p = PIDParams(kp=10.0, ki=1.0, kd=2.0)
        p2 = p.apply_delta(0.5, 0.5, 0.5, max_change_pct=0.15)
        # clamped to 15%
        assert abs(p2.kp - 11.5) < 1e-5

    def test_negative_delta_does_not_go_below_zero(self):
        p = PIDParams(kp=1.0, ki=0.0, kd=0.0)
        p2 = p.apply_delta(-0.99, -0.99, -0.99, max_change_pct=1.0)
        assert p2.kp >= 0.0
        assert p2.ki >= 0.0
        assert p2.kd >= 0.0
