# 平带/窄带灵感生成 Benchmark 预注册草案

状态：`DRAFT / FULL_FLOW_CONTRACT_IMPLEMENTED / REAL_EXECUTION_NOT_RUN / PILOT_NO_GO`

版本：v0.5，2026-08-10（Asia/Shanghai）

v0.5 变更：采纳 2026-08-10 用户评审（AI 代行分析、用户批准采纳）的修订——专家工作量
预算与校准计时试点（§7.1）、Fusion 组合算子按组件子集冻结（§6）、COI 操作化与替补校准
前置（§2/§7）、near-Fermi/带宽操作定义与 ±1.0 eV 窗口理由（§3）、companion alpha 与
`(0,0)` 占比预声明（§8）、OOD holdout 约束感知回退序（§5.4）、R2 后唯一一次 binary-gain
降级模式（§8）、专家公开描述模板（§13）。该评审是被用户采纳的分析证据，不构成独立红队、
外部 authority 或专家实例证据。

本文件冻结拟采用的科研问题、实验单元、标签、指标、统计检验、预算、盲法和停止规则。
它尚未完成外部注册，也不是科研结果。代码已经实现从 Pilot custody、Main120 冻结、模型原生
Arm receipt、Execution、双专家 Gold、Analysis V2、development promotion、locked 单次解封，
到内部签名审查和 sanitized public result 的 formal 全流程；这只是可重放合同能力。没有创建真实
private Main 结构/union/人工标注/Campaign 实例，没有发起真实模型调用，也没有运行真实 benchmark。
当前阶段不以性能或 benchmark 数值作为实现 Gate；不得把 schema、合成测试或空流程描述为材料发现、
科学结果或 Pilot GO。相邻 `PREREGISTRATION.sha256` 只标识本次 v0.5 draft checkpoint，不表示外部注册或
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
| public protocol bundle v2 | 身份见 `research_public_protocol.schema.sha256`；16 个 explicit public roots，含 source-catalog checkpoint 与 aggregate result；`protocol_readiness=PILOT_NO_GO` |
| private custody schema bundle v3 | 身份见 `research_private_custody.schema.sha256`；73 个 explicit custody roots，含完整 Pilot-round bundle；Schema 定义可公开，私有实例不得发布 |
| legacy v0 bundle | `research_contracts.schema.json`，历史只读，不是 active formal 输出 |
| production Inspiration V1 | 保持原冻结契约；本研究模块不得修改它 |
| formal Pilot chain | raw private structures → structure computation/private evidence → private lineage curation → `CandidatePoolReleaseV3` → eligibility/split/freeze → fresh full-pool union → `PilotPreBudgetClosureReleaseV3` → budget/execution → expert Gold/agreement |
| formal Main chain | Pilot Gate → sampling/candidate/eligibility/frozen/pre-budget custody → five phase authorizations → seven formal phase executions + one E1-local `NOT_RUN` closure → three Gold/verifier/Analysis releases → promotion/Fusion Gate → locked seal/plan/authorization/ledger/unseal → claim support/review/release control → sanitized result → `FlatBandCampaignReleaseV1` |
| formal calibration chain | exact calibration cases → two-reviewer `DerivativeScreeningReleaseV3` + `StructureGroupingPrivateEvidenceReleaseV2` → `CalibrationSetManifestV2` → `ExpertStudyRegistryV2` |
| formal expert chain | `ExpertStudyRegistryV2` + reviewer-specific `ReviewerManifestV2`/`PrivateIdentityMapV2` → sealed raw annotations → `FinalGoldReleaseV2` / pre-adjudication `FormalPilotAgreementReleaseV1` |

上表的 formal 类型名表示当前唯一允许的研究路径，不表示已注册或 Pilot GO。
正式 Pilot 前还必须冻结其协议层 SHA-256：

1. 本预注册终稿和标注指南终稿；
2. 与 Pilot/Main 均不重叠的专家校准集；
3. 两位独立 reviewer、一位 distinct adjudicator 与至少一位替补专家的 pseudonymous
   registry，含第 7 节 COI 取消资格/披露审查、work/case-level COI map 与 recusal 政策；
   替补在承担任何 assignment 前必须完成同一指南 SHA 的校准；
4. Pilot R1 split manifest；如触发，另建不重叠的 Pilot R2 manifest；
5. Main 的抽样 frame/配额、Pilot–Main 不重叠规则、`LeakageComponentReleaseV3` 独立性边政策、
   OOD taxonomy/holdout 选择算法与固定 seed；此时不冻结 Main 120 个具体 case ID；
