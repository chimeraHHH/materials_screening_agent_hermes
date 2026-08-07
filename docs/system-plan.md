# 强关联材料高通量筛选 Agent：系统蓝图

> **职责：** 本文档定义系统的长期产品与科学蓝图，以及相对稳定的范围、证据、安全、LLM、迁移和风险原则；当前任务、完成状态和按日安排统一在主计划中维护。
>
> **文档导航：** [技术架构](./architecture.md) · [主计划](../plans/master.md) · [原始总方案](./system-plan-original.md)
>
> **来源说明：** 本文由仓库原始总方案
> [`system-plan-original.md`](./system-plan-original.md) 拆分整理，并结合仓库 README
> 与现有分阶段计划澄清“当前实现”和“未来计划”的边界。若本文与 README、源码、配置或测试冲突，以后者为准；原始总方案保留不变。

版本：v0.1（拆分整理版）

原始方案日期：2026-07-24

长期方向：本地、可审计的材料筛选链路，逐步迁移到课题组服务器与真实科学后端

## 1. 执行结论

本项目的长期目标，是让科研人员用自然语言描述目标材料，由系统完成需求澄清、公开数据库检索、机器学习筛选、DFT 验证、多体数值分析，并返回晶体结构、性质、证据与完整计算溯源。

首个系统版本不追求“完整自动发现强关联材料”，而交付一条真实可运行、可恢复、可审计的纵向链路：

> 自然语言需求 → 结构化需求 → 人工确认 → Materials Project 检索 → 结构规范化与筛选 → 候选排序 → 证据报告

机器学习阶段采用“真实基础能力 + 可替换模型接口”；DFT 和多体阶段先建立严格输入校验、审批、状态机、Adapter 与不产生伪科研数值的可信测试后端，之后才接真实计算能力。

系统必须遵守以下总原则：

- 数据库代理指标、ML 预测、fixture 或 mock 结果不得表述为已经经过 DFT 或多体计算确认的科研结论。
- 科学阈值、方法参数和证据晋级不得由 LLM 静默决定。
- 任一昂贵或高风险步骤必须基于不可变输入快照审批。
- 失败、缺失输入、不适用、无匹配结果和科学上不满足条件必须分别表达。
- 每个外部任务只能有一个 execution backend 作为状态真源。
- 用户和课题组专家始终保留关键科学定义、方法与最终结论的审核权。

### 1.1 已冻结的产品级决策

| 事项 | 决定 |
|---|---|
| 初始运行环境 | macOS，本地 CLI/API，单用户、单项目串行推进 |
| 后续环境 | Linux 服务器或课题组集群 |
| 首个数据源 | Materials Project |
| 结构生成 | 已冻结的系统 v1 不生成新结构；P3 灵感生成器只允许白名单确定性 operator 产生 proposal，且不构成性质证据或 novelty 结论 |
| Agent 平台 | P3 引入独立进程 Hermes，统一 Skill/Tool/Provider 入口；科学状态继续由本系统独占 |
| 元数据与 Artifact | 小型状态持久化；科学大文件独立保存并通过 URI/hash 引用 |
| DFT 后端 | `DFTBackend` Adapter；先验证控制链，未来评估 VASPilot 等真实后端 |
| 多体后端 | `ManyBodyBackend` Adapter；先验证控制链，未来从窄范围真实 solver 开始 |
| 人工审批 | 需求确认、批量/昂贵计算、生成代码执行、高级方法 |
| LLM | Provider 可替换；无 API key 时必须可以离线演示 |

具体实现契约见[技术架构](./architecture.md)，这些能力当前是否可用见[主计划](../plans/master.md)。

## 2. 产品目标与边界

### 2.1 目标用户

- 凝聚态物理、材料计算或材料实验科研人员；
- 希望从公开数据库中寻找满足某类物性要求的候选材料；
- 能够审核筛选定义、DFT 参数、有效模型和最终科学结论的用户或课题组专家。

### 2.2 核心用户故事

用户可以提出类似需求：

> 我想找拓扑平带材料，必须包含某种元素，不能包含某些元素，并优先选择比较稳定的结构。

系统应当：

