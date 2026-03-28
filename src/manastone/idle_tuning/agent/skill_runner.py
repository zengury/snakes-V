"""SkillRunner — loads .md skill files and runs them via LLM."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional

import yaml

from manastone.common.models import ChainContext, PIDParams


class SkillRunner:
    def __init__(self, skills_dir: Optional[Path] = None, llm_client=None):
        self.skills_dir = skills_dir or (Path(__file__).parent / "skills")
        self.llm = llm_client
        self._skills: Dict[str, dict] = {}
        self._load_all()

    def _load_all(self) -> None:
        if not self.skills_dir.exists():
            return
        for f in self.skills_dir.glob("*.md"):
            try:
                skill = self._parse_skill(f)
                self._skills[skill["meta"]["name"]] = skill
            except Exception:
                pass

    def _parse_skill(self, path: Path) -> dict:
        content = path.read_text()
        parts = content.split("---", 2)
        if len(parts) < 3:
            raise ValueError(f"Invalid skill file: {path}")
        meta = yaml.safe_load(parts[1])
        body = parts[2].strip()
        return {"meta": meta, "system_prompt": body}

    async def run(
        self,
        skill_name: str,
        chain_context: ChainContext,
        xgb_prior: Optional[Dict] = None,
        confidence: float = 0.0,
    ) -> Dict[str, PIDParams]:
        """Run skill and return {joint_name: PIDParams}"""
        skill = self._skills.get(skill_name)
        if not skill:
            return self._conservative_fallback(chain_context)

        user_msg = self._format_context(chain_context, xgb_prior, confidence)

        try:
            response = await self.llm.call(
                system=skill["system_prompt"],
                user=user_msg,
            )
            return self._parse_yaml_output(response, chain_context)
        except Exception:
            return self._conservative_fallback(chain_context)

    def _format_context(self, ctx: ChainContext, xgb_prior, confidence) -> str:
        lines = [
            f"Chain: {ctx.chain_name}",
            f"Chain anomaly score: {ctx.chain_anomaly_score:.3f}",
            "",
        ]
        for jc in ctx.joints:
            lp = jc.last_params
            lines.append(f"Joint: {jc.joint_name}")
            lines.append(f"  anomaly_score: {jc.anomaly_score:.3f}")
            lines.append(f"  temp_c: {jc.temp_c:.1f}")
            lines.append(f"  torque_nm: {jc.torque_nm:.2f}")
            lines.append(f"  tracking_error_mean: {jc.tracking_error_mean:.4f}")
            if lp:
                lines.append(
                    f"  current_params: kp={lp.kp:.4f}, ki={lp.ki:.4f}, kd={lp.kd:.4f}"
                )

        if xgb_prior:
            lines.append(f"\nXGBoost prior (confidence={confidence:.2f}):")
            for joint_name, (dkp, dki, dkd) in xgb_prior.items():
                lines.append(
                    f"  {joint_name}: Δkp={dkp:+.4f}, Δki={dki:+.4f}, Δkd={dkd:+.4f}"
                )

        lines.append("\nOutput the complete params.yaml for all joints:")
        return "\n".join(lines)

    def _parse_yaml_output(self, response: str, ctx: ChainContext) -> Dict[str, PIDParams]:
        """Parse LLM yaml output → {joint_name: PIDParams}"""
        import re

        # Extract yaml block
        match = re.search(r"```yaml\s*(.*?)```", response, re.DOTALL)
        yaml_str = match.group(1) if match else response

        try:
            data = yaml.safe_load(yaml_str)
            if isinstance(data, dict) and "joints" in data:
                joints_data = data["joints"]
            else:
                joints_data = data

            result = {}
            for jc in ctx.joints:
                if isinstance(joints_data, dict) and jc.joint_name in joints_data:
                    j = joints_data[jc.joint_name]
                    if isinstance(j, dict):
                        result[jc.joint_name] = PIDParams(
                            kp=float(j.get("kp", jc.last_params.kp if jc.last_params else 1.0)),
                            ki=float(j.get("ki", jc.last_params.ki if jc.last_params else 0.1)),
                            kd=float(j.get("kd", jc.last_params.kd if jc.last_params else 0.1)),
                        )
                    else:
                        result[jc.joint_name] = jc.last_params or PIDParams(kp=1.0, ki=0.1, kd=0.1)
                else:
                    result[jc.joint_name] = jc.last_params or PIDParams(kp=1.0, ki=0.1, kd=0.1)
            return result
        except Exception:
            return self._conservative_fallback(ctx)

    def _conservative_fallback(self, ctx: ChainContext) -> Dict[str, PIDParams]:
        """No change — return current params."""
        result = {}
        for jc in ctx.joints:
            result[jc.joint_name] = jc.last_params or PIDParams(kp=1.0, ki=0.1, kd=0.1)
        return result
