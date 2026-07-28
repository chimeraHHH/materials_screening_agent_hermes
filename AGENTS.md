# AGENTS.md

本文件约束本仓库中的 Codex 与 subagent 开发协作。仓库现状、源码和测试优先于
计划文档中的建议结构；不得把尚未实现的 roadmap 当成现有能力。

## 文档职责

- [README.md](README.md)：当前可运行能力、环境、CLI、测试、Artifact 与已知限制。
- [系统蓝图](docs/system-plan.md)：长期产品与科学边界、证据等级、安全 Gate、LLM、
  迁移和风险原则。
- [技术架构](docs/architecture.md)：控制/执行/数据平面、状态机、模块边界、核心契约、
  Artifact/provenance 与后端集成设计。
- [主计划](plans/master.md)：当前里程碑、任务状态、全局优先级、依赖、阻塞项、验收
  和下一步，是项目管理真源。
- [原始总方案](docs/system-plan-original.md)：拆分前的历史方案，仅用于追溯；当前职责
  以上述三份文档为准。
- [Orchestrator Plan](plans/subagents/material-screening-orchestrator-plan.md)：控制平面、状态机、持久化
  与 runner 契约。
- [Agent01 Plan](plans/subagents/material-screening-agent01-plan.md)：Materials Project 检索、确定性筛选、
  结构处理和发布契约。
- [Agent02 Plan](plans/subagents/material-screening-ml-agent-plan.md)：ML 原生契约、适用域、worker 边界与
  后续真实 ML 路线。
- [Agent03 Plan](plans/subagents/material-screening-agent-dft-plan.md)：DFT Controller 的目标设计；当前
  没有对应源码。
- [Agent04 Plan](plans/subagents/material-screening-agent04-plan.md)：多体 Controller 的目标设计；当前
  没有对应源码。

各 subagent 的详细计划统一位于 `plans/subagents/`。不得虚构尚不存在的标准化 plan
路径。文档描述与实现冲突时，以 `src/`、配置和测试为准，并
在相应文档记录差异。

## 模块归属

| 路径 | 代码归属 |
|---|---|
| `src/material_agent/cli.py` | CLI 参数和用户入口 |
| `src/material_agent/orchestrator/` | LangGraph 控制流、控制契约、SQLite/checkpoint、审批、恢复和 runner registry |
| `src/material_agent/retrieval/` | Agent01：数据源、查询、结构、确定性判定、排序、Artifact 和报告 |
| `src/material_agent/ml_screening/` | Agent02：轻量原生契约、pre-filter、适用域、计划、数值/worker 校验和 Fake 实现 |
| `src/material_agent/dft/` | Agent03：v1 mock DFT 控制契约、计划、backend 生命周期、runner 和非科研报告 |
| `src/material_agent/many_body/` | Agent04：MVP 模型契约、验证、路由、mock backend、runner 和 evidence ceiling |
| `scripts/`、`tests/fixtures/contracts/` | 冻结契约 fixture 的生成与参考输出 |
| `tests/{unit,contract,integration,e2e,live}/` | 相应层级的验证 |

Agent01 是当前唯一已注册的生产科学 runner。Agent02 P0.2 Fake Adapter、Agent03 v1
mock 控制链和 Agent04 MVP mock 控制链已有源码，但 Agent02 尚无真实 CHGNet worker，
Agent03/04 尚无真实科学 backend。Agent02–04 默认 production capability 均未注册；
测试用 `FixtureStageRunner`、Fake Adapter/Worker 和 mock backend 不得注册或描述为
生产科学能力。

修改应留在负责模块及对应测试内。跨模块修改遵守以下规则：

1. Agent01 拥有检索与权威确定性筛选；Agent02 可保守复核输入，但不得翻转上游
   `REJECT` 或重写 Agent01 记录。
2. Orchestrator 控制契约与各 Agent 原生契约保持分离。公共 Schema、状态、Artifact
   引用或 runner 生命周期变化，必须同时检查 adapter、冻结 fixture、contract test
   和相关 plan，不能单边修改。
3. `ml_screening` 的默认导入保持轻量，不得把 Torch、CHGNet 或 ASE 加入主环境；
   真实 worker 依照 Agent02 Plan 使用独立环境和 JSON 边界。
4. 新增 Agent03/04 实现前，先以对应 plan 和现有 `StageRunner` 契约确认边界；不要
   预建计划中的空目录或伪结果。

## 工作流程

开发前：

1. 阅读 README、系统蓝图、技术架构、主计划、负责模块的 plan、现有实现和相关测试。
2. 运行 `git status --short`，确认工作树与其他 agent 的改动；不得覆盖无关修改。
3. 在负责模块的 plan 中记录本任务范围、依赖和验收标准。计划描述与源码冲突时先
   记录差异，不按计划臆造接口。
4. 确认是否涉及公共契约、外部服务、密钥、真实科学计算或人工审批。

开发中：

