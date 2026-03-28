"""MultiProfileCommissioning — runs tune_chain for multiple profiles."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from manastone.common.models import ChainTuningResult
from manastone.commissioning.chain_orchestrator import ChainTuningOrchestrator
from manastone.profiles.profile import TuningProfile
from manastone.profiles.registry import ProfileRegistry


class MultiProfileCommissioning:
    """Runs chain tuning for each profile in the registry or a given list."""

    def __init__(
        self,
        config: object,
        storage_dir: Optional[Path] = None,
        robot_id: str = "default",
        profile_ids: Optional[List[str]] = None,
    ) -> None:
        self._config = config
        self._storage_dir = storage_dir
        self._robot_id = robot_id
        self._profile_ids = profile_ids

    async def run(
        self,
        chain_name: str,
        target_score: float = 80.0,
        max_experiments_per_joint: int = 30,
    ) -> Dict[str, ChainTuningResult]:
        """Run tune_chain for each specified profile. Returns {profile_id: ChainTuningResult}."""
        registry = ProfileRegistry()

        if self._profile_ids is not None:
            profile_ids = self._profile_ids
        else:
            profile_ids = registry.list_compatible()

        results: Dict[str, ChainTuningResult] = {}

        for profile_id in profile_ids:
            profile: TuningProfile = registry.get(profile_id)
            orchestrator = ChainTuningOrchestrator(
                config=self._config,  # type: ignore[arg-type]
                profile=profile,
                storage_dir=self._storage_dir,
                robot_id=f"{self._robot_id}_{profile_id}",
            )
            result = await orchestrator.tune_chain(
                chain_name=chain_name,
                target_score=target_score,
                max_experiments_per_joint=max_experiments_per_joint,
            )
            results[profile_id] = result

        return results
