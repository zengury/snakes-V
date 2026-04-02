# TODO — Manastone (living)

This file is the short, actionable checklist companion to `docs/ROADMAP.md`.

Rule: if a TODO is important enough to mention in chat, it must be captured here.

---

## Now (Phase 1 hardening)

- [ ] Create `safety_gotcha.md` bootstrap templates tailored to Unitree G1 (real safety boundaries, not placeholders)
- [ ] Add a small CLI command to open/edit pinned memories (identity/safety) safely

---

## Next (Memory governance v1)

### Memory Write Protocol (implementation)
- [ ] Formalize the write-plan JSON schema in code and validate it strictly
- [ ] Add a `needs_review` outcome when conflicts are detected
- [ ] Add human approval gate for:
  - [ ] edits to `safety_gotcha.md`
  - [ ] deletes
  - [ ] edits to identity

### Incident retention
- [ ] Add an incident archive mechanism (monthly)
- [ ] Add recency weighting to recall selection

### Fleet overlay
- [ ] Add `storage/fleet_memory/memories/**`
- [ ] Add recall merge order: fleet safety → robot safety → identity → others
- [ ] Document fleet write policy (manual by default)

---

## Later

- [ ] Skill/runbook execution engine (pause/resume/checkpoint)
- [ ] Observability bundle export + replay harness
- [ ] Bounded control primitives + invariant tests
