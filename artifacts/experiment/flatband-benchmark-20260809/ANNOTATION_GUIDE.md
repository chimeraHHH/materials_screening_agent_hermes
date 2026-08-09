# 平带/窄带灵感 HypothesisPacket 专家标注指南

状态：v0.2 草案；`PILOT_NO_GO_PENDING_REVIEWER_PROJECTION_AND_GOLD_CLOSURE`

适用契约：当前 content-addressed research contract draft；digest 见
`research_contracts.schema.sha256`。现有 draft 尚不能安全生成 reviewer payload 或 final gold。

本指南用于两位独立 reviewer 和一位 distinct adjudicator。它评估一个受限 evidence packet
是否形成有科学用途、约束兼容、可证伪的平带/窄带研究假设，不评估 novelty，不证明材料
真实存在、可合成或具有已验证平带。

> **独立红队 Gate：** 本指南描述的是目标行为，不代表当前 Schema 已执行这些边界。Pilot 前必须
> 物理拆分私有 identity map 与 reviewer-facing packet；补齐可核验 evidence excerpt/scope、
> bridge 字段、case-level duplicate partition、raw/adjudication/final-gold closure、calibration
> completion 和 COI/recusal。对应反例测试全部通过前，不得给专家发放当前
> `BlindedEvaluationUnitV1` 或完整 `HypothesisPacketV1`。

## 1. 标注时你会看到什么

reviewer 可见：

- 冻结 case：parent、目标 `FB100/NB300`、near-Fermi 窗口、维度和硬/软约束；
- 候选变换及结构身份；
- source domain、mechanism family、shared invariant、target mapping；
- required/breaking conditions、contradictions 和 falsification plan；
- 规范化 work citation/identity、受限且不可变的 evidence excerpt、span type、claim summary、
  scope 和 provenance；来源 adapter/backend 名称不显示；
- 匿名 `blinded_unit_id`。

reviewer 不得看到或推断：

- `system_id`、system config、原始 `selection_rank`；
- 另一位 reviewer 的标签、理由、用时或 aggregate score；
- Pilot/Main 的系统性能、promotion 状态或 locked split 结果；
- novelty/prior-art 分数；
- 私有 identity map、系统提出的 duplicate group 和原始 packet/ranking ID。

本实验是 `identity-masked`，不是可保证完美的 double-blind；生成文本风格仍可能泄漏。统一
deterministic renderer、每位 reviewer 独立顺序和结构化字段用于降低风险。全部标签提交后，
reviewer 另行猜测 system origin；该猜测只评估 masking，不影响 grade。

若界面、导出文件、文件名或 packet 文风泄漏上述信息，立即停止并记录 protocol deviation，
不要继续标注。

## 2. 封闭证据原则

只使用 packet 内提供的证据。不得联网搜索、打开论文正文/PDF、询问系统作者、调用另一个模型，
或用个人熟悉的论文事实替 packet 补证。个人专业知识只用于判断物理逻辑和 scope 是否合理；
若关键事实未在 packet 中得到支持，应标为 unsupported/conditional，而不是凭记忆补全。

数据库的 flat-band tag、候选分数、DOS 峰、kagome/Lieb/line-graph motif 或源论文标题均不是
充分证据。结构 motif 在理想 nearest-neighbor 模型中允许平带，不等于真实材料在全 BZ、
给定 SOC/磁序/U 下满足带宽和 Fermi-window 条件。

## 3. 每个 unit 的固定工作流

按以下顺序作答；不要先给总体分再倒推子项。

0. **pre-run case audit**：在任何系统输出生成前，两位 reviewer 只看 case 独立判断资格，分歧
   由 adjudicator 解决；
1. **case validity**：已通过 pre-run audit 的 case 是否出现新的数据完整性问题？
2. **packet validity**：packet 是否完整、结构变换是否允许、是否存在 hard fail？
3. **evidence links**：逐条判断 relation、scope match 和 overclaim；
4. **bridge**：逐项判断 source mechanism 到 target 的转移链；
5. **relevance grade 0--3**：使用第 7 节的必要条件；
6. **mechanism family**：按物理主因而非关键词归类；
7. **confidence 1--5 与 rationale**：说明最低等级限制因素和最关键反证；
8. 完成同一 case 的全部 unit 后，在独立 case-level partition 界面判断 duplicate；
9. 将 unit labels 和 duplicate partition 作为同一 case batch 提交并密封。提交后不得覆盖。

## 4. Assessability

### `ASSESSABLE`

case 和 packet 足够完整，可以做 0--3 判断。证据可能很弱；“证据弱”通常是低 grade，
而不是 unassessable。

### `SYSTEM_PACKET_INVALID`

case 有效，但该系统 packet 无法作为候选判断，例如：

