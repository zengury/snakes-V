# Manastone

**Your Unitree G1 tunes itself. You watch it improve.**

Manastone is an autonomic operations layer that runs on the G1's onboard Jetson Orin NX.
It tunes all 29 joint PID controllers automatically — during commissioning, during idle time,
and as the robot accumulates wear. You don't have to touch a parameter by hand.

```
pip install -e .
export MANASTONE_MOCK_MODE=true
python -m pytest tests/   # 138 tests, no robot needed
```

---

## The problem it solves

A Unitree G1 has **29 joints**. Each one needs a PID controller. Each PID has 3 parameters.
That's 87 numbers to get right — and they drift as motors heat up, parts wear, and loads change.

Manual tuning by a skilled engineer: **3–5 days per robot.**
Fleet of 10 robots: **1–2 engineer-months, every cycle.**
Silent degradation when nobody notices parameters have drifted: **always.**

There is no off-the-shelf solution that runs on the G1's Orin NX and handles this automatically.
Until now.

---

## Who needs this

### Robotics engineers deploying G1 at scale

You're shipping G1s to a warehouse, factory floor, or service environment.
You cannot afford to send an expert to each site to retune every robot.
Manastone runs the commissioning, monitors drift, and self-corrects — hands-off.

### Research labs running RL training

Your RL policy is only as good as the physical robot it trains on.
If PID parameters are stale or inconsistent between robots, sim-to-real breaks.
Manastone gives you a reproducible, version-controlled baseline every time.

### Robot integrators building G1-based products

Your product ships with a G1 inside it. You need the motion layer to just work —
reliably, in varying conditions, without per-unit manual calibration.
Manastone handles that layer so you can focus on the application on top.

---

## What it does

```
┌─────────────────────────────────────────────────────────────────┐
│                        Manastone on Orin NX                      │
│                                                                   │
│  ┌──────────────┐   ┌──────────────┐   ┌──────────────────────┐ │
│  │ Commissioning│   │  Idle Tuning │   │    Agent Gateway     │ │
│  │              │   │              │   │                      │ │
│  │ Optuna TPE   │   │ XGBoost fast │   │  "调参左腿"          │ │
│  │ + LLM annot. │   │ path or LLM  │   │  "健康报告"          │ │
│  │ 29 joints    │   │ deep path    │   │  Natural language    │ │
│  │ git-tracked  │   │ every idle   │   │  :8090 (SSE/MCP)     │ │
│  └──────┬───────┘   └──────┬───────┘   └──────────────────────┘ │
│         │                  │                                      │
│  ┌──────▼──────────────────▼──────────────────────────────────┐  │
│  │              Safety Guard (always on)                       │  │
│  │  torque < 60Nm · velocity < 20 rad/s · temp < 65°C         │  │
│  └──────────────────────────────────────────────────────────── │  │
│                                                                   │
└─────────────────────────────────────────────────────────────────┘
            │ rosbridge WebSocket ws://localhost:9090
            ▼
    ┌───────────────┐
    │  G1 RockChip  │  ← motion controller, read-only
    │  192.168.123.161│
    └───────────────┘
```

**Commissioning** — First-time setup. Runs up to 30 experiments per joint using Bayesian
optimization (Optuna TPE). An LLM annotates each experiment with a hypothesis. Every result
is a git commit. Best parameters are tagged.

**Idle Tuning** — While the robot stands still (all joints < 0.02 rad/s for 30s), Manastone
picks the chain with the highest anomaly score and nudges parameters. Fast path: XGBoost model
predicts deltas in milliseconds. Deep path: LLM skill runs when XGBoost isn't confident enough.
Session results feed back into the XGBoost training flywheel.

**Agent Gateway** — One port (:8090) exposes a natural language interface. Ask it anything.
Give it commands. Teach it things about your specific robot. It remembers across sessions.

**Knowledge Transfer** — Tuning results from one robot become a template for the next.
New robots inherit a working baseline instead of starting from scratch.

---

## Five tuning profiles, out of the box

