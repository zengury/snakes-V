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

## Eng Review Amendments (from /autoplan Phase 3)

### Architecture amendments

- **A1 — State machine durability**: `lifecycle/state_machine.py` must checkpoint
  `{state, active_chain, timestamp}` to SQLite on every transition. On startup, read back
  last known state. Use `lifecycle_state` table in the same EventLog SQLite file.

- **A2 — MCPClientPool**: `agent/mcp_interface.py` must implement `MCPClientPool` — one
  `FastMCP.Client` per Layer 3 server (core:8080, joints:8081, power:8082, imu:8083,
  pid_tuner:8087, idle_tuner:8088). Lazily connected. Health-checked before each call.
  Connection errors surfaced as `MCPServerUnavailableError`.

- **A3 — SQLite WAL mode**: `runtime/event_store.py` must run
  `PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;` on first connection open.
  Required for multi-process write correctness (runtime monitor + session orchestrator
  + idle tuner all write concurrently).

- **A4 — Git workspace crash recovery**: `autoresearch/workspace.py` must write an
  `EXPERIMENT_IN_PROGRESS` sentinel file before each commit, remove after success.
  On `PIDWorkspace.__init__`, detect sentinel → `git reset --hard HEAD` before
  proceeding. Prevents dirty index from crashing the next run.

- **A5 — XGBoost training async**: `predictor/trainer.py` `.fit()` is CPU-bound
  (5-30s on Jetson Orin NX ARM). Must be offloaded via
  `await asyncio.get_event_loop().run_in_executor(ProcessPoolExecutor(), train_fn)`.
  The idle_tuning loop must not block while training.

- **A6 — rosbridge schema constants**: Add `ROSBRIDGE_SUBSCRIBE_MSG` TypedDict to
  `common/models.py`. Mock DDSBridge must use identical schema. Prevents mock/real
  divergence at the protocol layer.

### Code quality amendments

- **Q1 — Mock mode via ABC**: `DDSBridge` and `ParamWriter` become ABCs. Implementations:
  `RealDDSBridge`, `MockDDSBridge`, `RealParamWriter`, `MockParamWriter`. Selected by
  `ManaConfig.create_dds_bridge()` and `ManaConfig.create_param_writer()` factories.
  No `if MOCK_MODE:` in business logic.

- **Q2 — LLM token budget**: Add `max_tokens_per_session: int = 100_000` to `ManaConfig`.
  `LLMProxy` tracks cumulative token usage per session, raises `LLMBudgetExceededError`
  at limit, logs to EventStore. Commissioning and idle tuning both fall back to BO-only
  when budget is exceeded.

- **Q3 — Scorer consolidation**: Delete `autoresearch/scorer.py`. `AutoResearchLoop`
  accepts `scorer: BaseScorer` injected from the active `TuningProfile`. `ChainScorer`
  wraps the profile's scorer for chain-level aggregation. One scorer hierarchy.

- **Q4 — Feature dimension spec**: Add `predictor/features.py` with:
  `JOINT_FEATURE_COLS: list[str]` (10 per joint) and
  `CHAIN_FEATURE_COLS: list[str]` (60 = 6 joints × 10). The "19-dim per joint" in
  `model.py` is per-joint model features (position, velocity, torque, temperature,
  kp, ki, kd, tracking_error, anomaly_score, session_idx). Document explicitly.
  No magic numbers in model.py or chain_predictor.py.

- **Q5 — pyproject.toml**: Add `pyproject.toml` to the plan with:
  ruff `select = ["E","F","UP","B","I"]`, mypy `strict = true`,
  pytest `asyncio_mode = "auto"` (required for async test coroutines).

### Test plan (M1 + M2)

**M1 test files** (mock mode, all required for M1 green):
- `tests/test_safety.py` — StaticBoundsChecker: kp=999 blocked, boundary values, all params
- `tests/test_lifecycle.py` — state machine: valid transitions, InvalidTransitionError, SQLite restore
- `tests/test_commissioning.py` — chain_tune("left_leg"): 6 joints, causal order, git log, tsv
- `tests/test_idle_tuning.py` — idle trigger → session JSON; velocity-above-threshold no-trigger
- `tests/test_dds_bridge.py` — mock 50Hz emission, ring_buffer 30s window, drop oldest

**M2 test files** (required before Phase 3 Idle Tuning merge):
- `tests/test_dds_reconnect.py` — DDSBridge reconnect: 5s backoff, DDSConnectionLostError after 3
- `tests/test_predictor.py` — XGBoost flywheel: cold-start (0-9 sessions pure LLM), 10th → train
- `tests/test_llm_proxy.py` — token budget accumulation, LLMBudgetExceededError, BO fallback
- `tests/test_profiles.py` — ProfileRegistry hot-load YAML, invalid YAML → ValidationError
- `tests/test_features.py` — feature vector dimensions stable, column names match model input
- `tests/test_commissioning_eval.py` [→EVAL] — LLM param proposal quality regression

