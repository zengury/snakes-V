"""IdleTuningSession model and SessionStore for persisting tuning sessions."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from manastone.common.models import PIDParams


class IdleTuningSession(BaseModel):
    session_id: str
    robot_id: str
    timestamp: datetime = Field(default_factory=datetime.now)
    trigger: str = ""           # "mock_mode" / "velocity_inference" / "explicit_robot_state"
    chain_name: str
    joint_params: Dict[str, PIDParams]   # {joint_name: PIDParams}
    chain_validation_action: str = ""
    chain_validation_score: float = 0.0
    outcome: str = "unknown"    # "improved" / "neutral" / "rollback"
    reasoning: str = ""
    training_sample: bool = False


class SessionStore:
    def __init__(self, base_dir: Path):
        self.base = base_dir

    async def save(self, session: IdleTuningSession) -> Path:
        d = self.base / session.robot_id
        d.mkdir(parents=True, exist_ok=True)
        ts = session.timestamp.strftime("%Y%m%d_%H%M%S")
        path = d / f"{ts}_{session.chain_name}.json"
        path.write_text(session.model_dump_json(indent=2))
        return path

    async def query_by_chain(
        self, robot_id: str, chain_name: str, limit: int = 20
    ) -> List[IdleTuningSession]:
        d = self.base / robot_id
        if not d.exists():
            return []
        files = sorted(d.glob(f"*_{chain_name}.json"), reverse=True)[:limit]
        result = []
        for f in files:
            try:
                result.append(IdleTuningSession.model_validate_json(f.read_text()))
            except Exception:
                continue
        return result

    async def count_improved(self, robot_id: str) -> int:
        d = self.base / robot_id
        if not d.exists():
            return 0
        count = 0
        for f in d.glob("*.json"):
            try:
                data = json.loads(f.read_text())
                if data.get("outcome") == "improved":
                    count += 1
            except Exception:
                continue
        return count

    async def get_all_improved(self, robot_id: str) -> List[IdleTuningSession]:
        d = self.base / robot_id
        if not d.exists():
            return []
        result = []
        for f in sorted(d.glob("*.json")):
            try:
                s = IdleTuningSession.model_validate_json(f.read_text())
                if s.outcome == "improved":
                    result.append(s)
            except Exception:
                continue
        return result

    async def get_last_good_params(
        self, robot_id: str, chain_name: str
    ) -> Optional[Dict[str, PIDParams]]:
        sessions = await self.query_by_chain(robot_id, chain_name)
        for s in sessions:
            if s.outcome in ("improved", "neutral"):
                return s.joint_params
        return None