1. 提取明确的硬约束和软偏好；
2. 识别“拓扑”“平带”“稳定”等尚未操作化的概念；
3. 展示拟采用的筛选定义、证据等级和计算预算；
4. 要求用户确认并冻结需求版本；
5. 查询数据库并保留原始来源；
6. 规范化、去重、筛选和排序候选；
7. 根据证据缺口建议或路由 ML、DFT、多体阶段；
8. 对昂贵或高级步骤请求人工批准；
9. 返回候选、结构、性质、不确定性说明、来源和完整 provenance。

### 2.3 v1 边界

v1 聚焦于可审计的纵向能力，而不是全自动科研：

- 真实完成需求确认、Materials Project 检索、确定性筛选、结构保存、候选报告和恢复；
- 建立机器学习阶段的确定性适用域、模型注册、结果契约与可替换执行边界，并以受控小批量验证真实基础能力；
- 为 DFT 和多体阶段建立输入、计划、审批、状态、Adapter、恢复与证据防误晋级能力；
- 允许从任意阶段以显式、带 hash 的输入启动；缺输入时阻塞并说明补充方式；
- 保证 mock/fixture 只验证控制流，不产生或提升真实科学证据；
- 使用固定、简单且可查询的半导体用例作为工程验收入口，不能把其数据库带隙解释为实验带隙。

### 2.4 非目标

- 已冻结的系统 v1 不生成掺杂、元素替换、异质结、超晶胞或新晶体结构；P3 只在独立
  Inspiration companion 中开放经版本化 policy 允许的确定性 proposal operator；
- 不保证所有强关联目标都在 v1 达到真实 DFT 或多体验证；
- 不在初始 Mac 环境直接运行 VASPilot/VASP；
- 不支持无人监管的高通量昂贵计算；
- 不允许 Agent 自行执行未经审核的生成代码；
- 不建设课题组多用户 Web 平台；
- 不承诺“绝不答错”，而是显式表达不确定性、失败和证据等级；
- 不允许通过更换方法、阈值、模型或求解器来静默掩盖失败。

### 2.5 P3：Hermes 与灵感生成器范围

P3 将系统从“只筛选数据库已有候选”扩展为“先形成可审计灵感，再进入已有筛选和验证链”。
范围固定为：

1. Hermes 作为进程外 Agent 平台，统一版本化 Skill、Tool、Provider、subagent 和用户交互；
2. 材料引擎通过窄 Gateway 接受 Hermes 请求，继续独占 Requirement、审批、科学状态、
   Artifact 和 backend；
3. 灵感生成器执行 metadata-first 文献搜索、局部 Passage、EvidenceCard、TagGraph、
   跨领域 BridgePacket、白名单结构变换、内部去重和多样性 Top-K；
4. 默认不下载 PDF 全文，不把整页交给 embedding/LLM；每个证据和 proposal 都有来源、
   locator、hash、版本、预算和限制；
5. 本阶段明确不实现 novelty、prior-art 或专利判断，不输出“新材料”结论；
6. proposal 固定 `scientific_conclusion=false`，必须经过后续 ML/DFT/实验或专家验证。

详细实现和退出门槛见
[Hermes 与灵感生成器计划](../plans/subagents/material-screening-inspiration-plan.md)。

## 3. 科学目标与证据成熟度

### 3.1 支持的目标类别

首版 `target_class` 定义为：

- `simple_semiconductor`
- `fm_2d_semiconductor`
- `topological_flat_band`
- `mott_candidate`
- `charge_transfer_bilayer`
- `custom`

强关联目标能够被解析、表示和路由，不等于当前系统已经能对它们作真实验证。无法由当前阶段支持的目标必须形成明确的证据缺口。

### 3.2 统一证据等级

| 等级 | 含义 | 首版定位 |
|---|---|---|
| `L0_PARSED` | 自然语言需求已结构化并经用户确认 | 必须支持 |
| `L1_RETRIEVED` | 数据库记录或代理性质满足条件 | 必须真实支持 |
| `L2_ML_SCREENED` | 经过模型适用域检查的真实 ML 结果 | 受控、分阶段启用 |
| `L3_DFT_VALIDATED` | 真实 DFT 收敛且通过独立结果检查 | 先冻结契约；真实后端为未来 |
| `L4_MANY_BODY_VALIDATED` | 有效模型与真实多体计算通过验证 | 先冻结契约；真实 solver 为未来 |
| `L5_EXPERT_REVIEWED` | 科研人员完成最终审查 | 流程支持 |

