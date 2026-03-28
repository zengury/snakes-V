"""Feature column name constants for PIDPredictor and ChainPredictor.

Q4 from plan: explicit feature column names, no magic numbers.
"""

from __future__ import annotations

from typing import List

JOINT_FEATURE_COLS = [
    "temp_c",
    "temp_trend",
    "current_a",
    "torque_nm",
    "velocity_rad_s",
    "tracking_error_mean",
    "tracking_error_max",
    "torque_efficiency",
    "anomaly_score",
    "hours_since_commissioning_norm",  # / 1000.0
    "hours_since_last_tune",
    "tune_count_norm",                  # / 100.0
    "quality_trend_last",
    "quality_trend_mean",
    "quality_trend_std",
    "last_kp",
    "last_ki",
    "last_kd",
    "comm_lost_count",
]

assert len(JOINT_FEATURE_COLS) == 19, "PIDPredictor expects exactly 19 features"

# ChainContext.feature_vector uses 10 features per joint (from models.py)
CHAIN_JOINT_COLS = [
    "velocity_rad_s",
    "torque_nm",
    "temp_c",
    "last_kp",
    "last_ki",
    "last_kd",
    "tracking_error_mean",
    "anomaly_score",
    "hours_since_last_tune",
    "tune_count",
]

assert len(CHAIN_JOINT_COLS) == 10


def chain_feature_cols(joint_names: List[str]) -> List[str]:
    """Generate full chain feature column list: 10 features × N joints."""
    return [f"{j}_{col}" for j in joint_names for col in CHAIN_JOINT_COLS]
