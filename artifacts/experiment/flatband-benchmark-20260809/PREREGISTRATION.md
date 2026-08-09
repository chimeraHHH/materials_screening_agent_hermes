# 平带/窄带灵感生成 Benchmark 预注册草案

状态：`DRAFT / PILOT_NO_GO_PENDING_SCHEMA_FREEZE_AND_INDEPENDENT_REDTEAM`

版本：v0.2，2026-08-10（Asia/Shanghai）

本文件冻结拟采用的科研问题、实验单元、标签、指标、统计检验、预算、盲法和停止规则。
它尚未完成外部注册，也不是科研结果。当前 formal V2/V3 链只是本地候选实现；
终稿 Schema/文档哈希与独立红队尚未闭合。在全部 Gate 通过前，不得启动 30-case Pilot、
查看系统间性能差异或把任何输出描述为已验证材料。
相邻 `PREREGISTRATION.sha256` 只标识本次 v0.2 draft checkpoint，不表示外部注册或
Pilot GO；后续正文改动必须生成新内容身份。

## 1. 研究问题与证据边界

研究问题是：在相同的八次物理 metadata 检索预算下，受限语义推理、多源检索和结构化
跨领域机制 Tag，能否提高平带/窄带材料灵感 Top-5 的专家判定科学效用？

唯一锁定主假设为：只由 development Gate 通过的组件组成的 `Fusion`，相对 `B0`
提高 locked IID/OOD 等权的 adjudicated `aNDCG@5`。E1、E2、E3 的单因素比较用于
development 组件选择；不是额外的锁定主假设。

本研究明确不评估 novelty、专利性、优先权或合成可行性的普遍结论。系统输出始终是
`HYPOTHESIS`。专家给出的 grade 3 表示“在给定 packet 内强、证据匹配、约束兼容且可证伪”，
不表示材料已经由实验、DFT 或多体计算验证。

## 2. 当前身份链与未闭合 Gate

| 对象 | 当前身份 |
|---|---|
| source catalog v1 | `57c24de8f0cf616205b03ef16231def711f2dfa9fcac86d94beede9c01b0bb1f` |
| source catalog schema | `a796757159679a02146ad00f306f8bc6db2642fdd3fc51c82b25aba36936efc7` |
| public protocol bundle | `89023a4d07531b598a563e78b0c2b225876981ca9e1d5aea884a493f6aa7be81`；`protocol_readiness=PILOT_NO_GO` |
| private custody schema bundle | `97e95c079c82d41afda7cc5deca1229406dc69387a896da6a9c6686c312b194c`；Schema 定义可公开，私有实例不得发布 |
| legacy v0 bundle | `research_contracts.schema.json`，历史只读，不是 active formal 输出 |
| production Inspiration V1 | 保持原冻结契约；本研究模块不得修改它 |
| formal benchmark chain | private lineage curation → `CandidatePoolReleaseV3` → `CandidateEligibilityAssignmentReleaseV3` → `PreRunEligibilityReleaseV3` → `BenchmarkSplitManifestV2`/`LeakageComponentReleaseV3`/`ExpertStudyRegistryV2` → `FrozenCaseReleaseV3` → `PilotPreBudgetClosureReleaseV3` → `BudgetManifestV2`/`ExecutionReleaseV3` |
| formal expert chain | `ExpertStudyRegistryV2` + reviewer-specific `ReviewerManifestV2`/`PrivateIdentityMapV2` → sealed raw annotations → `FinalGoldReleaseV2` / pre-adjudication `FormalPilotAgreementReleaseV1` |

上表的 formal 类型名表示当前唯一允许的研究路径，不表示已注册或 Pilot GO。
正式 Pilot 前还必须冻结其协议层 SHA-256：

