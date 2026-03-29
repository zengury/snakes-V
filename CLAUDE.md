# CLAUDE.md — Manastone contributor guide

Quick orientation for Claude Code users working on this repo.

## What this project is

Manastone is an autonomic PID tuning layer for the Unitree G1 humanoid robot (29-DOF).
It runs on the onboard Jetson Orin NX. The system auto-tunes all 29 joint PID controllers
during commissioning, monitors for drift at runtime, and self-corrects during idle periods.

The G1's RockChip motion controller (`192.168.123.161`) is **read-only**. Never write to it.
All parameter writes go through the Orin NX's ROS2 stack.

## Hardware

```
Dev machine  ──Wi-Fi──►  Orin NX  192.168.123.164  ← deploy here
                          RockChip 192.168.123.161  ← read-only, DO NOT touch
```

DDS domain ID is always `0`.

## Run everything without a robot

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

export MANASTONE_MOCK_MODE=true
python -m pytest tests/ -q
# 138 passed
```

## Key design rules

- **No `if MOCK_MODE:` in business logic.** All mocking is done through ABC injection.
  `ManaConfig.create_dds_bridge()` and `ManaConfig.create_param_writer()` return the
  mock or real implementation depending on config. Business logic never checks the flag.

- **LLM is never in the control loop.** LLM calls happen on the commissioning / idle-tuning
  timescale (seconds to minutes), never in the 50 Hz joint-state loop.

- **Safety first.** Every parameter write must pass `StaticBoundsChecker` in
  `common/safety.py`. The bounds come from `config/robot_schema.yaml`. Writes that
  exceed bounds are blocked and logged — never silently clamped.

- **Git as state store.** Each robot x profile pair has its own Git workspace under
  `storage/pid_workspace/{robot_id}/`. Every experiment is a commit. Rollback is
  `git checkout HEAD -- params.yaml`.

- **One external port.** `:8090` only. All Layer 3 MCP servers bind `127.0.0.1`.

## Source layout

```
src/manastone/
├── common/          Models (Pydantic v2), config, safety bounds
├── runtime/         DDS bridge, ring buffers, anomaly scorer, SQLite event store
├── lifecycle/       State machine, context bridge, profile switching, Git repo mgmt
├── commissioning/   Bayesian + LLM autotuning (Optuna TPE), chain orchestrator
├── profiles/        5 built-in YAML profiles, scorer + generator plugin system
├── idle_tuning/     Idle detector, XGBoost flywheel, skill runner, param writer
├── agent/           LLM proxy, 3-tier memory, intent parser, MCP interface (:8090)
└── knowledge/       Template library, model zoo, parameter lineage, transfer
```

## How to add things

### New MCP tool

Add to `src/manastone/agent/mcp_interface.py`:

```python
async def tool_my_tool(param: str) -> dict:
    """Description shown to the LLM."""
    return await agent.my_method(param)
```

### New tuning profile

Create `src/manastone/profiles/builtin/my_profile.yaml`. The registry hot-loads on
re-instantiation — no restart needed. See `DEPLOYMENT.md` for the full YAML schema.

### New joint or robot

Edit `config/robot_schema.yaml`. Add the joint to a kinematic chain and specify
`chain_tuning_order` root-to-tip. Add `mock_physics.overrides` for mock mode physics.

## Code quality

```bash
ruff check src/    # lint (select: E, F, UP, B, I)
black src/         # format
mypy src/          # type check (strict mode)
```

Tests use `pytest asyncio_mode = "auto"` — async test functions work without decorators.

## Open issues worth knowing about

- **[#1](https://github.com/zengury/snakes-V/issues/1) — `:8090` has no auth.**
  Do not expose it on a shared network. SSH port-forward from your dev machine until fixed.

- **[#2](https://github.com/zengury/snakes-V/issues/2) — No systemd units.**
  The agent does not auto-restart on crash or reboot. Required before production deployment.
