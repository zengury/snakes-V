# Manastone 部署指南

> Unitree G1 自主运维系统 — snakes-V
> 138 tests passing | Python 3.10+ | Jetson Orin NX

---

## 硬件拓扑

```
开发机 (Mac)  ──Wi-Fi──►  G1 Jetson Orin NX (192.168.123.164)  ← 部署目标
                            G1 RockChip RK3588 (192.168.123.161)  ← 勿动，运动控制器
```

DDS domain ID 固定为 `0`，不可更改。

---

## 角色速查

| 我是… | 跳转 |
|-------|------|
| 机器人工程师 — 首次出厂调参 | [→ 出厂调参流程](#出厂调参流程机器人工程师) |
| 运维工程师 — 日常监控 / 闲时调优 | [→ 日常运维](#日常运维运维工程师) |
| AI/ML 工程师 — 训练预测模型 | [→ 预测模型飞轮](#预测模型飞轮aiml-工程师) |
| 平台工程师 — 多机器人知识迁移 | [→ 知识迁移](#知识迁移平台工程师) |
| 开发者 — 本地开发 / 跑测试 | [→ 本地开发](#本地开发开发者) |

---

## 环境准备（所有角色）

### 1. 克隆并安装

```bash
git clone https://github.com/zengury/snakes-V.git
cd snakes-V
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### 2. 环境变量

```bash
# 必须
export ANTHROPIC_API_KEY="sk-ant-..."       # LLM 调用（调参/分析用）

# 机器人连接（Orin NX 上运行时）
export ROSBRIDGE_URL="ws://localhost:9090"  # rosbridge WebSocket
export MANASTONE_SCHEMA_PATH="config/robot_schema.yaml"

# 可选
export MANASTONE_LLM_MODEL="claude-sonnet-4-20250514"
export MANASTONE_MAX_TOKENS="100000"        # 每次 session token 上限
export MANASTONE_STORAGE_DIR="storage"      # 数据存储目录

# 开发/测试模式（无需机器人）
export MANASTONE_MOCK_MODE=true
```

### 3. 验证安装

```bash
MANASTONE_MOCK_MODE=true python -m pytest tests/ -q
# 期望: 138 passed
```

---

## 出厂调参流程（机器人工程师）

机器人首次上线时，需要对全身 29 个关节的 PID 参数做出厂标定。

### 前提条件

- rosbridge 已在 Orin NX 上运行：`ros2 launch rosbridge_server rosbridge_websocket_launch.xml`
- 机器人处于安全站立姿态
- `ANTHROPIC_API_KEY` 已设置

### 步骤

**1. 确认硬件连接**

```bash
# 在 Orin NX 上
export ROSBRIDGE_URL="ws://localhost:9090"
export MANASTONE_SCHEMA_PATH="/path/to/config/robot_schema.yaml"
```

**2. 单链调参（推荐先单链验证）**

```python
import asyncio
from manastone.commissioning.chain_orchestrator import ChainTuningOrchestrator
from manastone.profiles.registry import ProfileRegistry
from manastone.common.config import ManaConfig

config = ManaConfig.get()
profile = ProfileRegistry().get("classic_precision")
orch = ChainTuningOrchestrator(config=config, profile=profile, robot_id="G1_001")

result = asyncio.run(orch.tune_chain("left_leg", target_score=80.0, max_experiments_per_joint=30))
print(f"链级得分: {result.chain_score:.1f}")
print(f"总实验次数: {result.total_experiments}")
```

**3. 全身调参（5 条链顺序执行）**

通过 Agent 命令：
```python
from manastone.agent.agent import ManastoneAgent
agent = ManastoneAgent(robot_id="G1_001")
result = asyncio.run(agent.command("全身调参"))
```

**4. 查看结果**

调参历史保存在：
```
storage/pid_workspace/G1_001/{joint_name}/
├── results.tsv        # 每次实验的评分记录
├── params.yaml        # 当前最优参数
└── program.md         # 调参上下文
```

Git 历史：
```bash
cd storage/pid_workspace/G1_001/left_knee
git log --oneline   # 每次实验一个 commit
git tag             # best_N 标签指向最优实验
```

**5. 选择调优 Profile**

| Profile | 适用场景 | 核心指标 |
|---------|----------|----------|
| `classic_precision` | 默认，精密操作 | 阶跃响应超调/稳态误差 |
| `rl_fidelity` | RL 步态训练 | 力矩跟踪误差 |
| `energy_saver` | 长时间待机 | 电流积分/温升 |
| `high_speed` | 快速搬运 | 响应速度 |
| `collision_safe` | 人机协作 | 接触力/柔顺性 |

```python
profile = ProfileRegistry().get("energy_saver")  # 切换 Profile
```

**安全中止条件（自动触发）**

- |torque| > 60 Nm → `status="safety_torque"`
- |velocity| > 20 rad/s → `status="safety_velocity"`
- 任何关节温度 > 65°C → `status="safety_thermal"`

---

## 日常运维（运维工程师）

机器人上线后，系统在空闲时自动优化参数。运维工程师主要通过 Agent 接口监控和干预。

### 启动系统

```bash
# Orin NX 上
cd snakes-V
source .venv/bin/activate
export ANTHROPIC_API_KEY="sk-ant-..."
export ROSBRIDGE_URL="ws://localhost:9090"

# 启动 Agent（唯一对外端口 :8090）
python -m manastone.agent.mcp_interface --host 0.0.0.0 --port 8090
```

### 通过 Agent 交互（4 个核心工具）

```python
import asyncio
from manastone.agent.agent import ManastoneAgent

agent = ManastoneAgent(robot_id="G1_001")

# 查询状态
status = asyncio.run(agent.status())
print(status["recent_events"])
print(status["token_usage"])

# 提问
answer = asyncio.run(agent.ask("最近调参结果如何？"))
print(answer)

# 下达指令
asyncio.run(agent.command("调参左腿"))
asyncio.run(agent.command("生成健康报告"))
asyncio.run(agent.command("暂停调优"))

# 教 Agent 知识（写入语义记忆）
asyncio.run(agent.teach("左膝关节在连续运行 4 小时后散热变差"))
```

### 闲时调优（自动）

系统检测到空闲（所有关节速度 < 0.02 rad/s 持续 30s）后自动触发：

```python
from manastone.idle_tuning.agent.loop import IdleTuningLoop
# ... 详见 src/manastone/idle_tuning/agent/loop.py

# 手动触发一次（调试用）
session = asyncio.run(idle_loop.run_once("G1_001"))
if session:
    print(f"调优链: {session.chain_name}, 结果: {session.outcome}")
    # session JSON 保存在 storage/sessions/G1_001/
```

**调优结果文件**：
```
storage/sessions/G1_001/
└── 20260328_143052_left_leg.json   # 每次调优一个文件
```

### 监控关键阈值

| 指标 | 警告 | 临界 |
|------|------|------|
| 关节温度 | > 50°C | > 65°C |
| 异常分 (anomaly_score) | > 0.3 | > 0.5 |
| 链级异常 | > 0.3 → 触发闲时调优 | > 0.5 → 强制 LLM 深路径 |

### 手动回滚参数

```python
from manastone.idle_tuning.executor.param_writer import MockParamWriter  # 或 RealParamWriter
from manastone.idle_tuning.collector.session_store import SessionStore

store = SessionStore(Path("storage/sessions"))
# 查询最近的 good session
good_params = asyncio.run(store.get_last_good_params("G1_001", "left_leg"))
# 写回
await param_writer.write_chain_params("left_leg", good_params)
```

---

## 预测模型飞轮（AI/ML 工程师）

系统内置 XGBoost 飞轮：积累 10 个 `improved` session 后自动训练，之后每 20 个 session 重训练。

### 模型架构

- **单关节模型** (`PIDPredictor`)：19 维特征 → 预测 Δkp/Δki/Δkd（3 个独立模型）
- **链级模型** (`ChainPredictor`)：60 维链特征 → 18 个模型（6 关节 × 3 参数）
- **运行时推理** (`RuntimePredictor`)：anomaly > 0.3 时给 ±5% 实时建议

### 特征维度（`predictor/features.py`）

```python
JOINT_FEATURE_COLS  # 19 维: temp_c, torque_nm, velocity_rad_s, kp/ki/kd, ...
CHAIN_JOINT_COLS    # 10 维 × N 关节 = chain feature vector
```

### 查看训练状态

```python
from manastone.idle_tuning.predictor.model import PIDPredictor
from pathlib import Path

predictor = PIDPredictor.load(Path("storage/predictors/G1_001/single_v1.json"))
print(f"已训练: {predictor.is_trained}")
print(f"置信度: {predictor.confidence:.2f}")
print(f"版本: {predictor.version}")
```

### 发布模型到 Model Zoo（跨机器人共享）

```python
from manastone.knowledge.model_zoo import ModelZoo

zoo = ModelZoo()
model_bytes = Path("storage/predictors/G1_001/single_v3.json").read_bytes()
zoo.publish(
    model_type="pid_predictor",
    model_data=model_bytes,
    source_robot="G1_001",
    source_profile="classic_precision",
    version="3.0",
    metadata={"samples": 150, "confidence": 0.82},
)

# 查询已发布模型
models = zoo.query("pid_predictor")
print(models)  # sorted by confidence desc
```

### XGBoost 训练参数调整

编辑 `src/manastone/idle_tuning/predictor/model.py`：

```python
XGB_PARAMS = {
    "max_depth": 4,      # 增大可提升拟合，但小数据集易过拟合
    "eta": 0.1,          # 学习率
    "min_child_weight": 3,
    "subsample": 0.8,
    "colsample_bytree": 0.8,
}
NUM_BOOST_ROUND = 50     # 训练轮数
EARLY_STOPPING = 10      # 早停
```

> 注：训练在 `ProcessPoolExecutor` 中异步执行，不阻塞调优主循环（A5）。

---

## 知识迁移（平台工程师）

管理多台机器人之间的参数继承和模板复用。

### 从已有机器人导出模板

```python
from manastone.knowledge.transfer import KnowledgeTransfer
from manastone.knowledge.template_library import TemplateLibrary
from manastone.common.models import PIDParams

transfer = KnowledgeTransfer()

# 导出 G1_001 的 classic_precision 参数为模板
best_params = {
    "left_knee":    PIDParams(kp=8.5, ki=0.12, kd=1.2),
    "left_hip_yaw": PIDParams(kp=5.0, ki=0.08, kd=0.8),
    # ... 其他关节
}
template_id = transfer.export_template(
    robot_id="G1_001",
    profile_id="classic_precision",
    params=best_params,
    environment={"task": "warehouse_picking", "terrain": "flat", "load_kg": 5},
    performance={"best_score": 87.0, "avg_score": 82.0, "sessions_count": 45},
)
print(f"模板 ID: {template_id}")
# 保存在 storage/knowledge_base/template_library/by_scenario/{template_id}.yaml
```

### 新机器人继承模板

```python
# strict: 直接使用，0 次实验
result = asyncio.run(transfer.inherit_template("G1_100", template_id, "strict"))

# adaptive: 继承后 ≤10 次适应性实验（推荐）
result = asyncio.run(transfer.inherit_template("G1_100", template_id, "adaptive"))

# zero_shot: 仅作初始猜测，完整标定 ≤50 次实验
result = asyncio.run(transfer.inherit_template("G1_100", template_id, "zero_shot"))

print(f"模式: {result['mode']}, 实验次数: {result['experiments']}")
```

### 查询相似环境模板

```python
from manastone.knowledge.template_library import TemplateLibrary

lib = TemplateLibrary()
similar = lib.query_similar({"task": "warehouse_picking", "terrain": "flat"})
for t in similar[:3]:
    print(f"{t['template_id']} — 相似度: {t['similarity']:.2f}, 得分: {t['performance']['best_score']}")
```

### 追踪参数血缘

```python
from manastone.knowledge.lineage import ParameterLineage

lineage = ParameterLineage()
trace = lineage.trace("G1_100")
for event in trace:
    print(f"[{event['timestamp'][:16]}] {event['type']}: {event}")
# 输出: inherited → tuned → tuned → exported → ...
```

### Per-Robot Per-Profile Git 仓库

```python
from manastone.lifecycle.lifecycle_repo import LifecycleRepository

repo = LifecycleRepository("G1_001")
repo.init()

# 创建 Profile 分支
workspace = repo.create_profile_branch("rl_fidelity")
# 分支名: G1_001/rl_fidelity

# 列出所有 Profile
profiles = repo.list_profiles()  # ["classic_precision", "rl_fidelity"]

# 打版本标签
repo.tag_version("classic_precision", "1.0", "stable")
# tag: classic_precision/v1.0-stable
```

### 运行时切换 Profile

```python
from manastone.lifecycle.switching import ProfileSwitchingStrategy

strategy = ProfileSwitchingStrategy()

# 检查是否需要切换
new_profile = asyncio.run(strategy.should_switch(
    robot_id="G1_001",
    current_profile="classic_precision",
    upcoming_context={
        "idle_duration_s": 400,      # > 300s → 建议切换到 energy_saver
        "recent_quality_score": 85,
    }
))
if new_profile:
    asyncio.run(strategy.execute_switch("G1_001", new_profile, reason="long_idle"))
```

---

## 本地开发（开发者）

### 纯 Mock 模式（无需机器人）

```bash
export MANASTONE_MOCK_MODE=true
export MANASTONE_SCHEMA_PATH=config/robot_schema.yaml
```

- 所有 DDS 数据由 `MockDDSBridge` 模拟（50Hz 仿真关节数据）
- PID 实验由 `MockExperimentRunner` + `MockJointSimulator` 执行（Euler 积分）
- 参数写回由 `MockParamWriter` 执行（内存存储）
- LLM 调用失败时自动降级到规则引擎 fallback

### 跑测试

```bash
# 全部测试
MANASTONE_MOCK_MODE=true python -m pytest tests/ -v

# 按模块
python -m pytest tests/test_safety.py -v
python -m pytest tests/test_commissioning.py -v
python -m pytest tests/test_idle_tuning.py -v
python -m pytest tests/test_agent.py -v
python -m pytest tests/test_knowledge.py -v
```

### 测试覆盖模块

| 测试文件 | 覆盖模块 | 测试数 |
|---------|---------|--------|
| `test_safety.py` | `common/safety.py` | 18 |
| `test_lifecycle.py` | `lifecycle/state_machine.py` | 13 |
| `test_dds_bridge.py` | `runtime/dds_bridge.py`, `ring_buffer.py` | 10 |
| `test_commissioning.py` | `commissioning/` | 13 |
| `test_profiles.py` | `profiles/` | 12 |
| `test_idle_tuning.py` | `idle_tuning/` | 10 |
| `test_agent.py` | `agent/` | 13 |
| `test_llm_proxy.py` | `agent/llm_proxy.py`, `token_budget.py` | 6 |
| `test_knowledge.py` | `knowledge/`, `lifecycle/stream.py` | 23 |
| **合计** | | **138** |

### 代码规范

```bash
ruff check src/          # lint
black src/               # format
mypy src/                # type check
```

`pyproject.toml` 配置：`ruff select = ["E","F","UP","B","I"]`，`mypy strict = true`，`pytest asyncio_mode = "auto"`。

### 新增 MCP Tool

在 `src/manastone/agent/mcp_interface.py` 添加：

```python
async def tool_my_new_tool(param: str) -> dict:
    """工具描述"""
    return await agent.my_method(param)
```

### 新增调优 Profile

在 `src/manastone/profiles/builtin/` 创建 YAML：

```yaml
profile_id: my_profile
version: "1.0"
description: "自定义场景"
compatible_joint_groups: [leg]
compatible_tasks: [my_task]
llm_prompt: |
  ... LLM 提示词模板（支持 {joint_name}, {kp_min}, {kp_max} 等变量）
scorer:
  class: manastone.profiles.scorers.step_response.StepResponseScorer
  params: {}
experiment_generator:
  class: manastone.profiles.generators.step.StepGenerator
  params: {setpoint: 0.3, duration_s: 2.0, sample_rate_hz: 100.0}
safety:
  max_param_change_pct: 0.15
features: [temp_c, torque_nm, anomaly_score]
```

热加载（无需重启）：

```python
from manastone.profiles.registry import ProfileRegistry
registry = ProfileRegistry()  # 重新实例化即可
profile = registry.get("my_profile")
```

---

## 存储目录结构

```
storage/
├── pid_workspace/{robot_id}/{joint_name}/    # 出厂调参 Git 工作区
│   ├── .git/
│   ├── params.yaml
│   ├── results.tsv
│   └── program.md
├── sessions/{robot_id}/                      # 闲时调优 Session JSON
│   └── 20260328_143052_left_leg.json
├── predictors/{robot_id}/                    # XGBoost 模型
│   ├── single_v1.json
│   └── chain_left_leg_v1.json
├── agent_memory/{robot_id}/                  # Agent 三层记忆
│   └── memory.json
├── lifecycle/{robot_id}/                     # 生命周期事件流
│   └── stream.jsonl
├── workspaces/{robot_id}/                    # Per-robot Per-profile Git 仓库
│   └── .git/  (branches: {robot_id}/{profile_id})
└── knowledge_base/                           # 跨机器人知识库
    ├── model_zoo/pid_predictor/
    ├── template_library/by_scenario/
    └── metadata/lineage.jsonl
```

---

## 端口说明

| 端口 | 绑定 | 服务 |
|------|------|------|
| `:8090` | `0.0.0.0` | Agent Gateway（唯一对外端口） |
| `:8080` | `127.0.0.1` | Core MCP Server |
| `:8081` | `127.0.0.1` | Joints MCP Server |
| `:8082` | `127.0.0.1` | Power MCP Server |
| `:8083` | `127.0.0.1` | IMU MCP Server |
| `:8087` | `127.0.0.1` | PID Tuner MCP Server |
| `:8088` | `127.0.0.1` | Idle Tuner MCP Server |
| `:9090` | `localhost` | rosbridge WebSocket (ROS2) |

Claude Desktop 配置（工程师本机）：

```json
{
  "mcpServers": {
    "manastone": {
      "url": "http://192.168.123.164:8090/mcp/sse"
    }
  }
}
```

---

## 常见问题

**Q: 调参时 LLM 报错怎么办？**
系统自动降级到规则引擎 fallback（DD-C05），调参继续运行，`status="llm_error"` 记录到 `results.tsv`。

**Q: Token 预算用完怎么办？**
`LLMBudgetExceededError` 被捕获后，commissioning 使用 Optuna BO 纯数值搜索（不调 LLM），idle_tuning 使用保守规则（不降增益）。预算每日 UTC 00:00 重置。

**Q: 没有 git 命令怎么办？**
`PIDWorkspace` 和 `LifecycleRepository` 检测到无 git 后自动降级：用 `params_history.json` 替代 Git 历史，功能正常，只是失去 git 追溯能力。

**Q: Mock 模式下 LLM 结果准确吗？**
Mock 模式的 `MockJointSimulator` 使用 Euler 积分二阶物理模型，LLM 调参结果会触发规则引擎 fallback（无 API key 时）。Mock 模式用于验证流程完整性，不用于评估 LLM 调参质量。

**Q: 如何添加新关节或新机器人型号？**
编辑 `config/robot_schema.yaml`：
```yaml
robot:
  kinematic_chains:
    left_leg: [left_hip_yaw, left_hip_roll, ...]
  chain_tuning_order:
    left_leg: [left_hip_yaw, left_hip_roll, ...]   # 必须从根到末端
  mock_physics:
    overrides:
      my_new_joint: {inertia: 0.20, friction: 1.0}
```

---

*生成于 2026-03-28 | snakes-V @ github.com/zengury/snakes-V*
