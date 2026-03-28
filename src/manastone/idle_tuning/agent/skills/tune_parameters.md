---
name: tune_parameters
version: "1.0"
input_schema:
  - chain_context: ChainContext
  - xgb_prior: Optional[Dict]
  - confidence: float
output_format: yaml
timeout_s: 30
---

# Idle-Time PID Tuning Skill

You are adjusting PID parameters for a Unitree G1 robot kinematic chain during idle time.
The robot has been running and you have real operational data showing current performance.

## Context Provided
- Chain name and all joint contexts (temperature, torque, tracking error, anomaly score)
- Current PID parameters for each joint
- XGBoost model suggestion (if available) with confidence score
- Recent quality trend (last 5 sessions)

## Rules
1. Output a YAML block with ALL joints' updated PID params
2. Maximum change per parameter: ±15% of current value
3. If anomaly_score > 0.7 for any joint: only DECREASE gains (conservative)
4. If XGBoost prior is provided with confidence > 0.6, bias toward it
5. Include reasoning as a YAML comment

## Output Format
```yaml
# reasoning: {your analysis}
joints:
  {joint_name}:
    kp: {value}
    ki: {value}
    kd: {value}
```
