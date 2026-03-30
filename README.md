# Manastone

**Autonomous PID tuning for the Unitree G1. Runs on the robot. No engineer on-site.**

---

## Status

| Capability | State |
|---|---|
| Mock-mode commissioning (all 5 chains, Bayesian + LLM) | ✅ Validated — 138 tests passing |
| Mock-mode idle tuning (XGBoost flywheel, anomaly detection) | ✅ Validated |
| Mock-mode agent gateway (natural language interface, memory) | ✅ Validated |
| Real rosbridge WebSocket connection (Orin NX ↔ RockChip) | 🔧 Implemented, pending real-hardware test |
| Real PID parameter write via ROS2 stack | 🔧 Implemented, pending real-hardware test |
| MCP/SSE server on `:8090` | 🔧 Scaffolded — real SSE wiring in progress (Phase 5) |
| Wrist joints (6 DOF to full 29) | 📋 Planned — current schema covers 23 joints |

The pipeline architecture is complete and testable without a robot. Hardware validation is the next milestone.

---

## The problem nobody has solved yet

A Unitree G1 has 29 joints. Each joint has a PID controller. Each controller has 3 parameters.

That is **up to 87 numbers** that have to be right — and they drift. Manastone currently covers 23 actuated joints (legs, arms, and waist); wrist joints are planned for the next schema revision. Motors heat up. Parts wear. Loads change. When the numbers are off, the robot moves badly. When they drift silently, nobody notices until something fails.

Manual tuning by a skilled engineer: **3–5 days per robot.**
Fleet of 10 robots: **1–2 engineer-months, every cycle.**
Silent degradation between tune cycles: **always.**

There is no off-the-shelf solution that runs on the G1's Orin NX and handles this automatically. Until Manastone.

---

## What Manastone does

Manastone runs on the onboard Jetson Orin NX. It owns the PID layer completely.

**First deployment** — it commissions the robot itself. Bayesian optimization (Optuna TPE) runs up to 30 experiments per joint, explores the parameter space, and locks in the best configuration. Every result is a git commit. The best parameters are tagged.

**While the robot works** — it monitors all 29 joints for anomaly. The moment something drifts, it flags the chain.

**When the robot is idle** — it tunes. Detects stillness (all joints < 0.02 rad/s for 30 seconds), picks the highest-anomaly chain, and nudges parameters. Fast path: XGBoost model predicts deltas in milliseconds. Deep path: LLM skill for cases where XGBoost isn't confident. Results feed back into the model.

**Fleet learning** — tuning results from robot 1 become the starting point for robot 2. New robots don't start from scratch.

```
┌─────────────────────────────────────────────────────────────────┐
│                        Manastone on Orin NX                      │
│                                                                   │
│  ┌──────────────┐   ┌──────────────┐   ┌──────────────────────┐ │
│  │ Commissioning│   │  Idle Tuning │   │    Agent Gateway     │ │
│  │              │   │              │   │                      │ │
│  │ Optuna TPE   │   │ XGBoost fast │   │  "tune left leg"     │ │
│  │ + LLM annot. │   │ path or LLM  │   │  "health report"     │ │
│  │ 29 joints    │   │ deep path    │   │  Natural language    │ │
│  │ git-tracked  │   │ every idle   │   │  :8090 (SSE/MCP)     │ │
│  └──────┬───────┘   └──────┬───────┘   └──────────────────────┘ │
│         │                  │                                      │
│  ┌──────▼──────────────────▼──────────────────────────────────┐  │
│  │              Safety Guard (always on)                       │  │
│  │  torque < 60 Nm · velocity < 20 rad/s · temp < 65°C        │  │
│  └────────────────────────────────────────────────────────────┘  │
│                                                                   │
└─────────────────────────────────────────────────────────────────┘
            │ rosbridge WebSocket ws://localhost:9090
            ▼
    ┌───────────────────┐
    │   G1 RockChip     │  ← motion controller, read-only
    │  192.168.123.161  │
    └───────────────────┘
```

