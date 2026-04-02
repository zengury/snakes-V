# Memory Write Protocol (Draft)

This document is a **design checkpoint** between Phase 1 (deterministic + manual) and Phase 2 (LLM-assisted writes).

Goal: make memory writes safe, reviewable, and conflict-aware.

---

## Non-negotiables

1. **LLM never writes files directly.**
   It outputs a structured **write plan**.
2. **Strict write boundary.**
   Only `storage/agent_memory/<robot_id>/memories/**` is writable.
3. **Human approval for risky changes.**
   Any plan that:
   - modifies `robot_identity.md`, or
   - deletes files, or
   - touches `safety_gotcha.md`
   requires explicit confirmation (tokenized confirm/cancel, TTL).

---

## Write plan schema (conceptual)

A plan is a list of operations:

- `create`: new file
- `update`: patch existing file
- `append`: append-only entry (preferred for `incident`)
- `no_op`

Each operation must include:
- `type`: one of the memory taxonomy types
- `filename`: sanitized
- `title` + `hook` (for index)
- `content`: markdown body (frontmatter added by program)
- `merge_strategy`: `replace_section | append | keep_existing`

---

## Update vs create

Rules (deterministic):

- If filename exists: treat as `update` (never `create`).
- If type is `incident`: default to `append` with timestamped entry.
- If type is `procedure` or `preference`: default to `replace_section` with stable headings.

---

## Conflict detection

Conflicts are detected by the program (not the LLM) before applying:

- **Type-level conflicts**: two files of the same type with overlapping scope.
- **Semantic conflicts (lightweight)**: contradictory statements under a stable heading.

Policy:
- On conflict: do not apply automatically; produce a `needs_review` outcome + human-readable diff summary.

---

## Retention policy (incidents)

Baseline:
- Incidents are append-only and timestamped.
- Older incidents may be summarized/archived monthly.

Proposed mechanism:
- `incidents/` keeps last N days (e.g., 30–90)
- older content is moved into `incidents/archive/YYYY-MM.md` with a short index hook.

---

## Fleet-shared memory (future)

Add a second, read-only recall layer:

- `storage/fleet_memory/memories/**` (or `agent_memory/_fleet/memories/**`)

Recall merge order:
1. Fleet `safety_gotcha` (pinned)
2. Robot `safety_gotcha` (pinned)
3. Robot identity
4. Other relevant memories (robot first, then fleet)

Fleet memory writes should be **separately permissioned** and default to manual.
