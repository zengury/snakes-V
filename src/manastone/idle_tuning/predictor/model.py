"""PIDPredictor — XGBoost-based single-joint PID delta predictor."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Tuple

import numpy as np

from manastone.common.models import JointContext, PIDParams
from .features import JOINT_FEATURE_COLS


class PIDPredictor:
    FEATURE_DIM = 19

    XGB_PARAMS = {
        "objective": "reg:squarederror",
        "max_depth": 4,
        "eta": 0.1,
        "min_child_weight": 3,
        "subsample": 0.8,
        "colsample_bytree": 0.8,
        "eval_metric": "rmse",
        "seed": 42,
    }
    NUM_BOOST_ROUND = 50
    EARLY_STOPPING = 10

    def __init__(self):
        self._models: Dict[str, Optional[object]] = {
            "delta_kp": None,
            "delta_ki": None,
            "delta_kd": None,
        }
        self.is_trained = False
        self.confidence = 0.0
        self.version = "untrained"
        self.last_trained_at: Optional[datetime] = None

    def extract_features(self, ctx: JointContext) -> np.ndarray:
        qt = ctx.quality_trend
        return np.array(
            [
                ctx.temp_c,
                ctx.temp_trend,
                ctx.current_a,
                ctx.torque_nm,
                ctx.velocity_rad_s,
                ctx.tracking_error_mean,
                ctx.tracking_error_max,
                ctx.torque_efficiency,
                ctx.anomaly_score,
                ctx.hours_since_commissioning / 1000.0,
                ctx.hours_since_last_tune,
                ctx.tune_count / 100.0,
                qt[-1] if qt else 0.5,
                float(np.mean(qt)) if qt else 0.5,
                float(np.std(qt)) if len(qt) > 1 else 0.0,
                ctx.last_params.kp if ctx.last_params else 0.0,
                ctx.last_params.ki if ctx.last_params else 0.0,
                ctx.last_params.kd if ctx.last_params else 0.0,
                float(ctx.comm_lost_count),
            ],
            dtype=np.float32,
        )

    def predict_delta(self, ctx: JointContext) -> Tuple[float, float, float]:
        """Returns (delta_kp_pct, delta_ki_pct, delta_kd_pct). 0 if untrained."""
        if not self.is_trained:
            return (0.0, 0.0, 0.0)

        try:
            import xgboost as xgb

            features = self.extract_features(ctx).reshape(1, -1)
            dm = xgb.DMatrix(features)
            dkp = float(self._models["delta_kp"].predict(dm)[0])
            dki = float(self._models["delta_ki"].predict(dm)[0])
            dkd = float(self._models["delta_kd"].predict(dm)[0])
            return (dkp, dki, dkd)
        except Exception:
            return (0.0, 0.0, 0.0)

    def train(
        self,
        X: np.ndarray,
        y_kp: np.ndarray,
        y_ki: np.ndarray,
        y_kd: np.ndarray,
    ) -> None:
        """Train 3 models. X shape: (n_samples, 19)."""
        try:
            import xgboost as xgb
            from sklearn.model_selection import train_test_split
        except ImportError:
            # No xgboost — mark as untrained
            return

        if len(X) < 3:
            return

        test_size = 0.2 if len(X) >= 5 else 0.0
        confidences = []

        for param, y in [("delta_kp", y_kp), ("delta_ki", y_ki), ("delta_kd", y_kd)]:
            if test_size > 0:
                X_train, X_val, y_train, y_val = train_test_split(
                    X, y, test_size=test_size, random_state=42
                )
            else:
                X_train, y_train = X, y
                X_val, y_val = X, y

            dtrain = xgb.DMatrix(X_train, label=y_train)
            dval = xgb.DMatrix(X_val, label=y_val)

            model = xgb.train(
                self.XGB_PARAMS,
                dtrain,
                self.NUM_BOOST_ROUND,
                evals=[(dval, "val")],
                early_stopping_rounds=self.EARLY_STOPPING,
                verbose_eval=False,
            )
            self._models[param] = model

            pred = model.predict(dval)
            ss_res = float(np.sum((y_val - pred) ** 2))
            ss_tot = float(np.sum((y_val - np.mean(y_val)) ** 2))
            r2 = max(0.0, 1.0 - (ss_res / ss_tot if ss_tot > 0 else 1.0))
            confidences.append(r2)

        self.confidence = float(np.mean(confidences))
        self.is_trained = True
        self.version = f"v_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        self.last_trained_at = datetime.now()

    def save(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            import xgboost as xgb  # noqa: F401

            data: Dict = {
                "confidence": self.confidence,
                "version": self.version,
                "last_trained_at": (
                    self.last_trained_at.isoformat() if self.last_trained_at else None
                ),
                "is_trained": self.is_trained,
            }
            for param, model in self._models.items():
                if model is not None:
                    model_path = path.with_suffix(f".{param}.xgb")
                    model.save_model(str(model_path))
                    data[f"{param}_path"] = str(model_path)
            path.write_text(json.dumps(data, indent=2))
        except Exception:
            pass

    @classmethod
    def load(cls, path: Path) -> "PIDPredictor":
        p = cls()
        try:
            import xgboost as xgb

            data = json.loads(Path(path).read_text())
            p.confidence = data.get("confidence", 0.0)
            p.version = data.get("version", "unknown")
            p.is_trained = data.get("is_trained", False)
            if data.get("last_trained_at"):
                p.last_trained_at = datetime.fromisoformat(data["last_trained_at"])
            for param in ["delta_kp", "delta_ki", "delta_kd"]:
                model_path = data.get(f"{param}_path")
                if model_path and Path(model_path).exists():
                    m = xgb.Booster()
                    m.load_model(model_path)
                    p._models[param] = m
        except Exception:
            pass
        return p
