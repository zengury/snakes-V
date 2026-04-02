# Manastone File Memory System (MemDir) — Design

**Status:** Phase 1 implemented (robot identity auto-maintained + LLM-assisted auto-enrichment when available)

This document is a design note shipped with the implementation.
It is intentionally detailed so future contributors can extend the system
without re-deriving the architecture.

---

## One-line

A file-based, auditable, bounded persistent memory system for the robot agent.

- **File-based**: memories are Markdown files that live on disk (diffable, reviewable).
- **Auditable**: no opaque vector DB as the source of truth.
- **Bounded**: memory index is size-capped; only a few relevant files are recalled.
- **Safe**: deterministic writes (Phase 1), and later LLM-driven updates gated by strict schemas.

This design is inspired by Claude Code's `memdir` approach, adapted for robotics.

---

## Goals

1. **Always know who the robot is.**
   The system must maintain a stable identity memory that answers: *"Who am I?"*
2. **Support gradual enrichment.**
   Safety gotchas, procedures, preferences, incidents, and service context should be addable later without redesign.
3. **Low token cost.**
   Injecting all history is expensive and noisy. We instead recall a small set of relevant memory files.
4. **Operational safety.**
   Memory writes must never grant broad filesystem write privileges.

---

## Memory taxonomy (robot-adapted)

We use a small set of memory types, each with a distinct purpose.

### 1) `robot_fact` (Phase 1: **forced write**)

**Definition:** Stable identity and environment facts about this robot instance.

**Purpose:** Ensure the agent can always answer *"Who am I?"* and can ground decisions
in stable facts (robot id, mode, endpoints, safety thresholds, chains).

**Phase 1 policy:** **always maintained automatically**. No human action required.

Example contents:
- robot_id
- robot_type
- mock_mode
- rosbridge_url
- kinematic chains
- safety thresholds

### 2) `safety_gotcha` (Phase 1: **manual + pinned recall**)

Hard safety boundaries and known failure modes.

**Phase 1 policy:**
- A `safety_gotcha.md` file is **bootstrapped if missing** (template content).
- The file is **human-maintained** thereafter (the program does not overwrite it).
- Recall always includes it with high priority (pinned), so safety constraints are never “future”.

### 3) `procedure` (future)

Runbooks / SOPs. Repeatable operational playbooks.

### 4) `preference` (future)

Operator/service preferences: reporting format, risk tolerance, confirmation expectations.

### 5) `incident` (future)

Timestamped case notes: what happened, symptoms, action taken, outcome.

### 6) `service_context` (future)

A self-model layer for the robot's operating context:
- Who are my service objects (operator, site, fleet, client)?
- What are their preferences?
- Have they changed over time?
- What runtime environment am I operating in?

**Policy:** treated like all other non-identity memory types: supported by the taxonomy, but not auto-written in Phase 1.

---

## Storage layout

Per-robot memory directory:

```
storage/agent_memory/<robot_id>/memories/
├── MEMORY.md              # index (one-line hooks)
├── robot_identity.md      # Phase 1: forced robot_fact
└── safety_gotcha.md       # Phase 1: manual baseline (bootstrapped)
```

### `MEMORY.md` index rules

- Index only (no memory content).
- One line per file:
  `- [Title](file.md) — one-line hook`
- Bounded:
  - max 200 lines
  - max 25KB

These caps keep the index safe to auto-inject.

---

## Phase 1 behavior (implemented)

### What is auto-written (always)

- `robot_identity.md` (type `robot_fact`) is always created/updated on agent startup (and refreshed after turns).
- A matching index entry in `MEMORY.md` is upserted.

### What is auto-enriched (when LLM is available)

All memory types are eligible for automatic enrichment via an LLM-assisted extractor.

**Trigger:** the extractor runs once per robot *operating cycle* (between idle windows):
- idle=True → idle=False starts a cycle
- idle=False → idle=True ends a cycle and triggers consolidation

**Mock-mode note:** In mock mode the idle detector is always idle by default, so no cycles occur.
For development/testing, you can simulate cycles by setting `MANASTONE_MOCK_CYCLE_TICKS=<N>`
which toggles idle↔active every N background observer ticks.

- `safety_gotcha`
- `procedure`
- `preference`
- `incident`
- `service_context`

**Important:** when no LLM is available (no API key / budget exceeded), auto-enrichment degrades to a no-op. Identity maintenance still works.

---

## Recall behavior (implemented)

The agent builds a compact *file-memory recall context* at query time:

- Always includes `robot_identity.md` if present.
- Always includes `safety_gotcha.md` if present.
- Optionally includes up to 3 additional memories selected via a simple keyword-overlap heuristic.

This remains offline-friendly; LLM usage is only required for **auto-writing/enrichment**, not for recall.

---

## Safety model

### Phase 1 invariants

- `robot_identity.md` is deterministic and always refreshed.
- `safety_gotcha.md` is human-maintained and always injected (pinned).

### LLM-assisted writes (Phase 2+)

We use a hybrid safety model:

- Identity (`robot_fact`) is written deterministically by the program.
- Auto-enrichment for other types is LLM-assisted, but **the LLM never writes files directly**.
  Instead it outputs a structured JSON "write plan" which the program applies.

LLM-assisted memory updates are gated by:

1. **Structured output schemas** for proposed changes
2. **Filename sanitization** and strict root confinement (no path traversal)
3. **Writes restricted to the memdir root only**

This mirrors the core safety principle in Claude Code's memdir subsystem.

---

## Implementation map

- `src/manastone/agent/memdir.py`
  - path layout
  - frontmatter parsing
  - index upsert + truncation
  - identity memory generator (deterministic)

- `src/manastone/agent/file_memory.py`
  - recall context builder (rule-based)

- `src/manastone/agent/memory_extractor.py`
  - LLM-assisted extraction (structured JSON write plan)
  - safe application of upserts/deletes under memdir root

- `src/manastone/agent/agent.py`
  - ensures identity memory exists on startup
  - injects file-memory recall context into `ask()` and health report workflow

- `src/manastone/agent/background.py`
  - detects idle transitions (cycle boundaries)
  - triggers one memdir consolidation per cycle (best-effort)

---

## Roadmap / TODO (avoid future “memory debt”)

These are intentionally written down early to prevent later patchwork:

1. **Memory write protocol (Phase 2 gate):** approvals, update-vs-create rules, conflict handling.
   See: `docs/memory-write-protocol.md`.

2. **Incident retention:** monthly archive + recency bias in recall.

3. **Fleet-shared memory layer:** allow a second read-only recall overlay for model/robot-family level SOPs and safety.
   (e.g. `storage/fleet_memory/memories/**` injected before per-robot memories.)

4. **Recall quality:** type priority (safety > procedure > incident > preference), recency weighting, and optional semantic fallback.