6. B0/E1/E2-A/E2-B/E3 的 system config、查询模板、TagGraph、代码 Git SHA；
7. LLM provider/model/revision、tokenizer、prompt 和结构化输出 schema；
8. E1-local 只冻结一个 `NOT_RUN_USER_PROHIBITED` 授权关闭记录；本研究不加载其 bundle、
   tokenizer 或 model card，不产生 local-model invocation/output，也不允许 promotion 或 locked 使用；
9. 每个来源的 exact API/snapshot/retrieval identity 和缓存 manifest；
10. structure grouping 参数、代码/依赖/native `libsymspg` identity、私有 raw artifact manifest、
    calibration/R1/R2 full-pool member releases 与 fresh union replay；
11. derivative screening policy、两位独立自然人 reviewer、一位 distinct adjudicator、逐 case
    assignment、两份 raw review、必要 adjudication 与全部 `NOT` Gate。

任何一项缺失均为 `NOT_STARTED`，不能用运行时“latest”、模型别名、网页当前内容或人工记忆补齐。

Main 的 exact 120 case IDs（development 60 / locked IID 30 / locked OOD 30）只能在 Pilot Gate
通过后、任何 Main 检索或系统执行前生成并冻结。该生成只能重放 Pilot 前已冻结的
抽样、不重叠与 OOD 政策/固定 seed；不得根据 Pilot 的系统效果、难例或标签选择个别 Main case。

### 2.1 当前 formal 全流程边界

语义判断、跨领域机制映射、排序解释和证据综合只允许由冻结身份的大模型原生调用产生，并以
`ModelNativeReasoningWorkItemV1`、`ResponseV1` 和 `ReceiptV1` 保留可见 request/response、字节数、
token usage 与 provider identity；不存 chain of thought。本地代码只执行 schema 校验、哈希、解析、
exact replay、固定指标和时序检查，不承担语义推理。旧 terminal LLM accounting 与本地语义模型都
不是 formal Main 路径。

七个 formal execution releases 是 development ablation、development Fusion、locked Fusion
components、locked primary 和三个 locked Fusion-minus。locked component 的 240 个 cells 只为
Fusion 父 Arm 提供 exact derivation preimage，`component_derivation_only=true`，不作为 Gold/Analysis
比较 Arm，也不进入其分母。E1-local 另以零 execution/output/invocation 的内容寻址 `NOT_RUN` release
关闭，不属于这七个 execution releases。

每个成功或失败的授权 case-role 都必须保留固定 Top-5 分母。FAILED cell 没有 Arm trace、review unit
或人工 label；Gold/Analysis 唯一派生五个 `SYSTEM_PACKET_INVALID/RUN_FAILED`、gain=0 的位置，禁止
complete-case 删除。Main raw label、label adjudication、raw duplicate partition 与 duplicate adjudication
以及科研 reviewer decision 使用预承诺 HMAC-SHA256 签名；密钥只作为 exact-replay 的临时输入，
不进入 artifact。该机制只闭合仓库内部真实性；真实外部 authority 身份、外部 key custody、外部
publication permission 和 provider execution attestation 均为 `NOT_PROVIDED`/false。

### 2.2 旧 V1 红队整改与当前 Pilot NO-GO 项

2026-08-09 的统计/标注/契约红队提出的旧 V1 结构缺口，已在当前本地候选链中改为：
private definition/assignment curation 先固定细粒度 mechanism lineage；
`CandidatePoolReleaseV3` 和 `CandidateEligibilityAssignmentReleaseV3` 固定全部 primary/replacement
候选、双 reviewer 资格审查与 distinct adjudicator；`PreRunEligibilityReleaseV3` 唯一派生
active selection；`BenchmarkSplitManifestV2` 和 `LeakageComponentReleaseV3` 决定 split/component；
`FrozenCaseReleaseV3` 绑定实际 case、专家 registry 与上游资格；
`PilotPreBudgetClosureReleaseV3` 在任何 budget 前以 fresh raw-structure union 重放
calibration/current full candidate pool，以及 R2 的 prior-R1 full candidate pool 不重叠；
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
7. formal 全流程已有本地实现和合成反例测试；但尚无真实私有结构/人工 derivative、Main Gold、
   external authority、release-control evidence 或 Campaign 实例，也没有真实模型执行和 benchmark 结果。
   是否开始 Pilot 需要在这些真实 custody/身份输入就绪后另作决定；当前不以性能 benchmark 作为
   本轮合同实现的完成条件。

