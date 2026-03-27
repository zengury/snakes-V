# Manastone Autonomic Operations Layer — Implementation Plan (snake-v)

> Source PRD: `~/Desktop/Snakes/pid2autopilot/PRD/manastone-claude-code-kit_4/`
> Target: Unitree G1 humanoid robot (29-DOF), runs on Jetson Orin NX (192.168.123.164)
> Language: Python 3.10+, FastMCP, Pydantic v2, XGBoost, rosbridge WebSocket

---

## What We're Building

A **robot full-lifecycle autonomous operations system** for the Unitree G1. The robot's
PID control parameters are not constants — they drift, degrade, and need tuning. This
system closes that loop automatically across three phases:

1. **Pre-deployment commissioning** — AutoResearch-style LLM-driven PID tuning per joint chain
2. **Runtime monitoring** — DDS data ingestion, anomaly scoring, semantic event generation
3. **Idle-time tuning** — While robot rests, run constrained optimization + XGBoost predictor flywheel

A 4th layer, the **Agent Runtime**, is the sole external gateway: all human interaction,
all LLM calls, and all events flow through it.

---

## Core Architecture — 5 Layers

```
Layer 4: Agent Runtime (:8090, 0.0.0.0)   ← ONLY external port
  LLMProxy | AgentMemory (3-tier) | WorkflowEngine | IntentParser

Layer 3: MCP Servers (127.0.0.1 only)
  core:8080 | joints:8081 | power:8082 | imu:8083 | pid_tuner:8087 | idle_tuner:8088
  + profiles/ | knowledge/ | lifecycle/

Layer 2: Data
  SQLite EventLog | Session JSON | XGBoost models | Git workspaces (per-robot per-profile)

Layer 1: ROS2 + rosbridge (:9090)
  /joint_states 50Hz | ROS2 param set | WebSocket

Layer 0: Unitree G1 Hardware (29-DOF)
```

---

## Module Breakdown

### `common/` — Shared foundation (Phase 1)
- `models.py` — all Pydantic models: PIDParams, JointContext, ChainContext, TuningSession, ChainTuningSession, InitialContext, CommissioningResult, etc.
- `safety.py` — 3-layer safety: StaticBoundsChecker + PreExperimentChecker + RuntimeMonitor + SafetyGuard facade
- `config.py` — ManaConfig singleton, reads `config/robot_schema.yaml`, env var overrides
- `llm_client.py` — thin Anthropic API wrapper (used by LLMProxy internally)

### `runtime/` — Always-on monitoring (Phase 1)
- `dds_bridge.py` — rosbridge WebSocket subscriber (Mock: simulated 50Hz joint data)
- `ring_buffer.py` — JointRingBuffer per joint, 30s sliding window
- `event_store.py` — SQLite EventStore
- `semantic_engine.py` — rule-based event generation (temp/torque/tracking thresholds)
- `anomaly_scorer.py` — weighted 0-1 score per joint

### `lifecycle/` — State machine + context bridge (Phase 1)
- `state_machine.py` — COMMISSIONING → RUNTIME → IDLE_TUNING → MAINTENANCE
- `context_bridge.py` — build JointContext/ChainContext for tuning loops
- `session_orchestrator.py` — rate limiting, cooldown rules
- `stream.py` — AgentRuntimeStream (lifecycle event JSONL)
- `lifecycle_repo.py` — per-robot per-profile Git repo management
- `switching.py` — ProfileSwitchingStrategy

### `commissioning/` — Pre-deployment PID AutoResearch (Phase 2)
- `autoresearch/agent_loop.py` — AutoResearchLoop (Karpathy-style LLM experiment loop)
- `autoresearch/workspace.py` — PIDWorkspace (Git-backed file workspace)
- `autoresearch/experiment.py` — ExperimentRunner (Mock: Euler-integration 2nd-order sim)
- `autoresearch/scorer.py` — compat wrapper → profiles/scorers
- `autoresearch/llm_client.py` — LLMParamEditor (uses injected LLMProxy)
- `chain_orchestrator.py` — ChainTuningOrchestrator (sequential per causal order)
- `chain_scorer.py` — ChainScorer (functional action validation)
- `multi_profile.py` — MultiProfileCommissioning