---

## Who needs this

### Deploying G1s at scale

You are shipping G1s to a warehouse, factory floor, or service environment. You cannot afford to fly an engineer to each site every time parameters drift. Manastone runs commissioning on arrival, monitors drift in the background, and self-corrects during idle time — without a person in the loop.

### Running RL training

Your policy is only as good as the physical robot it trains on. Stale or inconsistent PIDs break sim-to-real transfer. Manastone gives you a reproducible, version-controlled parameter baseline on every robot, every session.

### Building G1-based products

Your product ships with a G1 inside it. You need the motion layer to just work — reliably, across unit variation, across wear, across environmental conditions. Manastone owns that layer so you can focus on what runs on top of it.

---

## Control it in plain language

Connect Claude Desktop (or any MCP client) to the agent port:

```json
{
  "mcpServers": {
    "manastone": { "url": "http://192.168.123.164:8090/mcp/sse" }
  }
}
```

Then ask:

> *"How is the left leg doing?"*
> *"Tune the right arm."*
> *"Show me the last 10 experiments for the hip joint."*
> *"What changed since yesterday?"*

The agent remembers across sessions. It knows your robot's history.

---

## Five tuning profiles

| Profile | When to use |
|---------|-------------|
| `classic_precision` | General deployment, pick-and-place, default |
| `rl_fidelity` | RL policy training — maximizes reproducibility |
| `energy_saver` | Long-duration tasks, thermal management |
| `high_speed` | Fast manipulation, dynamic movement |
| `collision_safe` | Human-robot collaboration, contact tasks |

Switch profiles at runtime. Automatic switching: idle for 5 minutes triggers `energy_saver`.

---

## Safety

Every parameter write goes through three layers:

1. **Static bounds** — each joint has hard kp/ki/kd limits in `config/robot_schema.yaml`
2. **Runtime monitor** — torque, velocity, and temperature trip-wires abort any experiment immediately
3. **Rollback** — every session stores the previous parameters; one call restores them

The G1's RockChip motion controller (192.168.123.161) is never touched. Manastone is read-and-suggest toward that chip. All writes go through the Orin NX's ROS2 stack, which enforces its own hardware limits.

---

## Get started

**Without a robot** (mock mode, runs on any machine):

```bash
git clone https://github.com/zengury/snakes-V.git
cd snakes-V
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

export MANASTONE_MOCK_MODE=true
python -m pytest tests/ -q
# 138 passed in ~5s
```

**On the G1's Orin NX**:

> ⚠️ **Security:** The agent port `:8090` has no authentication yet ([issue #1](https://github.com/zengury/snakes-V/issues/1)). Do not expose it on a shared or public network. Use SSH port-forwarding until this is resolved.

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

See [DEPLOYMENT.md](DEPLOYMENT.md) for the full role-by-role guide: every command, every environment variable, every storage path.

---

## What's inside

```
70 Python source files · 5,979 lines · 138 tests · no robot required to develop

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

All modules use ABC-injected mocks. No `if MOCK_MODE:` in business logic.
Every subsystem degrades gracefully: no LLM falls back to a rule engine, no git falls back to JSON, no XGBoost falls back to the LLM deep path.

---

## Hardware

| Component | Spec |
|-----------|------|
| Robot | Unitree G1 (29-DOF) |
| Compute | Jetson Orin NX (onboard) |
| Connectivity | Wi-Fi to dev machine, rosbridge on port 9090 |
| Python | 3.10+ |
| LLM | Anthropic Claude via `ANTHROPIC_API_KEY`, or offline with rule fallback |

DDS domain ID must be `0` (fixed to match G1 firmware).

---

## License

MIT

---

*Manastone — autonomic operations for Unitree G1 · [github.com/zengury/snakes-V](https://github.com/zengury/snakes-V)*