**Coverage target**: M1 = 5 defined criteria. M2 = all 26 traced paths ≥ ★★.

### Test coverage diagram

```
CODE PATH COVERAGE — MANASTONE
===========================
[+] common/safety.py :: StaticBoundsChecker
    ├── [★★★ M1] kp=999 → BLOCK                          test_safety.py
    ├── [M1]     all params at boundary edge               test_safety.py
    └── [M2 →PROPERTY] Hypothesis: any out-of-range       test_safety.py

[+] lifecycle/state_machine.py
    ├── [★★★ M1] valid transitions all pass               test_lifecycle.py
    ├── [M1]     invalid transition → InvalidTransitionError test_lifecycle.py
    └── [M2]     crash recovery: restore from SQLite      test_lifecycle.py

[+] commissioning/ :: chain_tune("left_leg")
    ├── [★★★ M1] 6 joints complete, git history, tsv      test_commissioning.py
    ├── [M1]     causal order: hip→knee→ankle enforced    test_commissioning.py
    ├── [M2]     mid-chain failure → rollback             test_commissioning.py
    ├── [M2 →EVAL] LLM param proposal quality            test_commissioning_eval.py
    └── [M2]     LLMBudgetExceededError → BO fallback    test_commissioning.py

[+] runtime/dds_bridge.py
    ├── [M1]     MockDDSBridge 50Hz emission rate         test_dds_bridge.py
    ├── [M1]     ring_buffer: 30s window, drops oldest    test_dds_bridge.py
    ├── [M2]     reconnect after disconnect (5s backoff)  test_dds_reconnect.py
    └── [M2]     DDSConnectionLostError after 3 retries   test_dds_reconnect.py

[+] idle_tuning/ :: full idle path
    ├── [★★★ M1] idle trigger → session JSON persisted    test_idle_tuning.py
    ├── [M1]     velocity > threshold → no trigger        test_idle_tuning.py
    ├── [M2]     cold start: 0-9 sessions → LLM only     test_predictor.py
    ├── [M2]     flywheel: 10th session → train triggered test_predictor.py
    └── [M2]     ±5% nudge clipped by StaticBoundsChecker test_predictor.py

[+] agent/llm_proxy.py
    ├── [M2]     token budget accumulates across calls    test_llm_proxy.py
    └── [M2]     LLMBudgetExceededError at limit         test_llm_proxy.py

[+] profiles/registry.py
    ├── [M2]     hot-load YAML: new file → new profile    test_profiles.py
    └── [M2]     invalid YAML → ValidationError, no crash test_profiles.py

─────────────────────────────────────────────────────
COVERAGE TARGET:
  M1: 9 paths (5 M1 criteria + 4 additional)
  M2: 17 additional paths (all gaps closed)
  E2E: chain_tune full cycle [→E2E in M2]
  Eval: LLM param proposal quality [→EVAL in M2]
─────────────────────────────────────────────────────
```

---

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 1 | PASS | 6 findings: 2 critical (write access confirmed, hybrid BO+LLM chosen); 4 auto-decided (drift→TODO, OTEL→TODO, federation→stub, XGBoost kept) |
| Eng Review | `/plan-eng-review` | Architecture & tests | 1 | PASS_WITH_AMENDMENTS | 11 findings (A1-A6 arch, Q1-Q5 quality); test coverage diagram produced; M1+M2 test plan added |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | SKIPPED | Only 1 UI term match ("dashboard"), below threshold |
| Codex Review | `/codex review` | Independent 2nd opinion | 0 | — | — |

**VERDICT:** REVIEWED — 2/4 reviews complete. Plan amended with A1-A6 architecture fixes and Q1-Q5 code quality fixes. Ready to implement Phase 1 (common/ + runtime/ + lifecycle/).

**Key decisions locked:**
- Hybrid BO (Optuna TPE) + LLM (hypothesis annotation) for PID search
- Mock mode via ABC injection, not `if MOCK_MODE:` branches
- SQLite WAL mode for multi-process event log
- State machine checkpointed to SQLite on every transition
- XGBoost training in ProcessPoolExecutor (non-blocking)
- One scorer hierarchy (profiles/scorers/ only, autoresearch/scorer.py deleted)

**Deferred to TODOS.md:** policy drift detection, Grafana/OTEL export, multi-robot federation, systemd unit, Bearer token auth on :8090.
