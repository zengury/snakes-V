"""ChainTuningOrchestrator — orchestrates per-joint AutoResearch in causal order."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Dict, Optional

from manastone.common.models import ChainTuningResult, CommissioningResult
from manastone.common.safety import StaticBoundsChecker
from manastone.commissioning.autoresearch.agent_loop import AutoResearchLoop
from manastone.commissioning.autoresearch.experiment import (
    MockExperimentRunner,
    RealExperimentRunner,
)
from manastone.commissioning.autoresearch.llm_client import LLMParamEditor
from manastone.commissioning.autoresearch.workspace import PIDWorkspace
from manastone.commissioning.chain_scorer import ChainScorer

if TYPE_CHECKING:
    from manastone.common.config import ManaConfig
    from manastone.common.llm_client import LLMClient
    from manastone.profiles.profile import TuningProfile


class ChainTuningOrchestrator:
    """Orchestrates per-joint AutoResearch in causal order (root → tip)."""

    def __init__(
        self,
        config: "ManaConfig",
        profile: "TuningProfile",
        storage_dir: Optional[Path] = None,
        robot_id: str = "default",
        llm_client: Optional["LLMClient"] = None,
    ) -> None:
        self._config = config
        self._profile = profile
        self._storage_dir = storage_dir or config.get_storage_dir()
        self._robot_id = robot_id
        self._llm_client = llm_client

    async def tune_chain(
        self,
        chain_name: str,
        target_score: float = 80.0,
        max_experiments_per_joint: int = 30,
    ) -> ChainTuningResult:
        """Tune all joints in causal order. Returns ChainTuningResult.

        For each joint: AutoResearchLoop with chain_context = {prev_joints: their results}.
        After all joints: ChainScorer.validate().
        """
        joint_order = self._config.get_chain_tuning_order(chain_name)
        joint_results: Dict[str, CommissioningResult] = {}
        total_experiments = 0

        # Create LLM client once for the whole chain
        llm_client = self._llm_client
        if llm_client is None:
            from manastone.common.llm_client import LLMClient
            llm_client = LLMClient()

        # Create safety checker
        safety = StaticBoundsChecker()

        for joint_name in joint_order:
            # Build chain_context from already-tuned joints
            chain_context = dict(joint_results)

            # Create workspace
            workspace = PIDWorkspace(self._robot_id, joint_name, self._storage_dir)
            workspace.tag_chain_start(chain_name)

            # Create runner — ABC injection, no if MOCK_MODE in business logic
            if self._config.is_mock_mode():
                runner = MockExperimentRunner(self._config)
            else:
                runner = RealExperimentRunner(self._config)

            # Create LLM param editor
            llm_editor = LLMParamEditor(llm_client, self._profile)

            # Run research loop
            loop = AutoResearchLoop(
                workspace=workspace,
                runner=runner,
                llm_editor=llm_editor,
                scorer=self._profile.scorer,
                safety=safety,
                config=self._config,
                profile=self._profile,
            )
            result = await loop.run(
                joint_name=joint_name,
                target_score=target_score,
                max_experiments=max_experiments_per_joint,
                chain_context=chain_context,
            )

            joint_results[joint_name] = result
            total_experiments += result.experiment_count

        # Chain-level validation
        scorer = ChainScorer(self._profile)
        chain_score = scorer.validate(
            chain_name=chain_name,
            joint_results=joint_results,
            mock=self._config.is_mock_mode(),
        )

        return ChainTuningResult(
            chain_name=chain_name,
            joint_results=joint_results,
            chain_score=chain_score,
            total_experiments=total_experiments,
        )
