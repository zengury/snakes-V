"""
Unified data models for Manastone Autonomic Operations Layer.

All modules share these models. Do not define duplicate models elsewhere.
Import order: basic → single-joint → chain-level → ops-level.
"""

from __future__ import annotations

import numpy as np
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Basic layer
# ---------------------------------------------------------------------------


class LifecyclePhase(str, Enum):
    COMMISSIONING = "commissioning"
    RUNTIME = "runtime"
    IDLE_TUNING = "idle_tuning"
    MAINTENANCE = "maintenance"


class PIDParams(BaseModel):
    """PID parameters for one joint. apply_delta is the sole mutation path."""

    kp: float = Field(..., ge=0.0, description="Proportional gain")
    ki: float = Field(..., ge=0.0, description="Integral gain")
    kd: float = Field(..., ge=0.0, description="Derivative gain")

    def apply_delta(
        self,
        delta_kp_pct: float,
        delta_ki_pct: float,
        delta_kd_pct: float,
        max_change_pct: float = 0.15,
    ) -> "PIDParams":
        """Return new PIDParams with clamped fractional changes.

        Each delta is a fraction of the current value (e.g. 0.1 = +10%).
        max_change_pct caps the absolute magnitude of each delta.
        """
        clamped_kp = max(-max_change_pct, min(max_change_pct, delta_kp_pct))
        clamped_ki = max(-max_change_pct, min(max_change_pct, delta_ki_pct))
        clamped_kd = max(-max_change_pct, min(max_change_pct, delta_kd_pct))
        return PIDParams(
            kp=max(0.0, self.kp * (1.0 + clamped_kp)),
            ki=max(0.0, self.ki * (1.0 + clamped_ki)),
            kd=max(0.0, self.kd * (1.0 + clamped_kd)),
        )

    @field_validator("kp", "ki", "kd", mode="before")
    @classmethod
    def _round6(cls, v: Any) -> float:
        return round(float(v), 6)


class SystemIdResult(BaseModel):
    """Result from a system identification experiment."""

    joint_name: str
    inertia_kgm2: float
    friction_nm: float
    gravity_comp_nm: float
    noise_std: float
    timestamp: datetime = Field(default_factory=datetime.now)


# ---------------------------------------------------------------------------
# Single-joint layer
# ---------------------------------------------------------------------------


class CommissioningResult(BaseModel):
    """Best PID result after pre-deployment commissioning for one joint."""

    joint_name: str
    base_pid: PIDParams
    best_score: float = Field(0.0, ge=0.0, le=100.0)
    experiment_count: int = 0
    research_log: List[str] = Field(default_factory=list)
    variance_allowance: float = 0.15
    thermal_time_constant: Optional[float] = None


class ThermalModel(BaseModel):
    """Simple first-order thermal model parameters."""

    time_constant_s: float = 45.0
    ambient_c: float = 25.0
    max_safe_c: float = 70.0


class WearModel(BaseModel):
    """Simplified joint wear indicator."""

    cumulative_torque_nm_s: float = 0.0
    estimated_health_pct: float = Field(100.0, ge=0.0, le=100.0)


class JointContext(BaseModel):
    """19-dimensional feature vector for PIDPredictor. All fields required."""

    # Identity
    joint_name: str
    joint_id: int
    group: str  # "leg" / "arm" / "waist" / "head"

    # Thermal
    temp_c: float = 25.0
    temp_trend: float = 0.0  # °C/hour, positive = heating

    # Electrical / mechanical
    current_a: float = 0.0
    torque_nm: float = 0.0
    velocity_rad_s: float = 0.0

    # Control quality
    tracking_error_mean: float = 0.0
    tracking_error_max: float = 0.0
    torque_efficiency: float = 1.0  # 0-1, higher = better

    # Health
    anomaly_score: float = Field(0.0, ge=0.0, le=1.0)
    comm_lost_count: int = 0

    # History
    hours_since_commissioning: float = 0.0
    hours_since_last_tune: float = 0.0
    tune_count: int = 0
    last_params: Optional[PIDParams] = None
    quality_trend: List[float] = Field(default_factory=list)

    # Session context (filled by predictor feature extractor)
    session_idx: int = 0


