"""ProfileRegistry — loads builtin and user-defined tuning profiles."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from manastone.profiles.profile import TuningProfile


class ProfileNotFoundError(Exception):
    """Raised when a requested profile does not exist."""


class ProfileRegistry:
    """Loads profiles from builtin/ and config/profiles/ directories.

    Precedence: user overrides (config/profiles/) > builtin.
    """

    _BUILTIN_DIR = Path(__file__).parent / "builtin"
    _USER_DIR = Path("config/profiles")

    def __init__(self, user_profiles_dir: Optional[Path] = None) -> None:
        self._user_dir = user_profiles_dir or self._USER_DIR
        self._profiles: Dict[str, TuningProfile] = {}
        self._loaded = False

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True

        # Load builtin profiles first
        if self._BUILTIN_DIR.exists():
            for yaml_path in sorted(self._BUILTIN_DIR.glob("*.yaml")):
                try:
                    profile = TuningProfile.from_yaml(yaml_path)
                    self._profiles[profile.profile_id] = profile
                except Exception as exc:
                    import warnings
                    warnings.warn(f"Failed to load builtin profile {yaml_path}: {exc}")

        # Load user overrides (may override builtin)
        if self._user_dir.exists():
            for yaml_path in sorted(self._user_dir.glob("*.yaml")):
                try:
                    profile = TuningProfile.from_yaml(yaml_path)
                    self._profiles[profile.profile_id] = profile
                except Exception as exc:
                    import warnings
                    warnings.warn(f"Failed to load user profile {yaml_path}: {exc}")

    def get(self, profile_id: str) -> TuningProfile:
        """Return a TuningProfile by ID. Raises ProfileNotFoundError if not found."""
        self._ensure_loaded()
        if profile_id not in self._profiles:
            raise ProfileNotFoundError(
                f"Profile '{profile_id}' not found. Available: {list(self._profiles.keys())}"
            )
        return self._profiles[profile_id]

    def list_compatible(
        self,
        joint_group: Optional[str] = None,
        task_type: Optional[str] = None,
    ) -> List[str]:
        """List profile IDs compatible with the given joint group and/or task type.

        If both are None, returns all profile IDs.
        Profiles with empty compatible_joint_groups / compatible_tasks match any.
        """
        self._ensure_loaded()
        result = []
        for pid, profile in self._profiles.items():
            group_ok = (
                joint_group is None
                or not profile.compatible_joint_groups
                or joint_group in profile.compatible_joint_groups
            )
            task_ok = (
                task_type is None
                or not profile.compatible_tasks
                or task_type in profile.compatible_tasks
            )
            if group_ok and task_ok:
                result.append(pid)
        return result
