import asyncio
import os
import tempfile
import pytest
from pathlib import Path

os.environ["MANASTONE_MOCK_MODE"] = "true"
os.environ["MANASTONE_SCHEMA_PATH"] = "config/robot_schema.yaml"


def make_agent(tmpdir):
    from manastone.agent.agent import ManastoneAgent
    from manastone.common.config import ManaConfig
    ManaConfig.reset()
    return ManastoneAgent("test_robot", storage_dir=Path(tmpdir), daily_budget=100_000)


def test_agent_status():
    with tempfile.TemporaryDirectory() as tmpdir:
        agent = make_agent(tmpdir)
        result = asyncio.run(agent.status())
        assert "robot_id" in result
        assert result["robot_id"] == "test_robot"
        assert "token_usage" in result
        assert "recent_events" in result


def test_file_memdir_identity_created():
    """Phase 1: identity + safety baseline must exist."""
    with tempfile.TemporaryDirectory() as tmpdir:
        agent = make_agent(tmpdir)
        mem_root = Path(tmpdir) / "agent_memory" / "test_robot" / "memories"
        identity = mem_root / "robot_identity.md"
        safety = mem_root / "safety_gotcha.md"
        index = mem_root / "MEMORY.md"

        assert identity.exists()
        assert safety.exists()
        assert index.exists()

        assert "type: robot_fact" in identity.read_text()
        assert "robot_id: test_robot" in identity.read_text()

        assert "type: safety_gotcha" in safety.read_text()
        assert "human-maintained" in safety.read_text().lower()

        assert "robot_identity.md" in index.read_text()
        assert "safety_gotcha.md" in index.read_text()


def test_agent_teach():
    with tempfile.TemporaryDirectory() as tmpdir:
        agent = make_agent(tmpdir)
        result = asyncio.run(agent.teach("left knee runs hot after 2 hours"))
        assert result["stored"] is True
        # Insight should be in semantic memory
        insights = agent.memory.semantic.get("insights", [])
        assert any("left knee" in i["text"] for i in insights)


def test_agent_teach_in_memory_context():
    """teach() insight should appear in LLM context for subsequent calls."""
    with tempfile.TemporaryDirectory() as tmpdir:
        agent = make_agent(tmpdir)
        asyncio.run(agent.teach("left knee runs hot"))
        ctx = agent.memory.build_context_for_llm()
        assert "left knee" in ctx


def test_agent_command_chain_tune():
    with tempfile.TemporaryDirectory() as tmpdir:
        agent = make_agent(tmpdir)
        result = asyncio.run(agent.command("tune the left leg"))
        assert result["success"] is True
        assert result["action"] == "chain_tune"
        assert result["chain"] == "left_leg"


def test_agent_command_health_report():
    with tempfile.TemporaryDirectory() as tmpdir:
        agent = make_agent(tmpdir)
        result = asyncio.run(agent.command("generate health report"))
        assert result["success"] is True
        assert "summary" in result


def test_agent_command_pause_resume():
    with tempfile.TemporaryDirectory() as tmpdir:
        agent = make_agent(tmpdir)
        r1 = asyncio.run(agent.command("pause tuning"))
        assert r1["success"] is True
        assert agent.memory.working.get("tuning_paused") is True
        r2 = asyncio.run(agent.command("resume tuning"))
        assert r2["success"] is True
        assert agent.memory.working.get("tuning_paused") is False


def test_memory_persistence():
    """Memory survives across agent instances."""
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = Path(tmpdir)
        # Create agent and teach
        from manastone.agent.agent import ManastoneAgent
        from manastone.common.config import ManaConfig
        ManaConfig.reset()
        agent1 = ManastoneAgent("test_robot", storage_dir=storage)
        asyncio.run(agent1.teach("persistent insight test"))
        agent1.memory.save()

        # New agent loads from same storage
        ManaConfig.reset()
        agent2 = ManastoneAgent("test_robot", storage_dir=storage)
        insights = agent2.memory.semantic.get("insights", [])
        assert any("persistent insight" in i["text"] for i in insights)