| Profile | Use this when... |
|---------|-----------------|
| `classic_precision` | General deployment, pick-and-place |
| `rl_fidelity` | RL policy training, sim-to-real transfer |
| `energy_saver` | Long-duration tasks, thermal management |
| `high_speed` | Fast manipulation, dynamic movement |
| `collision_safe` | Human-robot collaboration, contact tasks |

Switch profiles at runtime. The system switches automatically (idle > 5 min → `energy_saver`).

---

## How to get started

**Try it without a robot** (mock mode, runs anywhere):

```bash
git clone https://github.com/zengury/snakes-V.git
cd snakes-V
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

export MANASTONE_MOCK_MODE=true
python -m pytest tests/ -q
# 138 passed in ~5s
```

**Deploy on the G1's Orin NX**:

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
export ROSBRIDGE_URL="ws://localhost:9090"

# Commission a single kinematic chain
python -c "
import asyncio
from manastone.commissioning.chain_orchestrator import ChainTuningOrchestrator
from manastone.profiles.registry import ProfileRegistry
from manastone.common.config import ManaConfig

orch = ChainTuningOrchestrator(ManaConfig.get(), ProfileRegistry().get('classic_precision'), 'G1_001')
result = asyncio.run(orch.tune_chain('left_leg'))
print(f'Chain score: {result.chain_score:.1f}')
"

# Start the agent (natural language interface + idle tuning)
python -m manastone.agent.mcp_interface --host 0.0.0.0 --port 8090
```

**Connect Claude Desktop to your robot**:

```json
{
  "mcpServers": {
    "manastone": { "url": "http://192.168.123.164:8090/mcp/sse" }
  }
}
```

Then ask: *"How is the left leg doing?"* or *"Tune the right arm."*

---

## Safety first

Every parameter write goes through a three-layer safety check:

1. **Static bounds** — each joint has hard kp/ki/kd limits in `config/robot_schema.yaml`
2. **Runtime monitor** — torque, velocity, and temperature trip-wires kill any experiment
3. **Rollback** — every session stores the previous parameters; one call restores them

The motion controller on the G1's RockChip (192.168.123.161) is never touched.
Manastone is read-and-suggest only toward that chip. All writes go through the Orin NX's
ROS2 stack, which enforces its own hardware limits.

---

## What's inside

```
70 Python source files · 5,979 lines · 138 tests · 0 robot required to develop

src/manastone/
├── common/          Models, config, safety bounds
├── runtime/         DDS bridge, ring buffers, anomaly scorer, event store
├── lifecycle/       State machine, context bridge, profile switching
├── commissioning/   Bayesian + LLM autotuning, chain orchestrator
├── profiles/        5 built-in profiles, scorer/generator plugin system
├── idle_tuning/     Idle detector, XGBoost flywheel, skill runner, param writer
├── agent/           LLM proxy, memory (3-tier), intent parser, workflows
└── knowledge/       Template library, model zoo, parameter lineage, transfer
```

All modules have ABC-injected mocks. No `if MOCK_MODE:` in business logic.
Every subsystem degrades gracefully: no LLM → rule engine, no git → JSON fallback,
no XGBoost → LLM deep path.

---

## Hardware requirements

| Component | Spec |
|-----------|------|
| Robot | Unitree G1 (29-DOF) |
| Compute | Jetson Orin NX (onboard) |
| Connectivity | Wi-Fi to dev machine, rosbridge on port 9090 |
| Python | 3.10+ |
| LLM | Anthropic Claude (any model via `ANTHROPIC_API_KEY`) or offline with rule fallback |

DDS domain ID must be `0` (fixed to match G1 firmware).

---

## Docs

- **[DEPLOYMENT.md](DEPLOYMENT.md)** — Role-by-role deployment guide: robot engineer, ops,
  ML engineer, platform engineer, developer. Includes every command, every env var,
  every storage path.

---

## License

MIT

---

*Manastone — autonomic operations for Unitree G1 · [github.com/zengury/snakes-V](https://github.com/zengury/snakes-V)*