## 3. 目标定义

本项目使用操作性分层，不声称领域存在单一平带阈值：

- `FB100`：跟踪能带带宽 `W <= 0.10 eV`；
- `NB300`：`0.10 < W <= 0.30 eV`；
- `BORDER500`：`0.30 < W <= 0.50 eV`，只进入边界/误差分析；
- `OUT_OF_SCOPE`：`W > 0.50 eV`；
- 主 near-Fermi 条件：`min_k |E(k) - E_F| <= 1.0 eV`，min 在该带所记录覆盖范围
  （full-BZ/Wannier grid/路径）的 k 点集合上取；不得以带中心或带边平均距离替代该 min 定义。

`W` 只对可唯一追踪的单条能带定义，为该带在所记录覆盖范围内的能量极差。与其他带存在
接触点或简并的目标带不因接触改变 `W` 的定义，但隔离隙字段必须如实记录为零/负；给定
band-tracking 方法不能唯一分辨目标带（如宽范围简并流形）时，该 case 不得进入 FB100/
NB300 正类抽样，只能进入 BORDER500/覆盖受限分层或被排除。±1.0 eV 窗口有意宽于关联
平带文献常用的 ±0.2--0.5 eV：它只是 case 抽样资格窗口（宽进），电子活跃性与物理相关性
由专家在 packet 证据层判断（严判），其边界效应由 near-Fermi 距离分层报告吸收。

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

每个校准 case 还必须经过独立的人工 derivative screening：冻结 policy 后，由两位自然人 reviewer
独立检查 exact、非空的 case source-record evidence 子集，分类为 `NOT`、`VACANCY`、
`INTERCALATION`、`NON_STOICHIOMETRIC` 或 `ORDERED_DEFECT`。只有两份 raw class 不一致时，
才由与两位 reviewer 均不同的自然人 adjudicator 裁决。校准 Gate 采用保守规则：任一 raw review
或 final judgment 非 `NOT` 均排除该 case；adjudication 保留为审计证据，但不得把 raw derivative
风险洗回校准集。该人工判断不称为自动真值、材料身份真值或实验/DFT 结论。它不同于候选资格链
中字段名为 `NOT_A_DERIVATIVE` 的 sibling taxonomy，不得跨 Schema 值混用。

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

结构不重叠不是只比较 30 个 active cases 或 caller 提供的 group key；R1 必须将完整 calibration
和完整 R1 candidate pool 的 raw structures fresh-union，R2 另加入完整 prior-R1 candidate pool，
包括所有 replacement candidates。任一 cross-owner prototype/fingerprint component 都使 PreBudget
closure 失败。

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

holdout 选择算法必须是约束感知的，并与 seed 一同在 Pilot 前冻结：在满足 development/IID
零命中、全部边际配额与 component 下限约束的可行 holdout 集合内，按可支持的 locked OOD
独立 component 数降序排序，数目相同时按冻结的 family 枚举序决胜，取第一个可行 holdout；
仅当可行集合为空时才宣告不建立 confirmatory split。不得在看到任何系统输出后重新排序或
更换 holdout。

### 5.5 冻结结构与 derivative 泄漏轴

结构轴只处理 ordered occupancy。2D 输入必须恰有一个 geometric vacuum axis，source cyclic gap
至少 8 Å；规范化使用 15 Å padding，并在 `symprec=[0.05,0.10] Å` 重放 layer group。3D 使用
`symprec=[0.01,0.05,0.10] Å` 并对阈值签名取保守并图。anonymous `StructureMatcher` 采用双向
fit 的 conservative OR、`attempt_supercell=true`；supercell site-ratio 上限为 2D 9、3D 8，
每个结构最多 128 sites/6 species。Pilot V0 单次 compute 最多 96 candidates、union 最多 32
member releases；PreBudget 再限制 raw owner projection 最多 84 candidates。该 96 上限只覆盖
calibration `<=12` 与 Pilot R1/R2 各 `<=36`；Main 必须另建版本化 capacity decision 和真实 dry-run，
不得静默复用 Pilot 上限。runtime identity 必须绑定 platform/Python、`requirements.lock`、
pymatgen/spglib/numpy/scipy、spglib extension、resolved `libsymspg` 与 grouping module hash；formal
native-library replay 当前只支持 Darwin/Linux。

