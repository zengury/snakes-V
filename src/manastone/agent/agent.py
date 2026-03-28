from pathlib import Path
from typing import Optional

from manastone.common.config import ManaConfig
from manastone.agent.memory import AgentMemory
from manastone.agent.token_budget import TokenBudget
from manastone.agent.llm_proxy import LLMProxy
from manastone.agent.event_sink import AgentEventSink
from manastone.agent.intent import IntentParser
from manastone.agent.workflows import WorkflowEngine
from manastone.agent.background import BackgroundObserver

AGENT_QA_PROMPT = """You are the Manastone robot operations agent for a Unitree G1 humanoid robot.
Answer questions using the provided robot memory and current state.
Be concise and factual. Focus on actionable information."""


class ManastoneAgent:
    """Top-level Agent. Wires all components together.

    Single entry point for all human interaction and all LLM calls.
    """

    def __init__(
        self,
        robot_id: str,
        config: Optional[ManaConfig] = None,
        storage_dir: Optional[Path] = None,
        daily_budget: int = 100_000,
    ):
        self.robot_id = robot_id
        self.config = config or ManaConfig.get()
        self._storage_dir = storage_dir or self.config.get_storage_dir()

        # Core components
        self.memory = AgentMemory(robot_id, self._storage_dir)
        self.token_budget = TokenBudget(daily_budget)
        self.llm_proxy = LLMProxy(self.memory, self.token_budget, self.config)
        self.event_sink = AgentEventSink(self.memory)
        self.intent_parser = IntentParser(self.llm_proxy)
        self.workflows = WorkflowEngine(self)
        self.background = BackgroundObserver(self)

    # ------------------------------------------------------------------ tools

    async def ask(self, question: str) -> str:
        """Answer a question using memory and state."""
        memory_ctx = self.memory.build_context_for_llm()

        try:
            answer = await self.llm_proxy.call(
                caller="agent",
                system_prompt=AGENT_QA_PROMPT,
                user_message=f"Question: {question}",
                inject_memory=True,
                max_tokens=500,
            )
        except Exception as e:
            answer = (
                f"[LLM unavailable: {str(e)[:80]}] Memory context: {memory_ctx[:200]}"
            )

        self.memory.record_event(
            "human_qa", f"Q: {question[:60]} A: {answer[:60]}"
        )
        return answer

    async def command(self, instruction: str) -> dict:
        """Parse and execute an instruction."""
        self.memory.record_event("human_command", instruction[:100])
        intent = await self.intent_parser.parse(instruction)
        result = await self._execute_intent(intent)
        self.memory.record_event(
            "command_result",
            f"action={intent.get('action')}, success={result.get('success', False)}",
        )
        return result

    async def status(self) -> dict:
        """Return comprehensive status."""
        return {
            "robot_id": self.robot_id,
            "recent_events": self.memory.get_recent_events(5),
            "insights": self.memory.semantic.get("insights", [])[-3:],
            "token_usage": self.token_budget.get_usage_summary(),
            "working_memory_keys": list(self.memory.working.keys()),
        }

    async def teach(self, insight: str) -> dict:
        """Store a human-provided insight in semantic memory."""
        self.memory.add_insight(insight, source="human")
        self.memory.record_event("human_teach", insight[:100])
        self.memory.save()
        return {"stored": True, "insight": insight}

    # ------------------------------------------------------------------ internal

    async def _execute_intent(self, intent: dict) -> dict:
        action = intent.get("action", "unknown")

        if action == "chain_tune":
            chain = intent.get("chain", "left_leg")
            self.memory.record_event("tune_started", f"{chain} chain_tune commanded")
            return {
                "success": True,
                "action": "chain_tune",
                "chain": chain,
                "note": "Use commissioning module to execute (requires robot)",
            }

        elif action == "workflow":
            workflow = intent.get("workflow", "health_report")
            return await self.workflows.run(workflow)

        elif action == "pause_tuning":
            self.memory.working["tuning_paused"] = True
            return {"success": True, "action": "pause_tuning"}

        elif action == "resume_tuning":
            self.memory.working["tuning_paused"] = False
            return {"success": True, "action": "resume_tuning"}

        elif action == "rollback":
            return {
                "success": False,
                "action": "rollback",
                "note": "Specify chain name",
            }

        elif action == "status":
            return await self.status()

        else:
            return {
                "success": False,
                "action": "unknown",
                "raw": intent.get("raw", ""),
            }