1. 本预注册终稿和标注指南终稿；
2. 与 Pilot/Main 均不重叠的专家校准集；
3. 两位独立 reviewer 和一位 distinct adjudicator 的 pseudonymous registry；
4. Pilot R1 split manifest；如触发，另建不重叠的 Pilot R2 manifest；
5. Main 的抽样 frame/配额、Pilot–Main 不重叠规则、`LeakageComponentReleaseV3` 独立性边政策、
   OOD taxonomy/holdout 选择算法与固定 seed；此时不冻结 Main 120 个具体 case ID；
6. B0/E1/E2-A/E2-B/E3 的 system config、查询模板、TagGraph、代码 Git SHA；
7. LLM provider/model/revision、tokenizer、prompt 和结构化输出 schema；
8. 若运行本地语义模型敏感性实验，其离线 bundle、tokenizer、model card 和许可证；
9. 每个来源的 exact API/snapshot/retrieval identity 和缓存 manifest。

任何一项缺失均为 `NOT_STARTED`，不能用运行时“latest”、模型别名、网页当前内容或人工记忆补齐。

Main 的 exact 120 case IDs（development 60 / locked IID 30 / locked OOD 30）只能在 Pilot Gate
通过后、任何 Main 检索或系统执行前生成并冻结。该生成只能重放 Pilot 前已冻结的
抽样、不重叠与 OOD 政策/固定 seed；不得根据 Pilot 的系统效果、难例或标签选择个别 Main case。

### 2.1 旧 V1 红队整改与当前 Pilot NO-GO 项

2026-08-09 的统计/标注/契约红队提出的旧 V1 结构缺口，已在当前本地候选链中改为：
private definition/assignment curation 先固定细粒度 mechanism lineage；
`CandidatePoolReleaseV3` 和 `CandidateEligibilityAssignmentReleaseV3` 固定全部 primary/replacement
候选、双 reviewer 资格审查与 distinct adjudicator；`PreRunEligibilityReleaseV3` 唯一派生
active selection；`BenchmarkSplitManifestV2` 和 `LeakageComponentReleaseV3` 决定 split/component；
`FrozenCaseReleaseV3` 绑定实际 case、专家 registry 与上游资格；
`PilotPreBudgetClosureReleaseV3` 在任何 budget 前重放 calibration/current/prior-round 不重叠；
`ExecutionReleaseV3` 给出 exact `case × system × rank` 终态；
`ReviewerManifestV2` 与 reviewer-specific `PrivateIdentityMapV2` 物理分离审阅投影和私有位置映射；
`FinalGoldReleaseV2` 闭合 raw/adjudication/duplicate；`FormalPilotAgreementReleaseV1` 只从裁决前
raw labels 和 V2/V3 身份链派生。后者的 `V1` 是 agreement release 自身的 schema 版本；
其 formal assembler 强制 native `ReviewerManifestV2`/`PrivateIdentityMapV2`，旧 V1 execution/blinding
bridge 不是 formal 研究路径。
该 2026-08-09 红队不构成对当前 V2/V3 候选链的独立通过证据；当前链仍须重做独立红队。

但“已有候选实现”不等于 Gate 已关闭。以下项目完成前，本草案保持 `PILOT_NO_GO`：

1. 冻结上述 formal 链的终稿 Schema/代码/文档 SHA，重生所有下游内容寻址实例；
2. 用独立红队复验 exact cover、错 split/component、alias、duplicate、missing/extra cell、
   reviewer 身份泄漏、caller 伪造分数和失败分母等反例；
3. 冻结 system config、逻辑 query/page/bytes/documents/cache 预算、E2 选择与 Fusion 组合规则；
4. 完成 content-addressed calibration completion、COI/recusal map、专家 assignment 和 actual raw-answer SHA；
5. 确认可执行 verifier 能重放所有 Pydantic semantic validator 与跨 release closure；
   JSON Schema validation 单独不构成科研验证；
