"""
SemanticEngine — converts raw joint context into semantic events.

Evaluated once per second across all joints (not on every 50Hz sample).
Events are written to EventStore by the caller.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Tuple

from manastone.common.models import JointContext


# (event_type, condition, severity, value_attr)
_Rule = Tuple[str, Callable[[JointContext], bool], str, str]

_RULES: List[_Rule] = [
    ("joint_temp_warning",    lambda j: j.temp_c > 50,              "warning",   "temp_c"),
    ("joint_temp_critical",   lambda j: j.temp_c > 70,              "critical",  "temp_c"),
    ("torque_spike",          lambda j: abs(j.torque_nm) > 40,      "warning",   "torque_nm"),
    ("torque_critical",       lambda j: abs(j.torque_nm) > 60,      "critical",  "torque_nm"),
    ("tracking_error_high",   lambda j: j.tracking_error_mean > 0.05,"warning",  "tracking_error_mean"),
    ("comm_lost",             lambda j: j.comm_lost_count > 0,       "critical",  "comm_lost_count"),
    ("velocity_spike",        lambda j: abs(j.velocity_rad_s) > 15,  "warning",  "velocity_rad_s"),
    ("efficiency_low",        lambda j: j.torque_efficiency < 0.5,   "info",     "torque_efficiency"),
]

# Map event_type → threshold used for EventStore.append
_THRESHOLDS: Dict[str, float] = {
    "joint_temp_warning":   50.0,
    "joint_temp_critical":  70.0,
    "torque_spike":         40.0,
    "torque_critical":      60.0,
    "tracking_error_high":  0.05,
    "comm_lost":            0.0,
    "velocity_spike":       15.0,
    "efficiency_low":       0.5,
}


class SemanticEngine:
    """Rule-based semantic event generator."""

    def evaluate(self, joint_ctx: JointContext) -> List[Dict[str, Any]]:
        """Return list of triggered event dicts for one joint."""
        events: List[Dict[str, Any]] = []
        for etype, condition, severity, value_attr in _RULES:
            if condition(joint_ctx):
                value = getattr(joint_ctx, value_attr, 0.0)
                events.append(
                    {
                        "event_type": etype,
                        "joint_name": joint_ctx.joint_name,
                        "severity": severity,
                        "value": float(value),
                        "threshold": _THRESHOLDS.get(etype, 0.0),
                        "description": f"{etype} triggered on {joint_ctx.joint_name}",
                    }
                )
        return events

    def evaluate_all(
        self, joint_contexts: List[JointContext]
    ) -> List[Dict[str, Any]]:
        """Evaluate rules for every joint in the list."""
        all_events: List[Dict[str, Any]] = []
        for jc in joint_contexts:
            all_events.extend(self.evaluate(jc))
        return all_events
