"""AutoResearchLoop — Karpathy-style PID research with Optuna TPE + LLM annotations."""

from __future__ import annotations

from typing import TYPE_CHECKING, Dict, Optional

from manastone.common.models import CommissioningResult, PIDParams
from manastone.profiles.scorers.base import ScorerResult

if TYPE_CHECKING:
    from manastone.common.config import ManaConfig
    from manastone.common.safety import StaticBoundsChecker
    from manastone.commissioning.autoresearch.experiment import ExperimentRunner
    from manastone.commissioning.autoresearch.llm_client import LLMParamEditor
    from manastone.commissioning.autoresearch.workspace import PIDWorkspace
    from manastone.profiles.profile import TuningProfile
    from manastone.profiles.scorers.base import BaseScorer


class AutoResearchLoop:
    """Karpathy-style PID research loop with Optuna TPE + LLM annotations.

    Inner search: Optuna TPE sampler proposes (kp, ki, kd) values.
    LLM role: generates hypothesis string only (not param values).
    """

    def __init__(
        self,
        workspace: "PIDWorkspace",
        runner: "ExperimentRunner",
        llm_editor: "LLMParamEditor",
        scorer: "BaseScorer",
        safety: "StaticBoundsChecker",
        config: "ManaConfig",
        profile: "TuningProfile",
    ) -> None:
        self._workspace = workspace
        self._runner = runner
        self._llm_editor = llm_editor
        self._scorer = scorer
        self._safety = safety
        self._config = config
        self._profile = profile

    async def run(
        self,
        joint_name: str,
        target_score: float = 70.0,
        max_experiments: int = 30,
        chain_context: Optional[Dict[str, CommissioningResult]] = None,
    ) -> CommissioningResult:
        """Run research loop for one joint. Returns best CommissioningResult."""
        safety_bounds = self._config.get_safety_bounds(joint_name)
        group = self._config.get_joint_group(joint_name)

        # Try to import optuna
        use_optuna = False
        study = None
        try:
            import optuna

            optuna.logging.set_verbosity(optuna.logging.WARNING)
            study = optuna.create_study(
                direction="maximize", sampler=optuna.samplers.TPESampler(seed=42)
            )
            use_optuna = True
        except ImportError:
            pass

        best_score = 0.0
        best_pid = self._workspace.read_params()
        consecutive_discards = 0
        last_result: Optional[ScorerResult] = None
        trial = None

        for exp_num in range(max_experiments):
            # 1. Propose params via Optuna (or random fallback)
            optuna_suggestion: Optional[Dict[str, float]] = None

            if use_optuna and study is not None and exp_num > 0:
                trial = study.ask()
                kp = trial.suggest_float("kp", safety_bounds["kp_range"][0], safety_bounds["kp_range"][1])
                ki = trial.suggest_float("ki", safety_bounds["ki_range"][0], safety_bounds["ki_range"][1])
                kd = trial.suggest_float("kd", safety_bounds["kd_range"][0], safety_bounds["kd_range"][1])
                optuna_suggestion = {"kp": kp, "ki": ki, "kd": kd}

            # 2. LLM generates hypothesis (and params if no Optuna)
            current_pid = self._workspace.read_params()
            recent_tsv = self._workspace.get_results_tsv_tail(15)
            chain_ctx_dict: Dict[str, object] = {}
            if chain_context:
                for k, v in chain_context.items():
                    chain_ctx_dict[k] = {
                        "joint_name": v.joint_name,
                        "best_score": v.best_score,
                        "base_pid": {"kp": v.base_pid.kp, "ki": v.base_pid.ki, "kd": v.base_pid.kd},
                        "experiment_count": v.experiment_count,
                    }

            try:
                new_pid, hypothesis = await self._llm_editor.propose_params(
                    joint_name=joint_name,
                    group=group,
                    safety_bounds=safety_bounds,  # type: ignore[arg-type]
                    recent_results_tsv=recent_tsv,
                    chain_context=chain_ctx_dict,
                    optuna_suggestion=optuna_suggestion,
                    current_pid=current_pid,
                    last_result=last_result,
                )
            except Exception:
                new_pid, hypothesis = self._llm_editor._fallback_rule_engine(
                    current_pid, last_result, safety_bounds  # type: ignore[arg-type]
                )

            # 3. Safety check — clamp to bounds if needed
            safety_result = self._safety.check(joint_name, new_pid)
            if not safety_result.safe:
                kp_range = safety_bounds["kp_range"]
                ki_range = safety_bounds["ki_range"]
                kd_range = safety_bounds["kd_range"]
                new_pid = PIDParams(
                    kp=max(float(kp_range[0]), min(float(kp_range[1]), new_pid.kp)),
                    ki=max(float(ki_range[0]), min(float(ki_range[1]), new_pid.ki)),
                    kd=max(float(kd_range[0]), min(float(kd_range[1]), new_pid.kd)),
                )

            # 4. Write params
            self._workspace.write_params(new_pid, hypothesis)

            # 5. Run experiment
            spec = self._profile.generator.generate(joint_name, group)
            data, status = await self._runner.run(new_pid, spec, joint_name)

            # 6. Score
            if status == "ok" and data:
                result = self._scorer.score(data, spec.setpoint)
                score = result.score
            else:
                score = 0.0
                result = ScorerResult(
                    score=0.0,
                    grade="F",
                    overshoot_pct=0.0,
                    rise_time_s=99.0,
                    settling_time_s=99.0,
                    sse_rad=99.0,
                    oscillation_count=0,
                )

            last_result = result

            # 7. Keep/discard
            keep = score > best_score or exp_num == 0
            if keep:
                best_score = score
                best_pid = new_pid
                consecutive_discards = 0
            else:
                consecutive_discards += 1

            # 8. Tell Optuna
            if use_optuna and study is not None and trial is not None and exp_num > 0:
                study.tell(trial, score)
                trial = None  # reset for next iteration

            # 9. Commit to git
            self._workspace.commit_experiment(exp_num, hypothesis, result, new_pid, status, keep)

            # 10. Reset to best after 3 consecutive discards
            if consecutive_discards >= 3:
                self._workspace.write_params(best_pid, "reset to best after 3 discards")
                consecutive_discards = 0

            # 11. Early stop
            if best_score >= target_score:
                break

        return CommissioningResult(
            joint_name=joint_name,
            base_pid=best_pid,
            best_score=best_score,
            experiment_count=exp_num + 1,
            research_log=[
                f"AutoResearch completed: {exp_num + 1} experiments, best={best_score:.1f}"
            ],
        )