6. 将 Pilot R1 的 30 个 `INCLUDED` case、完整 system matrix、reviewer assignment 和私有 custody
   身份全部内容寻址，且证明所有 case/source 均满足 `INCLUDE`-only 政策。
7. 完成 structure grouping 的 compute/finalize 与跨 release 联合重放：当前仅有私有原始结构
   解析、规范化与 2D/3D 真空轴证据；layer-group、3D 多容差并图、近重复 component、
   final-case 投影以及 CandidatePool/Calibration/PreBudget 的 exact binding 尚未实现，因此保持
   `PILOT_NO_GO`。

## 3. 目标定义

本项目使用操作性分层，不声称领域存在单一平带阈值：

- `FB100`：跟踪能带带宽 `W <= 0.10 eV`；
- `NB300`：`0.10 < W <= 0.30 eV`；
- `BORDER500`：`0.30 < W <= 0.50 eV`，只进入边界/误差分析；
- `OUT_OF_SCOPE`：`W > 0.50 eV`；
- 主 near-Fermi 条件：目标能带距 Fermi level 的绝对距离 `<= 1.0 eV`。

带宽覆盖范围必须区分 full-BZ grid、Wannier grid、完整高对称路径、局部路径、结构先验和
source-only label。SOC、磁序/自旋通道、Hubbard U、隔离隙、band tracking 方法和 k sampling
是正交字段。高对称路径窄带不能重写为 full-BZ 平带；数据库标签和模型分数只用于抽样，
不能直接成为 gold label。

## 4. 实验与判断单元

- **case**：冻结 parent structure、研究要求、硬约束、允许/禁止变换及开放来源证据；
- **system output**：一个 case 下某系统的有序 Top-5 `HypothesisPacket`；
- **expert judgment unit**：`case × exact packet`。多个系统返回完全相同 packet 时只标注一次；
- **primary analysis unit**：case；所有系统在同一 case 上配对；
- **resampling/randomization unit**：`LeakageComponentReleaseV3` 中由预先冻结 hard leakage edge
  形成的 connected component，而不是
  调用者任选的单个 group、packet 或候选；
- **duplicate unit**：专家最终发布的 case-local strict hypothesis partition；同一排名中该组第一次
  出现后的位置增益为零，系统自报 group 只作 proposal/audit。

最终盲法实现必须由两个物理分离对象组成：私有 identity map 逐位置覆盖所有纳入排名；
reviewer-facing packet 只含匿名 case/hypothesis/evidence 投影，不含 system/run/ranking/rank、
系统自报 group、另一位 reviewer 标签或聚合结果。每位 reviewer 使用独立随机顺序和统一 renderer。
来源 adapter 名称不显示；显示规范化 work identity/citation 和可核验的 bounded evidence。
每条 reviewer evidence 的 metadata preimage/原始记录哈希只在 `PrivateIdentityMapV2` 中做私有、
内部的确定性 replay；`evidence_preimages_replayed=true` 不是数据 provider 的签名、attestation
或对科学真实性的背书。
由于生成文本风格仍可能泄漏，正式报告称 `identity-masked` 而非完美 double-blind，并在提交后记录
reviewer 的 system-origin guess 作为盲法敏感性指标；guess 不参与 grade。

## 5. 语料构建、抽样与泄漏控制

### 5.1 来源和资格

case seeds 优先来自 COD、Materials Project core、精确 JARVIS Figshare、C2DB 和
2DMatPedia。TQC 仅作 pointer/anchor，ICSD 坐标不进入公开 benchmark；Crystal Net、
ELF 和 Struct2Flat 只产生 weak sampling/OOD strata。每条记录必须通过 source catalog
的 license、provenance、version、hash 和 public/private 字段 Gate。

同一 DOI/arXiv work 经多个图谱解析时是一条 evidence item 加多个 provenance，不是多次独立支持。
数据库缺失、未入选或低模型分数均为 unknown，不能创建 confirmed negative。

### 5.2 校准集