- 优先保持纯计算与 I/O、外部调用分离；所有状态、Artifact 和 operation 继续使用
  明确版本、URI、SHA-256 与幂等键。
- 缺输入、未知值、域外数据和不一致状态必须显式阻塞、失败或标记不确定，不得补造。
- 不静默改变科学阈值、模型、泛函、U、磁序、求解器、单位或证据等级。
- 先运行最相关的测试；跨契约改动同时增加/更新 unit、contract 和 integration/E2E
  覆盖。

完成后：

1. 运行相关测试，再运行完整离线 Gate、`pip check` 和 `git diff --check`。
2. 更新负责模块的 plan，记录实际状态、测试证据、限制和未完成项。
3. 检查 diff 只包含任务范围，没有密钥、真实运行产物、缓存或意外生成文件。
4. 报告未运行的 Gate、剩余风险和跨 agent 后续依赖。

## Plan 更新协议

- 每个 subagent 只维护自己的 plan：Orchestrator、Agent01、Agent02、Agent03、Agent04
  分别对应上文链接。
- 日常实现细节、局部决策、测试结果和本 agent 待办只更新自己的 plan。
- 跨 agent 依赖、全局优先级、里程碑、阻塞项或系统级验收变化更新
  `plans/master.md`。
- 长期产品/科学边界、公共证据或安全政策变化更新 `docs/system-plan.md`；控制契约、
  状态真源、模块边界或系统架构变化更新 `docs/architecture.md`。
- 不要把每日任务清单写入系统蓝图、技术架构或本文件；不要把长期设计细节复制到
  主计划。
- 公共契约变更同时在生产者 plan 和消费者 plan 中记录影响；未协调前保持向后兼容
  或停止变更。

## 现有命令

项目要求 Python `>=3.11,<3.12`，使用仓库本地 `.venv`。安装命令见 README：

```bash
.venv/bin/pip install -r requirements.lock
.venv/bin/pip install --no-deps -e .
```

完整离线 Gate：

```bash
PYTHONDONTWRITEBYTECODE=1 \
MPLCONFIGDIR=/tmp/material-agent-mpl \
.venv/bin/python -m pytest -q -p no:cacheprovider
```

依赖与 diff 检查：

```bash
.venv/bin/python -m pip check
git diff --check
```

CLI 运行示例以 README 为准。真实 Materials Project 测试仅在明确批准、网络可用且
通过安全 secret store 注入 `MP_API_KEY` 时运行：

```bash
.venv/bin/python -m pytest tests/live -m live_mp --run-live-mp
```

仓库当前未配置 lint、自动格式化或静态类型检查工具，也没有 CI 配置；不要虚构相应
命令。新增此类工具属于依赖/配置变更，必须单独明确范围。

## 科学严谨性与安全

- 使用 `L0_PARSED` 至 `L5_EXPERT_REVIEWED` 证据等级，且逐性质/claim 保留来源、
  单位、方法、版本、时间与限制。L1/L2 不得表述为 L3/L4。
- Materials Project 数值是数据库计算记录，不是实验值。MLIP 能量、收敛或磁矩不能
  表述为 formation energy、凸包稳定性、磁基态、拓扑、Mott 或 DFT 证明。
- mock/fixture 必须显式 `is_mock=true`；Fake Agent02 结果保持 `L1_RETRIEVED`。
  DFT/多体 mock 不得产生或暗示科研数值、observable 或证据晋级。
- 保留 `PASS/REJECT/UNCERTAIN/FAILED`、适用域、质量 warning 和 provenance 缺口；
  不为了得到候选而放宽条件或隐藏失败。
- Requirement 确认、昂贵批量/DFT/多体任务、参数扫描、生成代码执行和高级方法遵守
  系统蓝图的人工 Gate。批准必须绑定冻结的 plan、输入、policy 和 hash；输入变化
  后旧批准失效。
- `MP_API_KEY` 只从环境变量或 secret store 读取，禁止写入源码、配置、日志、
  fixture、报告或 Artifact。默认测试不得访问网络。
- Artifact 路径必须限制在项目 root，拒绝路径穿越、symlink 逃逸、pickle、任意代码
  执行和未校验外部输出。hash、状态或 backend 身份不一致时 fail closed。

## Git 与生成文件

- 遵守 `.gitignore`：不提交 `.venv/`、`workspace/`、`.env`、除
  `.env.example` 外的 `.env.*`、缓存、coverage、build/dist 或本地运行产物。
- 冻结 Agent01/Agent02 契约 fixture 只用 `scripts/generate_agent01_contract_fixture.py`
  或 `scripts/generate_agent02_contract_fixture.py` 重建；检查生成 diff，并运行对应
  frozen contract test 验证逐字节确定性和 hash。
- `pyproject.toml` 与 `requirements.lock` 仅在明确的依赖任务中修改，并验证 Python
  3.11 安装与 `pip check`。仓库没有额外的正式分支或提交消息规范，不要自行宣称。
