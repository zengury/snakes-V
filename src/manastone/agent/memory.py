import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


class AgentMemory:
    """3-tier memory: working (in-memory dict), episodic (event log), semantic (long-term)."""

    MAX_EPISODIC = 500  # rotate after this many events

    def __init__(self, robot_id: str, storage_dir: Path):
        self.robot_id = robot_id
        self._path = storage_dir / "agent_memory" / robot_id / "memory.json"

        # 3 tiers
        self.working: Dict[str, Any] = {}
        self.episodic: List[Dict] = []   # [{timestamp, type, caller, summary}]
        self.semantic: Dict[str, Any] = {
            "insights": [],
            "robot_profile": {},
            "strategy_preferences": {},
        }

        self._load()

    def _load(self):
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text())
                self.working = data.get("working", {})
                self.episodic = data.get("episodic", [])
                self.semantic = data.get("semantic", self.semantic)
            except Exception:
                pass

    def save(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps({
            "working": self.working,
            "episodic": self.episodic[-self.MAX_EPISODIC:],
            "semantic": self.semantic,
        }, indent=2, default=str))

    def record_event(self, event_type: str, summary: str, caller: str = "agent") -> None:
        self.episodic.append({
            "timestamp": datetime.now().isoformat(),
            "type": event_type,
            "caller": caller,
            "summary": summary,
        })
        # Rotate in memory too
        if len(self.episodic) > self.MAX_EPISODIC:
            self.episodic = self.episodic[-self.MAX_EPISODIC:]

    def add_insight(self, text: str, source: str = "agent") -> None:
        self.semantic.setdefault("insights", []).append({
            "text": text,
            "source": source,
            "timestamp": datetime.now().isoformat(),
        })
        # Keep last 50 insights
        self.semantic["insights"] = self.semantic["insights"][-50:]

    def build_context_for_llm(self, max_tokens: int = 1000) -> str:
        """Build a compact memory context string for LLM injection."""
        lines = []

        # Recent episodic events (last 10)
        recent = self.episodic[-10:]
        if recent:
            lines.append("Recent events:")
            for e in recent:
                ts = e["timestamp"][:16]
                lines.append(f"  [{ts}] {e['type']}: {e['summary'][:80]}")

        # Recent insights (last 3)
        insights = self.semantic.get("insights", [])[-3:]
        if insights:
            lines.append("Insights:")
            for ins in insights:
                lines.append(f"  - {ins['text'][:100]}")

        result = "\n".join(lines)
        # Rough token budget: 1 token ≈ 4 chars
        max_chars = max_tokens * 4
        return result[:max_chars]

    def get_recent_events(self, n: int = 5) -> List[Dict]:
        return self.episodic[-n:]
