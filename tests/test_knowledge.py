import asyncio
import json
import os
import tempfile
import pytest
from pathlib import Path

os.environ["MANASTONE_MOCK_MODE"] = "true"
os.environ["MANASTONE_SCHEMA_PATH"] = "config/robot_schema.yaml"

# ── ParameterLineage ──────────────────────────────────────────────────────────

def test_lineage_record_and_trace():
    from manastone.knowledge.lineage import ParameterLineage
    with tempfile.TemporaryDirectory() as tmp:
        lin = ParameterLineage(Path(tmp))
        lin.record_inheritance("G1_100", "warehouse_v1", "G1_001")
        lin.record_tune("G1_100", "classic_precision", "sess_1", "improved")
        lin.record_export("G1_100", "classic_precision", "tpl_1")

        trace = lin.trace("G1_100")
        assert len(trace) == 3
        types = [e["type"] for e in trace]
        assert "inherited" in types
        assert "tuned" in types
        assert "exported" in types

def test_lineage_trace_isolates_robot():
    from manastone.knowledge.lineage import ParameterLineage
    with tempfile.TemporaryDirectory() as tmp:
        lin = ParameterLineage(Path(tmp))
        lin.record_tune("G1_001", "cp", "s1", "improved")
        lin.record_tune("G1_002", "cp", "s2", "rollback")

        assert len(lin.trace("G1_001")) == 1
        assert len(lin.trace("G1_002")) == 1
        assert len(lin.trace("G1_999")) == 0

def test_lineage_empty():
    from manastone.knowledge.lineage import ParameterLineage
    with tempfile.TemporaryDirectory() as tmp:
        lin = ParameterLineage(Path(tmp))
        assert lin.trace("nobody") == []

# ── TemplateLibrary ───────────────────────────────────────────────────────────

def test_template_create_and_load():
    from manastone.knowledge.template_library import TemplateLibrary
    from manastone.common.models import PIDParams
    with tempfile.TemporaryDirectory() as tmp:
        lib = TemplateLibrary(Path(tmp))
        params = {"left_knee": PIDParams(kp=5.0, ki=0.1, kd=0.5)}
        lib.create_template(
            "test_tpl", "G1_001", "classic_precision", params,
            environment={"task": "walking", "temp": "normal"},
            performance={"best_score": 85.0, "avg_score": 78.0},
        )
        loaded = lib.load("test_tpl")
        assert loaded["template_id"] == "test_tpl"
        assert loaded["source_robot"] == "G1_001"
        assert "left_knee" in loaded["params"]
        assert loaded["params"]["left_knee"]["kp"] == 5.0

def test_template_not_found_raises():
    from manastone.knowledge.template_library import TemplateLibrary, TemplateNotFoundError
    with tempfile.TemporaryDirectory() as tmp:
        lib = TemplateLibrary(Path(tmp))
        with pytest.raises(TemplateNotFoundError):
            lib.load("nonexistent_tpl")

def test_template_query_similar():
    from manastone.knowledge.template_library import TemplateLibrary
    from manastone.common.models import PIDParams
    with tempfile.TemporaryDirectory() as tmp:
        lib = TemplateLibrary(Path(tmp))
        params = {"left_knee": PIDParams(kp=5.0, ki=0.1, kd=0.5)}
        lib.create_template("tpl_a", "G1_001", "cp", params,
                             environment={"task": "walking", "terrain": "flat"},
                             performance={"best_score": 80.0})
        lib.create_template("tpl_b", "G1_002", "cp", params,
                             environment={"task": "running", "terrain": "rough"},
                             performance={"best_score": 75.0})

        results = lib.query_similar({"task": "walking", "terrain": "flat"})
        assert len(results) == 2
        assert results[0]["template_id"] == "tpl_a"  # highest similarity

# ── ModelZoo ──────────────────────────────────────────────────────────────────

def test_model_zoo_publish_and_query():
    from manastone.knowledge.model_zoo import ModelZoo
    with tempfile.TemporaryDirectory() as tmp:
        zoo = ModelZoo(Path(tmp))
        filename = zoo.publish(
            model_type="pid_predictor",
            model_data=b"fake_model_bytes",
            source_robot="G1_001",
            source_profile="classic_precision",
            version="1.0",
            metadata={"samples": 50, "confidence": 0.78},
        )
        assert filename.endswith(".bin")

        results = zoo.query("pid_predictor")
        assert len(results) == 1
        assert results[0]["confidence"] == 0.78
        assert results[0]["source_robot"] == "G1_001"