所有候选和性质必须携带证据等级。候选的最高等级不能覆盖性质级证据差异；例如，一个候选拥有有效的 L2 结构预弛豫结果，并不意味着其数据库带隙、拓扑性质或磁基态也达到 L2。

### 3.3 科学严谨性约束

- Materials Project 数值必须保留单位、数据库版本、来源任务和计算层级；不得称为实验真值。
- ML 势能或弛豫结果不得称为 formation energy、energy above hull、热力学稳定性证明或 DFT 结果。
- backend 或调度器的“成功结束”不等于计算数值有效，也不自动支持某个 scientific claim。
- 单次 DFT+U 开隙不构成 Mott 物理证明；SOC 能带本身不构成拓扑不变量验证。
- 多体 solver 的数值可信度和有效模型与材料之间的映射可信度必须分别记录。
- fixture、mock、来源不完整的模型或未经专家批准的材料映射不得晋级真实 L2/L3/L4。
- 检索零结果是正常的科学结果，不是工具失败；结果不完整、无匹配和输入缺失必须有不同状态。

### 3.4 固定纵向验收场景

首个工程验收场景为：

> 从 Materials Project 中寻找同时包含 Si 和 O、带隙为 0.5–1.0 eV、energy above hull 不超过 0.05 eV/atom 的非金属材料。

该场景验证需求解析、确认、查询构造、单位、结构获取、候选排序、provenance、报告和中断恢复，不用于证明数据库计算值等同于实验值。当前验收进度和指标见[主计划](../plans/master.md)。

## 4. 人工审批与安全原则

### 4.1 强制 Gate

1. `REQUIREMENT_CONFIRMATION`
   - 用户确认机器可读需求、操作化定义和默认阈值。
2. `EXPENSIVE_BATCH_APPROVAL`
   - 任何真实 DFT/多体任务；
   - 超过候选数或预计成本阈值的 ML 批次；
   - 参数扫描。
3. `GENERATED_CODE_EXECUTION`
   - 未来任何自动生成代码的执行。
4. `ADVANCED_METHOD_APPROVAL`
   - HSE、cRPA、DMFT、DMRG、ED、RPA 等高级方法。

具体阶段可以增加更窄的专家 Gate，但不能取消上述强制下限。

### 4.2 审批不变量

- 审批必须绑定计划、输入快照、资源估算、policy 和全部相关 hash。
- 输入、方法、批次或关键参数改变后，旧审批立即失效。
- 审批拒绝不得删除已有结果；可选阶段应停止并形成可审计的部分报告。
- 审批本身不使域外模型、无效输入或不适用方法变得可执行。
- 所有审批决定、操作者、时间和理由都必须不可变留痕。

### 4.3 默认安全规则

- API key 只从环境变量、macOS Keychain 或服务器秘密存储读取；
- 配置快照必须脱敏，密钥不得进入源码、prompt、日志或项目 Artifact；
- 所有外部写操作必须有 tool-call 或 operation 审计；
- 路径必须限制在项目 workspace，拒绝绝对路径、路径穿越与 symlink 逃逸；
- 不执行任意 Python、shell 或 pickle；
- 不直接对公网开放本地服务；
- 失败时不得静默更换模型、泛函、U、磁序、求解器或收敛标准；
- 关键参数改变必须生成 before/after diff，并按需重新审批；
- VASP/POTCAR 的许可材料不得写入通用 Artifact Store、Git、prompt 或报告；
- 自动生成代码在 v1 生产图中禁用；未来若启用，必须使用隔离沙箱、diff、测试、风险摘要、hash 和人工 Gate。

## 5. LLM 策略

LLM 通过统一、可替换的 Provider 边界使用：

```python
class LLMProvider(Protocol):
    def structured_generate(self, prompt, schema, context) -> BaseModel: ...
```

候选实现可以是 OpenAI-compatible、DeepSeek-compatible 或其他可靠支持结构化输出的服务，也可以是 `MockLLMProvider` 与 `OfflineDemoParser`。

长期规则：

- 没有 API key 时，固定验收任务和自动测试仍必须可运行；
- LLM 只帮助解析、澄清、解释和摘要；
- LLM 不决定阶段路由、模型选择、科学阈值、预算、证据晋级或数值收敛；
- LLM 输出必须经过 Schema、版本化 policy 和人工 Gate，不能直接进入 backend request；
- 必须记录 provider、model ID、参数、prompt version 和响应 hash；
- 不允许在失败后静默切换 Provider 或改变科学含义。

