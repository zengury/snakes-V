import asyncio
import os
import tempfile
import pytest
from pathlib import Path

os.environ["MANASTONE_MOCK_MODE"] = "true"
os.environ["MANASTONE_SCHEMA_PATH"] = "config/robot_schema.yaml"


def test_token_budget_accumulates():
    from manastone.agent.token_budget import TokenBudget
    budget = TokenBudget(1000)
    assert budget.can_afford(500)
    budget.spend(500, "test")
    assert budget.daily_used == 500
    assert budget.can_afford(400)
    assert not budget.can_afford(600)


def test_token_budget_daily_reset():
    from manastone.agent.token_budget import TokenBudget
    from unittest.mock import patch
    from datetime import date
    budget = TokenBudget(1000)
    budget.spend(800, "test")
    assert budget.daily_used == 800
    # Simulate day change
    future_date = date(2030, 1, 1)
    with patch("manastone.agent.token_budget.date") as mock_date:
        mock_date.today.return_value = future_date
        assert budget.can_afford(800)  # resets on new day


def test_llm_budget_exceeded_error():
    """Over-budget call raises LLMBudgetExceededError."""
    from manastone.agent.token_budget import TokenBudget, LLMBudgetExceededError
    from manastone.agent.memory import AgentMemory
    from manastone.agent.llm_proxy import LLMProxy
    from manastone.common.config import ManaConfig
    ManaConfig.reset()
    with tempfile.TemporaryDirectory() as tmpdir:
        memory = AgentMemory("test_robot", Path(tmpdir))
        budget = TokenBudget(daily_budget=10)  # tiny budget
        config = ManaConfig.get()
        proxy = LLMProxy(memory, budget, config)

        with pytest.raises(LLMBudgetExceededError):
            asyncio.run(proxy.call(
                caller="test",
                system_prompt="x" * 100,
                user_message="y" * 100,
                max_tokens=1000,
            ))


def test_session_token_limit():
    """Session token limit raises LLMBudgetExceededError."""
    from manastone.agent.token_budget import LLMBudgetExceededError
    from manastone.agent.memory import AgentMemory
    from manastone.agent.token_budget import TokenBudget
    from manastone.agent.llm_proxy import LLMProxy
    from manastone.common.config import ManaConfig
    ManaConfig.reset()
    with tempfile.TemporaryDirectory() as tmpdir:
        memory = AgentMemory("test_robot", Path(tmpdir))
        budget = TokenBudget(daily_budget=100_000)
        config = ManaConfig.get()
        proxy = LLMProxy(memory, budget, config)
        proxy._session_token_limit = 1  # 1 token limit = always exceeded

        with pytest.raises(LLMBudgetExceededError):
            asyncio.run(proxy.call(
                caller="test",
                system_prompt="test",
                user_message="hello",
                max_tokens=100,
            ))


def test_budget_usage_summary():
    from manastone.agent.token_budget import TokenBudget
    budget = TokenBudget(5000)
    budget.spend(1000, "commissioning")
    budget.spend(500, "agent")
    summary = budget.get_usage_summary()
    assert summary["daily_used"] == 1500
    assert summary["remaining"] == 3500
    assert summary["daily_budget"] == 5000
    assert "utilization_pct" in summary


def test_memory_injection():
    """LLM call with inject_memory=True prepends memory context."""
    from manastone.agent.memory import AgentMemory
    from manastone.agent.token_budget import TokenBudget
    from manastone.agent.llm_proxy import LLMProxy
    from manastone.common.config import ManaConfig
    ManaConfig.reset()
    with tempfile.TemporaryDirectory() as tmpdir:
        memory = AgentMemory("test_robot", Path(tmpdir))
        memory.record_event("tune_result", "left_leg score=85 improved")
        budget = TokenBudget(daily_budget=100_000)
        config = ManaConfig.get()
        proxy = LLMProxy(memory, budget, config)

        # Verify memory.build_context_for_llm() includes the event
        ctx = memory.build_context_for_llm()
        assert "left_leg" in ctx or "tune_result" in ctx