正式时序为：member `input seal < run start <= completion <= computation creation < final-case
declaration <= private-release creation`，且 monotonic clock 同样要求 `seal < start <= completion`；
private structure release 创建不晚于 calibration freeze，derivative release 必须严格早于 calibration
freeze。union 要求每个 member release `< union input seal < run start <= completion <= computation
creation < verification < PreBudget seal < every budget`。这些是本地 UTC 与 monotonic replay；
`external_timestamp_attestation=false`，不构成第三方可信时间戳。

这些轴是确定性、内容寻址的泄漏启发式，不是晶体学或物理等价真值。8 Å 规则可能误判多孔或
大真空 cell；ordered/disordered occupancy 边界不在当前模型内；跨阈值并图和 anonymous
supercell match 可能保守过合并；parent/transformation derivative 也可能逃逸人工证据。
“zero cross-owner component”只表示冻结算法、参数和运行环境下未找到边，不表示真正科学独立。

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

`E1-local` 在本次研究中明确为 `NOT_RUN_USER_PROHIBITED`：不加载本地语义模型，不产生 invocation、
output 或 execution release，不允许进入 promotion、Fusion 或 locked analysis。E1 的语义推理只能
使用上述大模型原生 receipt 路径。

E2-A 与 E2-B 都相对 B0 运行同一 development Gate。若仅一个通过，选该版本；若二者都通过，
只有当 E2-B 的 `aNDCG@5` 至少比 E2-A 高 0.01 且仍满足全部 guardrail 时才选 E2-B，否则选
信息源更少的 E2-A；都不通过则 Fusion 不含 E2。E1/E3 独立按同一规则入选。Fusion 是 B0 加
全部通过的 E1、选定 E2 和 E3 的固定组合，不搜索任意子集；组合本身也须在 development 相对
B0 通过 Gate，否则不解封 locked labels。

Fusion 的组合算子在 Pilot 前对全部 `2^3` 个组件子集逐一冻结，消除 development 结果揭晓后
的配置自由度。算子按固定三层定义：检索层——E2 入选时采用选定 E2 变体的冻结请求分配
（E2-A `3/3/2`，E2-B `2/2/2/2`），否则用 B0 的 Crossref 8；候选生成层——E3 入选时使用 B0
词法路径与 E3 冻结 TagGraph 转移路线的并集并保留各自 lineage，否则仅 B0 路径；排序层——
E1 入选时在同一 2 调用/12,000 token 预算内对候选 bounded metadata packets 应用 E1 的冻结
语义重排，否则用 B0 词法排序。Top-5 选择统一使用与 B0 相同的冻结去重/多样性选择器。每个
子集组合的 exact system config（请求分配、TagGraph hash、prompt identity、选择器参数）在
Pilot 前内容寻址冻结；空子集时 Fusion 不成立，locked labels 不解封。

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

COI 与独立性按以下操作化规则执行：参与过本项目系统实现、TagGraph/prompt 设计或看过任何
系统输出与配置的人不得担任 reviewer 或 adjudicator；adjudicator 与任一 reviewer 之间不得
存在指导/被指导或直接上下级关系；reviewer 不得标注引用了本人署名文献的 packet，该情形走
work/case-level recusal 并由已完成校准的替补接手，替补机制不得静默缩小任何系统的分母。
与项目负责人的合著或机构隶属关系记录为披露项而非取消项，按第 13 节的预冻结聚合模板在
最终报告如实披露。COI map、recusal 与替补指派必须在任何系统结果生成前冻结。

### 7.1 专家资源与工作量预算

Pilot R1 的标注上限为 30 case × 4 系统 × Top-5 = 600 个 pooled unit（exact-packet pooling
只合并字节级相同 packet，实际 unique unit 数在 execution 后由 pooling 决定并记录）。冻结
的预算假设为每 unit 5--10 分钟，加上每 case 的 pre-run audit 与约 20 个匿名 packet 的
duplicate partition（每 case 10--20 分钟），每位 reviewer 的 R1 预算区间为 30--75 小时，
另加校准集 5--10 小时；触发 R2 则近似翻倍。Main 的审阅规模约为 Pilot 的 4--5 倍；Main
capacity decision 必须以 Pilot 实测 unit 用时重算 Main 预算，预算超过专家书面时间承诺时
不得启动 Main。

校准集兼作计时试点：每位专家的 calibration completion record 必须记录每 unit 实际用时。
若两位 reviewer 的校准中位用时超过 8 分钟/unit，必须在 Pilot R1 开始前完成一次预声明的
scope 缩减修订（注册前修订，产生新协议 SHA），缩减顺序预先冻结为：首先从 Pilot 系统集
移除 E2-B（其 packet 信息风格与 B0 最接近，且 Pilot 目的是手册可用性与一致性、不是系统
比较）；仍超载时再移除 E1 或 E3 之一，但 Pilot 必须始终保留至少一个非词法系统；不得缩减
case 数（30 是 alpha 精度下限）或 Top-5 深度（评价端点）。

