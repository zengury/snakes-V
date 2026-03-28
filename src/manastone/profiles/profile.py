"""TuningProfile — pluggable configuration for joint commissioning."""

from __future__ import annotations

import importlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from manastone.profiles.generators.base import BaseGenerator
from manastone.profiles.scorers.base import BaseScorer


@dataclass
class TuningProfile:
    profile_id: str
    version: str
    description: str
    llm_prompt_template: str
    compatible_joint_groups: List[str]
    compatible_tasks: List[str]
    scorer: BaseScorer
    generator: BaseGenerator
    safety_overrides: Dict[str, Any]
    feature_schema: List[str]

    def render_prompt(
        self,
        joint_name: str,
        group: str,
        safety_bounds: Dict[str, Any],
        recent_results_tsv: str = "",
        chain_context: Optional[Dict[str, Any]] = None,
    ) -> str:
        kp_min, kp_max = safety_bounds.get("kp_range", [0.1, 50.0])
        ki_min, ki_max = safety_bounds.get("ki_range", [0.0, 5.0])
        kd_min, kd_max = safety_bounds.get("kd_range", [0.0, 10.0])
        chain_ctx_str = json.dumps(chain_context or {}, indent=2)

        return self.llm_prompt_template.format(
            joint_name=joint_name,
            group=group,
            kp_min=kp_min,
            kp_max=kp_max,
            ki_min=ki_min,
            ki_max=ki_max,
            kd_min=kd_min,
            kd_max=kd_max,
            recent_results=recent_results_tsv,
            chain_context=chain_ctx_str,
        )

    @classmethod
    def from_yaml(cls, path: Path) -> "TuningProfile":
        """Load a TuningProfile from a YAML file, dynamically importing scorer/generator."""
        raw = yaml.safe_load(path.read_text())

        scorer = _instantiate(raw["scorer"]["class"], raw["scorer"].get("params", {}))
        generator = _instantiate(
            raw["experiment_generator"]["class"],
            raw["experiment_generator"].get("params", {}),
        )

        return cls(
            profile_id=raw["profile_id"],
            version=str(raw.get("version", "1.0")),
            description=raw.get("description", ""),
            llm_prompt_template=raw.get("llm_prompt", ""),
            compatible_joint_groups=raw.get("compatible_joint_groups", []),
            compatible_tasks=raw.get("compatible_tasks", []),
            scorer=scorer,
            generator=generator,
            safety_overrides=raw.get("safety", {}),
            feature_schema=raw.get("features", []),
        )


def _instantiate(class_path: str, params: Dict[str, Any]) -> Any:
    """Dynamically import and instantiate a class from a dotted path."""
    module_path, class_name = class_path.rsplit(".", 1)
    module = importlib.import_module(module_path)
    cls = getattr(module, class_name)
    return cls(**params)