在 Pilot 前建立一个不属于 Pilot R1、Pilot R2 或 Main 120 的冻结校准集。它应覆盖 0--3
四个等级、无效 case、无效 packet、证据 scope mismatch、正确/条件/错误 bridge 和 strict
duplicate。两位 reviewer 先独立密封作答，再与 adjudicator 讨论手册歧义；所有修改产生
新的指南 SHA。完成后才可将 `calibration_completed=true` 写入 expert registry。
校准分数不进入任何性能或一致性指标。

### 5.3 Pilot

Pilot R1 恰为 30 个 case，目标边际为 15 个 FB100、15 个 NB300，以及 15 个 2D、15 个
3D；至少覆盖五个 mechanism family，任一 family 不超过 6 个 case。抽样先按
`LeakageComponentReleaseV3` 的 hard leakage edges 构建 connected components，再在固定
seed 下做可复现的约束选择；至少保留 10 个独立 component。正式 Pilot 是
`INCLUDE`-only：每个被执行 case 及其 seed evidence 必须解析到
冻结 source catalog 的 `INCLUDE` 决策，并在 `PreRunEligibilityReleaseV3` 中为 `INCLUDED`；
`CONDITIONAL`/`EXCLUDE` 不得通过事后例外进入运行分母。Pilot 使用 B0、E1、E2-B、E3 的冻结输出，
Top-5 exact pooling 后由两位 reviewer 全量独立标注。Pilot 只校验手册可用性和一致性，
不用于选择效果最好的系统或调阈值。

### 5.4 Main 120

Main 恰为 development 60、locked IID 30、locked OOD 30。每个 split 保持 FB100/NB300
和 2D/3D 的边际尽可能平衡；精确配额在 Pilot 前写入 sampling policy，具体 120 case
按第 2 节时序后续冻结。组成、结构 prototype/graph、fingerprint cluster、共同论文/数据库
候选家族，以及由可核验 evidence 支持的 fine-grained mechanism lineage，才是 `LeakageComponentReleaseV3`
的 independence edges。broad `MechanismFamily` 只用于抽样分层与 OOD holdout taxonomy，
不得直接连成 leakage edge；专家事后的 family 标签也不得重写已冻结 component。
连通分量是唯一 split/resampling identity，任一 component 只能属于一个 split。development 至少
20 个独立 component，locked IID/OOD 各至少 10 个；不足即不建立 confirmatory split。

OOD 必须冻结完整的 mechanism 或 structure family，而不是普通元素替换。每个 OOD case 必须
命中至少一个 holdout family，development/IID 必须零命中。这里的 OOD 仅表示相对 benchmark
development case 的 family holdout，不声称 LLM 预训练或数据库历史中从未见过该材料。具体 holdout 只能在
合法 sampling frame 构建且 Pilot Gate 通过后、任何 Main 系统运行前写入 Main manifest；
本草案不伪造尚不存在的 family inventory。Pilot R1/R2 与 Main 在 case 和全部
`LeakageComponentReleaseV3` component 上严格不重叠。

## 6. 系统、预算和可比性

所有系统每 case 最多八次物理 metadata 请求，article-body fetch 与 full-PDF read 均为 0。
redirect 的每个物理 HTTP hop 计入预算。未使用的来源配额不能在看到 case 结果后转移。
这只是相同 physical-request cap，不等于相同信息量。正式 system config 还必须冻结每来源逻辑
query 数、page size、最大记录数、响应字节、唯一文档数、字段选择、排序、cache snapshot 和
cold/warm policy；超过任一上限即失败，不通过 cache hit 获得未计量额外语料。

