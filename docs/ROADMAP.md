# Manastone Roadmap — Robot Internal Agent Runtime

**Status:** living document (update on every meaningful architectural change)

This project’s long-term goal is to become an **on-robot agent runtime** for humanoid robots (Unitree G1 first), enabling:

- self-diagnosis / self-debugging
- self-operations / self-maintenance (runbooks + incident learning)
- safe self-control (bounded actuation, approvals)
- autonomous work execution (tasks, plans, monitoring)

We use the term “autonomy / self-model” rather than implying human-like consciousness.
The intent is: **a persistent, goal-directed runtime with a governed self-representation, safety boundaries, and continuous improvement loops.**

---

## Guiding principles (non-negotiables)

1. **Safety-first control**
   - No direct high-risk actuation without gating/approval.
   - Defaults are conservative; unsafe paths are opt-in.

2. **Deterministic core, LLM as cognitive layer**
   - Orchestration, rule checks, and safety gates are deterministic.
   - LLM is used for summarization/interpretation, not for authority.

3. **File-based auditable memory as source of truth**
   - Durable memory lives as markdown files (diffable, reviewable).
   - Vector/semantic retrieval may exist, but never as the primary truth.

4. **Offline-first degradation**
   - If network/LLM is unavailable, the robot still operates safely (reduced capabilities).

5. **Simulation-first & replayable evaluation**
   - Every “autonomy increase” must be testable in mock/sim, with logs/replays.

---

## Milestones

### M0 — Foundations (DONE / ongoing hardening)
**Goal:** stable base runtime loop + safe defaults.

Deliverables:
- structured output for intent (JSON schema)
- risky actions require confirmation gate (real-mode default)
- MemDir Phase 1: pinned identity + pinned safety baseline
- cycle-based consolidation (idle→active→idle)

DoD:
- unit tests pass
- mock mode can simulate cycles
- memory writes are bounded and path-safe

---

### M1 — Memory governance v1 (NEXT)
**Goal:** make memory safe to evolve over months/years without debt.

Deliverables:
1. **Memory Write Protocol (Phase 2 gate)**
   - define write-plan schema
   - define update vs create rules
   - define conflict detection + `needs_review` outcome
   - define human approvals for high-risk edits

2. **Incident retention & recency**
   - archive policy (monthly / size caps)
   - recency-weighted recall

3. **Fleet-shared memory overlay**
   - add `fleet_memory/**` layer (manual by default)
   - recall merge order: fleet safety → robot safety → identity → others

DoD:
- new doc: `docs/memory-write-protocol.md` is updated to “v1 implemented” sections
- test suite covers: pinned recall ordering, retention archive trigger, fleet overlay merge

---

### M2 — Observability & replayable ops
**Goal:** robot runtime is debuggable like a production service.

Deliverables:
- unified event log with severity + component attribution
- “flight recorder” replay bundle (config + events + actions + tool calls)
- redaction rules for privacy/secrets
- regression harness: replay → expected invariants

DoD:
- can export a single diagnostic bundle and replay locally
- privacy rules validated in tests

---

### M3 — Skills + runbook execution engine
**Goal:** turn procedures into executable, safe workflows.

Deliverables:
- declarative skill format (YAML/JSON) with:
  - preconditions
  - step plan
  - allowed tools
  - rollback plan
  - success metrics
- execution engine with:
  - pause/resume
  - checkpointing
  - approvals per step class

DoD:
- at least 5 real runbooks implemented (e.g., temperature anomaly triage, joint drift triage)
- all runbooks replayable in mock mode

---

### M4 — Control plane (bounded actuation)
**Goal:** safe self-control primitives with strong constraints.

Deliverables:
- action primitives with safety envelopes (speed/torque/temperature thresholds)
- “intent → plan → approve → execute → monitor → abort” pipeline
- formal safety invariants:
  - e-stop triggers
  - rate limits
  - environment constraints

DoD:
- no actuator command bypasses the gate
- explicit safety proofs/invariants in docs + tests

---

### M5 — Autonomous work execution (task autonomy)
**Goal:** the robot can execute operator goals end-to-end with monitoring.

Deliverables:
- task planner (hierarchical)
- monitoring loop (state estimator + anomaly detection)
- failure handling strategies:
  - retry
  - degrade
  - escalate to human

DoD:
- 3 end-to-end tasks with success metrics + failure playbooks

---

### M6 — Self-improvement loop (governed)
**Goal:** the runtime can improve itself without drifting into unsafe behavior.

Deliverables:
- learning channels:
  - incident → procedure update proposals
  - procedure effectiveness scoring
  - parameter tuning proposals
- governance:
  - proposal review queue
  - approval workflows
  - rollback snapshots

DoD:
- improvements are proposals first; no silent self-modification of safety-critical rules

---

## Backlog (epics)

### Memory
- [ ] implement fleet overlay directory + merge recall
- [ ] implement incident archive + recency weighting
- [ ] implement conflict detection + needs_review outputs

### Runtime safety
- [ ] unify confirmations across tool execution + memory edits
- [ ] add guardrails for file writes and command execution (sandbox/policy)

### Autonomy
- [ ] skill execution engine with pause/resume/checkpoint
- [ ] monitoring + abort controller

---

## Glossary

- **Pinned memory:** always injected into context regardless of query.
- **Fleet overlay:** shared memory layer applied before per-robot memory.
- **Governed self-model:** stable identity + safety + procedures updated via auditable protocol.