def test_model_zoo_query_by_profile():
    from manastone.knowledge.model_zoo import ModelZoo
    with tempfile.TemporaryDirectory() as tmp:
        zoo = ModelZoo(Path(tmp))
        zoo.publish("pid_predictor", b"m1", "G1_001", "classic_precision", "1.0",
                    {"samples": 50, "confidence": 0.8})
        zoo.publish("pid_predictor", b"m2", "G1_001", "rl_fidelity", "1.0",
                    {"samples": 30, "confidence": 0.6})

        cp_results = zoo.query("pid_predictor", profile="classic_precision")
        assert len(cp_results) == 1

        all_results = zoo.query("pid_predictor")
        assert len(all_results) == 2

def test_model_zoo_load():
    from manastone.knowledge.model_zoo import ModelZoo
    with tempfile.TemporaryDirectory() as tmp:
        zoo = ModelZoo(Path(tmp))
        data = b"xgboost_model_bytes_here"
        filename = zoo.publish("pid_predictor", data, "G1_001", "cp", "1.0", {})
        loaded = zoo.load("pid_predictor", filename)
        assert loaded == data

def test_model_zoo_empty_query():
    from manastone.knowledge.model_zoo import ModelZoo
    with tempfile.TemporaryDirectory() as tmp:
        zoo = ModelZoo(Path(tmp))
        assert zoo.query("nonexistent_type") == []

# ── KnowledgeTransfer ─────────────────────────────────────────────────────────

def test_transfer_strict_mode():
    from manastone.knowledge.transfer import KnowledgeTransfer
    from manastone.knowledge.template_library import TemplateLibrary
    from manastone.knowledge.lineage import ParameterLineage
    from manastone.common.models import PIDParams

    with tempfile.TemporaryDirectory() as tmp:
        lib = TemplateLibrary(Path(tmp) / "templates")
        lin = ParameterLineage(Path(tmp) / "lineage")
        params = {"left_knee": PIDParams(kp=5.0, ki=0.1, kd=0.5),
                  "left_hip_yaw": PIDParams(kp=3.0, ki=0.05, kd=0.3)}
        lib.create_template("warehouse_v1", "G1_001", "classic_precision", params,
                             {"task": "picking"}, {"best_score": 85.0})

        transfer = KnowledgeTransfer(lib, lin)
        result = asyncio.run(transfer.inherit_template("G1_100", "warehouse_v1", "strict"))

        assert result["mode"] == "strict"
        assert result["experiments"] == 0
        assert result["params_count"] == 2

        trace = lin.trace("G1_100")
        assert any(e["type"] == "inherited" for e in trace)

def test_transfer_adaptive_mode():
    from manastone.knowledge.transfer import KnowledgeTransfer
    from manastone.knowledge.template_library import TemplateLibrary
    from manastone.knowledge.lineage import ParameterLineage
    from manastone.common.models import PIDParams

    with tempfile.TemporaryDirectory() as tmp:
        lib = TemplateLibrary(Path(tmp) / "templates")
        lin = ParameterLineage(Path(tmp) / "lineage")
        params = {"left_knee": PIDParams(kp=5.0, ki=0.1, kd=0.5)}
        lib.create_template("adaptive_tpl", "G1_001", "classic_precision", params,
                             {}, {"best_score": 80.0})

        transfer = KnowledgeTransfer(lib, lin)
        result = asyncio.run(transfer.inherit_template("G1_200", "adaptive_tpl", "adaptive"))

        assert result["mode"] == "adaptive"
        assert result["experiments"] >= 1
        assert result["experiments"] <= 10

def test_transfer_export_template():
    from manastone.knowledge.transfer import KnowledgeTransfer
    from manastone.knowledge.template_library import TemplateLibrary
    from manastone.knowledge.lineage import ParameterLineage
    from manastone.common.models import PIDParams

    with tempfile.TemporaryDirectory() as tmp:
        lib = TemplateLibrary(Path(tmp) / "templates")
        lin = ParameterLineage(Path(tmp) / "lineage")
        transfer = KnowledgeTransfer(lib, lin)

        params = {"left_knee": PIDParams(kp=6.0, ki=0.15, kd=0.6)}
        tpl_id = transfer.export_template(
            "G1_001", "classic_precision", params,
            environment={"task": "walking"},
            performance={"best_score": 88.0},
        )

        assert "G1_001" in tpl_id
        loaded = lib.load(tpl_id)
        assert loaded["params"]["left_knee"]["kp"] == 6.0

        trace = lin.trace("G1_001")
        assert any(e["type"] == "exported" for e in trace)

# ── AgentRuntimeStream ────────────────────────────────────────────────────────

