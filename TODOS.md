# TODOS — Manastone Autonomic Operations Layer

Deferred items from /autoplan CEO + Eng review. Not blocking M1.

---

## Deferred from CEO Review

### T1 — Policy drift detection
Track when PID parameters drift over time (e.g., seasonal bearing wear). Currently the system
re-tunes reactively (anomaly score triggers idle tuning). Proactive drift prediction would
schedule preemptive tuning before anomaly threshold is hit.
- **Why deferred:** Requires 30+ days of production runtime data to build a baseline.
  Cannot implement without real robot history. Revisit after first real deployment.
- **Blocking:** nothing in M1-M5
- **Rough spec:** Add `DriftMonitor` to `runtime/semantic_engine.py`. Compare rolling 7-day
  mean PID effectiveness (from `results.tsv` history) to baseline. Emit `DRIFT_WARNING` event.

### T2 — Grafana / OpenTelemetry export
Export runtime metrics (anomaly scores, PID tracking errors, session outcomes) to Grafana
via OpenTelemetry OTLP exporter for production monitoring dashboards.
- **Why deferred:** No monitoring infra defined for Orin NX yet. Adds another port + process.
- **Blocking:** nothing in M1-M5
- **Rough spec:** Add `otel_exporter.py` to `runtime/`. Push `anomaly_score`, `joint_temp`,
  `session_outcome`, `llm_tokens_used` as gauge metrics. Batch 10s intervals.

### T3 — Multi-robot federation (build stub only in M5)
Allow a fleet of G1 robots to share tuning knowledge: XGBoost models trained on Robot A
inform the cold-start for Robot B with similar joint characteristics.
- **Why deferred:** Requires at least 2 physical robots to validate. Network topology TBD.
- **Blocking:** nothing in M1-M5. M5 `knowledge/` module includes stubs for ModelZoo + TemplateLibrary.
- **Rough spec:** `knowledge/model_zoo.py` publishes models to a shared NFS mount or S3 bucket.
  `knowledge/transfer.py` downloads and adapts models using `strict/adaptive/zero_shot` strategies.
  Phase 5 builds the stubs; federation activation is T3.

---

## Deferred from Eng Review

### T4 — systemd unit for auto-restart
Package the Agent Runtime (:8090) and all Layer 3 MCP servers as systemd services so they
restart on crash and start on boot on the Jetson Orin NX.
- **Why deferred:** Deployment config is out of scope for M1. Required before first real deployment.
- **Rough spec:** Write `deploy/systemd/manastone-agent.service` and
  `deploy/systemd/manastone-mcp@.service` (templated for each port). Include `Restart=always`,
  `RestartSec=5`, `After=network.target`.

### T5 — Bearer token auth on :8090
The Agent Runtime is the only external port. Production deployment needs Bearer token auth
on all `/ask`, `/command`, `/teach` endpoints.
- **Why deferred:** Security hardening is post-M1. Cannot ship publicly until T5 is done.
- **Rough spec:** Add `auth_token: str` to `ManaConfig`. `rest_api.py` validates
  `Authorization: Bearer <token>` header. Return 401 on mismatch. Token loaded from env var
  `MANASTONE_AUTH_TOKEN`. Not needed for localhost development.

---

## M2 Test gaps (from Eng Review test coverage diagram)

These tests are required before Phase 3 (idle_tuning) merges. Not blocking M1.

- [ ] `tests/test_dds_reconnect.py` — DDSBridge: 5s backoff, DDSConnectionLostError after 3 retries
- [ ] `tests/test_predictor.py` — XGBoost flywheel: cold-start (0-9 sessions LLM only), 10th → train
- [ ] `tests/test_llm_proxy.py` — token budget accumulation, LLMBudgetExceededError, BO fallback
- [ ] `tests/test_profiles.py` — ProfileRegistry hot-load YAML, invalid YAML → ValidationError
- [ ] `tests/test_features.py` — feature vector dimensions stable, column names match model input
- [ ] `tests/test_commissioning_eval.py` [EVAL] — LLM param proposal quality regression
- [ ] `tests/test_commissioning.py` — mid-chain failure → rollback to pre-chain state
- [ ] `tests/test_predictor.py` — ±5% nudge clipped by StaticBoundsChecker

---

## Revisit after M1

- [ ] MLP/TinyMLP vs XGBoost: revisit after Phase 3 baseline. If XGBoost F1 < 0.7 on validation
      set, evaluate `torch.nn` 3-layer MLP. For now, XGBoost is the pragmatic choice.
- [ ] `autoresearch/llm_client.py` model string: currently `claude-sonnet-4-20250514`.
      Update to latest model after M1 is green. Pin exact model in `ManaConfig.llm_model`.