class TuningSession(BaseModel):
    """Single-joint tuning session record."""

    session_type: Literal["single", "chain"] = "single"
    session_id: str
    robot_id: str
    joint_name: str
    timestamp: datetime = Field(default_factory=datetime.now)
    initial_params: PIDParams
    final_params: PIDParams
    validation_score: float = Field(0.0, ge=0.0, le=100.0)
    improvement_pct: float = 0.0
    rolled_back: bool = False
    notes: str = ""


# ---------------------------------------------------------------------------
# Chain-level layer
# ---------------------------------------------------------------------------


class ValidationAction(BaseModel):
    """Functional validation action specification."""

    action: str
    duration_s: float
    metrics: List[str]
    pass_threshold: float


class ChainContext(BaseModel):
    """Chain-level context aggregating joint contexts.

    feature_vector is a numpy array of shape (len(joints) * 10,).
    """

    chain_name: str
    joints: List[JointContext]
    chain_anomaly_score: float = Field(0.0, ge=0.0, le=1.0)
    cross_joint_coupling: Optional[Dict[str, float]] = None

    @property
    def feature_vector(self) -> "np.ndarray":
        """60-dim (6-joint chain) or N*10 feature array for ChainPredictor."""
        rows = []
        for jc in self.joints:
            lp = jc.last_params
            row = [
                jc.velocity_rad_s,
                jc.torque_nm,
                jc.temp_c,
                lp.kp if lp else 0.0,
                lp.ki if lp else 0.0,
                lp.kd if lp else 0.0,
                jc.tracking_error_mean,
                jc.anomaly_score,
                jc.hours_since_last_tune,
                float(jc.tune_count),
            ]
            rows.append(row)
        return np.array(rows, dtype=np.float32).flatten()

    model_config = {"arbitrary_types_allowed": True}


class ChainTuningResult(BaseModel):
    """Outcome of a chain-level tuning run."""

    chain_name: str
    joint_results: Dict[str, CommissioningResult]
    chain_score: float = Field(0.0, ge=0.0, le=100.0)
    total_experiments: int = 0
    rolled_back: bool = False
    validation_action: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.now)


class ChainTuningSession(TuningSession):
    """Chain-level tuning session. Extends TuningSession via discriminator."""

    session_type: Literal["chain"] = "chain"  # type: ignore[assignment]
    chain_name: str
    joint_sessions: List[TuningSession] = Field(default_factory=list)
    chain_score: float = Field(0.0, ge=0.0, le=100.0)
    validation_action: Optional[str] = None


# ---------------------------------------------------------------------------
# Ops layer
# ---------------------------------------------------------------------------


class InitialContext(BaseModel):
    """Exported context after commissioning. Persisted to JSON file."""

    robot_id: str
    commissioning_date: datetime = Field(default_factory=datetime.now)
    joints: Dict[str, CommissioningResult] = Field(default_factory=dict)
    profile_name: str = "classic_precision"


class RuntimeStateSlice(BaseModel):
    """Snapshot of runtime state for anomaly scoring."""

    timestamp: datetime = Field(default_factory=datetime.now)
    joint_name: str
    position_rad: float
    velocity_rad_s: float
    effort_nm: float
    temp_c: float = 25.0


class PostRunOutcome(BaseModel):
    """Outcome recorded after an experiment or idle-tuning session."""

    session_id: str
    success: bool
    score_before: float
    score_after: float
    rollback_triggered: bool = False
    reason: str = ""
    timestamp: datetime = Field(default_factory=datetime.now)


class ParameterFunction(BaseModel):
    """Symbolic parameter update rule (for coordination rules)."""

    source_joint: str
    source_param: str  # "kp" / "ki" / "kd"
    direction: Literal["increase", "decrease"]
    target_joint: str
    target_param: str
    magnitude_pct: float = 5.0
    description: str = ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def load_session(data: Dict[str, Any]) -> TuningSession:
    """Deserialize a session dict to the correct subclass."""
    if data.get("session_type") == "chain":
        return ChainTuningSession.model_validate(data)
    return TuningSession.model_validate(data)