- 缺结构身份、机制、required/breaking condition、证据或 falsifier；
- 引用了不存在/哈希不一致的 evidence span；
- 变换违反禁止规则或候选结构无效；
- 输出无法解析且不能由冻结规则恢复。

此时 `relevance_grade=0`，至少选择一个 hard-fail reason。缺失的 evidence/bridge/family 字段
保持空，不得为满足 Schema 补造 sentinel 科学判断。不要因为 packet 无效而把整个 case 标为无效。

### `CASE_INVALID`

所有系统都会受到同一问题影响，例如 parent structure/license/hash 无法确认，目标要求内部矛盾，
或缺少任何系统都需要的 case 字段。此状态不填写 candidate-level grade/evidence/bridge/confidence，
必须用 rationale 解释。若只是某个 packet 未使用已有 case 信息，应标 packet invalid。

## 5. Evidence link 判断

每条 evidence link 选择一个 relation：

- `VALID_SUPPORT`：来源内容直接支持 packet 使用的机制/条件，并且材料体系、尺度、observable
  和限定范围匹配；
- `VALID_COUNTER`：来源在匹配 scope 下明确削弱或反驳该 route；
- `CONTEXT_ONLY`：相关背景、同领域关键词或邻近现象，但不直接支持目标 claim；
- `UNSUPPORTED`：claim summary 超过 span、来源/哈希不可核、仅凭标题联想或 scope 明显错配；
- `INSUFFICIENT_PACKET`：提供的信息不足以判断该 link，而不是来源一定错误。

reviewer-facing packet 中的每条 evidence 必须恰好判断一次，不能增删 link，也不能引用其他
case/packet 的 link。`VALID_SUPPORT` 和 `VALID_COUNTER` 必须同时 `scope_match=true`；系统自己的
claim summary 只能帮助定位，最终 relation 必须以不可变 excerpt/metadata scope 为依据。

同时记录：

- `scope_match=true` 仅当 source system、机制、控制变量和 packet 声称的可迁移部分相容；
- `overclaim=true` 当 packet 把相关性写成因果、把局部路径写成 full BZ、把计算写成实验、把
  motif 写成真实平带，或隐去 SOC/磁性/U/尺度等必要限制；
- 同一 DOI 被 Crossref/OpenAlex/OpenAIRE/arXiv 多次解析仍是一条科学证据，不是多票支持。

grade >=2 至少需要一条 `VALID_SUPPORT`、总体 `evidence_valid=true` 且没有 hard fail。

## 6. Bridge 的七个子判断

每项选 `PASS/PARTIAL/FAIL/UNASSESSABLE`，最后选 overall。

1. **source_mechanism**：源领域现象是否被正确描述，而非只共享“flat”等关键词？
2. **shared_invariant**：是否给出跨领域保持的数学/物理不变量，如 connectivity、destructive
   interference、symmetry representation 或局域化约束？
3. **target_mapping**：源变量到目标材料的 orbital/site/hopping/strain/interface 等是否具体？
4. **transferable_control**：是否存在可实施的结构/成分/应变/层间耦合控制量？
5. **required_conditions**：成立所需近似、尺度和电子结构条件是否明确？
6. **breaking_conditions**：哪些 farther-neighbor hopping、SOC、磁序、disorder、hybridization、
   correlation 或额外 sublattice 会破坏结论？
7. **contradiction_handling**：packet 是否呈现并限制相反证据，而不是省略？

overall：

- `CORRECT`：source mechanism、shared invariant、target mapping、required conditions 全为 PASS，
  其余项没有 FAIL；
- `CONDITIONAL`：核心逻辑 plausibly transferable，但至少一项需要明确额外计算/条件；
  source mechanism、shared invariant、target mapping、required conditions 中不得有 FAIL；
- `INCORRECT`：核心映射错误、关键物理不守恒、已有 counter evidence 未处理或必要条件明显不满足；
- `UNASSESSABLE`：所有子项均因 packet 信息不足而不能判断。不能把普通 uncertainty 都填成它。

## 7. 0--3 relevance grade

### Grade 3 — 强、证据匹配、约束兼容、可证伪

必须同时满足：

- packet 有效且不违反任何 hard constraint；
- 至少一条 scope-matched `VALID_SUPPORT`，无 hard fail；
- bridge overall 为 `CORRECT`；
- candidate transformation、required/breaking conditions 和 observable 明确；
- falsification method 及 pass/fail 条件足以区分该机制是否成立。

Grade 3 仍然只是高质量研究假设，不是“已发现平带材料”。

### Grade 2 — plausible、受支持、值得验证

必须满足 evidence-valid、至少一条 `VALID_SUPPORT`、无 hard fail，并给出可执行验证方案。
bridge 可为 `CONDITIONAL`：例如 farther-neighbor hopping 或 SOC 尚未计算，但 packet 明确把它们
列为 required/breaking conditions。若关键映射只有关键词相似，不能给 2。

