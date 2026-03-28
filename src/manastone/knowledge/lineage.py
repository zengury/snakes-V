import json
from datetime import datetime
from pathlib import Path
from typing import List, Optional


class ParameterLineage:
    """Full provenance tracking for PID parameters across robots."""

    def __init__(self, base_dir: Path = Path("storage/knowledge_base/metadata")):
        self._file = base_dir / "lineage.jsonl"

    def record_inheritance(self, robot_id: str, template_id: str, source_robot: str) -> None:
        self._append({"type": "inherited", "robot_id": robot_id,
                       "template_id": template_id, "source_robot": source_robot})

    def record_tune(self, robot_id: str, profile_id: str, session_id: str, outcome: str) -> None:
        self._append({"type": "tuned", "robot_id": robot_id,
                       "profile_id": profile_id, "session_id": session_id, "outcome": outcome})

    def record_export(self, robot_id: str, profile_id: str, template_id: str) -> None:
        self._append({"type": "exported", "robot_id": robot_id,
                       "profile_id": profile_id, "template_id": template_id})

    def trace(self, robot_id: str) -> List[dict]:
        """Return all lineage events for a robot, chronological order."""
        if not self._file.exists():
            return []
        result = []
        for line in self._file.read_text().strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                if record.get("robot_id") == robot_id:
                    result.append(record)
            except json.JSONDecodeError:
                continue
        return result

    def _append(self, record: dict) -> None:
        record["timestamp"] = datetime.now().isoformat()
        self._file.parent.mkdir(parents=True, exist_ok=True)
        with open(self._file, "a") as f:
            f.write(json.dumps(record) + "\n")
