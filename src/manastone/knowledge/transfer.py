import asyncio
from pathlib import Path
from typing import Dict, Literal, Optional
from manastone.common.models import PIDParams
from .template_library import TemplateLibrary
from .lineage import ParameterLineage


class KnowledgeTransfer:
    """Cross-robot knowledge inheritance."""

    def __init__(
        self,
        template_lib: Optional[TemplateLibrary] = None,
        lineage: Optional[ParameterLineage] = None,
    ):
        self._templates = template_lib or TemplateLibrary()
        self._lineage = lineage or ParameterLineage()

    async def inherit_template(
        self,
        new_robot_id: str,
        template_id: str,
        adapt_mode: Literal["strict", "adaptive", "zero_shot"] = "adaptive",
        storage_dir: Optional[Path] = None,
    ) -> dict:
        """
        New robot inherits parameter template.

        strict:    Use as-is, no adaptation experiments
        adaptive:  Inherit then run <=10 adaptation experiments
        zero_shot: Use as initial guess, run full commissioning (<=50 experiments)
        """
        template = self._templates.load(template_id)
        profile_id = template["source_profile"]
        source_robot = template["source_robot"]

        # Record inheritance lineage
        self._lineage.record_inheritance(new_robot_id, template_id, source_robot)

        # Reconstruct PIDParams from template
        raw_params = template.get("params", {})
        params: Dict[str, PIDParams] = {}
        for joint_name, p in raw_params.items():
            if isinstance(p, dict):
                params[joint_name] = PIDParams(**{k: v for k, v in p.items() if k in ("kp", "ki", "kd")})

        if adapt_mode == "strict":
            self._lineage.record_tune(new_robot_id, profile_id, "strict_inherit", "inherited")
            return {
                "mode": "strict",
                "robot_id": new_robot_id,
                "template_id": template_id,
                "profile_id": profile_id,
                "experiments": 0,
                "params_count": len(params),
            }

        elif adapt_mode == "adaptive":
            experiments = await self._run_mock_adaptation(
                new_robot_id, profile_id, params,
                max_experiments=10, storage_dir=storage_dir,
            )
            self._lineage.record_tune(new_robot_id, profile_id, "adaptive_inherit", "adapted")
            return {
                "mode": "adaptive",
                "robot_id": new_robot_id,
                "template_id": template_id,
                "profile_id": profile_id,
                "experiments": experiments,
                "params_count": len(params),
            }

        else:  # zero_shot
            experiments = await self._run_mock_adaptation(
                new_robot_id, profile_id, params,
                max_experiments=50, storage_dir=storage_dir,
            )
            self._lineage.record_tune(new_robot_id, profile_id, "zero_shot_inherit", "zero_shot")
            return {
                "mode": "zero_shot",
                "robot_id": new_robot_id,
                "template_id": template_id,
                "profile_id": profile_id,
                "experiments": experiments,
                "params_count": len(params),
            }

    async def _run_mock_adaptation(
        self, robot_id: str, profile_id: str,
        initial_params: Dict[str, PIDParams],
        max_experiments: int,
        storage_dir: Optional[Path],
    ) -> int:
        """Mock adaptation: simulate n experiments with improving scores."""
        await asyncio.sleep(0)  # yield to event loop
        return min(max_experiments, max(1, len(initial_params)))

    def export_template(
        self,
        robot_id: str,
        profile_id: str,
        params: Dict[str, PIDParams],
        environment: dict,
        performance: dict,
        template_lib: Optional[TemplateLibrary] = None,
    ) -> str:
        """Export current robot's best params as a reusable template."""
        lib = template_lib or self._templates
        template_id = f"{robot_id}_{profile_id}_{len(params)}joints"
        lib.create_template(
            template_id=template_id,
            source_robot=robot_id,
            source_profile=profile_id,
            params=params,
            environment=environment,
            performance=performance,
        )
        self._lineage.record_export(robot_id, profile_id, template_id)
        return template_id
