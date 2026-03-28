"""Experiment runners: ABC + MockExperimentRunner."""

from __future__ import annotations

import math
import random
from abc import ABC, abstractmethod
from typing import List, Tuple

from manastone.common.models import PIDParams
from manastone.profiles.generators.base import ExperimentSpec


class MockJointSimulator:
    """Physics-based mock simulator for a single joint.

    Uses Euler integration of a PID-controlled second-order system.
    """

    # Safety abort thresholds
    MAX_TORQUE_NM = 60.0
    MAX_VEL_RAD_S = 20.0

    def __init__(self, physics: dict) -> None:
        self.inertia: float = float(physics.get("inertia", 0.15))
        self.friction: float = float(physics.get("friction", 0.8))
        self.gravity_comp: float = float(physics.get("gravity_comp", 0.0))
        self.noise_std: float = float(physics.get("noise_std", 0.002))
        self.dt: float = 0.01

    def step_response(
        self,
        kp: float,
        ki: float,
        kd: float,
        setpoint: float,
        duration: float = 2.0,
    ) -> List[Tuple[float, float, float, float]]:
        """Simulate PID step response via Euler integration.

        Returns [(t, pos, vel, torque), ...]
        Aborts early if |torque| > MAX_TORQUE_NM or |vel| > MAX_VEL_RAD_S.
        """
        data: List[Tuple[float, float, float, float]] = []
        pos = 0.0
        vel = 0.0
        integral = 0.0
        prev_error = setpoint - pos

        n_steps = int(duration / self.dt)

        for i in range(n_steps):
            t = i * self.dt
            error = setpoint - pos
            integral += error * self.dt
            derivative = (error - prev_error) / self.dt

            # PID control output
            control = kp * error + ki * integral + kd * derivative

            # Net torque = control - friction*vel - gravity_comp
            torque = control - self.friction * vel - self.gravity_comp

            # Safety abort
            if abs(torque) > self.MAX_TORQUE_NM or abs(vel) > self.MAX_VEL_RAD_S:
                # Record this step and stop
                noise = random.gauss(0, self.noise_std) if self.noise_std > 0 else 0.0
                data.append((t, pos + noise, vel, torque))
                break

            # Euler integration: a = torque / inertia
            accel = torque / max(self.inertia, 1e-6)
            vel = vel + accel * self.dt
            pos = pos + vel * self.dt

            noise = random.gauss(0, self.noise_std) if self.noise_std > 0 else 0.0
            data.append((t, pos + noise, vel, torque))
            prev_error = error

        return data


class ExperimentRunner(ABC):
    """Abstract base for experiment runners."""

    @abstractmethod
    async def run(
        self, pid: PIDParams, spec: ExperimentSpec, joint_name: str
    ) -> Tuple[List[Tuple[float, float, float, float]], str]:
        """Run experiment. Returns (time_series_data, status).

        status: 'ok' | 'safety_torque' | 'safety_velocity' | 'empty'
        """
        ...


class MockExperimentRunner(ExperimentRunner):
    """Mock experiment runner using physics simulation."""

    def __init__(self, config: object) -> None:
        # config is ManaConfig — stored for physics lookup
        self._config = config

    async def run(
        self, pid: PIDParams, spec: ExperimentSpec, joint_name: str
    ) -> Tuple[List[Tuple[float, float, float, float]], str]:
        from manastone.common.config import ManaConfig

        physics = self._config.get_mock_physics(joint_name)  # type: ignore[attr-defined]
        sim = MockJointSimulator(physics)
        data = sim.step_response(pid.kp, pid.ki, pid.kd, spec.setpoint, spec.duration_s)

        if not data:
            return [], "empty"

        # Determine if simulation aborted due to safety
        last_t = data[-1][0]
        expected_duration = spec.duration_s - 2 * sim.dt  # slight tolerance
        aborted = last_t < expected_duration

        if aborted:
            last_torque = abs(data[-1][3])
            last_vel = abs(data[-1][2])
            if last_torque > MockJointSimulator.MAX_TORQUE_NM:
                return data, "safety_torque"
            elif last_vel > MockJointSimulator.MAX_VEL_RAD_S:
                return data, "safety_velocity"
            else:
                return data, "safety_torque"  # generic abort

        return data, "ok"


class RealExperimentRunner(ExperimentRunner):
    """Stub for real robot experiment runner (Phase 3)."""

    def __init__(self, config: object) -> None:
        self._config = config

    async def run(
        self, pid: PIDParams, spec: ExperimentSpec, joint_name: str
    ) -> Tuple[List[Tuple[float, float, float, float]], str]:
        raise NotImplementedError("RealExperimentRunner is implemented in Phase 3")
