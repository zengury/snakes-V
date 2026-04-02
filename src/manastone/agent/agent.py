from pathlib import Path
from typing import Any, Optional

import secrets
from datetime import datetime, timezone

from manastone.common.config import ManaConfig
from manastone.agent.memory import AgentMemory
from manastone.agent.file_memory import FileMemoryStore
from manastone.agent.memdir import ensure_robot_identity_memory, ensure_safety_gotcha_memory
from manastone.agent.memory_extractor import MemDirExtractor, MemoryTurnContext
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

    Safety:
    - In real mode, risky actions require a second explicit confirmation.
      This is a lightweight analogue of a tool-permission gate.
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

        # File-based persistent memory
        self.file_memory = FileMemoryStore(robot_id, self._storage_dir)
        self.mem_extractor = MemDirExtractor(robot_id, self._storage_dir, self.llm_proxy)

        # Always maintain robot identity (robot_fact)
        try:
            ensure_robot_identity_memory(self._storage_dir, robot_id, config=self.config)
            ensure_safety_gotcha_memory(self._storage_dir, robot_id)
        except Exception:
            # Never block agent startup on memory IO.
            pass

        self.event_sink = AgentEventSink(self.memory)
        self.intent_parser = IntentParser(self.llm_proxy)
        self.workflows = WorkflowEngine(self)
        self.background = BackgroundObserver(self)

    # ------------------------------------------------------------------ tools

    async def ask(self, question: str) -> str:
        """Answer a question using memory and state."""
        memory_ctx = self.memory.build_context_for_llm()
        file_mem_ctx = self.file_memory.build_recall_context(question)

        user_message = f"Question: {question}"
        if memory_ctx or file_mem_ctx:
            user_message = (
                f"=== EPISODIC/SEMANTIC MEMORY (JSON) ===\n{memory_ctx}\n\n"
                f"{file_mem_ctx}\n"
                f"=== TASK ===\nQuestion: {question}"
            )

        try:
            answer = await self.llm_proxy.call(
                caller="agent",
                system_prompt=AGENT_QA_PROMPT,
                user_message=user_message,
                inject_memory=False,
                max_tokens=500,
            )
        except Exception as e:
            preview = (memory_ctx + "\n" + file_mem_ctx).strip()[:250]
            answer = f"[LLM unavailable: {str(e)[:80]}] Memory context: {preview}"

        self.memory.record_event(
            "human_qa", f"Q: {question[:60]} A: {answer[:60]}"
        )
        return answer

    async def command(self, instruction: str) -> dict:
        """Parse and execute an instruction."""
        self.memory.record_event("human_command", instruction[:100])

        # 0) Confirmation gate: if a risky action is pending, user must confirm or cancel.
        pending = self._get_pending_confirmation()
        if pending is not None:
            token = str(pending.get("token", ""))
            if self._is_confirmation_message(instruction, token):
                intent = dict(pending.get("intent", {}))
                intent["confirmed"] = True
                self._clear_pending_confirmation()
                result = await self._execute_intent(intent)
                self.memory.record_event(
                    "command_result",
                    f"action={intent.get('action')}, success={result.get('success', False)}, confirmed=true",
                )
                return result

            if self._is_cancel_message(instruction):
                self._clear_pending_confirmation()
                return {
                    "success": True,
                    "action": "cancel",
                    "canceled": True,
                    "message": "Canceled pending action.",
                }

            return {
                "success": False,
                "error": "pending_confirmation",
                "message": "A risky action is pending confirmation. Reply with 'confirm <token>' to proceed or 'cancel' to abort.",
                "confirm_token": token,
                "pending_action": pending.get("intent", {}).get("action"),
                "pending_preview": pending.get("preview", ""),
            }

        # 1) Parse intent
        intent = await self.intent_parser.parse(instruction)

        # 2) If risky, require explicit confirmation (real mode by default)
        if self._requires_confirmation(intent) and not bool(intent.get("confirmed")):
            token = self._set_pending_confirmation(intent, preview=instruction[:160])
            return {
                "success": False,
                "requires_confirmation": True,
                "confirm_token": token,
                "message": "This action may change robot parameters. Reply with 'confirm <token>' to proceed, or 'cancel' to abort.",
                "intent": {k: intent.get(k) for k in ("action", "chain", "profile", "workflow", "raw") if k in intent},
            }

        # 3) Execute
        result = await self._execute_intent(intent)
        self.memory.record_event(
            "command_result",
            f"action={intent.get('action')}, success={result.get('success', False)}",
        )
        return result

    # ------------------------------------------------------------------ confirmation gate helpers

    def _requires_confirmation(self, intent: dict) -> bool:
        if not self.config.require_confirmations():
            return False
        action = intent.get("action")
        # Expand this set as more write-capable actions are added.
        return action in {"chain_tune", "rollback"}

    def _get_pending_confirmation(self) -> Optional[dict]:
        pending = self.memory.working.get("pending_confirmation")
        if not isinstance(pending, dict):
            return None

        created_at = pending.get("created_at")
        if not isinstance(created_at, str):
            self._clear_pending_confirmation()
            return None

        try:
            created = datetime.fromisoformat(created_at)
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
        except Exception:
            self._clear_pending_confirmation()
            return None

        age_s = (datetime.now(timezone.utc) - created).total_seconds()
        # 5-minute TTL
        if age_s > 300:
            self._clear_pending_confirmation()
            return None

        return pending

    def _set_pending_confirmation(self, intent: dict, preview: str) -> str:
        token = secrets.token_urlsafe(8)
        self.memory.working["pending_confirmation"] = {
            "token": token,
            "intent": intent,
            "preview": preview,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        return token

    def _clear_pending_confirmation(self) -> None:
        self.memory.working.pop("pending_confirmation", None)

    @staticmethod
    def _is_confirmation_message(text: str, token: str) -> bool:
        t = text.strip().lower()
        if token and token in text:
            return True
        return t in {"confirm", "yes", "y", "确认", "好的", "ok", "okay"} or t.startswith("confirm ")

    @staticmethod
    def _is_cancel_message(text: str) -> bool:
        t = text.strip().lower()
        return t in {"cancel", "abort", "no", "n", "取消", "停止"}

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
            try:
                from manastone.commissioning.chain_orchestrator import (
                    ChainTuningOrchestrator,
                )
                from manastone.profiles.registry import ProfileRegistry

                profile_id = intent.get("profile", "classic_precision")
                profile = ProfileRegistry().get(profile_id)
                orch = ChainTuningOrchestrator(
                    config=self.config,
                    profile=profile,
                    storage_dir=self._storage_dir,
                    robot_id=self.robot_id,
                )
                result = await orch.tune_chain(
                    chain_name=chain,
                    target_score=intent.get("target_score", 80.0),
                    max_experiments_per_joint=intent.get("max_experiments", 30),
                )
                self.memory.record_event(
                    "tune_completed",
                    f"{chain} score={result.chain_score:.1f} exps={result.total_experiments}",
                )
                return {
                    "success": True,
                    "action": "chain_tune",
                    "chain": chain,
                    "chain_score": result.chain_score,
                    "total_experiments": result.total_experiments,
                    "profile": profile_id,
                }
            except Exception as exc:
                self.memory.record_event("tune_error", f"{chain}: {str(exc)[:80]}")
                return {
                    "success": False,
                    "action": "chain_tune",
                    "chain": chain,
                    "error": str(exc),
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
