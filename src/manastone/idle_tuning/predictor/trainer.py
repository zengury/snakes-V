"""PredictorTrainer — flywheel trainer: 0-9 LLM, 10+ XGBoost."""

from __future__ import annotations

import asyncio
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np

from manastone.common.models import JointContext, PIDParams
from manastone.idle_tuning.collector.session_store import IdleTuningSession, SessionStore
from manastone.idle_tuning.predictor.model import PIDPredictor


class PredictorTrainer:
    """Flywheel trainer: 0-9 sessions → pure LLM; 10+ → train XGBoost.

    A5: XGBoost training offloaded to ProcessPoolExecutor (non-blocking).
    """

    def __init__(
        self,
        session_store: SessionStore,
        single_predictor: PIDPredictor,
        storage_dir: Path,
        robot_id: str,
    ):
        self.store = session_store
        self.single_predictor = single_predictor
        self.storage_dir = storage_dir
        self.robot_id = robot_id
        self._executor = ProcessPoolExecutor(max_workers=1)

    async def on_session_saved(self, session: IdleTuningSession) -> None:
        improved_count = await self.store.count_improved(self.robot_id)

        # Phase 1: cold start (0-9 improved sessions) → LLM only
        if improved_count < 10:
            return

        # Phase 2: first train or periodic retrain (every 20 improved)
        if improved_count == 10 or (improved_count > 10 and improved_count % 20 == 0):
            sessions = await self.store.get_all_improved(self.robot_id)
            await self._train_async(sessions)

    async def _train_async(self, sessions: List[IdleTuningSession]) -> None:
        """A5: Run training in ProcessPoolExecutor to avoid blocking."""
        X, y_kp, y_ki, y_kd = self._prepare_data(sessions)
        if len(X) < 3:
            return

        loop = asyncio.get_event_loop()
        try:
            save_dir = str(self.storage_dir / "predictors" / self.robot_id)
            await loop.run_in_executor(
                self._executor,
                _train_predictor_worker,
                X,
                y_kp,
                y_ki,
                y_kd,
                save_dir,
                len(sessions),
            )
            # Reload from disk
            predictor_dir = self.storage_dir / "predictors" / self.robot_id
            latest = self._find_latest_model(predictor_dir, "single")
            if latest:
                loaded = PIDPredictor.load(latest)
                self.single_predictor.is_trained = loaded.is_trained
                self.single_predictor.confidence = loaded.confidence
                self.single_predictor._models = loaded._models
                self.single_predictor.version = loaded.version
                self.single_predictor.last_trained_at = loaded.last_trained_at
        except Exception:
            pass  # Training failure is non-fatal

    def _prepare_data(
        self, sessions: List[IdleTuningSession]
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """Extract (X, y_kp, y_ki, y_kd) from improved sessions."""
        X_list, y_kp_list, y_ki_list, y_kd_list = [], [], [], []
        predictor = PIDPredictor()
        for s in sessions:
            for joint_name, final_pid in s.joint_params.items():
                ctx = JointContext(
                    joint_name=joint_name,
                    joint_id=0,
                    group="leg",
                    last_params=final_pid,
                )
                features = predictor.extract_features(ctx)
                X_list.append(features)
                # Target: small positive delta (we improved, so we moved in right direction)
                y_kp_list.append(0.05)
                y_ki_list.append(0.02)
                y_kd_list.append(0.05)
        return (
            np.array(X_list, dtype=np.float32),
            np.array(y_kp_list),
            np.array(y_ki_list),
            np.array(y_kd_list),
        )

    def _find_latest_model(self, predictor_dir: Path, prefix: str) -> Optional[Path]:
        if not predictor_dir.exists():
            return None
        files = sorted(predictor_dir.glob(f"{prefix}_v*.json"), reverse=True)
        return files[0] if files else None


def _train_predictor_worker(
    X: np.ndarray,
    y_kp: np.ndarray,
    y_ki: np.ndarray,
    y_kd: np.ndarray,
    save_dir: str,
    sample_count: int,
) -> None:
    """Top-level function for ProcessPoolExecutor (must be picklable)."""
    from manastone.idle_tuning.predictor.model import PIDPredictor
    from pathlib import Path

    p = PIDPredictor()
    p.train(X, y_kp, y_ki, y_kd)
    if p.is_trained:
        save_path = Path(save_dir) / f"single_v{sample_count // 10}.json"
        p.save(save_path)