### Grade 1 — 相关但弱

主题或 motif 相关，但只有 context evidence，跨领域 invariant/target mapping 不完整，关键条件或
falsifier 模糊，或存在未解决的 overclaim。Grade 1 表示可作为早期 brainstorming 线索，尚不足以
进入受证据支持的候选集合。

### Grade 0 — 无效、无支持或明确错误

包括 malformed/invalid packet、禁用变换、硬约束冲突、无任何可用 evidence、核心 bridge 错误、
机制被 packet 内 counter evidence 直接否定、或把 source label 当成目标真实性。

### 降级规则

先应用硬上限：

- `evidence_valid=false` 或无 `VALID_SUPPORT`：最高 grade 1；
- bridge overall 非 `CORRECT`：最高 grade 2；
- 任一 hard fail：grade 0；
- packet 只报告 motif，没有 target-specific orbital/hopping mapping：最高 grade 1；
- 没有具体 falsifier：最高 grade 1；
- 未披露显而易见的 breaking condition：通常最高 grade 2，若会直接推翻核心机制则 grade 0/1。

不要为“想法有趣”跳过必要条件。

## 8. Hard-fail reasons

- `INVALID_STRUCTURE`：候选结构身份/组成/几何无效或无法核对；
- `DISALLOWED_TRANSFORMATION`：使用 case 明确禁止的变换；
- `HARD_CONSTRAINT_VIOLATION`：维度、元素、拓扑或其他硬约束失败；
- `MECHANISM_CONTRADICTED`：packet 内直接反证否定核心机制；
- `NO_USABLE_EVIDENCE`：没有任何可判断且 scope-matched 的 evidence；
- `UNMARKED_INFERENCE`：关键因果/数值由系统补造且未标为假设；
- `MALFORMED_PACKET`：必需字段、引用或结构化格式不可恢复。

多个 hard fail 按 enum 名称排序记录。普通 evidence weakness 不自动等于 hard fail。

## 9. Mechanism family 与 strict duplicate

按 route 的主要物理因果选择一个 family：

- lattice interference；line graph；orbital frustration/hybridization；
- symmetry induced；moire/superlattice；confinement；
- correlation renormalization；strain/interface/defect；
- atomic-orbital localization；other/unknown。

不要仅因结构含 kagome 字样就选择 lattice interference；若 packet 主张的是 orbital-selective
hybridization，应按主因归类。

duplicate 不是逐 unit 自由填写字符串。完成一个 case 的所有独立 grade 后，界面同时展示该 case
的全部匿名候选，以 same/different 决策形成一个完整 partition。候选结构、变换 operator、shared
invariant、target mapping 和关键 required conditions 实质相同时属于同一组；仅改措辞、交换同义
Tag、换一个 metadata 来源或增加同一 DOI provenance 仍是 duplicate。若结构相同但机制和
falsifier 物理上独立，可属于不同组。两位 reviewer 的 partition 先密封，再整体比较/裁决；最终
cluster ID 由成员 packet identities 规范化生成，不能采用系统 proposal 或 reviewer 自选名称。

## 10. Confidence 与 rationale

confidence 只表示你对“本次标签正确”的把握，不表示候选成功概率：

- 5：规则直接决定，证据和 bridge 清晰；
- 4：结论清楚，只有非决定性不确定性；
- 3：两个相邻 grade 均有理由，但当前证据略偏向一个；
- 2：packet 信息弱，标签高度依赖 scope 解释；
- 1：勉强可判断，应优先审查手册/packet 设计。

rationale 至少写明：决定 grade 的最弱必要条件、最强 support/counter link、以及下一项能使标签
上升或下降的计算/证据。不要写系统名称、猜测排名或 novelty 评价。

## 11. 示例

以下示例为本项目编写的教学例，不进入 calibration、Pilot 或 Main。

### 示例 A：Grade 3

packet 引用 photonic Lieb lattice 中 destructive interference 的直接 evidence，明确把连通性和
compact localized state 映射到目标电子 sublattice，候选变换保留目标维度和 graph，列出
farther-neighbor hopping/SOC 为 breaking conditions，并要求 SOC-resolved Wannier/full-path
band tracking 检验 `W<=0.10 eV`。证据 scope、bridge、约束和反证均闭合，可给 3。

### 示例 B：Grade 2（边界）

packet 对 orbital frustration 的来源与 target orbital mapping 有直接支持，并提出 DFT/Wannier
验证，但目标材料的 SOC 与 magnetic order 尚未知；二者被显式列为条件，且失败条件明确。
这是值得验证的 conditional bridge，可给 2，不能给 3。

