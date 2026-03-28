from manastone.agent.memory import AgentMemory


class AgentEventSink:
    """Callback interface for the 4-phase system to notify the Agent."""

    def __init__(self, memory: AgentMemory):
        self.memory = memory

    def on_tune_started(self, chain_name: str, phase: str) -> None:
        self.memory.record_event("tune_started", f"{chain_name} {phase} started", caller=phase)

    def on_tune_result(self, chain_name: str, score: float, duration_s: float, outcome: str) -> None:
        self.memory.record_event(
            "tune_result",
            f"{chain_name} score={score:.1f}, {duration_s:.0f}s, {outcome}",
            caller="commissioning",
        )
        # Update consecutive rollbacks counter
        if outcome == "rollback":
            self.memory.working["consecutive_rollbacks"] = (
                self.memory.working.get("consecutive_rollbacks", 0) + 1
            )
        else:
            self.memory.working["consecutive_rollbacks"] = 0

    def on_anomaly(self, joint_name: str, anomaly_score: float, reason: str) -> None:
        self.memory.record_event(
            "anomaly_observed",
            f"{joint_name} anomaly={anomaly_score:.2f}, {reason}",
            caller="runtime",
        )

    def on_predictor_trained(self, model_type: str, version: str, confidence: float) -> None:
        self.memory.record_event(
            "predictor_trained",
            f"{model_type} {version}, confidence={confidence:.2f}",
            caller="predictor",
        )

    def on_lifecycle_transition(self, from_state: str, to_state: str) -> None:
        self.memory.record_event(
            "lifecycle_transition",
            f"{from_state} → {to_state}",
            caller="lifecycle",
        )