def test_event_sink_records():
    with tempfile.TemporaryDirectory() as tmpdir:
        agent = make_agent(tmpdir)
        agent.event_sink.on_tune_result("left_leg", 82.0, 300.0, "improved")
        events = agent.memory.episodic
        assert any(e["type"] == "tune_result" for e in events)


def test_event_sink_rollback_counter():
    with tempfile.TemporaryDirectory() as tmpdir:
        agent = make_agent(tmpdir)
        agent.event_sink.on_tune_result("left_leg", 50.0, 60.0, "rollback")
        agent.event_sink.on_tune_result("left_leg", 50.0, 60.0, "rollback")
        assert agent.memory.working.get("consecutive_rollbacks") == 2
        agent.event_sink.on_tune_result("left_leg", 80.0, 60.0, "improved")
        assert agent.memory.working.get("consecutive_rollbacks") == 0


def test_intent_parser_regex_fast_path():
    from manastone.agent.intent import IntentParser
    parser = IntentParser()

    r1 = asyncio.run(parser.parse("tune the right arm"))
    assert r1["action"] == "chain_tune"
    assert r1["chain"] == "right_arm"

    r2 = asyncio.run(parser.parse("generate health report"))
    assert r2["action"] == "workflow"

    r3 = asyncio.run(parser.parse("pause tuning now"))
    assert r3["action"] == "pause_tuning"


def test_confirmation_gate_requires_confirm_token():
    """When MANASTONE_REQUIRE_CONFIRMATION=true, risky actions must be confirmed."""
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ["MANASTONE_REQUIRE_CONFIRMATION"] = "true"
        try:
            agent = make_agent(tmpdir)
            r1 = asyncio.run(agent.command("tune the left leg"))
            assert r1["success"] is False
            assert r1.get("requires_confirmation") is True
            token = r1.get("confirm_token")
            assert isinstance(token, str) and len(token) > 0

            r2 = asyncio.run(agent.command(f"confirm {token}"))
            assert r2["success"] is True
            assert r2["action"] == "chain_tune"
            assert r2["chain"] == "left_leg"
        finally:
            os.environ.pop("MANASTONE_REQUIRE_CONFIRMATION", None)


def test_confirmation_gate_cancel():
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ["MANASTONE_REQUIRE_CONFIRMATION"] = "true"
        try:
            agent = make_agent(tmpdir)
            r1 = asyncio.run(agent.command("tune the left leg"))
            assert r1.get("requires_confirmation") is True

            r2 = asyncio.run(agent.command("cancel"))
            assert r2["success"] is True
            assert r2["canceled"] is True

            # After cancel, a new tune request should again ask for confirmation
            r3 = asyncio.run(agent.command("tune the left leg"))
            assert r3.get("requires_confirmation") is True
        finally:
            os.environ.pop("MANASTONE_REQUIRE_CONFIRMATION", None)


def test_background_observer_instantiates():
    async def _run():
        with tempfile.TemporaryDirectory() as tmpdir:
            agent = make_agent(tmpdir)
            # Can start and stop without error
            agent.background._interval = 9999  # don't actually trigger
            agent.background.start()
            agent.background.stop()

    asyncio.run(_run())


def test_workflow_health_report():
    with tempfile.TemporaryDirectory() as tmpdir:
        agent = make_agent(tmpdir)
        agent.memory.record_event("tune_result", "left_leg score=80 improved")
        agent.memory.add_insight("robot is performing well")
        result = asyncio.run(agent.workflows.run("health_report"))
        assert result["success"] is True
        assert "summary" in result


def test_workflow_unknown():
    with tempfile.TemporaryDirectory() as tmpdir:
        agent = make_agent(tmpdir)
        result = asyncio.run(agent.workflows.run("nonexistent_workflow"))
        assert result["success"] is False