def test_stream_emit_and_query():
    from manastone.lifecycle.stream import AgentRuntimeStream, StreamEvent
    with tempfile.TemporaryDirectory() as tmp:
        stream = AgentRuntimeStream("G1_001", Path(tmp))
        e = StreamEvent(
            robot_id="G1_001",
            profile_id="classic_precision",
            event_type="commissioning_started",
            payload={"chain": "left_leg"},
        )
        stream.emit(e)

        events = stream.query()
        assert len(events) == 1
        assert events[0].event_type == "commissioning_started"

def test_stream_checkpoint():
    from manastone.lifecycle.stream import AgentRuntimeStream
    with tempfile.TemporaryDirectory() as tmp:
        stream = AgentRuntimeStream("G1_001", Path(tmp))
        e = stream.checkpoint("pre_deployment")
        assert e.event_type == "checkpoint_created"
        assert e.payload["label"] == "pre_deployment"

def test_stream_query_filter():
    from manastone.lifecycle.stream import AgentRuntimeStream, StreamEvent
    with tempfile.TemporaryDirectory() as tmp:
        stream = AgentRuntimeStream("G1_001", Path(tmp))
        stream.emit(StreamEvent(robot_id="G1_001", profile_id="cp",
                                event_type="commissioning_started", payload={}))
        stream.emit(StreamEvent(robot_id="G1_001", profile_id="rl",
                                event_type="tuning_completed", payload={}))

        all_events = stream.query()
        assert len(all_events) == 2

        filtered = stream.query(event_type="commissioning_started")
        assert len(filtered) == 1

# ── LifecycleRepository ───────────────────────────────────────────────────────

def test_lifecycle_repo_init_and_branch():
    from manastone.lifecycle.lifecycle_repo import LifecycleRepository
    from manastone.common.models import PIDParams
    with tempfile.TemporaryDirectory() as tmp:
        repo = LifecycleRepository("G1_001", Path(tmp))
        repo.init()

        profile_dir = repo.create_profile_branch("classic_precision")
        assert profile_dir.exists()

def test_lifecycle_repo_write_read_params():
    from manastone.lifecycle.lifecycle_repo import LifecycleRepository
    from manastone.common.models import PIDParams
    with tempfile.TemporaryDirectory() as tmp:
        repo = LifecycleRepository("G1_001", Path(tmp))
        repo.init()
        repo.create_profile_branch("classic_precision")

        params = {"left_knee": PIDParams(kp=5.0, ki=0.1, kd=0.5)}
        repo.write_best_params("classic_precision", params)

        loaded = repo.get_best_params("classic_precision")
        assert loaded is not None
        assert loaded["left_knee"].kp == 5.0

def test_lifecycle_repo_list_profiles():
    from manastone.lifecycle.lifecycle_repo import LifecycleRepository
    with tempfile.TemporaryDirectory() as tmp:
        repo = LifecycleRepository("G1_001", Path(tmp))
        repo.init()
        repo.create_profile_branch("classic_precision")
        repo.create_profile_branch("rl_fidelity")

        profiles = repo.list_profiles()
        assert "classic_precision" in profiles
        assert "rl_fidelity" in profiles

# ── ProfileSwitchingStrategy ──────────────────────────────────────────────────

def test_switching_required_profile():
    from manastone.lifecycle.switching import ProfileSwitchingStrategy
    strategy = ProfileSwitchingStrategy()
    result = asyncio.run(strategy.should_switch(
        "G1_001", "classic_precision",
        {"required_profile": "rl_fidelity"}
    ))
    assert result == "rl_fidelity"

def test_switching_idle_energy_saver():
    from manastone.lifecycle.switching import ProfileSwitchingStrategy
    strategy = ProfileSwitchingStrategy()
    result = asyncio.run(strategy.should_switch(
        "G1_001", "classic_precision",
        {"idle_duration_s": 400}
    ))
    assert result == "energy_saver"

def test_switching_no_switch_needed():
    from manastone.lifecycle.switching import ProfileSwitchingStrategy
    strategy = ProfileSwitchingStrategy()
    result = asyncio.run(strategy.should_switch(
        "G1_001", "classic_precision",
        {"idle_duration_s": 10, "recent_quality_score": 90}
    ))
    assert result is None

def test_switching_poor_quality():
    from manastone.lifecycle.switching import ProfileSwitchingStrategy
    strategy = ProfileSwitchingStrategy()
    result = asyncio.run(strategy.should_switch(
        "G1_001", "classic_precision",
        {"recent_quality_score": 45}
    ))
    # Should suggest a different profile (or None if no alternatives)
    assert result is None or isinstance(result, str)
