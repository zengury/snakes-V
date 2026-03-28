"""IdleTuningLoop — main orchestrator for idle-time PID tuning."""

from __future__ import annotations

from typing import Dict, Optional
from uuid import uuid4

from manastone.common.models import ChainContext, CommissioningResult, JointContext, PIDParams
from manastone.idle_tuning.collector.session_store import IdleTuningSession, SessionStore
from manastone.idle_tuning.executor.param_writer import ParamWriter
from manastone.idle_tuning.predictor.model import PIDPredictor
from manastone.idle_tuning.predictor.trainer import PredictorTrainer


class IdleTuningLoop:
    """Main idle-time tuning loop. Called by core_server every 10s."""

    def __init__(
        self,
        config,
        detector,
        skill_runner,
        param_writer: ParamWriter,
        session_store: SessionStore,
        trainer: PredictorTrainer,
        predictor: PIDPredictor,
        safety,
        robot_id: str,
        anomaly_threshold: float = 0.3,
    ):
        self.config = config
        self.detector = detector
        self.skill_runner = skill_runner
        self.param_writer = param_writer
        self.session_store = session_store
        self.trainer = trainer
        self.predictor = predictor
        self.safety = safety
        self.robot_id = robot_id
        self.anomaly_threshold = anomaly_threshold

    async def run_once(self, robot_id: str) -> Optional[IdleTuningSession]:
        """Run one iteration. Returns session if tuning happened, else None."""

        # 1. Check idle + safe
        idle, reason = await self.detector.is_idle()
        if not idle:
            return None
        safe, issues = await self.detector.is_safe_to_tune()
        if not safe:
            return None

        # 2. Select target chain (highest anomaly above threshold)
        target_chain = await self._select_chain(robot_id)
        if not target_chain:
            return None

        # 3. Build chain context
        chain_ctx = self._build_mock_chain_context(target_chain)

        # 4. Dual-path inference
        suggested_params = await self._dual_path_inference(chain_ctx)

        # 5. Safety clamp
        safe_params = self._apply_safety(suggested_params, chain_ctx)

        # 6. Write params
        prev_params = {
            jc.joint_name: jc.last_params
            for jc in chain_ctx.joints
            if jc.last_params
        }
        await self.param_writer.write_chain_params(target_chain, safe_params)

        # 7. Chain validation (mock)
        from manastone.profiles.registry import ProfileRegistry
        from manastone.commissioning.chain_scorer import ChainScorer

        profile = ProfileRegistry().get("classic_precision")
        scorer = ChainScorer(profile)
        joint_results = {}
        for jc in chain_ctx.joints:
            pid = safe_params.get(
                jc.joint_name, jc.last_params or PIDParams(kp=1.0, ki=0.1, kd=0.1)
            )
            joint_results[jc.joint_name] = CommissioningResult(
                joint_name=jc.joint_name,
                base_pid=pid,
                best_score=max(0.0, (1.0 - jc.anomaly_score) * 100),
            )
        chain_score = scorer.validate(target_chain, joint_results, mock=True)

        # 8. Outcome decision
        baseline = (1.0 - chain_ctx.chain_anomaly_score) * 100
        if chain_score >= baseline * 0.95:
            outcome = "improved" if chain_score > baseline else "neutral"
        else:
            outcome = "rollback"
            await self.param_writer.rollback_chain(target_chain, prev_params)

        # 9. Persist session
        session = IdleTuningSession(
            session_id=str(uuid4()),
            robot_id=robot_id,
            trigger=reason,
            chain_name=target_chain,
            joint_params=safe_params,
            chain_validation_action="mock_stand_single_leg",
            chain_validation_score=chain_score,
            outcome=outcome,
            reasoning="dual_path_inference",
            training_sample=(outcome == "improved"),
        )
        await self.session_store.save(session)

        # 10. Trigger training flywheel
        await self.trainer.on_session_saved(session)

        return session

    async def _select_chain(self, robot_id: str) -> Optional[str]:
        """Select chain with highest anomaly > threshold."""
        chain_scores = await self._compute_chain_anomalies(robot_id)
        eligible = [
            (name, score)
            for name, score in chain_scores.items()
            if score > self.anomaly_threshold
        ]
        if not eligible:
            return None
        return max(eligible, key=lambda x: x[1])[0]

    async def _compute_chain_anomalies(self, robot_id: str) -> Dict[str, float]:
        """Compute chain anomaly scores. In mock mode, use injected values."""
        if hasattr(self, "_mock_anomalies"):
            return self._mock_anomalies
        # Default: all chains have low anomaly
        chains = self.config.get_kinematic_chains()
        return {name: 0.1 for name in chains}

    def set_mock_anomalies(self, scores: Dict[str, float]) -> None:
        """Test helper: inject anomaly scores."""
        self._mock_anomalies = scores

    def _build_mock_chain_context(self, chain_name: str) -> ChainContext:
        joints = self.config.get_chain_tuning_order(chain_name)
        joint_contexts = []
        anomaly = (
            self._mock_anomalies.get(chain_name, 0.35)
            if hasattr(self, "_mock_anomalies")
            else 0.35
        )
        for i, jname in enumerate(joints):
            jc = JointContext(
                joint_name=jname,
                joint_id=i,
                group=self.config.get_joint_group(jname),
                anomaly_score=anomaly,
                last_params=PIDParams(kp=5.0, ki=0.1, kd=0.5),
            )
            joint_contexts.append(jc)
        return ChainContext(
            chain_name=chain_name,
            joints=joint_contexts,
            chain_anomaly_score=anomaly,
        )

    async def _dual_path_inference(
        self, chain_ctx: ChainContext
    ) -> Dict[str, PIDParams]:
        """Fast path (XGBoost) or deep path (LLM skill)."""
        # Try fast path
        if (
            self.predictor.is_trained
            and chain_ctx.chain_anomaly_score <= 0.5
            and self.predictor.confidence >= 0.7
        ):
            deltas = {
                jc.joint_name: self.predictor.predict_delta(jc)
                for jc in chain_ctx.joints
            }
            result = {}
            for jc in chain_ctx.joints:
                lp = jc.last_params or PIDParams(kp=1.0, ki=0.1, kd=0.1)
                dkp, dki, dkd = deltas[jc.joint_name]
                result[jc.joint_name] = lp.apply_delta(dkp, dki, dkd, max_change_pct=0.15)
            return result

        # Deep path: LLM skill
        xgb_prior = None
        if self.predictor.is_trained:
            xgb_prior = {
                jc.joint_name: self.predictor.predict_delta(jc)
                for jc in chain_ctx.joints
            }

        return await self.skill_runner.run(
            "tune_parameters",
            chain_context=chain_ctx,
            xgb_prior=xgb_prior,
            confidence=self.predictor.confidence,
        )

    def _apply_safety(
        self, params: Dict[str, PIDParams], chain_ctx: ChainContext
    ) -> Dict[str, PIDParams]:
        result = {}
        for jc in chain_ctx.joints:
            pid = params.get(
                jc.joint_name, jc.last_params or PIDParams(kp=1.0, ki=0.1, kd=0.1)
            )
            bounds = self.config.get_safety_bounds(jc.joint_name)
            clamped = PIDParams(
                kp=max(bounds["kp_range"][0], min(bounds["kp_range"][1], pid.kp)),
                ki=max(bounds["ki_range"][0], min(bounds["ki_range"][1], pid.ki)),
                kd=max(bounds["kd_range"][0], min(bounds["kd_range"][1], pid.kd)),
            )
            result[jc.joint_name] = clamped
        return result
