"""
ManaConfig — singleton configuration manager.

Reads config/robot_schema.yaml (or MANASTONE_SCHEMA_PATH).
All modules access configuration through this singleton.
Never call yaml.safe_load directly in other modules.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml


class ManaConfig:
    """Singleton. Load once, read everywhere."""

    _instance: Optional["ManaConfig"] = None

    def __init__(self) -> None:
        self._schema: Optional[Dict[str, Any]] = None
        self._schema_path = Path(
            os.environ.get("MANASTONE_SCHEMA_PATH", "config/robot_schema.yaml")
        )

    @classmethod
    def get(cls) -> "ManaConfig":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset singleton — for testing only."""
        cls._instance = None

    @property
    def schema(self) -> Dict[str, Any]:
        if self._schema is None:
            self._schema = yaml.safe_load(self._schema_path.read_text())
        return self._schema

    # ------------------------------------------------------------------ mode

    def is_mock_mode(self) -> bool:
        return os.environ.get("MANASTONE_MOCK_MODE", "false").lower() == "true"

    def get_storage_dir(self) -> Path:
        return Path(os.environ.get("MANASTONE_STORAGE_DIR", "storage"))

    def get_rosbridge_url(self) -> str:
        return os.environ.get("ROSBRIDGE_URL", "ws://localhost:9090")

    # --------------------------------------------------------------- robot

    def get_robot_type(self) -> str:
        return str(self.schema["robot"]["type"])

    def get_motor_index_map(self) -> Dict[str, int]:
        return dict(self.schema["robot"]["motor_index_map"])

    def get_joint_group(self, joint_name: str) -> str:
        for chain_name, joints in self.get_kinematic_chains().items():
            if joint_name in joints:
                if "leg" in chain_name:
                    return "leg"
                if "arm" in chain_name:
                    return "arm"
                if "waist" in chain_name:
                    return "waist"
        return "head"

    def get_kinematic_chains(self) -> Dict[str, List[str]]:
        return dict(self.schema["robot"]["kinematic_chains"])

    def get_chain_tuning_order(self, chain_name: str) -> List[str]:
        return list(self.schema["robot"]["chain_tuning_order"][chain_name])

    def get_all_joint_names(self) -> List[str]:
        return list(self.get_motor_index_map().keys())

    # ------------------------------------------------------------- lifecycle

    def get_lifecycle_config(self) -> Dict[str, Any]:
        return dict(self.schema["robot"]["lifecycle"])

    def get_safety_bounds(self, joint_name: Optional[str] = None) -> Dict[str, Any]:
        # Per-joint overrides deferred to future version.
        return dict(
            self.schema["robot"]["lifecycle"]["commissioning"]["safety_bounds"]
        )

    def get_idle_trigger_config(self) -> Dict[str, Any]:
        return dict(self.schema["robot"]["lifecycle"]["idle_tuning"]["trigger"])

    def get_scheduling_config(self) -> Dict[str, Any]:
        return dict(self.schema["robot"]["lifecycle"]["idle_tuning"]["scheduling"])

    def get_validation_action(self, action_name: str) -> Dict[str, Any]:
        return dict(self.schema["robot"]["validation_actions"][action_name])

    # --------------------------------------------------------------- physics

    def get_mock_physics(self, joint_name: str) -> Dict[str, Any]:
        mp = self.schema["robot"].get("mock_physics", {})
        default: Dict[str, Any] = mp.get(
            "default",
            {"inertia": 0.15, "friction": 0.8, "gravity_comp": 0.0, "noise_std": 0.002},
        )
        overrides: Dict[str, Any] = mp.get("overrides", {}).get(joint_name, {})
        return {**default, **overrides}

    def get_thresholds(self) -> Dict[str, float]:
        return dict(self.schema["robot"]["thresholds"])

    # ------------------------------------------------------------------ LLM

    def get_llm_model(self) -> str:
        return os.environ.get("MANASTONE_LLM_MODEL", "claude-sonnet-4-20250514")

    def get_llm_timeout(self) -> int:
        return int(os.environ.get("MANASTONE_LLM_TIMEOUT", "60"))

    def get_max_tokens_per_session(self) -> int:
        return int(os.environ.get("MANASTONE_MAX_TOKENS", "100000"))

    def create_param_writer(self):
        from manastone.idle_tuning.executor.param_writer import MockParamWriter, RealParamWriter

        if self.is_mock_mode():
            return MockParamWriter()
        return RealParamWriter(self.get_rosbridge_url())


# Convenience shortcuts used throughout the codebase.
def is_mock_mode() -> bool:
    return ManaConfig.get().is_mock_mode()


def load_robot_schema() -> ManaConfig:
    return ManaConfig.get()