P3 Inspiration 是上述规则的受控扩展：LLM 可以在严格 Schema 下提出查询词、
`BridgePacket` 和 registry 中的 transformation 选择，但不能直接修改 curated TagGraph、
生成坐标、执行代码、决定结构有效性或产生科学结论。每个 Bridge 必须包含共享 invariant、
成立条件和失效条件，并由真实检索反馈与本地 validator 约束。

系统可提供独立的只读研究顾问 companion flow：它只能从校验过 hash 的报告提取已存在的
证据缺口，并生成由本地 policy 固定的“审阅/配置前提条件/专家复核”提议。LLM 若启用，
只能解释该冻结快照和既有提议，不能新增动作、修改 Requirement、路由、预算、审批、模型
选择或证据等级，更不能直接启动 ML、DFT 或多体任务。该输出不是科学结论。

当前联网 Provider 的接入状态见[主计划](../plans/master.md)。

## 6. Mac 到课题组服务器的迁移路线

### 6.1 本地阶段

- 单用户；
- SQLite；
- 本地 Artifact Store；
- localhost CLI/API；
- 单项目串行推进；
- 环境变量或 Keychain 管理凭据；
- DFT/多体使用不产生科研数值的控制链测试后端。

### 6.2 服务器阶段

计划增加：

- PostgreSQL 与服务器级 checkpoint；
- 共享文件系统或对象存储；
- 后台任务队列；
- 用户登录、RBAC、项目隔离和资源配额；
- API Token、TLS 和专用低权限服务账号；
- Slurm Adapter；
- 备份、迁移、监控、告警和审计日志；
- 模型/API 成本统计；
- 向外部 LLM 发送数据的合规策略。

Global State 必须使用逻辑 Artifact URI，而不是依赖 Mac 绝对路径，以降低迁移成本。迁移不能改变已经冻结运行的输入、hash、证据或审批历史。

## 7. 长期路线图

### P0.3：Hermes 平台与灵感生成器

- 固定 Hermes release/commit，建立独立 profile、Skill 和进程外 Gateway；
- 冻结 SearchHit、Passage、EvidenceCard、TagGraph、BridgePacket 和 proposal 契约；
- 实现 metadata-first 搜索、局部向量化、白名单结构变换、内部去重和多样性 Top-K；
- 分别验证真实公共文献搜索/真实 parent structure 纵切，以及 Hermes/MCP/审批/持久化纵切；
- 最终以一次自然语言 Hermes turn 驱动完整固定流程，并明确是否包含公共搜索；
- 首批结果只作为可审计 proposal，不进行 novelty 或性质背书。

截至 2026-08-08，单用户本机 pilot 已通过两个分离 Gate：公共 Crossref metadata live
runner 验证真实搜索/真实 `pymatgen` 结构路径；fixture-backed 四工具 MCP run 验证 Hermes
profile、Gateway、operator 审批、持久化和非空 bundle。固定 Hermes 为 `v2026.8.3`
（package `0.20.0`）。自然语言 Hermes Agent turn 仍需 Provider 设备授权，也尚未声称一个
Hermes turn 同时使用公共搜索；当前证据与限制见[首个 pilot 运行记录](./runs/2026-08-08-hermes-inspiration-pilot.md)。

### P1：可靠 ML 筛选

- 选择并冻结一个真实 MLIP；
- 建立适用域、版本化模型注册表和健康快照；
- 受控小批量弛豫；
- 建立 phase stability、uncertainty 和科学 silver set；
- 先用 benchmark 证明可靠边界，再增加性质模型。

### P2：单体系真实 DFT 后端

- 取得合法 VASP、POTCAR 与 Slurm 环境；
- 冻结课题组方法 policy；
- 部署隔离、认证、幂等的结构化 backend bridge；
- 以一个非 SOC 小体系完成 relaxation → SCF → band/DOS；
- 对全部输入输出执行人工审核和独立结果验证。

### P3：固定 DFT 工作流

- relaxation → SCF → band/DOS；
- 磁序枚举；
- 经审批的 SOC；
- HSE、phonon、Wannier 等按资源与 benchmark 逐项加入；
- 确定性错误分类和 remediation proposal 优先于 LLM 修复。

