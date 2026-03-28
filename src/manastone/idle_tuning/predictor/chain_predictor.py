"""ChainPredictor — XGBoost-based chain-level PID delta predictor."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional, Tuple

import numpy as np

from manastone.common.models import ChainContext


class ChainPredictor:
    def __init__(self, chain_name: str, config):
        self.chain_name = chain_name
        self.joint_names = config.get_chain_tuning_order(chain_name)
        self.n_joints = len(self.joint_names)
        self._models: Dict[str, Any] = {}  # "{joint}_{param}" → xgb.Booster
        self.is_trained = False
        self.confidence = 0.0
        self.last_trained_at: Optional[datetime] = None

    def predict_chain_delta(
        self, ctx: ChainContext
    ) -> Dict[str, Tuple[float, float, float]]:
        """Returns {joint_name: (dkp_pct, dki_pct, dkd_pct)}. 0 if untrained."""
        result = {}
        for jc in ctx.joints:
            result[jc.joint_name] = (0.0, 0.0, 0.0)

        if not self.is_trained:
            return result

        try:
            import xgboost as xgb

            features = np.array(ctx.feature_vector, dtype=np.float32).reshape(1, -1)
            dm = xgb.DMatrix(features)
            for jc in ctx.joints:
                kp_model = self._models.get(f"{jc.joint_name}_kp")
                ki_model = self._models.get(f"{jc.joint_name}_ki")
                kd_model = self._models.get(f"{jc.joint_name}_kd")
                dkp = float(kp_model.predict(dm)[0]) if kp_model is not None else 0.0
                dki = float(ki_model.predict(dm)[0]) if ki_model is not None else 0.0
                dkd = float(kd_model.predict(dm)[0]) if kd_model is not None else 0.0
                result[jc.joint_name] = (dkp, dki, dkd)
        except Exception:
            pass
        return result