### `profiles/` — Pluggable tuning profiles (Phase 2)
- `registry.py` — ProfileRegistry, hot-load from YAML
- `profile.py` — TuningProfile runtime object
- `scorers/` — BaseScorer, StepResponseScorer, TorqueScorer, EnergyScorer
- `generators/` — BaseGenerator, StepGenerator, SinusoidalGenerator
- `builtin/` — 5 YAML profiles: classic_precision, rl_fidelity, energy_saver, high_speed, collision_safe

### `idle_tuning/` — Idle-time autonomous tuning (Phase 3)
- `agent/loop.py` — IdleTuningLoop: chain-level anomaly selection + dual-path inference
- `agent/idle_detector.py` — velocity threshold + explicit state detection
- `agent/skill_runner.py` — Markdown Skill execution engine
- `executor/param_writer.py` — ParamWriter (rosbridge ROS2 param set)
- `predictor/model.py` — PIDPredictor (XGBoost, 19-dim features)
- `predictor/chain_predictor.py` — ChainPredictor (60-dim, 18 models for 6-joint leg)
- `predictor/trainer.py` — flywheel: 0→10 sessions LLM-only, 10+ train XGBoost
- `predictor/runtime_predictor.py` — embedded in core_server, ±5% real-time nudge

### `agent/` — Agent Runtime gateway (Phase 4)
- `agent.py` — ManastoneAgent main class
- `llm_proxy.py` — LLMProxy: memory injection + token budget + call logging
- `event_sink.py` — AgentEventSink (4-phase system callbacks)
- `memory.py` — AgentMemory: working / episodic / semantic
- `intent.py` — IntentParser: regex fast-path + LLM fallback
- `workflows.py` — WorkflowEngine (commissioning_full, health_report)
- `mcp_interface.py` — MCP Server :8090, tools: ask/command/status/teach
- `rest_api.py` — REST API for Dashboard

### `knowledge/` — Cross-robot knowledge transfer (Phase 5)
- `model_zoo.py` — ModelZoo (shared XGBoost model repository)
- `template_library.py` — TemplateLibrary (parameter templates)
- `transfer.py` — KnowledgeTransfer (strict/adaptive/zero_shot inheritance)
- `lineage.py` — ParameterLineage (full provenance tracking)

---

## Implementation Phases

| Phase | Scope | Key Deliverable |
|-------|-------|-----------------|
| **P1: Foundation** | common/ + runtime/ + lifecycle/ state machine | Mock mode: joint data flows, anomaly scoring, state machine transitions |
| **P2: Commissioning** | commissioning/ + profiles/ | Mock mode: `chain_tune("left_leg")` completes 6 joints with LLM + Git history |
| **P3: Idle Tuning** | idle_tuning/ (full stack) | Mock: idle trigger → chain selection → dual-path → validation → session JSON |
| **P4: Agent Gateway** | agent/ + MCP servers wiring | `ask/command/teach` tools work; all LLM calls flow through LLMProxy |
| **P5: Knowledge** | knowledge/ + lifecycle repo | Template inherit + Model Zoo publish + lineage trace |

---

## Key Design Constraints

- **Mock mode always works**: `MANASTONE_MOCK_MODE=true` — every module has a mock path
- **LLM never in the control loop**: LLM = minutes/hours timescale only
- **Safety layers**: params always pass StaticBoundsChecker before write; rollback on chain validation failure
- **One external port**: :8090 only; all internal servers bind 127.0.0.1
- **Git as state store**: per-robot per-profile branches; every experiment is a commit
- **DDS domain 0**: must match G1 config, never change

---

## Stack

```
Runtime:   Python 3.10+, asyncio, FastMCP (SSE transport)
Data:      Pydantic v2, SQLite (aiosqlite), XGBoost, numpy, scikit-learn
Robot I/O: websockets (rosbridge), rclpy (optional, Orin NX only)
LLM:       Anthropic Claude (claude-sonnet-4-20250514), via ANTHROPIC_API_KEY
Dev tools: pytest, ruff, black, mypy
Config:    robot_schema.yaml + env var overrides
```

---

## Success Criteria (M1)

1. `MANASTONE_MOCK_MODE=true python -m pytest tests/` — all green
2. `chain_tune("left_leg")` in mock mode: 6 joints complete, git history + results.tsv present
3. Idle trigger → chain scoring → session JSON persisted
4. Safety: inject kp=999 → StaticBoundsChecker blocks it
5. State machine: all valid transitions pass, invalid transitions raise InvalidTransitionError
