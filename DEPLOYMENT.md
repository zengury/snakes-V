# Manastone Deployment Guide

> Unitree G1 Autonomic Operations System — snakes-V
> 138 tests passing | Python 3.10+ | Jetson Orin NX

---

## Hardware topology

```
Dev machine (Mac)  ──Wi-Fi──►  G1 Jetson Orin NX (192.168.123.164)  ← deploy here
                                G1 RockChip RK3588  (192.168.123.161) ← DO NOT touch
```

DDS domain ID is fixed at `0`. Do not change it — must match G1 firmware.

---

## Who are you?

| Role | Jump to |
|------|---------|
| Robot engineer — first-time commissioning | [→ Commissioning](#commissioning-robot-engineer) |
| Ops engineer — daily monitoring / idle tuning | [→ Operations](#operations-ops-engineer) |
| AI/ML engineer — training the predictor | [→ XGBoost flywheel](#xgboost-flywheel-aiml-engineer) |
| Platform engineer — multi-robot knowledge transfer | [→ Knowledge transfer](#knowledge-transfer-platform-engineer) |
| Developer — local dev / running tests | [→ Local development](#local-development-developer) |

---

## Environment setup (all roles)

### 1. Clone and install

```bash
git clone https://github.com/zengury/snakes-V.git
cd snakes-V
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### 2. Environment variables

```bash
# Required
export ANTHROPIC_API_KEY="sk-ant-..."         # LLM calls (tuning and analysis)

# Robot connection (when running on Orin NX)
export ROSBRIDGE_URL="ws://localhost:9090"    # rosbridge WebSocket
export MANASTONE_SCHEMA_PATH="config/robot_schema.yaml"

# Optional
export MANASTONE_LLM_MODEL="claude-sonnet-4-20250514"
export MANASTONE_MAX_TOKENS="100000"          # token budget per session
export MANASTONE_STORAGE_DIR="storage"        # data storage root

# Development / test mode (no robot needed)
export MANASTONE_MOCK_MODE=true
```

### 3. Verify installation

```bash
MANASTONE_MOCK_MODE=true python -m pytest tests/ -q
# Expected: 138 passed
```

---

## Commissioning (robot engineer)

First-time setup. Calibrates all 29 joints using Bayesian optimization + LLM annotation.

### Prerequisites

- rosbridge running on Orin NX: `ros2 launch rosbridge_server rosbridge_websocket_launch.xml`
- Robot in a safe standing pose
- `ANTHROPIC_API_KEY` set

### Steps

**1. Confirm hardware connection**

```bash
# On Orin NX
export ROSBRIDGE_URL="ws://localhost:9090"
export MANASTONE_SCHEMA_PATH="/path/to/config/robot_schema.yaml"
```

**2. Commission a single chain (recommended first)**

```python
import asyncio
from manastone.commissioning.chain_orchestrator import ChainTuningOrchestrator
from manastone.profiles.registry import ProfileRegistry
from manastone.common.config import ManaConfig

config = ManaConfig.get()
profile = ProfileRegistry().get("classic_precision")
orch = ChainTuningOrchestrator(config=config, profile=profile, robot_id="G1_001")

result = asyncio.run(orch.tune_chain("left_leg", target_score=80.0, max_experiments_per_joint=30))
print(f"Chain score: {result.chain_score:.1f}")
print(f"Total experiments: {result.total_experiments}")
```

**3. Commission the full body (5 chains, sequential)**

Via the agent:
```python
from manastone.agent.agent import ManastoneAgent
agent = ManastoneAgent(robot_id="G1_001")
result = asyncio.run(agent.command("commission full body"))
```

**4. Inspect results**

Tuning history is saved at:
```
storage/pid_workspace/G1_001/{joint_name}/
├── results.tsv    # score record for every experiment
├── params.yaml    # current best parameters
└── program.md     # tuning context
```

Git history:
```bash
cd storage/pid_workspace/G1_001/left_knee
git log --oneline   # one commit per experiment
git tag             # best_N tag points to highest-scoring experiment
```

**5. Choose a tuning profile**

| Profile | When to use | Key metric |
|---------|-------------|------------|
| `classic_precision` | Default, precision tasks | Step response overshoot / steady-state error |
| `rl_fidelity` | RL gait training | Torque tracking error |
| `energy_saver` | Long-duration standby | Current integral / temperature rise |
| `high_speed` | Fast manipulation | Response speed |
| `collision_safe` | Human-robot collaboration | Contact force / compliance |

```python
profile = ProfileRegistry().get("energy_saver")  # switch profile
```

**Safety abort conditions (triggered automatically)**

- `|torque| > 60 Nm` → `status="safety_torque"`
- `|velocity| > 20 rad/s` → `status="safety_velocity"`
- Any joint temperature > 65°C → `status="safety_thermal"`

---

## Operations (ops engineer)

After commissioning, the system self-optimizes during idle periods. Your job is monitoring and intervention via the agent interface.

### Start the system

```bash
# On Orin NX
cd snakes-V
source .venv/bin/activate
export ANTHROPIC_API_KEY="sk-ant-..."
export ROSBRIDGE_URL="ws://localhost:9090"

# Start the agent (the only external port: :8090)
python -m manastone.agent.mcp_interface --host 0.0.0.0 --port 8090
```

> **Security note:** `:8090` currently has no authentication. See [issue #1](https://github.com/zengury/snakes-V/issues/1).
> Until that is fixed, do not expose this port on a shared network. Use SSH port-forwarding from your dev machine.

### Connect Claude Desktop

```json
{
  "mcpServers": {
    "manastone": { "url": "http://192.168.123.164:8090/mcp/sse" }
  }
}
```

Then ask: *"How is the left leg doing?"* or *"Tune the right arm."*

### Interact via the agent (4 core tools)

```python
import asyncio
from manastone.agent.agent import ManastoneAgent

agent = ManastoneAgent(robot_id="G1_001")

# Query status
status = asyncio.run(agent.status())
print(status["recent_events"])
print(status["token_usage"])

# Ask questions
answer = asyncio.run(agent.ask("How did the last tuning session go?"))
print(answer)

# Issue commands
asyncio.run(agent.command("tune left leg"))
asyncio.run(agent.command("generate health report"))
asyncio.run(agent.command("pause tuning"))

# Teach the agent (writes to semantic memory)
asyncio.run(agent.teach("Left knee joint dissipates heat poorly after 4 continuous hours"))
```

### Idle tuning (automatic)

The system detects idle state (all joint velocities < 0.02 rad/s for 30s) and triggers automatically:

```python
from manastone.idle_tuning.agent.loop import IdleTuningLoop

# Manually trigger one session (for debugging)
session = asyncio.run(idle_loop.run_once("G1_001"))
if session:
    print(f"Chain tuned: {session.chain_name}, outcome: {session.outcome}")
    # Session JSON saved to storage/sessions/G1_001/
```

**Session files:**
```
storage/sessions/G1_001/
└── 20260328_143052_left_leg.json
```

### Monitoring thresholds

| Metric | Warning | Critical |
|--------|---------|----------|
| Joint temperature | > 50°C | > 65°C |
| Anomaly score | > 0.3 | > 0.5 |
| Chain anomaly | > 0.3 → triggers idle tuning | > 0.5 → forces LLM deep path |

### Manual parameter rollback

```python
from manastone.idle_tuning.executor.param_writer import MockParamWriter  # or RealParamWriter
from manastone.idle_tuning.collector.session_store import SessionStore

store = SessionStore(Path("storage/sessions"))
good_params = asyncio.run(store.get_last_good_params("G1_001", "left_leg"))
await param_writer.write_chain_params("left_leg", good_params)
```

### Connecting real sensor data

In mock mode, all sensor data flows from `MockDDSBridge`. On the real robot you replace it with `RealDDSBridge`, which subscribes to rosbridge WebSocket topics and feeds the same downstream pipeline — no other code changes required.

**Step 1 — Start rosbridge on the Orin NX**

```bash
# On Orin NX, in your ROS2 workspace
source /opt/ros/humble/setup.bash   # or foxy/galactic depending on your install
ros2 launch rosbridge_server rosbridge_websocket_launch.xml
# Listening on ws://localhost:9090
```

**Step 2 — Wire `RealDDSBridge` into `ManaConfig`**

```python
import asyncio
from manastone.common.config import ManaConfig
from manastone.runtime.dds_bridge import RealDDSBridge
from manastone.runtime.ring_buffer import JointRingBuffer
from manastone.runtime.anomaly_scorer import AnomalyScorer

config = ManaConfig.get()                         # reads ROSBRIDGE_URL from env

bridge = RealDDSBridge(config)
await bridge.connect()

# Subscribe to joint states — callback feeds the ring buffer
buffer = JointRingBuffer(duration_s=30, sample_rate_hz=50)
await bridge.subscribe(
    topic="/lowstate",
    msg_type="unitree_msgs/LowState",
    callback=buffer.push,
)
```

**Step 3 — Feed `AnomalyScorer`**

```python
from manastone.common.models import JointContext

scorer = AnomalyScorer()

# Periodically (e.g., every 1 s) pull the latest window and score each joint
snapshot = buffer.get_latest_window("left_knee")
joint_ctx = JointContext(
    joint_name="left_knee",
    temp_c=snapshot["temp_mean"],
    torque_nm=snapshot["torque_rms"],
    velocity_rad_s=snapshot["velocity_mean"],
    tracking_error=snapshot["tracking_error"],
    torque_efficiency=snapshot["efficiency"],
)
score = scorer.score(joint_ctx, recent_events=[])
print(f"left_knee anomaly: {score:.3f}")   # > 0.3 triggers idle tuning
```

**Step 4 — Wire `AnomalyScorer` into `IdleTuningLoop`**

`IdleTuningLoop._compute_chain_anomalies()` uses `self._anomaly_provider` when injected, otherwise returns all-low defaults. Inject a real provider at construction time:

```python
from manastone.idle_tuning.agent.loop import IdleTuningLoop

# Build a dict of chain → anomaly score using the scorer above
async def live_anomaly_provider(robot_id: str) -> dict[str, float]:
    chain_scores = {}
    for chain_name, joints in config.get_kinematic_chains().items():
        scores = [
            scorer.score(
                JointContext.from_buffer(buffer.get_latest_window(j)), []
            )
            for j in joints
        ]
        chain_scores[chain_name] = max(scores)
    return chain_scores

loop = IdleTuningLoop(
    robot_id="G1_001",
    config=config,
    dds_bridge=bridge,
    anomaly_provider=live_anomaly_provider,   # inject here
)
await loop.start()
```

**Step 5 — Verify end-to-end**

```bash
# Quick smoke test: confirm real joint data is arriving
python3 - <<'EOF'
import asyncio, os
from manastone.common.config import ManaConfig
from manastone.runtime.dds_bridge import RealDDSBridge

async def main():
    config = ManaConfig.get()
    bridge = RealDDSBridge(config)
    await bridge.connect()
    received = []
    await bridge.subscribe("/lowstate", "unitree_msgs/LowState",
                           lambda msg: received.append(msg))
    await asyncio.sleep(0.5)
    print(f"Messages received: {len(received)}")   # expect ~25 at 50 Hz
    await bridge.disconnect()

asyncio.run(main())
EOF
```

> **Tip:** If `ROSBRIDGE_URL` is not set, `ManaConfig` defaults to `ws://localhost:9090`. On the Orin NX this is always correct. From a dev machine, set it to `ws://192.168.123.164:9090`.

---

## XGBoost flywheel (AI/ML engineer)

The built-in predictor accumulates session data and trains automatically: first training after 10 `improved` sessions, then retraining every 20 sessions.

### Model architecture

- **Single-joint model** (`PIDPredictor`): 19 features → predicts Δkp / Δki / Δkd (3 independent models)
- **Chain model** (`ChainPredictor`): 60-dim chain features → 18 models (6 joints × 3 params)
- **Runtime predictor** (`RuntimePredictor`): when anomaly > 0.3, suggests ±5% real-time nudge

### Feature dimensions (`predictor/features.py`)

```python
JOINT_FEATURE_COLS  # 19 dims: temp_c, torque_nm, velocity_rad_s, kp/ki/kd, ...
CHAIN_JOINT_COLS    # 10 dims × N joints = chain feature vector
```

### Check training status

```python
from manastone.idle_tuning.predictor.model import PIDPredictor
from pathlib import Path

predictor = PIDPredictor.load(Path("storage/predictors/G1_001/single_v1.json"))
print(f"Trained: {predictor.is_trained}")
print(f"Confidence: {predictor.confidence:.2f}")
print(f"Version: {predictor.version}")
```

### Publish to Model Zoo (cross-robot sharing)

```python
from manastone.knowledge.model_zoo import ModelZoo

zoo = ModelZoo()
model_bytes = Path("storage/predictors/G1_001/single_v3.json").read_bytes()
zoo.publish(
    model_type="pid_predictor",
    model_data=model_bytes,
    source_robot="G1_001",
    source_profile="classic_precision",
    version="3.0",
    metadata={"samples": 150, "confidence": 0.82},
)

models = zoo.query("pid_predictor")
print(models)  # sorted by confidence desc
```

### Tune XGBoost hyperparameters

Edit `src/manastone/idle_tuning/predictor/model.py`:

```python
XGB_PARAMS = {
    "max_depth": 4,        # increase for better fit; risks overfitting on small datasets
    "eta": 0.1,            # learning rate
    "min_child_weight": 3,
    "subsample": 0.8,
    "colsample_bytree": 0.8,
}
NUM_BOOST_ROUND = 50       # training rounds
EARLY_STOPPING = 10        # early stopping patience
```

> Training runs in a `ProcessPoolExecutor` — it does not block the tuning loop (design spec A5).

---

## Knowledge transfer (platform engineer)

Manage parameter inheritance and template reuse across a fleet of robots.

### Export a template from a trained robot

```python
from manastone.knowledge.transfer import KnowledgeTransfer
from manastone.common.models import PIDParams

transfer = KnowledgeTransfer()

best_params = {
    "left_knee":    PIDParams(kp=8.5, ki=0.12, kd=1.2),
    "left_hip_yaw": PIDParams(kp=5.0, ki=0.08, kd=0.8),
    # ... other joints
}
template_id = transfer.export_template(
    robot_id="G1_001",
    profile_id="classic_precision",
    params=best_params,
    environment={"task": "warehouse_picking", "terrain": "flat", "load_kg": 5},
    performance={"best_score": 87.0, "avg_score": 82.0, "sessions_count": 45},
)
print(f"Template ID: {template_id}")
# Saved to: storage/knowledge_base/template_library/by_scenario/{template_id}.yaml
```

### New robot inherits from template

```python
# strict: use directly, 0 experiments
result = asyncio.run(transfer.inherit_template("G1_100", template_id, "strict"))

# adaptive: inherit + up to 10 adaptation experiments (recommended)
result = asyncio.run(transfer.inherit_template("G1_100", template_id, "adaptive"))

# zero_shot: use as initial guess, full commissioning up to 50 experiments
result = asyncio.run(transfer.inherit_template("G1_100", template_id, "zero_shot"))

print(f"Mode: {result['mode']}, experiments: {result['experiments']}")
```

### Query similar-environment templates

```python
from manastone.knowledge.template_library import TemplateLibrary

lib = TemplateLibrary()
similar = lib.query_similar({"task": "warehouse_picking", "terrain": "flat"})
for t in similar[:3]:
    print(f"{t['template_id']} — similarity: {t['similarity']:.2f}, score: {t['performance']['best_score']}")
```

### Trace parameter lineage

```python
from manastone.knowledge.lineage import ParameterLineage

lineage = ParameterLineage()
trace = lineage.trace("G1_100")
for event in trace:
    print(f"[{event['timestamp'][:16]}] {event['type']}: {event}")
# Output: inherited → tuned → tuned → exported → ...
```

### Per-robot per-profile Git repositories

```python
from manastone.lifecycle.lifecycle_repo import LifecycleRepository

repo = LifecycleRepository("G1_001")
repo.init()

# Create a profile branch
workspace = repo.create_profile_branch("rl_fidelity")
# Branch name: G1_001/rl_fidelity

# List all profiles
profiles = repo.list_profiles()  # ["classic_precision", "rl_fidelity"]

# Tag a version
repo.tag_version("classic_precision", "1.0", "stable")
# Tag: classic_precision/v1.0-stable
```

### Switch profiles at runtime

```python
from manastone.lifecycle.switching import ProfileSwitchingStrategy

strategy = ProfileSwitchingStrategy()

new_profile = asyncio.run(strategy.should_switch(
    robot_id="G1_001",
    current_profile="classic_precision",
    upcoming_context={
        "idle_duration_s": 400,       # > 300s → suggests energy_saver
        "recent_quality_score": 85,
    }
))
if new_profile:
    asyncio.run(strategy.execute_switch("G1_001", new_profile, reason="long_idle"))
```

---

## Local development (developer)

### Pure mock mode (no robot)

```bash
export MANASTONE_MOCK_MODE=true
export MANASTONE_SCHEMA_PATH=config/robot_schema.yaml
```

- All DDS data is simulated by `MockDDSBridge` (50 Hz simulated joint data)
- PID experiments run via `MockExperimentRunner` + `MockJointSimulator` (Euler integration)
- Parameter writes handled by `MockParamWriter` (in-memory)
- LLM failures fall back to the rule engine automatically

### Run tests

```bash
# All tests
MANASTONE_MOCK_MODE=true python -m pytest tests/ -v

# By module
python -m pytest tests/test_safety.py -v
python -m pytest tests/test_commissioning.py -v
python -m pytest tests/test_idle_tuning.py -v
python -m pytest tests/test_agent.py -v
python -m pytest tests/test_knowledge.py -v
```

### Test coverage by module

| Test file | Covers | Tests |
|-----------|--------|-------|
| `test_safety.py` | `common/safety.py` | 18 |
| `test_lifecycle.py` | `lifecycle/state_machine.py` | 13 |
| `test_dds_bridge.py` | `runtime/dds_bridge.py`, `ring_buffer.py` | 10 |
| `test_commissioning.py` | `commissioning/` | 13 |
| `test_profiles.py` | `profiles/` | 12 |
| `test_idle_tuning.py` | `idle_tuning/` | 10 |
| `test_agent.py` | `agent/` | 13 |
| `test_llm_proxy.py` | `agent/llm_proxy.py`, `token_budget.py` | 6 |
| `test_knowledge.py` | `knowledge/`, `lifecycle/stream.py` | 23 |
| **Total** | | **138** |

### Code quality

```bash
ruff check src/   # lint
black src/        # format
mypy src/         # type check
```

`pyproject.toml` config: `ruff select = ["E","F","UP","B","I"]`, `mypy strict = true`, `pytest asyncio_mode = "auto"`.

### Add a new MCP tool

In `src/manastone/agent/mcp_interface.py`:

```python
async def tool_my_new_tool(param: str) -> dict:
    """Tool description shown to the LLM."""
    return await agent.my_method(param)
```

### Add a new tuning profile

Create a YAML file in `src/manastone/profiles/builtin/`:

```yaml
profile_id: my_profile
version: "1.0"
description: "Custom scenario"
compatible_joint_groups: [leg]
compatible_tasks: [my_task]
llm_prompt: |
  ... LLM prompt template (supports {joint_name}, {kp_min}, {kp_max} variables)
scorer:
  class: manastone.profiles.scorers.step_response.StepResponseScorer
  params: {}
experiment_generator:
  class: manastone.profiles.generators.step.StepGenerator
  params: {setpoint: 0.3, duration_s: 2.0, sample_rate_hz: 100.0}
safety:
  max_param_change_pct: 0.15
features: [temp_c, torque_nm, anomaly_score]
```

Hot-reload (no restart needed):

```python
from manastone.profiles.registry import ProfileRegistry
registry = ProfileRegistry()   # re-instantiate to pick up the new file
profile = registry.get("my_profile")
```

---

## Storage layout

```
storage/
├── pid_workspace/{robot_id}/{joint_name}/    # commissioning Git workspaces
│   ├── .git/
│   ├── params.yaml
│   ├── results.tsv
│   └── program.md
├── sessions/{robot_id}/                      # idle tuning session JSON
│   └── 20260328_143052_left_leg.json
├── predictors/{robot_id}/                    # XGBoost models
│   ├── single_v1.json
│   └── chain_left_leg_v1.json
├── agent_memory/{robot_id}/                  # agent 3-tier memory
│   └── memory.json
├── lifecycle/{robot_id}/                     # lifecycle event stream
│   └── stream.jsonl
├── workspaces/{robot_id}/                    # per-robot per-profile Git repos
│   └── .git/  (branches: {robot_id}/{profile_id})
└── knowledge_base/                           # cross-robot knowledge
    ├── model_zoo/pid_predictor/
    ├── template_library/by_scenario/
    └── metadata/lineage.jsonl
```

---

## Ports

| Port | Binding | Service |
|------|---------|---------|
| `:8090` | `0.0.0.0` | Agent Gateway (the only external port) |
| `:8080` | `127.0.0.1` | Core MCP Server |
| `:8081` | `127.0.0.1` | Joints MCP Server |
| `:8082` | `127.0.0.1` | Power MCP Server |
| `:8083` | `127.0.0.1` | IMU MCP Server |
| `:8087` | `127.0.0.1` | PID Tuner MCP Server |
| `:8088` | `127.0.0.1` | Idle Tuner MCP Server |
| `:9090` | `localhost` | rosbridge WebSocket (ROS2) |

---

## FAQ

**Q: LLM call fails during tuning — what happens?**
The system falls back to the rule engine automatically (design spec DD-C05). Tuning continues, `status="llm_error"` is recorded in `results.tsv`.

**Q: Token budget exhausted — what happens?**
`LLMBudgetExceededError` is caught. Commissioning switches to Optuna BO (numerical search, no LLM). Idle tuning uses conservative rules (no gain reduction). Budget resets daily at UTC 00:00.

**Q: No git binary on the machine — what happens?**
`PIDWorkspace` and `LifecycleRepository` detect the missing binary and fall back to `params_history.json`. All functionality works; you lose git-based history and rollback.

**Q: Is mock mode LLM output accurate?**
No. `MockJointSimulator` uses Euler-integration second-order physics. Without an `ANTHROPIC_API_KEY`, LLM calls fall back to the rule engine. Mock mode validates the full pipeline flow, not LLM tuning quality.

**Q: How do I add a new joint or robot model?**
Edit `config/robot_schema.yaml`:

```yaml
robot:
  kinematic_chains:
    left_leg: [left_hip_yaw, left_hip_roll, ...]
  chain_tuning_order:
    left_leg: [left_hip_yaw, left_hip_roll, ...]   # must be root-to-tip order
  mock_physics:
    overrides:
      my_new_joint: {inertia: 0.20, friction: 1.0}
```

---

*snakes-V · github.com/zengury/snakes-V*