| 系统 | 冻结干预 | 请求分配 | LLM |
|---|---|---:|---:|
| B0 | Crossref、curated Tags、lexical features/dedup/diversity | Crossref 8 | 0 |
| E1 | B0 检索 + bounded metadata packet 上的结构化语义推理 | Crossref 8 | <=2 calls |
| E2-A | B0 排序逻辑 + 多源 metadata | Crossref/OpenAlex/arXiv = 3/3/2 | 0 |
| E2-B | E2-A + OpenAIRE | 四来源各 2 | 0 |
| E3 | B0 检索 + 冻结 mechanism TagGraph/transfer rules | Crossref 8 | 0 |
| Fusion | 仅含通过 development Gate 的组件 | 总计 8，配置预先冻结 | 按入选组件冻结 |

E1 最多接收按本地确定性规则选出的 20 个 metadata packets，最多 2 次模型调用和 12,000
input tokens/case。prompt 只给 title、可合法使用的 abstract/summary span、keywords/topics、
IDs、来源和局部 citation/concept 字段；不给正文、PDF、专家标签、locked annotation 或系统
性能。输出必须是 strict JSON，至少含 source domain、mechanism family、shared invariant、
target mapping、transferable control、required/breaking conditions、supporting span IDs、
contradictions、falsifier 和 confidence。解析失败不自由重试，按冻结失败规则记入 underfill。

本地语义 embedding 只能作为另行标识的 `E1-local` 敏感性实验：模型 bundle 必须可离线加载、
许可证允许、维度/tokenizer/model-card 哈希固定。它不能与 E1 主干混合后仍沿用同一 system ID。

E2-A 与 E2-B 都相对 B0 运行同一 development Gate。若仅一个通过，选该版本；若二者都通过，
只有当 E2-B 的 `aNDCG@5` 至少比 E2-A 高 0.01 且仍满足全部 guardrail 时才选 E2-B，否则选
信息源更少的 E2-A；都不通过则 Fusion 不含 E2。E1/E3 独立按同一规则入选。Fusion 是 B0 加
全部通过的 E1、选定 E2 和 E3 的固定组合，不搜索任意子集；组合本身也须在 development 相对
B0 通过 Gate，否则不解封 locked labels。

E3 Tag 是机制转移约束，不是关键词堆叠。每条 route 必须显式表达 source mechanism、shared
invariant、target mapping、可控变量、成立条件、破坏条件与反证。候选领域可含 photonic、
acoustic、mechanical、circuit 和 cold-atom lattices。Tag 提案只可离线生成并由专家发布；
benchmark 运行中不得在线修改 TagGraph。

每次运行先绑定不可变 budget/system manifest；ranking 只向前引用该 manifest，不反向引用 terminal
ledger。运行结束后由 terminal result manifest 同时绑定 ranking（若有）、实际 ledger、Git/system/
case identity 和 failure reason，避免内容哈希循环。成本或失败记录缺失的 case 仍必须由
ExecutionMatrix 生成显式 failure cell，不允许 complete-case 删除。

## 7. 双专家标注与裁决

两位 reviewer 对全部 pooled units 独立作答；adjudicator 不得兼任 reviewer。所有人绑定同一
Annotation Guide SHA 和 calibration set SHA。reviewer 只能使用 packet 内证据，不得自行联网
补证或根据熟悉的论文/系统风格推测来源。

在任何系统输出生成前，两位 reviewer 先只看 case 做 eligibility/assessability audit；分歧先裁决，
无效 case 在不接触系统结果时按冻结规则替换。系统输出后的单边 `CASE_INVALID` 不得在 alpha 中
静默删除：它触发数据完整性审查，并使该 Pilot round 不能直接通过。

主要 0--3 relevance grade：

- **0**：无效、硬约束冲突、无可用证据、明显错误 bridge 或不可恢复的 malformed packet；
- **1**：主题相关但机制/映射弱，证据仅为 context，或关键条件/反证缺失；
- **2**：plausible、至少一条 scope-matched valid support、无 hard fail，bridge 至少条件成立且有
  明确验证方案；