### P4：多体求解

- 先冻结有效模型 Schema、模型—材料 linkage 和证据范围；
- 针对一种物理目标选择一种窄范围真实 solver；
- 建立小模型解析 benchmark、收敛和交叉验证；
- 再评估 ED、DMFT、DMRG 等扩展。

### P5：多人平台

- Web 或其他交互界面；
- 多用户权限、任务队列和配额；
- 课题组 gold set；
- 生产运维与合规。

## 8. 主要风险

| 风险 | 影响 | 缓解 |
|---|---|---|
| 初始范围过大 | 主链路无法稳定交付 | 优先冻结可恢复 P0，后续能力按独立 Gate 启用 |
| LLM API key 或服务不可用 | 自然语言链路阻塞 | Offline Parser + Mock Provider |
| 数据库计算值被当作实验真值 | 科学误导 | evidence、method、origin、unit、uncertainty |
| 强关联目标定义不明确 | 路由与结论漂移 | 版本化 policy + 用户/专家确认 |
| ML 模型不支持材料域 | 错误预测或伪置信度 | model registry + applicability Gate + benchmark |
| VASPilot 依赖 Slurm/VASP/POTCAR | 本地无法实算 | Adapter + 安全 Gate；服务器阶段再接 |
| 自动生成代码 | 安全与科研错误 | v1 禁用；未来沙箱 + 审批 |
| 双重编排 | 状态冲突、重复提交 | 每个任务唯一 execution backend 真源 |
| 缺少专家 gold set | 无法衡量科学准确率 | 先测工程正确性，再维护 silver/gold set |
| 多体模型不完整 | 求解结果无物理意义 | 强制 EffectiveModelPackage、linkage 与专家审批 |
| mock 或 fixture 污染证据 | 伪科研结果 | `is_mock` 不变量、空科学数值、证据上限测试 |
| 参数静默变化 | 结果不可复现、不可比 | 不可变快照、hash、diff、新审批 |
| Hermes 与科学图双重编排 | 状态冲突、重复副作用 | Hermes 只调用粗粒度 Gateway；科学事务仍由现有 Runtime 独占 |
| 开放网页 prompt injection | Agent 被页面文字诱导调用高风险工具 | 页面视为不可信数据、工具 allowlist、Passage 抽取、容器隔离和硬预算 |
| 跨领域类比空泛 | tag 很新奇但无法转成可验证材料假设 | 强制 shared invariant、成立/失效条件、EvidenceCard 和最便宜证伪测试 |
| 生成 proposal 被误称为新材料 | 科学和知识产权误导 | 不做 novelty；输出显式免责声明；proposal 与性质证据分离 |

项目当前的阻塞项、负责人待定事项和下一步见[主计划](../plans/master.md)。

## 9. 资料与方法依据

### 9.1 仓库内依据

- [原始系统总方案](./system-plan-original.md)
- [Orchestrator 详细计划](../plans/subagents/material-screening-orchestrator-plan.md)
- [Agent 01 详细计划](../plans/subagents/material-screening-agent01-plan.md)
- [Agent 02 详细计划](../plans/subagents/material-screening-ml-agent-plan.md)
- [Agent 03 DFT 详细计划](../plans/subagents/material-screening-agent-dft-plan.md)
- [Agent 04 多体详细计划](../plans/subagents/material-screening-agent04-plan.md)
- [仓库 README](../README.md)

原始方案还基于用户提供的阶段目录设计、组会架构材料、高通量筛选与多体方法资料、VASPilot 论文及仓库调研材料；这些资料若未存入当前仓库，应视为外部来源，而不是当前仓库内可验证事实。

### 9.2 外部方法资料

- [Materials Project API 查询](https://docs.materialsproject.org/downloading-data/using-the-api/querying-data)
- [Materials Project 电子结构方法](https://docs.materialsproject.org/methodology/electronic-structure)
- [Materials Project 磁性方法](https://docs.materialsproject.org/methodology/materials-methodology/magnetic-properties)
- [VASPilot](https://github.com/JiaxuanLiu-Arsko/VASPilot)

外部依赖、接口和上游项目都可能变化；真实集成前必须固定版本或 commit，并重新执行能力、安全和科学方法审查。
