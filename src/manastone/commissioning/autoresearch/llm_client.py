"""LLMParamEditor — wraps LLMClient for PID param suggestion with Optuna hybrid."""

from __future__ import annotations

import random
from typing import TYPE_CHECKING, Dict, Optional, Tuple

import yaml

from manastone.common.models import PIDParams
from manastone.profiles.scorers.base import ScorerResult

if TYPE_CHECKING:
    from manastone.common.llm_client import LLMClient
    from manastone.profiles.profile import TuningProfile


class LLMParamEditor:
    """Wraps LLMClient for PID param editing.

    Hybrid BO+LLM: Optuna TPE proposes numerical params,
    LLM generates the hypothesis annotation.
    If LLM is unavailable, falls back to rule engine.
    """

    def __init__(self, llm_client: "LLMClient", profile: "TuningProfile") -> None:
        self._llm = llm_client
        self._profile = profile

    async def propose_params(
        self,
        joint_name: str,
        group: str,
        safety_bounds: Dict[str, object],
        recent_results_tsv: str,
        chain_context: Dict[str, object],
        optuna_suggestion: Optional[Dict[str, float]] = None,
        current_pid: Optional[PIDParams] = None,
        last_result: Optional[ScorerResult] = None,
    ) -> Tuple[PIDParams, str]:
        """Returns (new_params, hypothesis_str).

        If optuna_suggestion is provided, use those values and ask LLM for hypothesis only.
        If LLM fails, use fallback rule engine.
        """
        if optuna_suggestion is not None:
            # Optuna proposed the numerical values — ask LLM for hypothesis only
            new_pid = PIDParams(
                kp=float(optuna_suggestion["kp"]),
                ki=float(optuna_suggestion["ki"]),
                kd=float(optuna_suggestion["kd"]),
            )
            hypothesis = await self._get_hypothesis_from_llm(
                joint_name, group, safety_bounds, recent_results_tsv,
                chain_context, new_pid
            )
            return new_pid, hypothesis

        # No Optuna suggestion — ask LLM for full params
        try:
            if not self._llm.available:
                raise RuntimeError("LLM unavailable")

            prompt = self._profile.render_prompt(
                joint_name=joint_name,
                group=group,
                safety_bounds=safety_bounds,  # type: ignore[arg-type]
                recent_results_tsv=recent_results_tsv,
                chain_context=chain_context,  # type: ignore[arg-type]
            )
            system = "You are a PID tuning expert. Output only valid params.yaml content."
            raw = await self._llm.call(system=system, user=prompt, max_tokens=500)
            return self._parse_llm_output(raw, current_pid, safety_bounds)
        except Exception:
            return self._fallback_rule_engine(current_pid, last_result, safety_bounds)

    async def _get_hypothesis_from_llm(
        self,
        joint_name: str,
        group: str,
        safety_bounds: Dict[str, object],
        recent_results_tsv: str,
        chain_context: Dict[str, object],
        proposed_pid: PIDParams,
    ) -> str:
        """Ask LLM for a hypothesis annotation only."""
        try:
            if not self._llm.available:
                raise RuntimeError("LLM unavailable")

            user = (
                f"Joint: {joint_name}, Group: {group}\n"
                f"Proposed: kp={proposed_pid.kp:.4f}, ki={proposed_pid.ki:.4f}, kd={proposed_pid.kd:.4f}\n"
                f"Recent results:\n{recent_results_tsv}\n"
                f"Write ONE sentence hypothesis (no YAML, just text):"
            )
            system = "You are a PID tuning expert. Output only a single hypothesis sentence."
            hypothesis = await self._llm.call(system=system, user=user, max_tokens=100)
            return hypothesis.strip().replace("\n", " ")
        except Exception:
            return f"Optuna TPE proposal: kp={proposed_pid.kp:.3f}, ki={proposed_pid.ki:.3f}, kd={proposed_pid.kd:.3f}"

    def _parse_llm_output(
        self,
        raw: str,
        current_pid: Optional[PIDParams],
        safety_bounds: Dict[str, object],
    ) -> Tuple[PIDParams, str]:
        """Parse LLM YAML output. Extract hypothesis comment."""
        from manastone.common.llm_client import LLMClient

        yaml_text = LLMClient.extract_yaml(raw)

        # Extract hypothesis comment
        hypothesis = ""
        for line in yaml_text.splitlines():
            stripped = line.strip()
            if stripped.startswith("# hypothesis:"):
                hypothesis = stripped[len("# hypothesis:"):].strip()
                break

        try:
            parsed = yaml.safe_load(yaml_text)
            if not isinstance(parsed, dict):
                raise ValueError("LLM output is not a YAML dict")

            kp_range = safety_bounds.get("kp_range", [1.0, 50.0])  # type: ignore[assignment]
            ki_range = safety_bounds.get("ki_range", [0.0, 10.0])  # type: ignore[assignment]
            kd_range = safety_bounds.get("kd_range", [0.0, 20.0])  # type: ignore[assignment]

            kp = float(parsed.get("kp", current_pid.kp if current_pid else 5.0))
            ki = float(parsed.get("ki", current_pid.ki if current_pid else 0.1))
            kd = float(parsed.get("kd", current_pid.kd if current_pid else 0.5))

            # Clamp to bounds
            kp = max(float(kp_range[0]), min(float(kp_range[1]), kp))  # type: ignore[index]
            ki = max(float(ki_range[0]), min(float(ki_range[1]), ki))  # type: ignore[index]
            kd = max(float(kd_range[0]), min(float(kd_range[1]), kd))  # type: ignore[index]

            new_pid = PIDParams(kp=kp, ki=ki, kd=kd)
            if not hypothesis:
                hypothesis = "LLM proposal"
            return new_pid, hypothesis

        except Exception:
            return self._fallback_rule_engine(current_pid, None, safety_bounds)

    def _fallback_rule_engine(
        self,
        current: Optional[PIDParams],
        last_result: Optional[ScorerResult],
        safety_bounds: Optional[Dict[str, object]] = None,
    ) -> Tuple[PIDParams, str]:
        """Rule engine fallback from SPEC DD-C05.

        Rules:
        - overshoot > 15% → Kp *= 0.9, Kd *= 1.1
        - rise_time > 0.8s → Kp *= 1.05
        - oscillation > 2 → Kd *= 1.15, Ki *= 0.8
        - default → small random perturbation
        """
        if current is None:
            kp_range = safety_bounds.get("kp_range", [1.0, 50.0]) if safety_bounds else [1.0, 50.0]  # type: ignore
            ki_range = safety_bounds.get("ki_range", [0.0, 10.0]) if safety_bounds else [0.0, 10.0]  # type: ignore
            kd_range = safety_bounds.get("kd_range", [0.0, 20.0]) if safety_bounds else [0.0, 20.0]  # type: ignore
            kp = random.uniform(float(kp_range[0]), float(kp_range[1]) * 0.3)  # type: ignore[index]
            ki = random.uniform(float(ki_range[0]), float(ki_range[1]) * 0.2)  # type: ignore[index]
            kd = random.uniform(float(kd_range[0]), float(kd_range[1]) * 0.2)  # type: ignore[index]
            return PIDParams(kp=kp, ki=ki, kd=kd), "rule: initial random guess"

        kp, ki, kd = current.kp, current.ki, current.kd
        reason = "rule: small perturbation"

        if last_result is not None:
            if last_result.overshoot_pct > 15.0:
                kp *= 0.9
                kd *= 1.1
                reason = f"rule: overshoot={last_result.overshoot_pct:.1f}% → reduce kp, increase kd"
            elif last_result.rise_time_s > 0.8:
                kp *= 1.05
                reason = f"rule: rise_time={last_result.rise_time_s:.2f}s → increase kp"
            elif last_result.oscillation_count > 2:
                kd *= 1.15
                ki *= 0.8
                reason = f"rule: oscillation={last_result.oscillation_count} → increase kd, reduce ki"
            else:
                # Small random perturbation
                kp *= random.uniform(0.95, 1.05)
                ki *= random.uniform(0.95, 1.05)
                kd *= random.uniform(0.95, 1.05)
        else:
            kp *= random.uniform(0.95, 1.05)
            ki *= random.uniform(0.95, 1.05)
            kd *= random.uniform(0.95, 1.05)

        # Clamp to bounds
        if safety_bounds:
            kp_range = safety_bounds.get("kp_range", [1.0, 50.0])  # type: ignore
            ki_range = safety_bounds.get("ki_range", [0.0, 10.0])  # type: ignore
            kd_range = safety_bounds.get("kd_range", [0.0, 20.0])  # type: ignore
            kp = max(float(kp_range[0]), min(float(kp_range[1]), kp))  # type: ignore[index]
            ki = max(float(ki_range[0]), min(float(ki_range[1]), ki))  # type: ignore[index]
            kd = max(float(kd_range[0]), min(float(kd_range[1]), kd))  # type: ignore[index]

        return PIDParams(kp=max(0.0, kp), ki=max(0.0, ki), kd=max(0.0, kd)), reason
