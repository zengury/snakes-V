import yaml
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from manastone.common.models import PIDParams


class TemplateNotFoundError(Exception):
    pass


class TemplateLibrary:
    """Inheritable parameter template library."""

    def __init__(self, base_dir: Path = Path("storage/knowledge_base/template_library")):
        self._base = base_dir

    def create_template(
        self,
        template_id: str,
        source_robot: str,
        source_profile: str,
        params: Dict[str, PIDParams],
        environment: dict,
        performance: dict,
    ) -> Path:
        """Create a template from a robot's tuning results."""
        d = self._base / "by_scenario"
        d.mkdir(parents=True, exist_ok=True)

        template = {
            "template_id": template_id,
            "source_robot": source_robot,
            "source_profile": source_profile,
            "created_at": datetime.now().isoformat(),
            "params": {k: v.model_dump() for k, v in params.items()},
            "environment": environment,
            "performance": performance,
        }
        path = d / f"{template_id}.yaml"
        path.write_text(yaml.dump(template, default_flow_style=False))
        return path

    def load(self, template_id: str) -> dict:
        for subdir in ["by_scenario", "by_degradation"]:
            path = self._base / subdir / f"{template_id}.yaml"
            if path.exists():
                return yaml.safe_load(path.read_text())
        raise TemplateNotFoundError(f"Template not found: {template_id}")

    def list_all(self) -> List[dict]:
        result = []
        for f in self._base.rglob("*.yaml"):
            try:
                result.append(yaml.safe_load(f.read_text()))
            except Exception:
                continue
        return result

    def query_similar(self, environment: dict) -> List[dict]:
        """Query templates ranked by environment similarity."""
        templates = []
        for t in self.list_all():
            sim = self._env_similarity(environment, t.get("environment", {}))
            templates.append({**t, "similarity": sim})
        return sorted(templates, key=lambda t: t["similarity"], reverse=True)

    @staticmethod
    def _env_similarity(env_a: dict, env_b: dict) -> float:
        if not env_a or not env_b:
            return 0.0
        common = set(env_a) & set(env_b)
        if not common:
            return 0.0
        matches = sum(1 for k in common if env_a[k] == env_b[k])
        return matches / max(len(env_a), len(env_b))