- **3**：满足 grade 2，且 source mechanism、shared invariant、target mapping 和 required
  conditions 全部通过，无 bridge 子项失败，候选约束兼容且 falsifier 具体。

grade >=2 必须 `evidence_valid=true` 且至少一条 `VALID_SUPPORT`；grade 3 必须
`bridge.overall=CORRECT`。所有 grade 仍为专家对 proposal utility 的判断，不是材料真实性结论。
原始标签先密封，只有分歧单元进入 distinct adjudicator；裁决不得覆盖或删除原始标签。
每位 reviewer 只从自己的 `ReviewerManifestV2` 接收投影，与其 `PrivateIdentityMapV2`
通过 SHA/ID 成对闭合；不接受 V1 reviewer projection 或 caller 自报 system/rank/case 身份。
`FinalGoldReleaseV2` 必须为每个 present pooled unit 恰好生成一条 `AGREED_RAW` 或
`ADJUDICATED` 记录，精确绑定两个 raw reviews、`ExpertStudyRegistryV2`、reviewer/private-map
引用、execution packet 和 case；跨 unit/case alias 必须 fail closed。duplicate partition 在同一 case 的
全部匿名 packet 标注完成后单独密封并裁决，不能由 unit 内自由字符串或系统 proposal 决定。

## 8. Pilot 一致性 Gate

一致性主量为裁决前两位 reviewer 原始 0--3 relevance grade 的 ordinal Krippendorff alpha，
使用 original ordinal distance。`FormalPilotAgreementReleaseV1` 必须只从当轮
`CandidatePoolReleaseV3` + `PreRunEligibilityReleaseV3` + `BenchmarkSplitManifestV2` +
`LeakageComponentReleaseV3` + `FrozenCaseReleaseV3` + `PilotPreBudgetClosureReleaseV3` +
`ExecutionReleaseV3` + `ExpertStudyRegistryV2` 中的 assignment +
两份 reviewer-specific `ReviewerManifestV2`/`PrivateIdentityMapV2` + 裁决前 raw annotations
唯一派生，不接受 caller 提供的 RatedUnit、case/component 或 metric float。

每个 present pooled unit 必须恰好有两个 assigned reviewer labels。双方都为
`SYSTEM_PACKET_INVALID` 时，该 unit 以固定 `(0,0)` 纳入 alpha；单边 `SYSTEM_PACKET_INVALID`、
任一 `CASE_INVALID`、普通 missing、多余/第三个标签或 orphan unit 都使整轮 fail closed，不得
通过删除“难例”计算 alpha。expected disagreement 为 0 时 alpha 记为 undefined，不能当作 1。
置信区间按 `LeakageComponentReleaseV3 component → case` 两层做 50,000 次 percentile
bootstrap，固定 seed 为 `20260809`。minimum exact agreement 只作描述统计，不参与 Gate。

- `alpha >= 0.80`：R1 直接通过；
- `0.667 <= alpha < 0.80`：只能修改标注手册，不得修改系统、case 定义或已密封 R1 标签；
  随后在完全不重叠的 30-case R2 重新独立标注，R2 必须 `>=0.80`；
- `alpha < 0.667`：停止扩展，重新评估判断任务；
- R2 `<0.80`：停止，不构建 Main 120。

无论总体 alpha 是否通过，都报告 grade confusion、证据有效性一致率、bridge overall 一致率、
各 mechanism/2D-3D/FB-NB 子组和 undefined bootstrap 比例。不得以裁决后的标签计算 alpha。

## 9. 指标

### 9.1 唯一主指标

主指标为 `aNDCG@5`，不是 pooled-IDCG 的经典 nDCG。对 rank `r=1..5`：

`DCG = sum(g_r / log2(r+1))`

主分析 `g_r = grade_r`；缺失位置和同一 final expert strict group 第一次出现后的所有位置为 0。
固定分母是假设五个位置均为 grade 3 的 `8.8453773566`，因此弱系统不能通过缩小自己的
ideal list 获得 1.0。`2^grade-1` 增益及固定分母 `20.6392138322` 只作敏感性分析。

