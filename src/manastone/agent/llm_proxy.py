"""
LLMProxy — unified LLM gateway for the Agent layer.

Wraps LLMClient with:
1. Token budget enforcement (raises LLMBudgetExceededError if over budget)
2. Memory context injection (robot history → LLM)
3. Call logging to episodic memory
4. Single LLMClient instance

Q2 compliance: max_tokens_per_session tracked; LLMBudgetExceededError raised at limit.
"""

from __future__ import annotations

from manastone.common.llm_client import LLMClient
from manastone.common.config import ManaConfig
from manastone.agent.memory import AgentMemory
from manastone.agent.token_budget import TokenBudget, LLMBudgetExceededError


class LLMProxy:
    """Unified LLM gateway. All LLM calls must go through here."""

    def __init__(self, memory: AgentMemory, budget: TokenBudget, config: ManaConfig):
        self.memory = memory
        self.budget = budget
        self.config = config
        self._client = LLMClient()
        self._session_tokens_used: int = 0
        self._session_token_limit: int = config.get_max_tokens_per_session()

    async def call(
        self,
        caller: str,
        system_prompt: str,
        user_message: str,
        inject_memory: bool = True,
        max_tokens: int = 2000,
    ) -> str:
        """Unified LLM call. Raises LLMBudgetExceededError if over budget."""

        # 1. Budget check
        estimated = (len(system_prompt) + len(user_message)) // 4 + max_tokens
        if not self.budget.can_afford(estimated):
            raise LLMBudgetExceededError(
                f"Daily budget exhausted ({self.budget.daily_used}/{self.budget.daily_budget} tokens),"
                f" caller={caller}"
            )

        # Session-level budget check (Q2)
        if self._session_tokens_used + estimated > self._session_token_limit:
            raise LLMBudgetExceededError(
                f"Session token limit reached ({self._session_tokens_used}/{self._session_token_limit}),"
                f" caller={caller}"
            )

        # 2. Inject memory context
        final_user_message = user_message
        if inject_memory:
            memory_ctx = self.memory.build_context_for_llm(max_tokens=1000)
            if memory_ctx:
                final_user_message = (
                    f"=== ROBOT MEMORY ===\n{memory_ctx}\n\n"
                    f"=== TASK ===\n{user_message}"
                )

        # 3. Call LLM
        try:
            response = await self._client.call(
                system=system_prompt,
                user=final_user_message,
                max_tokens=max_tokens,
            )
        except Exception as e:
            self.memory.record_event(
                "llm_error", f"caller={caller}, error={str(e)[:100]}", caller=caller
            )
            raise

        # 4. Account for tokens (estimate since we don't always get usage back)
        actual = estimated  # conservative estimate
        self.budget.spend(actual, caller=caller)
        self._session_tokens_used += actual

        self.memory.record_event(
            "llm_call",
            f"caller={caller}, tokens≈{actual}, preview={user_message[:60]}...",
            caller=caller,
        )

        return response

    def reset_session(self) -> None:
        """Reset per-session token counter (call at start of each commissioning session)."""
        self._session_tokens_used = 0
