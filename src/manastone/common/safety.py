"""
Three-layer safety system.

Layer 1: StaticBoundsChecker  — parameter range validation
Layer 2: PreExperimentChecker — pre-experiment conditions
Layer 3: RuntimeMonitor       — per-sample emergency check
Facade:  SafetyGuard          — composes all three + chain constraint application
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np

from manastone.common.models import ChainContext, JointContext, PIDParams


@dataclass
class SafetyResult:
    safe: bool
    issues: List[str] = field(default_factory=list)
    severity: str = "ok"  # "ok" / "warning" / "critical" / "emergency"

    @classmethod
    def ok(cls) -> "SafetyResult":
        return cls(safe=True, issues=[], severity="ok")


# ---------------------------------------------------------------------------
# Layer 1: static bounds
# ---------------------------------------------------------------------------


class StaticBoundsChecker:
    """Validates PID parameters against configured per-joint bounds."""

    def check(self, joint_name: str, params: PIDParams) -> SafetyResult:
        from manastone.common.config import ManaConfig

        bounds = ManaConfig.get().get_safety_bounds(joint_name)
        issues: List[str] = []

        kp_min, kp_max = bounds["kp_range"]
        ki_min, ki_max = bounds["ki_range"]
        kd_min, kd_max = bounds["kd_range"]

        if not (kp_min <= params.kp <= kp_max):
            issues.append(f"kp={params.kp:.4f} out of range [{kp_min}, {kp_max}]")
        if not (ki_min <= params.ki <= ki_max):
            issues.append(f"ki={params.ki:.4f} out of range [{ki_min}, {ki_max}]")
        if not (kd_min <= params.kd <= kd_max):
            issues.append(f"kd={params.kd:.4f} out of range [{kd_min}, {kd_max}]")

        return SafetyResult(
            safe=len(issues) == 0,
            issues=issues,
            severity="critical" if issues else "ok",
        )


# ---------------------------------------------------------------------------
# Layer 2: pre-experiment conditions
# ---------------------------------------------------------------------------


class PreExperimentChecker:
    """Checks pre-conditions before running a tuning experiment."""

    SOC_MIN = 20.0
    TEMP_MAX = 60.0

    async def check(
        self,
        joint_name: str,
        battery_soc: Optional[float] = None,
        joint_temp: Optional[float] = None,
        comm_ok: Optional[bool] = None,
        mock: bool = False,
    ) -> SafetyResult:
        if mock:
            return SafetyResult.ok()

        issues: List[str] = []

        soc = battery_soc if battery_soc is not None else await self._get_battery_soc()
        temp = joint_temp if joint_temp is not None else await self._get_joint_temp(joint_name)
        comm = comm_ok if comm_ok is not None else await self._check_rosbridge()

        if soc < self.SOC_MIN:
            issues.append(f"Battery SOC {soc:.0f}% < {self.SOC_MIN:.0f}%")
        if temp > self.TEMP_MAX:
            issues.append(f"Joint temp {temp:.1f}°C > {self.TEMP_MAX:.1f}°C")
        if not comm:
            issues.append("rosbridge connection lost")

        severity = "critical" if not comm else ("warning" if issues else "ok")
        return SafetyResult(safe=len(issues) == 0, issues=issues, severity=severity)

    async def _get_battery_soc(self) -> float:
        return 100.0

    async def _get_joint_temp(self, joint_name: str) -> float:
        return 25.0

    async def _check_rosbridge(self) -> bool:
        return True


# ---------------------------------------------------------------------------
# Layer 3: runtime monitor
# ---------------------------------------------------------------------------


class RuntimeMonitor:
    """Per-sample emergency check during experiment execution."""

    LIMITS = {
        "max_torque_nm": 60.0,
        "max_velocity_rad_s": 20.0,
        "max_temp_rise_c": 5.0,
    }

    def check_sample(
        self,
        torque: float,
        velocity: float,
        temp_current: float,
        temp_start: float,
    ) -> SafetyResult:
        issues: List[str] = []

        if abs(torque) > self.LIMITS["max_torque_nm"]:
            issues.append(
                f"|torque|={abs(torque):.1f}Nm > {self.LIMITS['max_torque_nm']}"
            )
        if abs(velocity) > self.LIMITS["max_velocity_rad_s"]:
            issues.append(
                f"|velocity|={abs(velocity):.1f}rad/s > {self.LIMITS['max_velocity_rad_s']}"
            )
        temp_rise = temp_current - temp_start
        if temp_rise > self.LIMITS["max_temp_rise_c"]:
            issues.append(
                f"temp_rise={temp_rise:.1f}°C > {self.LIMITS['max_temp_rise_c']}"
            )

        return SafetyResult(
            safe=len(issues) == 0,
            issues=issues,
            severity="emergency" if issues else "ok",
        )


# ---------------------------------------------------------------------------
# Facade
# ---------------------------------------------------------------------------


class SafetyGuard:
    """Unified safety facade. Use this in all business logic."""

    def __init__(self) -> None:
        self.static = StaticBoundsChecker()
        self.pre_exp = PreExperimentChecker()
        self.runtime = RuntimeMonitor()

    def check_params(self, joint_name: str, params: PIDParams) -> SafetyResult:
        return self.static.check(joint_name, params)

    async def check_pre_experiment(
        self,
        joint_name: str,
        *,
        battery_soc: Optional[float] = None,
        joint_temp: Optional[float] = None,
        comm_ok: Optional[bool] = None,
    ) -> SafetyResult:
        from manastone.common.config import is_mock_mode

        return await self.pre_exp.check(
            joint_name,
            battery_soc=battery_soc,
            joint_temp=joint_temp,
            comm_ok=comm_ok,
            mock=is_mock_mode(),
        )

    def check_runtime_sample(
        self,
        torque: float,
        velocity: float,
        temp_current: float,
        temp_start: float,
    ) -> SafetyResult:
        return self.runtime.check_sample(torque, velocity, temp_current, temp_start)

    def apply_chain_constraints(
        self,
        suggested: Dict[str, PIDParams],
        chain_ctx: ChainContext,
        max_change_pct: float = 0.15,
    ) -> Dict[str, PIDParams]:
        """Apply safety constraints to chain-level suggested params.

        Three-step pipeline per joint:
        1. Clamp fractional change to max_change_pct.
        2. Clip to static bounds.
        3. If anomaly_score > 0.7, only allow decreases.
        """
        from manastone.common.config import ManaConfig

        safe_params: Dict[str, PIDParams] = {}

        for jc in chain_ctx.joints:
            name = jc.joint_name
            if name not in suggested:
                continue

            bounds = ManaConfig.get().get_safety_bounds(name)
            kp_range = bounds["kp_range"]
            ki_range = bounds["ki_range"]
            kd_range = bounds["kd_range"]

            # Step 1: clamp fractional change
            if jc.last_params:
                lp = jc.last_params
                delta_kp = (suggested[name].kp - lp.kp) / lp.kp if lp.kp > 0 else 0.0
                delta_ki = (suggested[name].ki - lp.ki) / lp.ki if lp.ki > 0 else 0.0
                delta_kd = (suggested[name].kd - lp.kd) / lp.kd if lp.kd > 0 else 0.0
                safe = lp.apply_delta(delta_kp, delta_ki, delta_kd, max_change_pct)
            else:
                safe = suggested[name]

            # Step 2: clip to static bounds
            safe = PIDParams(
                kp=float(np.clip(safe.kp, kp_range[0], kp_range[1])),
                ki=float(np.clip(safe.ki, ki_range[0], ki_range[1])),
                kd=float(np.clip(safe.kd, kd_range[0], kd_range[1])),
            )

            # Step 3: anomaly guard — only allow decreases when score > 0.7
            if jc.anomaly_score > 0.7 and jc.last_params:
                lp = jc.last_params
                safe = PIDParams(
                    kp=min(safe.kp, lp.kp),
                    ki=min(safe.ki, lp.ki),
                    kd=min(safe.kd, lp.kd),
                )

            safe_params[name] = safe

        return safe_params