### 9.2 次指标

- Success@5：是否至少有一个非重复 grade >=2；
- StrongSuccess@5：是否至少有一个非重复 grade 3；
- EvidenceValid@5：五个固定位置中 evidence-valid 的比例，缺失位置为 0；
- Completion@5：返回数/5；
- strict duplicate rate：`(returned - unique strict groups)/(returned - 1)`，0/1 个返回时为 0；
- bridge correctness、mechanism-family coverage、fill/underfill、request/token/latency；
- IID/OOD、FB100/NB300、2D/3D、SOC/磁性/覆盖范围的预声明分层结果。

case-level 分数先在 case 内计算，再按预声明 split 聚合；不得把候选位置当独立样本。

## 10. 统计分析与决策规则

### 10.1 Development promotion

每个 E1/E2/E3 相对其匹配 baseline 只有在 development 60 同时满足下列条件时才可进入 Fusion：

- `delta aNDCG@5 >= 0.03`；
- EvidenceValid@5 下降不超过 0.03；
- duplicate rate 上升不超过 0.02；
- Success@5 下降不超过 0.02；
- 不增加八次物理请求预算，不违反正文/PDF=0 和身份闭合。

这些阈值是预声明的 observed development guardrails，不是显著性或非劣推断。组件未通过即不进入 Fusion，但其失败结果和完整分母仍发布。Fusion 配置只可使用 development，
随后连同所有 identity 冻结。

### 10.2 Locked primary

locked IID 30 和 OOD 30 内分别计算 Fusion-B0 paired case delta，再以 0.5/0.5 等权：

`Delta = 0.5 * mean(delta_IID) + 0.5 * mean(delta_OOD)`。

主假设通过必须同时满足：

1. observed `Delta aNDCG@5 >= 0.05`；
2. 按 `LeakageComponentReleaseV3 component → case` 两层重抽的 50,000-replicate paired
   bootstrap 95% CI 下界 `>0`；
3. connected-component 共同变号的 one-sided paired randomization test：`2^G<=100,000` 时
   精确枚举，否则固定 seed 做 100,000 draws 并使用 add-one `p < 0.05`；
4. Success@5 和 EvidenceValid@5 差值均 `>= -0.05`；duplicate-rate 差值 `<= +0.05`。

第 4 项只是 observed safety guardrail，不能表述为统计非劣。第 1 项是实践意义点估计门槛；
CI/randomization 检验的是 `Delta>0`，不能声称统计上证明 `Delta>=0.05`。

只有一个 locked primary，因此不做 Holm。预声明 secondary family 含五个单侧
`aNDCG@5` 假设：Fusion>B0 的 IID-only、OOD-only，以及 Fusion>Fusion-minus-E1、
Fusion>Fusion-minus-E2、Fusion>Fusion-minus-E3；未进入 Fusion 的组件对应 leave-one 假设记为
`NOT_APPLICABLE` 并保守指定 `p=1`，不以其余 p 值重定义 family。每个可用假设使用同一
connected-component sign randomization，固定五个 p 值按 Holm step-down 调整。其他
subgroup/error analyses均标为 descriptive/exploratory，不生成新的通过结论。

bootstrap 与 randomization seed 必须在运行前写入分析 manifest。若可枚举的 group-level sign
assignments 少于 100,000，则精确枚举；否则固定 seed Monte Carlo。

## 11. 缺失、失败与无效 case

- 唯一 release assembler 必须输出全部预期 `case × system` 行；system underfill、解析失败、
  deadline、检索失败和无 ranking 保留在分母，缺失排名位置按 0 gain、evidence-invalid、
  completion 缺失处理；
