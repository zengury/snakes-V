from typing import Dict, Any, Optional

WORKFLOWS = {
    "commissioning_full": {
        "description": "Full-body pre-deployment commissioning",
        "chains": ["left_leg", "right_leg", "waist", "left_arm", "right_arm"],
    },
    "health_report": {
        "description": "Generate comprehensive health report",
        "steps": ["get_system_summary", "analyze_anomalies", "llm_summarize"],
    },
}


class WorkflowEngine:
    def __init__(self, agent):
        self._agent = agent

    async def run(self, workflow_name: str, **kwargs) -> Dict[str, Any]:
        if workflow_name not in WORKFLOWS:
            return {"success": False, "error": f"Unknown workflow: {workflow_name}"}

        wf = WORKFLOWS[workflow_name]
        self._agent.memory.record_event(
            "workflow_started", f"{workflow_name}: {wf['description']}"
        )

        try:
            if workflow_name == "commissioning_full":
                return await self._run_commissioning_full(**kwargs)
            elif workflow_name == "health_report":
                return await self._run_health_report(**kwargs)
            else:
                return {"success": False, "error": "Workflow not implemented"}
        except Exception as e:
            self._agent.memory.record_event(
                "workflow_error", f"{workflow_name}: {str(e)[:100]}"
            )
            return {"success": False, "error": str(e)}

    async def _run_commissioning_full(self, **kwargs) -> Dict[str, Any]:
        """Stub: queues chain_tune for each chain in order."""
        chains = WORKFLOWS["commissioning_full"]["chains"]
        results = {}
        for chain in chains:
            results[chain] = {"status": "queued", "note": "Commissioning requires robot access"}
        self._agent.memory.record_event(
            "workflow_result", f"commissioning_full queued {len(chains)} chains"
        )
        return {"success": True, "chains_queued": chains, "results": results}

    async def _run_health_report(self, **kwargs) -> Dict[str, Any]:
        """Generate a health report from memory."""
        recent = self._agent.memory.get_recent_events(20)
        insights = self._agent.memory.semantic.get("insights", [])[-5:]

        # If LLM available, summarize
        summary = f"Recent events: {len(recent)}. Insights: {len(insights)}."
        if self._agent.llm_proxy:
            try:
                event_text = "\n".join(
                    f"- {e['type']}: {e['summary']}" for e in recent[-10:]
                )
                insight_text = "\n".join(f"- {i['text']}" for i in insights)
                summary = await self._agent.llm_proxy.call(
                    caller="agent",
                    system_prompt=(
                        "You are a robot health analyst. Summarize the robot's recent "
                        "performance and key insights in 3-5 bullet points."
                    ),
                    user_message=f"Events:\n{event_text}\n\nInsights:\n{insight_text}",
                    inject_memory=False,
                    max_tokens=400,
                )
            except Exception:
                pass  # Fall back to simple summary

        self._agent.memory.record_event("workflow_result", "health_report generated")
        return {
            "success": True,
            "summary": summary,
            "event_count": len(recent),
            "insight_count": len(insights),
        }
