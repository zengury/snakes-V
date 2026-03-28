import re
import json
from typing import Dict, Any, Optional

INTENT_PARSE_PROMPT = """Parse the user's instruction into a JSON intent object.

Output ONLY valid JSON with these fields:
- action: one of "chain_tune", "workflow", "pause_tuning", "resume_tuning", "rollback", "status", "unknown"
- chain: chain name if action is "chain_tune" (e.g. "left_leg", "right_arm")
- workflow: workflow name if action is "workflow" (e.g. "commissioning_full", "health_report")
- raw: the original instruction

Example: {"action": "chain_tune", "chain": "left_leg", "raw": "tune the left leg"}
"""


class IntentParser:
    QUICK_PATTERNS = [
        (r"(调参|tune|calibrate).*(左腿|left.?leg)", {"action": "chain_tune", "chain": "left_leg"}),
        (r"(调参|tune|calibrate).*(右腿|right.?leg)", {"action": "chain_tune", "chain": "right_leg"}),
        (r"(调参|tune|calibrate).*(左臂|left.?arm)", {"action": "chain_tune", "chain": "left_arm"}),
        (r"(调参|tune|calibrate).*(右臂|right.?arm)", {"action": "chain_tune", "chain": "right_arm"}),
        (r"(调参|tune|calibrate).*(腰|waist)", {"action": "chain_tune", "chain": "waist"}),
        (r"(全身|full.?body|所有).*(调参|tune)", {"action": "workflow", "workflow": "commissioning_full"}),
        (r"(暂停|停止|pause|stop).*(调优|tuning)", {"action": "pause_tuning"}),
        (r"(恢复|resume).*(调优|tuning)", {"action": "resume_tuning"}),
        (r"(报告|report|health)", {"action": "workflow", "workflow": "health_report"}),
        (r"(回滚|rollback)", {"action": "rollback"}),
        (r"(状态|status)", {"action": "status"}),
    ]

    def __init__(self, llm_proxy=None):
        self._llm_proxy = llm_proxy

    async def parse(self, instruction: str) -> Dict[str, Any]:
        """Parse instruction. Fast path: regex. Slow path: LLM fallback."""
        lowered = instruction.lower()
        for pattern, intent in self.QUICK_PATTERNS:
            if re.search(pattern, lowered, re.IGNORECASE):
                return {**intent, "raw": instruction}

        # LLM fallback
        if self._llm_proxy:
            try:
                response = await self._llm_proxy.call(
                    caller="agent",
                    system_prompt=INTENT_PARSE_PROMPT,
                    user_message=instruction,
                    inject_memory=False,
                    max_tokens=200,
                )
                # Extract JSON
                match = re.search(r"\{.*\}", response, re.DOTALL)
                if match:
                    return json.loads(match.group())
            except Exception:
                pass

        return {"action": "unknown", "raw": instruction}