- 两位 reviewer 必须完成每个 pooled unit 后才计算 Pilot/Main 指标，不做 label imputation；
- 若 case 经独立裁决为 `CASE_INVALID`，所有系统在该 case 上对称排除并单列原因，不按系统选择；
- locked 阶段发现 invalid case 不补样。invalid case 超过 locked 60 的 5% 时不发布主通过结论；
- `UNRESOLVABLE` packet 固定为 grade/evidence gain 0 并保留位置；比例超过全部 pooled units 的
  5% 时停止主分析并回到标注协议设计；
- development/Pilot 若在任何系统结果或性能标签揭盲前发现 source/license/structure 无效，可按同一
  预冻结抽样规则替换，并发布原 case、原因和 replacement lineage；揭盲后不得替换；
- API/provider 版本漂移、许可漂移、prompt/model identity 不一致或成本账本不闭合均 fail closed。

## 12. 锁定测试纪律、停止与修订

locked labels 只在 Fusion、分析代码、环境和全部哈希冻结后解封一次。解封后不得改 query、
prompt、Tag、排序、去重、阈值、split、统计尾部或非劣界限。代码错误只能在不查看 system
identity 的独立复核下修复；任何会改变候选或分数的修复使该 locked run 失效，必须公开
记录为 protocol deviation，而不能称为首次 confirmatory test。

立即停止条件包括：Pilot Gate 失败；许可证无法支持可复现公开 benchmark；出现 case/group
泄漏；系统间预算不等；专家或模型接触 locked labels；运行 identity 漂移；或需要在看到
locked 结果后修改规则。

本草案正式注册前允许用户手动修订。编辑中 draft 必须更新版本/变更说明，但只在
候选终稿密封时重生 SHA sidecar；明示过期的 sidecar 不得作为 protocol identity。Pilot R1
必须在终稿 SHA 闭合后才能开始；开始后只允许第 8 节声明的 manual-only revision；
Main system runs 开始后不得改变 endpoint、
split、预算或 promotion Gate。

## 13. 发布与科研表述

公开协议层可包含 Schema、verifier、抽样/统计算法、字段说明、空或合成示例及其 SHA；
这不授权公开任何在进行中的 custody instance。活跃 `ReviewerManifestV2`、
`PrivateIdentityMapV2`、专家 assignment/COI、raw annotation、未发布 Gold 与裁决前 Agreement 实例、
私有 metadata preimage、blinding key 与运行数据库必须分离、最小权限保管，不进入 GitHub。
公开包只含许可证允许的结构/事实字段、source IDs、查询、manifest、prompt/schema、项目自有
标签、派生指标和 provenance。原始受限 abstracts、文章正文/PDF、ICSD/AFLOW/S2 数据、API key
和 provider transcript 不公开。全部标签密封、locked analysis 解封且许可/隐私复核通过后，
只可发布不含专家顺序/密钥/受限文本的 sanitized mapping 或派生 release，以支持结果复核。

最终报告必须同时给出所有 case 的 outcome/underfill denominator、组件失败、IID/OOD 差异、
专家一致性和协议偏差。`scientific_conclusion=false` 保持到独立科研审查；即使 primary 通过，
也只能声称“在本 benchmark 与预算下提高了专家判定的 hypothesis utility”。

## 参考依据

- Regnault et al., *Catalogue of flat-band stoichiometric materials*, Nature
  (2022), DOI: [10.1038/s41586-022-04519-1](https://doi.org/10.1038/s41586-022-04519-1)。
- Moustafa et al., *Struct2Flat*, Science Advances (2026), DOI:
  [10.1126/sciadv.aea3611](https://doi.org/10.1126/sciadv.aea3611)。
- Järvelin and Kekäläinen, cumulative gain metrics, DOI:
  [10.1145/582415.582418](https://doi.org/10.1145/582415.582418)。
- Hayes and Krippendorff, reliability measurement, DOI:
  [10.1080/19312450709336664](https://doi.org/10.1080/19312450709336664)。