专家的时间承诺、报酬或致谢安排记录在 `ExpertStudyRegistryV2` 的私有字段，不进入公开包；
无偿承诺同样必须显式记录，不得默认。

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
- R2 `<0.80`：除下述唯一一次 binary 降级分支适用外，停止，不构建 Main 120。

无论总体 alpha 是否通过，都报告 grade confusion、证据有效性一致率、bridge overall 一致率、
各 mechanism/2D-3D/FB-NB 子组和 undefined bootstrap 比例。不得以裁决后的标签计算 alpha。

另行预声明一个 companion 描述统计：在双方 assessability 均为 `ASSESSABLE` 的 unit 子集上
计算同一 ordinal alpha，并报告双方 `SYSTEM_PACKET_INVALID` 即 `(0,0)` unit 的数量与占比。
companion alpha 不是 Gate；但 Gate alpha 通过而 companion alpha 低于 0.667 时，必须在
agreement 报告中显式量化机械一致对 Gate alpha 的贡献，并完成数据完整性审查后才可继续。

预声明唯一一次 endpoint 降级模式，只在 R2 之后作为最后的确定性分支求值：仅当 R2 的
graded alpha 落在 `[0.667, 0.80)`，且同一批裁决前 raw 标签二值化（grade `>=2` 记 1，其余
含 `SYSTEM_PACKET_INVALID` 记 0）后的 binary Krippendorff alpha `>= 0.80` 时，改用
binary-gain benchmark 继续，否则停止。binary 模式下 gain 为二值，固定分母为五个位置均为
1 的 DCG 值 `2.9484591189`；development promotion 主阈值换算为 `delta >= 0.045`、locked
primary 换算为 `delta >= 0.075`（按最坏情形——全部增益来自 grade 2——与 graded 门槛等效的
保守 1.5 倍放大；增益来自 grade 3 时该换算更严），不依赖 grade 粒度的 guardrail 保持原值。
降级必须公开记录为 endpoint downgrade，此后全部结论只能以 binary utility 表述，不再保留
graded 主假设，不得事后在两种端点间择优；R1 或 R2 的 graded alpha `< 0.667` 时不允许
降级，直接停止。

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
- Main FAILED cell 不生成 review unit 或人工 label；Gold 与 Analysis 固定派生五个
  `SYSTEM_PACKET_INVALID/RUN_FAILED` 零位置，并保留其授权 case-role 分母；
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
raw structure bytes/private artifact URI、structure member/union instances、derivative roster/assignment/
raw review/adjudication/rationale、私有 metadata preimage、blinding key 与运行数据库必须分离、
最小权限保管，不进入 GitHub。
活跃 private root 应在 Git 外通过 0600 `PrivateArtifactEnvelopeV1` 操作包装封存；该通用 payload
wrapper 不是 research-schema root，不能替代被包装的 73-root 类型校验。
公开包只含许可证允许的结构/事实字段、source IDs、查询、manifest、prompt/schema、项目自有
标签、派生指标和 provenance。原始受限 abstracts、文章正文/PDF、ICSD/AFLOW/S2 数据、API key
和 provider transcript 不公开。全部标签密封、locked analysis 解封且许可/隐私复核通过后，
只可发布不含专家顺序/密钥/受限文本的 sanitized mapping 或派生 release，以支持结果复核。

内部 annotation/review/release-control HMAC 只证明相对于预承诺 key commitment
的 exact replay；它们不是外部机构身份、外部密钥托管或外部发布许可。公开结果必须显式保留这些
`NOT_PROVIDED`/false 证据上限，且只能嵌 aggregate projection 与授权/审查引用，不嵌 raw label、
签名值、key commitment、私有 identity map、受限文本或 provider transcript。

专家的公开描述采用预冻结的聚合模板：只公布 reviewer/adjudicator 人数、学科方向、职业阶段
区间（如"博士生/博士后/研究员"）、与平带/窄带主题相关发表经历的有无，以及披露的与项目
负责人关系类别；不公布姓名、机构、可反推身份的字段组合或 pseudonym 与真实身份的映射。
该模板在专家 registry 冻结时一同冻结，揭盲后不得临时改写。

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