### 示例 C：Grade 1

packet 看到 parent 含 kagome motif，并引用另一材料的 kagome flat-band 标题，但未说明 active
orbital、hopping hierarchy、Fermi alignment 或真实 target mapping，只建议“做能带计算”。证据为
context、关键词转移，不足以支持 route，给 1。

### 示例 D：Grade 0

packet 把声学谐振器中的局域模直接称为目标材料的电子平带，未给变量映射；同时候选删除了 case
要求保留的全部 parent sites，证据 span 只支持声学现象。存在 disallowed transformation、hard
constraint violation 和错误 bridge，给 0。

### 示例 E：source label 不是 gold

来源页面把一个材料标为 flat-band candidate，但只报告非 SOC 高对称路径。packet 将其写成
“full-BZ SOC-protected isolated flat band”。应标 overclaim；若没有其他 valid support，最高 grade 1。

### 示例 F：duplicate

两个 packet 使用相同 candidate structure、同一 substitution operator、同一 destructive-
interference invariant 和同一验证方案，仅分别引用 Crossref 与 OpenAlex 对同一 DOI 的记录。
它们属于同一 strict hypothesis group。

## 12. Reviewer 分歧与 adjudication

reviewer 必须先独立提交，不能讨论个案。系统只向 adjudicator 展示两个已密封 raw annotations、
packet 和同一指南；仍隐藏 system/rank。adjudicator：

1. 明确 disagreement fields；
2. 逐项引用本指南规则，不按多数或平均分裁决；
3. 形成 final assessability、逐 link evidence、grade、bridge、family、hard-fail 和 case-level
   duplicate partition；
4. 给出固定 reason code 和 rationale；
5. 不删除、不编辑原始 review。

两位 reviewer 完全一致的 unit 不创建 adjudication record，但 final-gold assembler 必须生成一条
`AGREED_RAW` 记录并绑定两份 raw SHA；存在分歧时生成 `ADJUDICATED` 并精确绑定真实 raw records、
registry、guide、masked packet 与时间顺序。若 case invalid 或无法在 packet 内解决，使用对应
`CASE_INVALID/UNRESOLVABLE`，不得查询外部事实后补裁决。`UNRESOLVABLE` 在指标中固定 grade/
evidence gain 为 0；比例超过全部 pooled units 的 5% 时停止主分析。

## 13. 校准与 Pilot agreement

正式注册前，三位专家绑定同一指南 SHA 和一个与所有 benchmark connected components 不重叠的
calibration set SHA。每位专家产生内容寻址 completion record，绑定 role、guide/set、讨论前 raw
answer SHA 和完成时间。两位 reviewer 独立作答后才讨论；任何手册改动产生新 SHA，旧
calibration completion 失效，需重新确认。另在系统结果生成前冻结 work/case-level COI map、
recusal 与替补专家；不得让作者识别后的临时回避静默缩小某系统分母。

Pilot alpha 只用裁决前两位 reviewer 的 raw 0--3 grade，并按 case cluster bootstrap。所有 unit
必须双人完成；单边 CASE_INVALID、缺失 completion 或 final-gold coverage 不全使该 round fail closed，
不能被 alpha 实现静默删除：

- R1 `alpha>=0.80`：通过；
- `[0.667,0.80)`：只许修改手册，在不重叠的 30-case R2 复测，R2 必须 `>=0.80`；
- `<0.667` 或 R2 `<0.80`：停止扩展。

不得用裁决后标签、删除难例、只保留两人都完成的“容易 subset”或重新定义 grade 来提高 alpha。

## 14. 数据与行为安全

- 不把受限 abstract、正文、PDF 或私有 blinding map 复制到公开 rationale；
- 不上传 packet 到第三方模型/云服务；
- 不把 pseudonymous expert ID 与公开身份在 benchmark 文件内关联；
- 发现 license/provenance/hash 漂移立即停止；
- 发现指南无法覆盖的新冲突先标记，不私自改规则；
- novelty、专利、合成成功率和“已发现新材料”均不在本任务范围。

## 15. 提交前检查

每个 `ASSESSABLE` unit 提交前确认：

- [ ] 我没有看到或使用 system/rank/peer label；
- [ ] 我只使用 packet 内证据；
- [ ] 我逐条判断了 evidence scope 和 overclaim；
- [ ] 我先判断 bridge 子项，再给 overall 和 grade；
- [ ] grade >=2 有 valid support，grade 3 有 correct bridge；
- [ ] hard fail 与 grade 一致；
- [ ] 我已完成整个 case 的匿名 duplicate partition，并按物理 route 而非措辞判断；
- [ ] rationale 写了限制和 falsifier，不含 novelty/真实性结论；
- [ ] confidence 表示标注把握，而不是候选成功概率。
