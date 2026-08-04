# Agent 01：Materials Project 材料检索与确定性筛选实施计划

- 版本：v0.4
- 日期：2026-07-25
- 最近进度更新：2026-07-26
- 工期：总计划 Day 3–5，约 18–21 小时
- 依据：[`docs/system-plan.md`](../../docs/system-plan.md)、[`docs/architecture.md`](../../docs/architecture.md) 与 [`Orchestrator 计划`](material-screening-orchestrator-plan.md)

## 开始开发前必读

- [README](../../README.md)：当前 Agent01 CLI、数据源、测试和 Artifact 限制。
- [系统蓝图](../../docs/system-plan.md)：证据等级、Materials Project 数值边界和安全 Gate。
- [技术架构 Agent01 边界](../../docs/architecture.md#53-agent-01公开数据库检索与确定性筛选)：模块职责、公共控制契约和下游关系。
- [主计划](../master.md)：当前状态、跨 agent 依赖和系统验收。
- [Orchestrator 计划](material-screening-orchestrator-plan.md)：StageRunner 生命周期与控制面适配边界。
- [原始系统总方案](../../docs/system-plan-original.md)：仅用于历史追溯。

### 模块职责与边界

- **职责：** Materials Project 查询、原始响应归档、结构规范化、确定性硬约束、去重/聚类、排序、Candidate manifest、报告和 provenance。
- **输入：** 已确认且不可变的 Requirement revision、查询 policy、MP 凭据（仅环境/secret store）和可校验 Artifact 引用。
- **输出：** 带 URI/hash 的原始数据、结构、PropertyValue、`PASS/REJECT/UNCERTAIN/FAILED` 判定、manifest、StageResultEnvelope 和报告。
- **不负责：** ML/DFT/多体计算、LLM 决策、生成新结构、替换实验值或改变上游科学阈值。
- **不可修改范围：** Agent02 的 pre-filter/适用域、Orchestrator 控制契约、冻结 fixture、公共依赖和其他 agent 计划。

当前没有独立 integration Markdown；Materials Project 外部数据源边界以本计划和[技术架构](../../docs/architecture.md)为准。

### 当前下一步、依赖与阻塞

1. 为逐页 cursor checkpoint 写一个独立的分页契约/遗漏与重叠测试，并在测试通过前保持现有 chunk 语义。
2. 为大规模 StructureMatcher 性能优化建立可重复 benchmark，先记录基线再实现优化。
3. 将并行结构分析、dimensionality 交叉验证和 Similarity endpoint 对照分别拆成独立可回退任务。
4. 扩充科学 silver set、多数据库 Adapter 和扫描上限提升审批；每项都必须保留 provenance 和原有状态语义。

### 当前任务：MP 过渡金属二维平带的有界深筛执行闭环（2026-08-03）

范围：将已确认的 20 候选 MP 自适应筛选从仅声明深端点修正为实际执行；Summary 返回的
记录先按冻结的过渡金属条件预筛，再按稳定 `material_id` 顺序限制为用户批准的
`deep_screen_limit`，避免将数千个 pymatgen Structure 常驻内存。对每个入选材料归档
uniform/line band structure、oxidation-state、robocrys/bonds 的可序列化响应，并仅以本地
确定性函数生成带宽、价态、交点风险、连通性和 vdW 正向文本 proxy。费米窗内“第一条带”
与投影轨道占比在 MP 当前 Agent01 能力目录中仍不可验证，必须保留为 `UNCERTAIN`，不得
发布为满足全部用户条件的 PASS。

验收：深端点在 runner 中真实调用且每项原始响应有 immutable Artifact；预算外 Summary
记录和扫描截断写入 warning/report；缺失或不支持证据 fail closed；离线单元/集成回归、
完整 Gate、`pip check`、`git diff --check` 通过。实现后需用新冻结 plan/hash 重新请求
用户批准，不复用先前未完成运行的 plan。

实现结果（待重新冻结的真实 MP run）：

- [x] `MaterialsProjectAdapter` 与 fixture adapter 增加有界 deep-screen 调用；uniform/line
  band structure 保留为运行时对象用于特征计算，同时将无凭据的可序列化端点响应归档；
- [x] Runner 以 Summary 的过渡金属预筛后稳定 `material_id` 顺序选取最多 20 个，未选记录
  在 warning 和 `scan_truncated` 中显式呈现；不再将所有原始 Summary 结构保留在内存；
- [x] 深筛在 hard evaluation 前附加带宽、常见价态、层状、交点风险、TM 子晶格连通性及
  vdW 正向文本 proxy；端点失败或缺字段保留 missing/uncertain，不放宽筛选；
- [x] 未映射的费米窗首带身份与投影轨道阈值现在以 `MP_EVIDENCE_UNSUPPORTED` 进入每条
  Candidate 的缺失证据，阻止其成为 PASS；
- [x] 离线 Gate `464 passed, 9 skipped`、新增/相关单元 `27 passed`、`pip check` 和
  `git diff --check` 通过。尚未运行新的 live MP 深筛，必须先为新的 Run ID 冻结计划并重新
  绑定人工批准。
- [x] 新计划已冻结在临时 Artifact Store：Run `tm-flat-band-v2`，plan SHA-256
  `da24381bb7171cb61da9613e06419c486dd7bba84e67ed5e4c358de75faf3add`，idempotency key
  `debcc04ea7e43797cb5f605084b2f9a05cc22eaab0285f66eda38c6c4ef51570`，MP database
  version `2026.04.13`；待人工批准后才调用 Summary 与五个深端点。
- [x] 已批准的 live MP 深筛以干净 Run `tm-flat-band-v3` 完成：复用同一 database version
  与 query fingerprint 的 5,000 条不可变 Summary 原始响应；过渡金属预筛后有 2,932 条，
  按已批准的 20 条预算实际执行。20/20 在结构维度硬约束失败（19 条为 3D、1 条为 1D），
  因此没有发布候选。所有 uniform/line bandstructure 与 robocrys 请求分别以
  `KeyError`/`OSError`/`TypeError` 或 MP REST 错误不可用，故带宽、交点和 vdW proxy 保持
  缺失；这不是这些性质的负结论。两个用户指定但目录不支持的条件仍保留为 missing。
  Stage 为 `PARTIAL`，完整审计、原始端点响应、报告和 operation record 已写入临时
  Artifact Store；不得把该批的零发布解读为全数据库无候选。

流程修复（2026-08-03，尚未重跑 live MP）：

- [x] v3 审计发现 `deep_screen_limit` 曾在层状 hard constraint 前按 material ID 截断，
  技术运行完成但科学上无效；现改为对全部过渡金属 Summary 记录进行不保留 Structure 的
  维度预筛，并归档完整的 `adaptive_layered_prefilter.jsonl`，再对保留的 2D 记录消耗深筛
  预算；
- [x] 修复 mp-api 0.45 的 robocrys 调用：按 material ID 必须使用 `search_docs` 而不是
  keyword-only 的 `search`；
- [x] 新增“层状预筛必须先于深筛预算”回归测试；完整离线 Gate `466 passed, 9 skipped`，
  `pip check`、`git diff --check` 通过；
- [ ] 该修复改变了候选选择集合，必须以新的 Run ID 重跑一次已批准的最多 20 个深筛，不能
  用 v3 的 20 个 3D/1D 记录代表修复后的结果。

重跑结果（2026-08-03）：

- [x] 修复后的 v4 使用同一版本的冻结 Summary：2,932 条过渡金属记录完成无常驻
  Structure 的维度预筛，保留 209 条 2D；确定性选取其中 20 条做深端点访问，余 189 条
  明确为未深筛，v3 的任意 3D/1D 选择问题已消除；
- [x] robocrys `search_docs` 已成功返回结构描述；但 20 条的 MP line/uniform bandstructure
  请求分别返回 `OSError`/`KeyError`/`MPRestError`，不能取得 W 或交点证据；部分 bonds
  同样不可用。氧化态端点有响应但某些记录的 possible valences 为空，按缺失处理；
- [x] v4 写入完整 prefilter、candidate audit、端点原始响应和 operation record，结果为
  `PARTIAL`、20 条 `UNCERTAIN`、0 条 PASS/发布。此时 Agent01 流程已在结构筛选、预算和
  失败闭环上跑通；没有材料可在现有 MP 端点证据下满足全部严格条件。

跨 agent 依赖：Agent02 只消费 Agent01 发布的不可变 manifest/结构/性质 provenance；Orchestrator 只依赖冻结的 Agent01 原生 Envelope。当前无实现阻塞，P1 性能/扩展项不应改变 P0 契约或把 `REJECT` 重新发布为候选。

### 当前任务：多数据库性质覆盖与 Agent01 判定边界（2026-08-03）

范围：基于已有单来源 Adapter 启用 C2DB、NOMAD、MC3D、TQC 与 NIMS SuperCon 的可审计
筛选；每个来源仅用其自身返回字段以及对其 canonical structure 执行的确定性结构计算。
禁止跨库补值，禁止在无 band dispersion、投影轨道或氧化态数据时把平带、费米窗首带、
轨道贡献、价态或交点条件判为通过。C2DB 的 GPAW/PBE band gap、凸包距离、层群和磁性
标签应作为 L1 数据库证据保留；所有有结构来源继续计算结构维度。真实 Agent02 仅在既有
独立 worker 配置和适用域 Gate 已满足时才可作为 L2 后续复核；Agent03/04 mock 不得用于
补齐科学性质。

实现进度：

- [x] 新增 `source_property_coverage.json` Stage Artifact，按来源写入可在 Agent01 判断的
  native/structure-derived 性质、方法及明确不判断的电子结构性质；
- [x] C2DB 的 `layer_group`、磁性标签被规范化为带结构 task provenance 的 PropertyValue；
  PBE band gap、凸包距离与金属性沿用既有 C2DB table 映射；
- [x] 新增 C2DB coverage、native property 与 NOMAD Runner Artifact 单元覆盖；完整离线
  Gate 为 `469 passed, 9 skipped`，`pip check` 和 `git diff --check` 通过，未进行跨库值
  填充或证据升级；
- [x] NOMAD public archive release gate 曾真实通过（结构、解析 band gap 与 entry-specific
  method provenance）；随后的重复请求在 DNS 解析阶段失败，记录为外部瞬时不可用而非零
  结果。C2DB `/help` 可达，但本次 Python metadata 探针超时；两者均没有取得可用于本任务
  的 band dispersion、轨道投影或氧化态证据。
- [x] 非 MP 来源在 `metadata` 网络失败前也会持久化其
  `source_property_coverage.json` 并把它列入 retryable failure 的 Artifact；因此外部网络
  故障不会掩盖 Agent01 的来源性质边界。针对 TQC metadata timeout 的注入测试已覆盖。
- [x] TQC 真实只读探针验证 v4 Fe 搜索与一个 ICSD detail：详情含 CIF、拓扑分类、
  `nbrFermiCrossing`、`smLineCrossing` 与 crossing type，但没有能量—k 数组、轨道投影或
  氧化态。将 crossing count/label 作为 L1 诊断 PropertyValue 接入；因无法识别交叉属于
  哪条能带，严格无交叉条件仍保持 Agent01 不判断。
- [x] 发布策略更新：数据库证据缺失导致的 `UNCERTAIN` 与 `PASS` 一同进入
  `candidate_manifest.jsonl`（排序在 PASS 之后），携带完整 `missing_evidence` 与 L1 ceiling；
  仅明确 `MISMATCH` 的 `REJECT` 和 `FAILED` 记录被阻断。Agent01、NOMAD 与 Agent02/
  Orchestrator 契约回归 `48 passed`。
- [x] MP oxidation-state 端点返回的 `average_oxidation_states` 现被保守解析：每个 TM 的
  整数平均价态属于 pymatgen 常见价态时可作为 L1 `oxidation_common=True`；分数平均或
  非常见平均价态不被冒充为混合价态证明，继续 `MISSING`。对冻结的 20 条二维 TM replay
  得到 10 条价态通过、10 条不确定；完整 Gate `474 passed, 9 skipped`。

完成每个任务后，只在本计划记录实际完成项、测试证据、限制和对 Agent02/Orchestrator 的影响；公共契约或阈值变更需先同步相关 agent plan、fixture、contract test，并按职责更新主计划或技术架构。

### 凭据解析修复（2026-07-30）

范围：使真实 `MaterialsProjectAdapter` 与 Stage 0 LLM 保持一致的秘密来源策略，避免
用户已经在 macOS Keychain 保存 MP 凭据后仍需手动导出环境变量。优先顺序固定为：显式
Adapter 注入（仅测试/集成）→ `MP_API_KEY` → Keychain（服务
`material-screening-agent-mp-api`、账户为当前用户）。失败信息不得包含密钥、Keychain
输出或命令 stderr；不得改变 Agent01 public contract、Artifact 或 provenance。

验收：覆盖优先级、Keychain 无 shell、缺凭据 fail-closed；相关 MP contract/E2E 测试、
完整离线 Gate、`pip check`、`git diff --check` 通过。非 macOS 平台继续要求由其 secret
store 注入 `MP_API_KEY`。

实现结果：

- [x] `MaterialsProjectAdapter` 按显式注入 → `MP_API_KEY` → macOS Keychain 解析，默认
  Keychain service 为 `material-screening-agent-mp-api`、账户为当前用户；解析保持惰性；
- [x] Keychain 调用使用参数数组而非 shell，限制 5 秒；缺失、空值或调用失败均 fail closed，
  对外错误不含 secret 或 Keychain stderr；
- [x] 增加优先级、Keychain 参数和缺失凭据的离线 contract 测试；README 已同步非 macOS
  注入边界；Agent01 contract、Artifact/provenance 与查询语义未变；
- [x] 相关测试 `19 passed`；完整离线 Gate `429 passed, 9 skipped`；`pip check` 和
  `git diff --check` 通过。未运行需网络的 `live_mp` release Gate。

### TQC 真实检索回归修复（2026-07-30）

范围：修复真实 TQC Bi/Te Run 暴露的三个 Agent01 边界问题：(1) CIF 中带氧化态的
`Species` 必须规范化为元素符号，不能将 `Bi3+`/`Te2-` 误判为缺少 `Bi`/`Te`；(2) 对
明确命名为 `Topological materials` 的 L1 target，使用 TQC provenance 的非 trivial 分类、
SOC 标记和非空拓扑指数建立保守的数据库标签判定，保留其 L1 ceiling；(3) TQC search
绝不能超过冻结 `max_records_scanned`。不得把 TQC 标签提升为实验、ML、DFT 或拓扑证明，
不得更改 MP v1 fixture 或跨库契约。

验收：新增 TQC Adapter/Runner 离线回归覆盖氧化态元素、target 的 `PASS` 与 trivial 的
`REJECT`、以及跨 `similarICSD` item 的严格上限；相关 source/Orchestrator 测试、完整离线
Gate、`pip check`、`git diff --check` 通过。对已有 `tqc-dialog-001` 的 Artifact 不做
就地改写；修复后使用新的 Run ID 重新联网验证。

真实验证补充：TQC 网络连接失败发生在 Agent01 `prepare`（metadata）阶段时，控制面必须
把该已尝试编号写入 retry counter；否则用户批准重试会将不同 operation key 写入同一个
不可变 stage-attempt。此修复只纠正 attempt 计数与审计，不能掩盖网络失败或重复使用先前
的数据库结果。

实现结果：

- [x] TQC 从 CIF Structure 提取元素时兼容 `Element` 与带氧化态 `Species`，统一发布裸
  元素符号；Bi/Te 过滤不再被 `Bi3+`/`Te2-` 误拒绝；
- [x] `Topological materials` L1 target 仅在 TQC 记录同时具有非 `trivial` 分类、`soc=true`
  和非空拓扑指数时通过；`trivial` 标签明确 `REJECT`，缺任一字段保持 `UNCERTAIN`；
  这只是 TQC 数据库标签，不提升至实验、ML、DFT 或拓扑证明；
- [x] TQC 在一个 `similarICSD` item 达到上限后停止外层遍历，并在详情请求前再次切片，
  保证不超过 `max_records_scanned`；
- [x] Agent01 prepare 阶段的 retry 记录 attempt，重试不会因复用 attempt 1 的不可变
  operation key 失败；
- [x] source/runner/Orchestrator/contract 回归与完整离线 Gate 为 `438 passed, 9 skipped`，
  `pip check`、`git diff --check` 通过；
- [ ] 使用原冻结 DeepSeek Requirement 的两个新真实 TQC Run 均在远端 metadata 调用返回
  `ConnectionError`；第二次 Run 的人工 retry 已进入 attempt 2 且安全暂停，证明重试
  控制修复有效，但不构成成功的联网候选复验。

### 当前任务：P1 单数据源 NOMAD 接入（2026-07-29）

范围：

- 单次 Agent01 Run 只允许选择一个数据源：`materials_project` 或 `nomad`；
- Materials Project 继续作为默认来源，既有 `agent01-contract-v1`、冻结 fixture、
  query fingerprint 和 CLI 行为保持可重建；
- NOMAD 使用公开只读 REST API、`owner=public` 和
  `/entries/archive/query`，不引入 NOMAD Python 客户端或新依赖；
- NOMAD 原始 entry 必须在 Adapter 内映射为 Agent01 的规范化输入，结构从解析后的
  topology/atoms 引用读取，长度从米显式换算为 Å，能量从焦耳显式换算为 eV；
- NOMAD 不存在可直接等同于 Materials Project `energy_above_hull` 的统一来源字段，
  因而该性质保持缺失并按既有 `UNCERTAIN` 语义处理；不得推测或跨库补值；
- NOMAD 结果使用新的 `agent01-contract-v2` Envelope/Candidate，旧 v1 模型和 fixture
  继续只描述 Materials Project；Agent02 转换器只增加读取 v2 的兼容性，不修改
  Agent01 权威记录或提升证据；
- Orchestrator 只记录和传递显式 source selection，不改变控制面状态机、SQLite
  migration、审批、路由顺序或 checkpoint schema。

依赖：

- 复用主环境已锁定的 `requests` 和 `pymatgen`；
- 真实 NOMAD release test 必须保持显式 opt-in，默认测试使用 fake HTTP response，
  不访问网络；
- NOMAD 公共 API 没有稳定数据库 release snapshot，必须记录 API 版本、endpoint、
  entry ID、parser/method/program 和获取时间，并在报告中声明该限制。

验收标准：

1. 默认 MP 相关 unit、contract、fixture、integration 和 E2E 输出无回归；
2. NOMAD Query Plan 对元素和可支持的 band-gap 范围做确定性下推，其他约束明确留给
   本地复核；
3. NOMAD Adapter 覆盖 API 版本、游标分页、结构/单位映射、schema drift、429/5xx、
   零结果和扫描截断；
4. standalone CLI 与 Orchestrator Run 均能显式选择 `nomad`，且同一 Run 不混合来源；
5. 每条 NOMAD Candidate 使用 source-qualified ID，保留 entry/parser/method
   provenance、结构 URI/hash 和 `L1_RETRIEVED` ceiling；
6. 相关测试、完整离线 Gate、`pip check` 和 `git diff --check` 通过，并在本节记录
   实际测试证据与剩余限制。

实现结果：

- [x] `material-agent retrieval` 与 Orchestrator `run` 均支持
  `--source materials_project|nomad`，默认 MP；source selection 写入 Run state，
  Runner factory 每次只构造一个 Adapter；
- [x] 新增 NOMAD public Archive Adapter、查询计划、OpenAPI 版本快照、稳定游标分页、
  schema 校验、结构米→Å、band gap J→eV、method/parser provenance 和受控重试；
- [x] NOMAD Candidate/Envelope 使用 `agent01-contract-v2` 与 source-qualified ID；
  MP v1 Candidate/Envelope、ID namespace 和冻结 fixture 逐字节重建测试未改变；
- [x] NOMAD 没有被安全映射为 MP `energy_above_hull` 的字段；该值固定保留缺失，
  相应硬约束得到 `MISSING`，Candidate 为 `UNCERTAIN`，不跨库补值；
- [x] Agent02 loader 增加 v2 只读兼容；Orchestrator 报告按所选来源声明 L1，
  控制契约、审批和 SQLite migration 未改变；
- [x] fake HTTP unit、standalone CLI、Orchestrator integration、MP v1 contract/frozen
  fixture 与 Agent02 兼容回归通过；完整离线 Gate 为
  `392 passed, 9 skipped`，`pip check` 为 `No broken requirements found`，
  `git diff --check` 通过；
- [ ] 新增 `live_nomad` 发布探针默认跳过，本次未执行；实现前通过一次受限公开查询
  验证了元素、band-gap 查询语法和 archive 映射，但这不替代正式 release Gate。

剩余限制：

- NOMAD 公共 API 只记录当前 API version，没有 MP 式稳定数据库 release snapshot；
- 当前结构映射只接受可解析且三轴周期的 resolved topology atoms；缺失或非周期结构按
  既有结构失败/不确定语义处理，不猜测晶格；
- 多个 NOMAD band-gap 记录按
  `minimum_nonnegative_reported_gap-v1` 选择最小非负值并保留选择 provenance；
- 尚未实现跨库合并、fallback 或去重；这是“单次运行选择单一来源”的刻意边界。

### 当前任务：P1 公共数据库扩展（2026-07-30）

范围：

- 保持“单次 Run 只选择一个来源”，在现有 Materials Project/NOMAD 之外接入
  `mc3d`、`c2db`、`topological_quantum_chemistry` 和受限的
  `nims_supercon`；
- MC3D 使用公开 OPTIMADE 1.2 结构接口；C2DB 使用官方只读检索页和逐材料 JSON
  下载；TQC 使用站点公开配置所指向的 v4 搜索/v1 详情 API；
- NIMS 用户给定链接是 MDR SuperCon Datasheet，不包含晶体结构。允许检索和归档
  版本化元数据，但必须将记录标记为结构缺失、不得发布给 Agent02；
- Atomly 当前公开说明 API 仅供内部测试和合作者使用。在取得授权 API 文档与凭据
  前不实现网页反向工程或批量抓取，并把该项记录为外部阻塞；
- 新来源均复用 `agent01-contract-v2`，不修改 MP v1 冻结 fixture，不跨库补齐缺失
  性质，不把拓扑分类、SuperCon 文献 Tc 或数据库 DFT 值提升为更高证据等级。

依赖：

- 只复用主环境已锁定的 `requests`、`pymatgen` 和 Python 标准库，不新增依赖；
- 所有默认测试使用 fake HTTP response，不访问网络；live 探针若增加必须显式 opt-in；
- 外部 API/网页没有稳定 release snapshot 时记录 API/数据集版本、endpoint、许可和
  获取限制，schema drift 时 fail closed。

验收标准：

1. CLI 与 Orchestrator 能显式选择新增来源，且同一 Run 不混合来源；
2. MC3D/C2DB/TQC 的元素、结构和可安全映射性质保留来源、单位、方法与 L1 ceiling；
3. 不支持的硬约束保持本地 `MISSING/UNCERTAIN`，NIMS 无结构记录为 `FAILED` 且不发布；
4. 覆盖查询构造、分页、结构映射、schema drift、零结果、429/5xx 和来源身份测试；
5. MP v1 frozen contract、NOMAD v2 与 Agent02 兼容性无回归；
6. 相关测试、完整离线 Gate、`pip check` 与 `git diff --check` 通过，并记录真实证据。

实现结果：

- [x] 新增 `mc3d`、`c2db`、`topological_quantum_chemistry` 和
  `nims_supercon` source selection、policy、CLI/Orchestrator factory 与报告标签；
- [x] MC3D 使用 PBE-v1 OPTIMADE 1.2 分页结构查询；真实受限 probe 返回并解析 100 条
  Si/O 结构，带隙、凸包能和金属标记保持缺失；
- [x] C2DB 使用官方 search session、确定性分页和逐 UID ASE JSON；真实固定 Si/O
  条件为零结果，补充 Mo/S probe 成功映射 25 条结构、PBE gap/ehull；
- [x] TQC 使用公开配置所指向的 v4 search/v1 detail，保存 ICSD、SOC、拓扑分类和
  指数 provenance；历史无效 CIF 按单记录结构失败处理；
- [x] NIMS SuperCon 固定 DOI `10.48505/nims.4487`/Ver.240322，解析双行 TSV header；
  真实 probe 找到 9 条 Si/O 记录，全部明确无原子坐标并通过测试证明不发布下游；
- [x] Atomly 未接入：公开站点说明 API 仅供内部测试和合作者使用，当前缺授权 API
  文档/凭据；未反向工程私有接口或批量抓取；
- [x] 新增 fake HTTP/schema/映射、结构缺失下游阻断和全部非 MP CLI source 测试；
  完整离线 Gate `423 passed, 9 skipped`，`pip check` 无破损依赖，
  `git diff --check` 通过。

剩余限制：

- TQC 真实 25 条 probe 在多次详情请求后被远端代理断开；缩小到 3 条重试仍被该代理
  拒绝。此前已验证搜索与单条详情 schema，离线映射测试通过，但正式 live release
  Gate 尚未建立；
- C2DB/TQC 是官方站点的版本化/只读接口，但没有可验证的不可变数据库 release；
  默认扫描上限分别保守设为 200 和 25，并在达到上限时报告截断；
- NIMS SuperCon 是性质/文献数据表而非晶体结构库，当前只用于检索审计，不是
  Agent02 候选来源；
- 尚未实现跨库联合检索、fallback 或跨库去重；每个 Run 继续只冻结一个来源。

### 外部来源接入核查：Atomly 与 SpringerMaterials（2026-07-30）

结论：本次未新增 Adapter 或 source enum，避免将网页抓取误包装成公开数据接口。

- Atomly 已由用户在 macOS Keychain 注入授权 API Key；受限只读 probe 验证
  `POST /api/matdata/search_by_formula/`、`Authorization: token …` 和
  `POST /api/matdata/get_struct_detail/` 可用。前者返回未命名位置数组，后者返回
  `output_structs`、lattice、band\_gap、decomposition、run\_type 等结构/性质对象。
  但 `POST /api/matdata/search_by_elements/` 对 `{"include":["Si","O"]}` 和
  `{"include":"Si,O"}` 均返回 HTTP 500，公开页面没有元素检索 body、结果列含义、
  分页或 `output_structs` 内 CIF 路径的 schema；不得猜测字段含义或以公式检索替代
  通用元素约束。
- SpringerMaterials 是受许可访问的材料数据库。Springer Nature 公开 API 覆盖文献
  metadata/open-access 内容，而非可供 Agent01 使用的 SpringerMaterials 结构/性质
  检索契约；官方说明的 TDM 使用必须遵循许可并取得相应 API/TDM 条款。

Atomly 后续依赖：提供方需给出 `search_by_elements` 的有效请求/响应 schema 并修复
500，明确公式检索数组列名、分页和限流、`output_structs` 的 CIF 路径以及 band gap /
decomposition 的单位与方法；随后才能制作脱敏离线 fixture 并实现 Adapter。Springer
Materials 仍需数据提供方或机构的书面自动化/TDM授权、版本化 API/OpenAPI 或等价
schema、认证/secret-store 交付、查询/分页/限流语义、结构与单位映射示例、可归档与
再分发范围及授权测试样本。两者在满足各自依赖前保持不可选择，不能用浏览器 cookie、
页面 HTML 或下载按钮替代正式 API。

### 当前任务：P1 数据源确认（2026-08-05）

范围与验收：移除 `auto` 数据源推断。CLI 的 `run` 与 standalone `retrieval`
必须接收用户明确确认的具体 `--source`，并将其冻结进 checkpoint、Agent01 query
plan 与报告；LLM 推荐仍是建议，必须由用户把推荐结果转成具体 source 后才能执行。

实现结果：

- [x] 删除 `auto` CLI 选项和按 `target_class` 推断数据库的代码路径；
- [x] 显式 source 选择继续保持单来源、不可跨库补全；
- [x] `run` 与 `retrieval` 的 `--source` 改为必填，直接 API 仍保留 MP 默认以兼容
  已冻结的程序化调用；待提交前运行完整离线 Gate。

剩余限制：这不是多库联合检索。实现“同一 Run 返回多个数据库结果”需要新的聚合
Envelope、每来源 Artifact namespace、候选去重/冲突政策和下游 manifest 版本，必须单独
设计并协调 Agent02/Orchestrator 公共契约后再做。

## 0. 当前实施进度

当前状态：**Agent 01 P0 与第 8 节增强 Gate 已完成；Materials Project 公共契约继续
冻结为** **`agent01-contract-v1`。P1 已完成 NOMAD 单来源接入并发布
`agent01-contract-v2`，其他性能和扩展项尚未开始。**

### 0.1 已完成

- [x] 在当前目录初始化独立 Git 仓库，当前分支为 `main`；
- [x] 建立仓库本地 Python 3.11.13 `.venv` 隔离环境；
- [x] 固定直接依赖和完整传递依赖，核心版本为 `mp-api==0.45.15`、`pymatgen==2025.10.7`、`pydantic==2.12.5` 和 `emmet-core==0.86.2`；
- [x] 建立 `material-agent retrieval` CLI、Pydantic 输入输出模型和 StageResultEnvelope；
- [x] 实现 Requirement 校验、Query Plan、query fingerprint、MP 查询参数下推和本地约束复核；
- [x] 实现真实 Materials Project Adapter 与不依赖 API key 的离线 fixture Adapter；
- [x] 实现公开只读 NOMAD Archive Adapter、单 Run 单来源选择和 NOMAD v2 契约；
- [x] 实现 database version snapshot；兼容固定版本客户端的 `get_database_version()` 接口；
- [x] 实现受控查询、瞬时错误重试、schema drift 检测、零结果和扫描截断状态；
- [x] 实现 gzip JSONL 原始响应归档、manifest、原子写入、hash 校验和 artifact 路径保护；
- [x] 实现结构解析、source JSON、canonical CIF、稳定 candidate ID、structure ID 和 query ID；
- [x] 实现 summary/structure 一致性检查、CrystalNN + Larsen 维度分析及显式质量标记；
- [x] 实现 band gap、energy above hull、is\_metal 等性质的 origin task 解析和降级状态；
- [x] 实现 `PASS/REJECT/UNCERTAIN/FAILED` 决策、闭区间容差和缺失证据处理；
- [x] 实现精确重复标注、非破坏性 StructureMatcher 相似聚类和确定性排序；
- [x] 实现全量审计账本、下游 candidate manifest、JSON/Markdown 报告和幂等结果复用；
- [x] 实现 resume 时对数据库版本、Requirement revision 和 policy 变化的拒绝检查；
- [x] API key 仅通过 `MP_API_KEY` 读取，未写入源码、配置、报告或其他项目产物；
- [x] 清理真实 API 运行产物、Python/pytest 缓存和安装元数据；运行目录可在下次执行时自动重建。
- [x] 创建首次 Git 基线提交 `bcaf8fc`，`.venv`、`workspace/`、环境文件、密钥和缓存均未进入版本控制；
- [x] 将 Requirement URI、SHA-256、revision 和规范化内容作为真实输入完整性边界，失败时不调用外部 Adapter；
- [x] 将权威 candidate manifest 调整为 `stages/agent01/<run_id>/candidate_manifest.jsonl`；
- [x] 为 Candidate 和 StageResultEnvelope 增加结构/manifest/input snapshot 的 URI 与 SHA-256 lineage；
- [x] 完成 operation 增加不可变 artifact registry；已登记产物缺失、损坏或冲突时返回 `BACKEND_INCONSISTENT`，不静默重查；
- [x] 查询成功但报告阶段中断时，从已校验 raw-response checkpoint 恢复，不重复执行数据库 search；
- [x] metadata、summary search 和 origin resolution 使用统一有限瞬时重试；
- [x] 结构派生 formula 成为规范化真源，summary formula 仅保留于 provenance 并参与一致性检查；
- [x] canonical CIF 回读校验、0D/1D/2D/3D 固定结构基准和 CrystalNN/Larsen warning 质量标记已落地；
- [x] 加入默认不访问网络的 `live_mp` opt-in release test；
- [x] README 已补充离线运行、真实验收、测试、Artifact 布局、恢复语义和已知限制；
- [x] 系统总 Plan 已补充 Agent 01 详细计划链接和与 Agent 02 的职责边界。
- [x] 使用 macOS Keychain 临时注入轮换后的 API key，当前最终代码的真实 Materials Project release test 已通过；

P2 系统 v1 收尾复核（2026-07-28）：Agent01 继续是默认生产科学 runner，`agent01-contract-v1`
及确定性筛选阈值保持不变。完整离线 Gate 为 `337 passed, 7 skipped`；未运行联网
Materials Project Gate、未读取或生成 `MP_API_KEY`。benchmark、扩展适用域、OOD 与
不确定性校准不属于本次收尾。

- [x] 冻结 `validate_input/prepare/start/reconcile` StageRunner 生命周期以及 Candidate、PropertyValue、StageResultEnvelope 公共契约，版本为 `agent01-contract-v1`；
- [x] 生成 44 KB 最小离线冻结契约 fixture，包含 1 条候选、source JSON、CIF、manifest、StageResult 与 JSON Schema，并验证 artifact hash 和逐字节确定性重建；
- [x] Orchestrator P0.1 通过 `Agent01RunnerAdapter` 使用冻结契约，代码、依赖和测试基线提交为 `d681de8`；

### 0.2 验证状态

- 这里记录的 `82 项 / 81 passed, 1 skipped` 是历史验证快照；当前测试集合已扩展，实际结果以当前工作树执行的离线 Gate 为准；
- 唯一跳过项为显式 opt-in 的 `tests/live/test_live_mp_release.py`，默认测试不会读取 `MP_API_KEY` 或访问网络；
- `pip check` 当前通过；
- 2026-07-26 使用当前最终代码和独立 `live_mp` 流程完成固定 Si/O 真实 release Gate：
  - 测试结果：`1 passed`；
  - 公共契约版本：`agent01-contract-v1`；
  - Materials Project database version：`2026.04.13`；
  - `mp-api==0.45.15`，`pymatgen==2025.10.7`；
  - 数据库返回并规范化 81 条候选；
  - 81 条全部通过 Si/O、0.5–1.0 eV band gap、energy above hull ≤ 0.05 eV/atom 和非金属约束；
  - 约束违规、结构文件缺失、结构文件为空、规范化失败、来源未解析均为 0；
  - 405 项候选性质 origin 状态全部为 `RESOLVED`；
  - 未发生扫描截断或发布截断；
  - 同一 run 的第二次执行完全复用已校验结果，没有重复查询；
  - 81 条 dimensionality warning 和 7 条 CIF round-trip warning 作为质量标记保留，没有被误判为结构失败；
  - 对测试临时目录逐文件检查，API key 字节泄漏为 0。

真实 API 的 raw response、候选 CIF、manifest 和筛选报告只存在于 pytest 系统临时目录，不进入项目或 Git；记录 Gate 指标后删除。仓库中的冻结契约 fixture 只由离线数据生成。

### 0.3 尚未完成

- [ ] P1：真正的逐页 cursor checkpoint；
- [ ] P1：大规模 StructureMatcher 性能优化；
- [ ] P1：并行结构分析；
- [ ] P1：robocrystallographer dimensionality 交叉验证；
- [ ] P1：MP Similarity endpoint 对照；
- [ ] P1：多数据库 Adapter；
- [ ] P1：科学 silver set 扩充；
- [ ] P1：用户可配置的扫描上限提升审批；
- [x] Orchestrator P0：实现 `Requirement 确认与冻结 → ExecutionPlan → Agent 01 → Envelope 校验 → Report → checkpoint/resume`；
- [x] 完成 Gate 后提交本轮完整性、测试和文档增强变更。

## 1. 目标、边界与完成标准

### 1.1 定位

Agent 01 是一个确定性的科学数据处理阶段，不使用 LLM 做查询、筛选、排序或去重决策。

职责为：

1. 接收已确认且不可变的 Requirement revision；
2. 将可下推约束翻译为 Materials Project 查询；
3. 获取、归档并规范化数据库记录；
4. 保存来源结构和性质 provenance；
5. 本地复核全部硬约束；
6. 校验结构并计算版本化的结构维度；
7. 识别精确重复和结构相似簇，但不合并不同 MP material；
8. 生成 `PASS/REJECT/UNCERTAIN/FAILED` 判定；
9. 对 `PASS/UNCERTAIN` 做确定性排序并发布给 Agent 02；
10. 输出审计账本、候选 manifest、结构文件和筛选报告。

Agent 02 不再重复元素、带隙、稳定性、原子数等确定性筛选，只消费 Agent 01 发布的候选并执行 ML/MLIP 处理。

### 1.2 v1 支持范围

真实支持以下基础约束：

- 必须同时包含给定元素，语义为 `all-of`；
- 不得包含任一排除元素，语义为 `none-of`；
- band gap 数值范围；
- energy above hull 数值范围；
- 金属/非金属要求；
- 最大原子位点数；
- 0D/1D/2D/3D 结构维度；
- 用户确认的基础性质排序偏好。

`topological_flat_band`、`mott_candidate` 等高级目标如果不能从 MP 字段或版本化结构算法验证，只生成 `missing_evidence`，不得由 Agent 01 猜测或宣布满足。

### 1.3 固定验收用例

必须真实完成：

> 同时包含 Si 和 O、band gap 为 0.5–1.0 eV、energy above hull 不超过 0.05 eV/atom、非金属。

验收结果必须包含：

- 实际 MP 查询参数；
- MP database version；
- 每条候选的 MP material ID；
- canonical CIF；
- band gap 与 hull energy 的单位、来源和 origin；
- 每条约束的本地复核结果；
- 候选判定理由；
- 完整与截断状态；
- Markdown 和 JSON 报告。

MP 官方客户端的 Summary endpoint 当前支持 `elements`、`exclude_elements`、`band_gap`、`energy_above_hull`、`is_metal`、`num_sites`、字段选择和受控 chunk 数量，可作为 v1 主查询接口。[SummaryRester 官方接口](https://materialsproject.github.io/api/_autosummary/mp_api.client.routes.materials.summary.SummaryRester.html)

### 1.4 明确非目标

- 不生成新结构、掺杂、替换或超晶胞；
- 不进行 ML、DFT 或多体计算；
- 不把 MP 计算值描述为实验值；
- 不使用 LLM 生成查询条件；
- 不将结构相似的不同多晶型合并成同一候选；
- 不在 v1 进行全库下载；
- 不支持多数据源联合检索；
- 不因 origin 解析失败而伪造计算方法。

## 2. 公共接口与数据契约

### 2.1 Requirement 补充

现有 Requirement 需要明确以下类型：

- `NumericRange`
  - `min: float | null`
  - `max: float | null`
  - `unit: str`
  - 上下界均为闭区间；
  - 至少一个边界非空；
  - 禁止 `NaN`、无穷值及 `min > max`。
- `hard_constraints.is_metal: bool | null`
  - 固定验收用例取 `false`。
- `hard_constraints.dimensionality: 0 | 1 | 2 | 3 | null`
- `RankingPreference`
  - `property`：v1 只接受 `energy_above_hull`、`band_gap`、`num_sites`；
  - `mode`：`minimize | maximize | target`；
  - `target`：仅 `target` 模式必填；
  - 多条偏好按用户声明顺序做字典序比较，不使用隐式加权总分。
- `data_sources.materials_project.include_gnome`
  - 默认 `false`；
  - 开启时必须产生新的 Requirement revision 并由用户确认。

### 2.2 阶段接口

实现统一的 `RetrievalStageRunner`：

```text
validate_input(context) -> StageInputValidation
prepare(context) -> RetrievalStagePlan
start(plan, idempotency_key) -> StageOutcome
reconcile(external_job_ref) -> StageOutcome
```

Agent 01 没有长时间外部 job，因此 `reconcile` 只用于检查已完成操作、Artifact hash 和数据库版本，不轮询后台任务。

输入 `RetrievalStageInput` 至少包含：

- `project_id`
- `run_id`
- `requirement_revision`
- `requirement_artifact_uri`
- `requirement_hash`
- `retrieval_policy_version`
- `confirmed_by_user=true`

校验失败条件：

- Requirement 未确认；
- revision、URI 和 hash 不一致；
- 数值单位不支持；
- 元素符号非法；
- `max_candidates <= 0`；
- 请求了 v1 未声明的硬约束；
- API 凭据或依赖配置缺失。

### 2.3 Query Plan

`RetrievalQueryPlan` 固定包含：

- `query_id`
- `source_database="materials_project"`
- `endpoint="/materials/summary"`
- `database_version`
- `requirement_hash`
- `pushdown_filters`
- `local_only_constraints`
- `requested_fields`
- `chunk_size`
- `max_records_scanned`
- `max_candidates_published`
- `include_gnome`
- `include_deprecated`
- `theoretical_policy`
- `sort_fields`
- `query_fingerprint`
- `client_version`
- `created_at`

`query_fingerprint` 由规范化后的查询参数、Requirement hash、policy version、数据库版本和客户端版本计算 SHA-256。

### 2.4 Candidate 与审计记录

每个已扫描 MP material 都产生 `CandidateAuditRecord`，包括被拒绝和处理失败的记录。

在既有 Candidate 基础上增加：

- `source_database_version`
- `source_last_updated`
- `query_id`
- `constraint_evaluations[]`
- `scientific_target_evaluations[]`
- `exact_duplicate_group_id`
- `similarity_cluster_id`
- `publication_rank`
- `published_downstream`
- `data_quality_flags[]`

`ConstraintEvaluation` 包含：

- `constraint_id`
- `constraint_type`
- `expected`
- `observed`
- `unit`
- `result: MATCH | MISMATCH | MISSING | ERROR`
- `reason_code`
- `property_origin`

决策优先级固定为：

1. 结构缺失、结构解析失败或候选核心记录无法构建：`FAILED`；
2. 任一硬约束已知违反：`REJECT`；
3. 没有已知违反，但硬约束或所需科学证据无法验证：`UNCERTAIN`；
4. 所有硬约束和当前阶段应提供的证据均满足：`PASS`。

已知违反与其他字段缺失并存时仍为 `REJECT`，但必须同时记录缺失项。

### 2.5 PropertyValue 扩展

每个规范化性质增加：

- `endpoint`
- `database_version`
- `origin_task_id`
- `run_type`
- `task_type`
- `calc_type`
- `provenance_status: RESOLVED | PARTIAL | UNRESOLVED`
- `is_derived`
- `derived_from_structure_id`
- `derivation_policy_version`

允许 `method=null`，但只能同时标记 `provenance_status=UNRESOLVED` 并生成警告，不能补写推测方法。

Materials Project 官方文档建议通过 summary `origins` 找到性质来源 task，并结合 task/thermo 数据确定计算方法；同时建议只请求实际需要的字段以减少传输量。[MP 查询与 origins 文档](https://docs.materialsproject.org/downloading-data/using-the-api/querying-data)

## 3. 查询、筛选与结构处理流程

### 3.1 数据集政策

v1 固定：

- `deprecated=false`；
- `include_gnome=false`；
- `theoretical=None`，即不因理论材料身份排除；
- 用户显式开启 GNoME 后才改为 `include_gnome=true`；
- API key 只从 `MP_API_KEY` 或后续 Keychain provider 读取；
- Artifact 不保存 key、请求认证头或完整异常上下文中的秘密。

MP 数据库的 consolidated material 数据会随数据库发布改变，因此每次运行必须记录数据库版本；Adapter 优先读取新版客户端的 `MPRester.db_version`，锁定的 `mp-api==0.45.15` 则回退到 `get_database_version()`。恢复运行时如果数据库版本已变化，禁止把新旧结果混在一个 Stage Run 中。[MP database version 说明](https://materialsproject.github.io/api/_modules/mp_api/client/mprester.html)

### 3.2 查询下推规则

| Requirement 约束     | MP 查询下推                       | 本地复核        |
| ------------------ | ----------------------------- | ----------- |
| `include_elements` | `elements=[...]`              | 必须全部出现      |
| `exclude_elements` | `exclude_elements=[...]`      | 不得出现任一元素    |
| 完整 band gap 范围     | `band_gap=(min,max)`          | 按闭区间复核      |
| 仅 band gap 上限      | `band_gap=(0,max)`            | 按用户边界复核     |
| 仅 band gap 下限      | v1 不构造虚假上限，留给本地筛选             | 本地复核        |
| 完整 hull energy 范围  | `energy_above_hull=(min,max)` | 按闭区间复核      |
| 仅 hull energy 上限   | `energy_above_hull=(0,max)`   | 本地复核        |
| `is_metal`         | `is_metal=...`                | 本地复核        |
| `max_num_sites`    | `num_sites=(1,max)`           | 以解析后结构位点数复核 |
| `dimensionality`   | 不下推                           | 本地结构算法      |
| 高级科学目标             | 不下推                           | 生成证据缺口      |

所有已下推约束必须再次本地复核，避免 API 字段映射、边界或版本变化造成静默误筛。

启动时读取 `summary.available_fields` 并保存 capability snapshot。核心字段缺失属于 `PERMANENT_CONFIGURATION`，可选报告字段缺失只产生警告。

固定请求字段至少包括：

- `material_id`
- `formula_pretty`
- `chemsys`
- `elements`
- `nelements`
- `nsites`
- `structure`
- `band_gap`
- `energy_above_hull`
- `is_metal`
- `deprecated`
- `theoretical`
- `origins`
- `last_updated`

### 3.3 数量和分页政策

固定 policy：

- `chunk_size=500`；
- `max_records_scanned=5000`；
- `budget.max_candidates` 默认 200，只控制发布给下游的数量；
- P0 单查询流串行执行，不并发轰击 MP；
- 使用锁定版本 `mp-api` 的公开 `search()` 接口完成受控 chunk 检索，返回后本地按 `material_id` 稳定排序；
- 使用 `num_chunks=10` 限制最多扫描 5000 条。

P0 不依赖逐页流式恢复：官方 Python 客户端完成受控 chunk 查询后，结果先按 `material_id` 排序，再按每 500 条切分并原子保存为压缩 JSONL artifact。锁定的 `mp-api==0.45.15` 不公开支持 `_sort_fields`，因此不得把该参数透传给 `search()`。查询过程中失败时重试整个查询操作，但幂等账本保证不会重复写入候选。

如果返回量等于 5000，保守设置：

- `scan_truncated=true`
- Stage 状态为 `PARTIAL`
- 报告明确说明完整数据库结果可能超过扫描上限
- 禁止将结果称为“全部满足条件的 MP 材料”

逐页 checkpoint/cursor 作为 P1；只有在固定客户端版本的分页契约测试确认无遗漏、无重叠后启用。

### 3.4 结构保存与 canonicalization

每条候选结构保存两个版本：

1. `source_structure.json`
   - 保存 MP 返回结构的规范化 JSON 表示；
   - 不做原胞、惯用胞或对称化变换。
2. `canonical.cif`
   - 周期性坐标移回单位胞；
   - 按 species 和分数坐标稳定排序；
   - 晶格和坐标统一保留 12 位小数；
   - 不改变晶胞选择和化学计量；
   - 使用固定 pymatgen 版本写出。

结构校验至少覆盖：

- 非空位点；
- 晶格矩阵、体积和坐标均为有限值；
- 体积大于零；
- occupancy 合法；
- 结构组成与 summary formula/元素集合一致；
- 结构位点数与 summary `nsites` 一致。

summary 与结构不一致时以结构为结构规则的判定真源，同时记录 `SOURCE_FIELD_MISMATCH`。

身份规则：

- `candidate_id`：UUIDv5，输入为 `project_id + materials_project + material_id`；
- `structure_id`：`str_` 加 canonical structure JSON 的 SHA-256 前 24 位；
- 相同 MP material 在同一项目中始终保持 candidate ID；
- MP 数据库更新导致结构内容变化时 candidate ID 不变、structure ID 改变。

### 3.5 维度判定

使用：

- `CrystalNN` 构建 `StructureGraph`；
- `get_dimensionality_larsen` 计算 0D/1D/2D/3D；
- policy 名称：`dimensionality-larsen-crystalnn-v1`。

冻结参数：

```text
CrystalNN(
  weighted_cn=False,
  cation_anion=False,
  distance_cutoffs=(0.5, 1.0),
  x_diff_weight=3.0,
  porous_adjustment=True,
  search_cutoff=7.0,
  fingerprint_length=None
)
```

输出性质：

- `name="structural_dimensionality"`
- `unit="dimensionless"`
- `source="derived_from_mp_structure"`
- `evidence_level="L1_RETRIEVED"`
- `is_derived=true`
- `derived_from_structure_id`
- pymatgen 版本、算法名和全部参数

算法失败时不拒绝候选，记录 `DIMENSIONALITY_EVALUATION_FAILED`；如果 dimensionality 是硬约束，则候选为 `UNCERTAIN`。

pymatgen 明确提供 Larsen dimensionality 算法，并要求先构建带键信息的 `StructureGraph`。[pymatgen dimensionality 文档](https://pymatgen.org/pymatgen.analysis.html)

### 3.6 Origin 解析

对所有已规范化并进入审计账本的性质执行：

1. 从 summary `origins` 建立 `property -> task_id` 映射；
2. 汇总唯一 task ID，每批最多 100 个；
3. 查询 task/core material 元数据，填充 `run_type`、`task_type`、`calc_type`；
4. thermo/structure origin 无法从 task 数据解析时，按 material ID 批量读取 thermo entries 并匹配 task ID；
5. 仍无法解析则保留 task ID、数据库版本和 endpoint，`method=null`；
6. origin 解析失败不改变已观测数值，但产生 provenance warning；
7. 若所有候选的关键性质 provenance 均无法解析，Stage 为 `PARTIAL`。

### 3.7 确定性规则执行

数值比较：

- 上下界均包含；
- 不先四舍五入再比较；
- eV 和 eV/atom 使用 `1e-8` 绝对容差；
- 非有限数值视为缺失；
- 单位错误视为候选处理错误，不做隐式换算。

元素比较以解析后结构 composition 为主，summary 元素集作为交叉检查。

科学目标处理：

- 可由已有 PropertyValue 或维度算法直接证明的目标，生成 evaluation；
- 需要能带形状、拓扑不变量、局域相互作用或层间电荷转移证据的目标，记录 `SCIENTIFIC_TARGET_UNSUPPORTED_AT_L1`；
- 若目标要求 L2/L3/L4，Agent 01 不升级证据，候选保持 `UNCERTAIN` 并发布给适当下游阶段。

### 3.8 去重和结构聚类

不得因为结构相似而删除候选。

分两层处理：

1. 精确重复
   - 对所有成功解析结构按完整 canonical structure hash 分组；
   - 相同 structure ID 的不同 candidate 建立 `exact_duplicate_group_id`；
   - 所有 candidate 均保留。
2. 结构相似簇
   - 只对排序后拟发布的最多 200 个候选执行；
   - 先按 reduced composition 分桶；
   - 使用 `StructureMatcher`；
   - 参数固定为 `ltol=0.2`、`stol=0.3 Å`、`angle_tol=5°`、`primitive_cell=true`、`scale=true`、`attempt_supercell=false`、`allow_subset=false`；
   - 使用严格 species comparator；
   - `similarity_cluster_id` 由排序后的成员 candidate ID 计算；
   - cluster 只用于报告多样性，绝不选代表并删除其他多晶型。

pymatgen 的 `StructureMatcher` 支持按晶格、位点和角度容差分组结构；这里将其限制为注释性聚类而非科学实体合并。[StructureMatcher 文档](https://pymatgen.org/pymatgen.core.html#pymatgen.core.structure_matcher.StructureMatcher)

### 3.9 排序与下游发布

排序规则：

1. `PASS` 在 `UNCERTAIN` 前；
2. 若用户提供 ranking preferences，按声明顺序做字典序排序；
3. 缺少某排序性质的候选排在拥有该性质的候选之后；
4. 同分时按 `missing_evidence` 数量升序；
5. 最终以 MP material ID 稳定打破平局。

没有 ranking preferences 时，不擅自加入稳定性或带隙偏好，仅使用：

```text
decision → missing_evidence_count → material_id
```

发布规则：

- 只有 `PASS/UNCERTAIN` 可进入 `candidate_manifest.jsonl`；
- 最多发布 `budget.max_candidates` 条；
- `REJECT/FAILED` 只进入 Agent 01 审计账本；
- 发布上限造成的截断是预期预算行为，不使 Stage 变为 `PARTIAL`；
- 扫描上限造成的不完整检索才使 Stage 变为 `PARTIAL`。

## 4. Artifact、状态与错误处理

### 4.1 必须生成的 Artifact

Agent 01 阶段目录按需生成：

- `input_snapshot.json`
- `capability_snapshot.json`
- `query_plan.json`
- `raw_response_batches/*.jsonl.gz`
- `raw_response_manifest.jsonl`
- `origin_resolution.jsonl`
- `candidate_audit.jsonl`
- `candidate_manifest.jsonl`
- `dedup_map.jsonl`
- `structure_clusters.jsonl`
- `structures/<structure_id>.source.json`
- `structures/<structure_id>.cif`
- `retrieval_report.json`
- `retrieval_report.md`
- `stage_result.json`

所有文件使用临时文件、SHA-256 校验和原子 rename；SQLite/Global State 只保存 Artifact URI 和 hash。

### 4.2 报告内容

报告必须展示筛选漏斗：

```text
数据库返回
→ 成功规范化
→ 结构有效
→ REJECT
→ UNCERTAIN
→ PASS
→ 下游发布
```

并列出：

- Requirement revision/hash；
- query fingerprint；
- MP database/client 版本；
- 数据集政策；
- 每条查询下推规则；
- 扫描量和发布上限；
- PASS/REJECT/UNCERTAIN/FAILED 数量；
- reason code 分布；
- 缺失字段和 provenance 解析失败统计；
- exact duplicate 与 similarity cluster 数量；
- 是否扫描截断；
- 科学限制与证据等级声明。

### 4.3 错误分类

- timeout、429、临时 5xx：`TRANSIENT_EXTERNAL`，最多三次，退避 1、2、4 秒并加 jitter；
- API key 缺失/无效：`PERMANENT_CONFIGURATION`；
- MP 字段或客户端签名变化：`API_SCHEMA_DRIFT`，禁止静默忽略；
- 数据库版本在恢复期间变化：`BACKEND_INCONSISTENT`；
- 零候选：`SCIENTIFIC_NO_MATCH`，Stage `SUCCEEDED`；
- 单个结构损坏：该候选 `FAILED`；
- 部分候选失败但仍有有效结果：Stage `PARTIAL`；
- 扫描到安全上限：Stage `PARTIAL`；
- 报告生成失败：保留先前 Artifact，允许幂等重试；
- 重复执行同一 query fingerprint：复用已校验 Artifact，不重复创建候选。

## 5. Day 3–5 实施顺序

### Day 3：查询与原始数据链路

- 建立独立 Python 3.11 环境；
- 固定 `mp-api==0.45.15`、`pymatgen==2025.10.7` 和 Pydantic v2；
- 实现 Retrieval 输入、Query Plan、policy 和 adapter 契约；
- 实现 Requirement 到 MP 查询参数的纯函数映射；
- 实现 capability 与 database version snapshot；
- 完成受控 chunk 查询、重试、脱敏和 raw batch 归档；
- 用真实 API 跑通固定 Si/O 查询；
- 建立离线 MP fixture，自动测试不依赖 API key。

当日完成标准：能够保存真实、带 database version 的 MP 原始候选数据。

### Day 4：规范化、结构与 provenance

- 实现 CandidateAuditRecord、ConstraintEvaluation 和 PropertyValue 扩展；
- 解析并校验 MP structure；
- 生成 source JSON、canonical CIF、candidate ID 和 structure ID；
- 实现 summary/structure 一致性检查；
- 实现 origin task 批量解析与降级行为；
- 实现 CrystalNN + Larsen dimensionality policy；
- 完成缺字段、非法结构、origin 缺失和维度失败测试。

当日完成标准：每条可用候选都有结构、规范化性质、单位和可审计来源。

### Day 5：筛选、去重、排序与报告

- 实现硬约束纯函数执行器和决策优先级；
- 实现 exact duplicate mapping；
- 对发布池执行 StructureMatcher 相似聚类；
- 实现显式排序偏好和无偏好稳定排序；
- 生成全量审计账本与下游 manifest；
- 生成 JSON/Markdown 报告和 StageResultEnvelope；
- 接入 Orchestrator `StageRunner`；
- 完成固定用例 E2E、失败注入、幂等和 resume 测试。

当日完成标准：Agent 01 可作为独立阶段运行，也可被 Orchestrator 调用，并产生可供 Agent 02 消费的 manifest。

### P1 延后项

不阻塞第 7 天演示：

- 真正的逐页 cursor checkpoint；
- 大规模 StructureMatcher 性能优化；
- 并行结构分析；
- robocrystallographer dimensionality 交叉验证；
- MP Similarity endpoint 对照；
- 多数据库 Adapter；
- 科学 silver set 扩充；
- 用户可配置的扫描上限提升审批。

## 6. 测试与验收

### 6.1 单元测试

必须覆盖：

- Requirement 到 MP 查询参数的逐字段映射；
- include 元素为 all-of、exclude 为 none-of；
- 数值闭区间边界及 `1e-8` 容差；
- 单边范围下推策略；
- 决策优先级；
- 缺字段为 `UNCERTAIN`；
- 结构失败为 `FAILED`；
- candidate/structure/query ID 稳定性；
- 默认排序不隐式加入科学偏好；
- 用户排序偏好字典序；
- GNoME 默认关闭；
- secret redaction；
- Artifact 原子写与 hash 校验。

### 6.2 结构测试

准备固定结构 fixture：

- 0D 分子晶体；
- 1D 链；
- 2D 层状材料；
- 3D 网络；
- 相同结构但不同位点顺序；
- 相同 composition 的不同多晶型；
- 非法晶格；
- occupancy 异常；
- summary composition 与 structure 不一致。

验收：

- 位点重排不改变 structure ID；
- 不同多晶型不被合并；
- StructureMatcher 只产生 cluster；
- 维度算法失败不会变成 REJECT；
- CIF 可被 pymatgen 重新读取且 composition 不变。

### 6.3 Adapter 与失败注入

覆盖：

- 真实字段 capability；
- timeout、429、5xx 后成功；
- 三次重试耗尽；
- 非法 API 响应；
- API schema drift；
- origin task 不存在；
- database version 变化；
- 零结果；
- 返回量恰好达到 5000；
- 重复 material ID；
- 查询成功后报告生成中断；
- 同一 run 恢复不重复候选或 Artifact。

### 6.4 E2E 验收

固定 Si/O 用例必须证明：

- 查询参数与确认后的 Requirement 完全一致；
- 每个发布候选都含 Si/O；
- band gap 在 0.5–1.0 eV 闭区间；
- hull energy 不超过 0.05 eV/atom；
- `is_metal=false`；
- 每个候选都有可读取 CIF；
- 每个性质有单位、数据库版本和 origin 状态；
- 没有候选被提升到 L2/L3/L4；
- 中断恢复后 candidate ID、structure ID、query fingerprint 和已保存 Artifact hash 不变。

## 7. 已确认假设与仓库同步

- Agent 01 负责检索、确定性规则筛选、结构校验、去重聚类和排序；Agent 02 不重复这些规则。
- v1 泛化支持基础 Requirement 约束，高级目标只记录证据缺口。
- 所有扫描记录进入审计账本；只有 `PASS/UNCERTAIN` 发布下游。
- `max_candidates` 是发布上限，独立的扫描安全上限固定为 5000。
- 不同 MP material ID 始终保留为独立 candidate。
- 无用户排序偏好时不加入隐式科学排序。
- 缺硬约束字段为 `UNCERTAIN`，结构处理错误为 `FAILED`。
- 维度由本地版本化 Larsen + CrystalNN 算法计算。
- 关键性质尽力解析 origin task 方法，失败时显式保留未知状态。
- 默认排除 deprecated 和 GNoME，保留理论材料。
- Agent 01 必须在 Day 3–5 P0 时间内交付，性能增强延至 P1。
- 当前仓库已包含 Agent 01 源码、测试、fixture、依赖锁文件和独立实施计划；已使用仓库本地 Python 3.11.13 `.venv`，不依赖本机默认 Python 3.13.2。
- 已新增独立 [`plans/subagents/material-screening-agent01-plan.md`](material-screening-agent01-plan.md)；总计划的 Agent 01 小节已补充该文档链接和“确定性筛选归 Agent 01、ML 归 Agent 02”的职责说明，未覆盖原总计划。

## 8. 下一步实施规划与 Orchestrator 进入 Gate

本节基于 2026-07-26 的仓库只读扫描，作为 Agent 01 从“离线主链已运行”进入“真实可验收、接口可冻结”阶段的执行顺序。

### 8.1 当前实现基线

当前仓库已经具备：

- 独立 Git 仓库，`main` 已建立首次可回退基线提交 `bcaf8fc`；
- Python 3.11.13 仓库本地 `.venv`；
- 锁定依赖且 `pip check` 通过；
- Agent 01 查询、Adapter、规范化、结构处理、筛选、排序、去重聚类、报告、CLI 和本地 Artifact Store；
- 固定 Si/O 离线 fixture；
- 这里记录的 `82 个测试 / 81 passed, 1 skipped` 是历史验证快照；当前测试集合已扩展，实际结果以当前工作树执行的离线 Gate 为准；
- 当前最终代码的真实 MP release Gate 已通过；
- Agent 01 公共契约已冻结为 `agent01-contract-v1`，最小输出 fixture 已提交并可逐字节重建。

当前 Agent 01 可以视为完成第 8 节 Gate；后续工作进入 Orchestrator P0，不在 Agent 01 内先展开 Agent 02–04。

### 8.2 执行顺序

下一步固定按以下顺序实施，不先展开完整 LangGraph Orchestrator：

1. [x] 建立当前通过测试状态的 Git 基线，确认 `.venv`、`workspace/`、环境文件、密钥和缓存未进入版本控制。
2. [x] 修复 Requirement 输入、快照、manifest 和 operation artifact 的完整性与不可变语义。
3. [x] 补齐单位、结构、筛选、排序、API 错误、恢复和报告测试。
4. [x] 使用仅对测试进程可见的 `MP_API_KEY` 执行固定 Si/O 真实查询。
5. [x] 冻结 Agent 01 对 Agent 02 和 Orchestrator 的公共输出契约。
6. [x] 生成一份由冻结契约校验过的 Agent 01 输出 fixture。
7. [x] Agent 01 通过本节 Gate 后，实现 Orchestrator 的真实 `Stage 0 → Agent 01 → Report` 链路。

本阶段允许实现 Orchestrator 所需的 `StageRunner` 接口语义，但不实现 LangGraph 状态图、SQLite checkpoint、审批流程或 Agent 02–04 路由。

### 8.3 输入和 Artifact 完整性

`RetrievalStageRunner` 必须增加以下约束：

- Requirement artifact 必须存在于当前 project Artifact Store；
- artifact 实际 SHA-256 必须等于 `RetrievalStageInput.requirement_hash`；
- artifact 中的 Requirement revision 必须等于 stage input revision；
- artifact 解析结果必须与传入 Runner 的 Requirement 规范化内容完全一致；
- 任一校验失败时不得调用 Materials Project；
- 缺失 URI/hash/revision 使用 `BLOCKED_MISSING_INPUT`；
- 已存在但 hash 或内容冲突使用结构化完整性错误，不得覆盖旧快照。

以下 artifact 视为当前 Stage Run 的不可变证据：

- `input_snapshot.json`
- `capability_snapshot.json`
- `query_plan.json`
- raw response batches 和 manifest
- candidate audit
- candidate manifest
- dedup/cluster map
- retrieval report
- operation result

权威候选 manifest 路径调整为：

```text
stages/agent01/<run_id>/candidate_manifest.jsonl
```

共享结构仍按内容寻址保存：

```text
candidates/structures/<structure_id>.source.json
candidates/structures/<structure_id>.cif
```

同一 operation 已完成且所有 hash 正确时直接复用；若 operation 记录存在但任一已登记 artifact 缺失或 hash 不匹配，则返回 `BACKEND_INCONSISTENT`，不得静默重新查询或覆盖历史结果。

### 8.4 查询、结构和 provenance 补强

Requirement 单位固定为：

- `band_gap_ev.unit="eV"`
- `energy_above_hull_ev_atom.unit="eV/atom"`

其他单位在查询前阻塞，不做隐式换算。

结构处理补强：

- 使用解析后结构 composition 作为元素、位点数和化学计量的真源；
- summary formula 与结构不一致时记录 `SOURCE_FORMULA_MISMATCH`；
- 候选规范化 formula 使用结构派生结果，summary formula 只保留在 provenance；
- canonical payload 使用实际 `canonicalization_policy_version`，不得硬编码；
- CIF 必须可以由锁定版本 pymatgen 回读，且 composition、位点数和晶格保持一致；
- CrystalNN/Larsen 警告进入 data-quality/report，不因普通 warning 直接将候选标记为失败。

外部调用补强：

- metadata、summary search 和 origin resolution 使用统一的有限瞬时重试；
- timeout、429 和临时 5xx 最多三次，指数退避并带 jitter；
- 401/403 或缺少 `MP_API_KEY` 为 `PERMANENT_CONFIGURATION`；
- 公共错误不得包含 API key、认证头或完整敏感 traceback；
- origin resolution 为每个请求 task 显式记录 `RESOLVED/PARTIAL/UNRESOLVED`，不能只输出成功项。

### 8.5 StageRunner 与公共接口冻结

Agent 01 保留独立 CLI 的 `run(requirement, stage_input)` 包装，同时对齐公共接口：

```text
validate_input(context) -> StageInputValidation
prepare(context) -> RetrievalStagePlan
start(plan, idempotency_key) -> StageOutcome
reconcile(operation_ref) -> StageOutcome
```

Agent 01 为本地同步 Stage：

- `start` 执行受控检索并返回完成、阻塞或失败结果；
- 不返回长时间外部 job；
- `reconcile` 只检查 operation、数据库版本和 artifact hash；
- `run` 只负责按顺序组合上述方法，不另建一套状态语义。

Agent 02 和 Orchestrator 可依赖的冻结输出至少包括：

- candidate ID 和 publication rank；
- source material ID 和 MP database version；
- source structure ID、URI 和 SHA-256；
- 规范化 `PropertyValue[]` 与 property origin；
- constraint/scientific-target evaluations；
- `PASS/REJECT/UNCERTAIN/FAILED`；
- missing evidence 和 reason codes；
- evidence level；
- exact duplicate/similarity cluster 标记；
- Requirement/query/policy provenance；
- run-scoped candidate manifest URI/hash；
- 合法 `StageResultEnvelope`。

### 8.6 测试补齐

在现有 27 个测试基础上至少补齐：

1. Requirement 和查询
   - 非法元素符号；
   - NaN/Infinity；
   - 非法单位；
   - 单边和双边范围；
   - 上下界闭区间及 `1e-8` 容差；
   - include `all-of`、exclude `none-of`；
   - GNoME 开关；
   - query/candidate ID 和 fingerprint 稳定性。
2. 筛选与排序
   - 元素、金属性、原子数和维度；
   - 已知违反优先于其他缺失；
   - 缺失 required property 为 `UNCERTAIN`；
   - minimize、maximize、target；
   - 多偏好字典序；
   - 排序字段缺失；
   - 最终 MP material ID 稳定打破平局。
3. 结构
   - 固定 0D、1D、2D、3D fixture；
   - 位点顺序不改变 structure ID；
   - 相同 composition 的不同多晶型不合并；
   - 非法晶格、非法 occupancy 和非有限坐标；
   - summary composition/formula/nsites 不一致；
   - CIF round-trip；
   - dimensionality 失败保持 `UNCERTAIN`。
4. Adapter、恢复和失败注入
   - metadata/search/origin 的 timeout、429 和 5xx；
   - 三次重试耗尽；
   - API Schema drift；
   - origin task 不存在；
   - database version 变化；
   - 零结果；
   - 返回量恰好达到扫描上限；
   - 重复 material ID；
   - artifact 缺失或被篡改；
   - query 成功后报告生成中断；
   - 重复 run/resume 不重复候选或覆盖 artifact。

### 8.7 真实 Materials Project 验收

真实验收是 Agent 01 完成的硬 Gate：

- API key 只通过环境变量 `MP_API_KEY` 提供；
- 默认 CI 不运行真实网络测试；
- 真实测试使用单独的 `live_mp` marker 和新的 workspace/run ID；
- 使用固定 Si/O Requirement；
- 保存真实 database version、client version、query plan 和 raw response；
- 验证所有发布候选都满足已确认的本地硬约束；
- 每个发布候选都有可读取 CIF；
- 每个性质都有单位、来源和 origin 状态；
- 任何候选都不得提升到 L2/L3/L4；
- 对同一完成 run 再次执行时必须复用相同结果；
- workspace、报告和日志中不得出现 API key。

真实查询返回零候选仍属于 `SCIENTIFIC_NO_MATCH` 和 Stage `SUCCEEDED`，不能为了演示而修改查询结果或伪造候选。

2026-07-26 验收结果：真实 test `1 passed`；database version
`2026.04.13`；返回、规范化并发布 81 条候选；失败、拒绝、证据不确定、
扫描截断、发布截断和 API key 泄漏均为 0；同一 run 第二次执行复用相同
StageResult。真实产物在指标提取后删除，不作为公共 fixture。

### 8.8 Agent 01 完成 Gate

### 8.9 C2DB 发布与溯源修正（2026-07-30；2026-08-03 更新）

- `PASS` 与 `UNCERTAIN` 候选均可进入下游 manifest；后者保留完整 `missing_evidence`、
  `UNCERTAIN` decision 和 L1 evidence ceiling，供下游科学复核。只有数据库证据明确
  `MISMATCH` 的 `REJECT` 记录以及 `FAILED` 记录被阻断。
- Agent01 Markdown 报告显示 `local_only_constraints`、检索时间，并对 C2DB `undated-live-web` 快照限制给出提示。
- Stage0 DeepSeek 提示明确：数据库筛选描述应建模为硬约束，不得生成无法由所选数据库验证的自由文本 `scientific_targets`。
- 相关单元测试通过（35 passed）；完整离线 Gate 通过（439 passed, 9 skipped）。

### 8.10 Materials Project 富媒体检索报告（2026-07-30）

- 已实现报告专用 `agent01-mp-report-v1` enrichment Artifact，保留冻结的
  `agent01-contract-v1` candidate manifest 不变。
- 对所有发布候选本地渲染常规胞/对称性结构 PNG；Top-N（默认 20，可由 CLI
  配置）采集重端点的电子、声子、谱学、异质结构和电荷密度数据。原始端点对象以
  确定性 gzip JSON 归档；结构与电荷切片以本地 Matplotlib 渲染。
- 报告缺失信息显式标记 `NOT_AVAILABLE`，端点/渲染失败保留候选并将 Stage 置为
  `PARTIAL`。实验措辞仅表示 MP experimental-database provenance，绝不把计算
  性质表述为实验验证。
- 新增直接依赖 `matplotlib==3.11.1`、`mp-pyrho==0.5.1`；后者用于 CHGCAR
  标准化至 electrons/Å³。

只有以下条件全部满足，才进入完整 Orchestrator 实施：

- 依赖锁文件可在 Python 3.11 环境复现；
- `pip check` 通过；
- 所有离线 unit、contract、integration 和 E2E 测试通过；
- 真实 MP Si/O release test 通过；

### Adaptive MP screening v2（2026-07-31）

- 新增版本化 `mp-capability-catalog-v1` 与独立 `mp-screening-spec-v1`；LLM 只能选择受控 capability ID。
- `retrieval-policy-mp-adaptive-v2` 编译 Summary pushdown/返回字段，代理条件仅参与排序，v1 fixture 保持兼容。
- 增加带宽、交叉风险、常见价态和周期连通性特征提取器，以及 spec hash、预算和确认校验。
- 离线 Gate：447 passed, 9 skipped；`pip check` 与 `git diff --check` 通过。

### Adaptive Summary provenance 修正（2026-07-31）

- 修复 adaptive v2 新增 Summary 属性缺少 origin alias 时触发的 `KeyError`；该错误此前会被
  外层错误标记成 `STRUCTURE_INVALID`。新增属性现在以其自身字段名作为 provenance fallback。
- 真实 MP `run_008` 证实原始结构 payload 有效；该 run 保留为失败审计证据，后续运行使用新 run ID。

### Adaptive Summary 标量契约修正（2026-07-31）

- 真实 MP `run_011` 发现 `possible_species` 是字符串列表；将其直接写入冻结的
  `PropertyValue` 标量字段会触发 `ValidationError`，并被外层误报为结构处理失败。
- adaptive v2 现在以排序后的 `"; "` 分隔字符串记录该报告属性，原始列表继续保留在
  不可变的 Summary gzip artifact 中；不改变 `agent01-contract-v1` JSON Schema 或 fixture。

### Adaptive Summary 早期过滤与运行反馈（2026-07-31）

- `composition.has_transition_metal` 为本地精确硬条件，不可错误下推为 MP `elements` 的
  AND 查询；现在在原始 Summary 归档后、结构规范化前执行，因此不匹配记录不会触发 CIF
  往返或 CrystalNN 二维性计算。
- 仅抑制 pymatgen 已知的 `gcd is deprecated` 重复 `FutureWarning`；其余结构与
  二维性 warning 继续写入审计和报告。
- 真实 `run_012` 在 5000 条截断 Summary 中仅有 191 条包含过渡金属、其中 2 条为二维；
  因此该截断窗口不能用于“数据库中不存在候选”的科学结论。均匀 k 网格带宽仍需要用户
  明确费米能量窗口，缺失时保持 `UNCERTAIN`。

### StageRunner warning shadowing 修复（2026-08-03）

- 修复 `run()` 内阶段 warning 列表覆盖 Python `warnings` 模块，导致结构处理在
  `warnings.catch_warnings()` 处触发 `AttributeError` 并连锁破坏 Agent01/Orchestrator
  回归的问题；阶段 warning 列表改用独立名称。
- Agent01 契约、检索 E2E 与 adaptive 单元测试通过；完整离线 Gate 为 `454 passed,
  9 skipped`，`pip check` 与 `git diff --check` 通过。
- Requirement 和全部输出 artifact 的 hash 校验闭环；
- run-scoped manifest 和结构 lineage 可供 Agent 02 使用；
- resume 不重复查询、不覆盖已完成证据；
- 报告漏斗、状态和证据措辞符合本计划；
- 公共 StageRunner、Candidate、PropertyValue 和 StageResultEnvelope 契约冻结；
- README 包含离线、真实查询、测试、artifact 布局和已知限制；
- 系统总 Plan 补充 Agent 01 详细计划链接和职责边界。

通过 Gate 后的下一项工作 Orchestrator P0 已完成：

> Requirement 确认与冻结 → ExecutionPlan → Agent 01 → Envelope 校验 → Report → checkpoint/resume

Agent 02、Agent 03 和 Agent 04 可在公共契约冻结后使用 fixture 并行开发，但不阻塞 Orchestrator P0 的真实 Agent 01 主链。

### 用户可选数据库入口核查与修正（2026-08-03）

- `material-agent run` 和 `material-agent retrieval` 现只显示并接受已注册的
  `materials_project`、`nomad`、`mc3d`、`c2db`、
  `topological_quantum_chemistry`、`nims_supercon` 及 `auto`。未注册的
  `atomly` 不再出现在 CLI，直接 API 调用也会在 query planning 阶段 fail closed；避免
  用户在 runner factory 才看到无 Adapter 的错误。
- 仍保持一个 Run 只选择一个来源。`auto` 对 `fm_2d_semiconductor` 选 C2DB、对
  `topological_flat_band` 选 TQC、其余选 MP；不会跨库补全缺失性质或合并候选。
- 当前公开只读联网 probe：MC3D metadata 与 1 条检索成功；C2DB metadata 成功、Si/O
  限定查询返回 0 条；TQC metadata 与 1 条详情检索成功；NIMS SuperCon metadata 与 1 条
  数据表检索成功。NIMS 明确没有原子坐标，仍阻止其记录进入下游结构筛选。
- 验收：来源 adapter/query/orchestrator/CLI 聚焦测试 `35 passed`；完整离线 Gate
  `463 passed, 9 skipped`，`pip check`、`git diff --check` 通过。

### NOMAD/C2DB 公开访问重试与平带证据语义修正（2026-08-04）

- 以公开、只读 HTTPS 验证 NOMAD OpenAPI（`v1, NOMAD 1.4.3.post1`）和 C2DB
  `/help` 可访问；NOMAD 的显式 `live_nomad` release Gate 成功（`1 passed`）。此前
  NOMAD 批量失败源于大页/不完整 archive，而非服务不可达；当前以 10 条分页和 5.2 秒
  限流完成 20 条 Fe 限定窗口。
- C2DB 以 Fe 限定、小页会话检索完成 20 条窗口，并逐条取得官方结构 JSON；两来源的
  Agent01 运行均完整保存 raw response、query fingerprint、coverage 和 manifest。
- 修复一个目标语义缺口：`topological_flat_band` 目标现在将所选来源在 coverage catalog
  中明确“不判定”的带宽、费米窗第一带、轨道投影、交叉、TM 价态、贡献子晶格连通性及
  vdW gap 写入每条候选的 `missing_evidence`，从而得到 `UNCERTAIN` 而非仅因 Fe/二维
  结构匹配而得出的 `PASS`。`UNCERTAIN` 仍遵守发布策略进入下游；明确 `REJECT` 或
  `FAILED` 仍被阻断。
- 真实结果：C2DB 返回 20、明确拒绝 5、发布 15 个 `UNCERTAIN`；NOMAD 返回 20、结构
  无法规范化 6、明确拒绝 13、发布 1 个 `UNCERTAIN`。两次均为 `PARTIAL`，原因是有界
  窗口截断（以及 NOMAD 的 6 个无效结构），不表示数据库访问失败。
- 新增目标证据单元覆盖；相关 evaluator/source capability/NOMAD/C2DB 测试
  `29 passed`。本次最终完整离线 Gate、`pip check` 与 `git diff --check` 待提交前复跑。

### 单数据库选择与 LLM 来源推荐（2026-08-04）

范围：在既有单来源 Adapter/query plan 基础上，为已确认 Requirement 提供五个主要数据库
（Materials Project、C2DB、NOMAD、TQC、MC3D）的结构化 LLM 推荐入口；每次推荐只能返回一个
数据库，不能修改 Requirement、合并来源或补齐跨库性质。既有 NIMS SuperCon 显式适配器保留
向后兼容，但不进入推荐目录，因为它没有 canonical structure。

- [x] 新增 `retrieval.source_recommendation` 的严格 `SourceRecommendation` 契约、版本化 prompt、
  五库 capability catalog、Requirement hash 和 provider audit 保存；LLM 输出在本地再次校验。
- [x] 新增 CLI `material-agent recommend-source --requirement ...`；未配置显式 LLM 时 fail closed，
  推荐结果需由用户再传给正常 `--source` 检索入口。
- [x] 新增单元覆盖：五库允许集合、NIMS/Atomly/多源拒绝、未确认 Requirement 拒绝、hash 与
  provider payload 绑定、extra field 拒绝；相关来源与 E2E 测试 `22 passed`。
- [x] 完整离线 Gate `489 passed, 9 skipped`、`.venv/bin/python -m pip check` 和
  `git diff --check` 通过；真实 LLM 仅在显式批准、密钥注入和新 run/artifact 范围下验证，
  不作为默认测试。

### 五来源完整响应保留与连通性核查（2026-08-04）

- [x] 审计确认五个 Adapter 均已接入 `metadata → query plan → search → Runner`，但此前只把
  统一字段写入 raw batch；现已保留来源响应：MP metadata advertised fields、NOMAD archive
  entry、MC3D complete OPTIMADE entry、C2DB table HTML/row/download JSON、TQC search item/detail
  JSON/CIF。Candidate manifest 仍只发布规范化字段。
- [x] MP query plan 改为请求当前 metadata 宣布的全部字段；MC3D 移除 response_fields 限制；
  各来源原始响应仍受既有扫描上限、分页、Artifact hash 和重试策略约束。
- [x] 新增上述 raw payload 的 Adapter 单元断言；来源回归 `29 passed`，完整离线 Gate
  `489 passed, 9 skipped`。
- [x] 受控公开只读探针已验证 C2DB（无元素限制 1 条）、NOMAD（1 条）、MC3D（1 条）和
  TQC（1 条）均可返回记录，且四者均有 `source_response`；C2DB 的 Si/O 条件零结果被确认
  是查询结果而非 Adapter 失败。NOMAD 首次 archive 请求超时，第二次 60 秒单条请求成功，
  说明外部服务仍需遵守既有重试/限流策略。
- [x] 使用用户临时注入的 `MP_API_KEY` 完成真实 MP release Gate：固定 Si/O 查询 `1 passed`
  （81 条返回、81 条通过并发布；可选重端点失败按 `PARTIAL` 明确记录），Orchestrator
  restart/resume `1 passed`；均验证 Artifact 完整性、幂等性和凭据未写入产物。凭据未写入
  仓库、配置或日志。
- [x] 真实五来源闭环现已具备证据：C2DB、NOMAD、MC3D、TQC 各 1 条公开只读探针成功，MP
  两项 release Gate 成功；来源原始响应均保留于 raw Artifact，无法提供的来源性质仍显式
  标记为缺失/不确定。

### 八类用户硬约束跨来源复核（2026-08-05）

- [x] `exact_formula` 在 Requirement contract 阶段校验，并在候选评估阶段按 canonical
  reduced composition 比较；公式缺失为 `UNCERTAIN`，配比不符为 `REJECT`。
- [x] `include_elements`、`exclude_elements`、`band_gap_ev`、
  `energy_above_hull_ev_atom`、`is_metal`、`dimensionality`、`max_num_sites` 和
  `exact_formula` 均在最终本地 evaluator 逐条核验。数据源不提供的性质不被推测，保持
  `MISSING/UNCERTAIN`；已有的来源级 pushdown 仅作为扫描优化。
- [x] MP、C2DB、NOMAD、TQC、MC3D 的 query plan 均记录 `exact_formula` 为本地约束，
  避免依赖未经来源 API 契约确认的公式过滤参数。
- [x] 新增公式匹配/不匹配/缺失、非法公式及五来源 query-plan 覆盖；相关测试
  `40 passed`。完整离线 Gate、`pip check` 和 `git diff --check` 待本轮结束复跑。

### 数据库专属简单约束（2026-08-05）

- [x] 新增严格的 `hard_constraints.source_constraints`：MP 支持 density、volume、
  formation energy、stability、crystal system、space-group number、direct-gap flag
  和 magnetic ordering；C2DB 支持 layer group、magnetic label；TQC 支持
  topological classification/subclassification、topological-index presence、SOC、
  Fermi-crossing count 和 line-crossing label。
- [x] 这些字段均在 normalization 后由统一 evaluator 做标量/区间/标签比较；来源不匹配
  时 query planning fail closed，字段缺失或类型错误时为 `UNCERTAIN`/`ERROR`，不跨库补值。
- [x] NOMAD 与 MC3D 当前没有额外稳定且标准化的简单字段，因此不虚构专属约束；已有的
  公共八类约束继续按其实际字段覆盖执行。
- [x] 更新 source capability catalog、README 与冻结 Agent01 fixture；完整离线 Gate
  `498 passed, 9 skipped`，`pip check` 和 `git diff --check` 待本轮结束复跑。

### 真实 DeepSeek 与五来源端到端验收（2026-08-05）

- [x] 冻结真实测试需求：必须包含 Si/O、排除 C、带隙 `0–10 eV`、凸包上方能量
  `0–10 eV/atom`、非金属、指定维度、最多 100 个原子；并对每个来源使用实际可检索
  的元素窗口。需求经 DeepSeek `deepseek-v4-pro` 真实调用后，返回单一推荐
  `materials_project`，置信度 `0.9`，Requirement hash 和 provider audit 均通过本地校验。
- [x] 真实 MP 小窗口：返回 10 条、归一化 10 条、8 条 `PASS`、2 条 `REJECT`；8 条
  公共约束均进入审计，且 density、formation energy、crystal system、spacegroup 等
  MP 扩展字段真实写入候选属性。可选重型报告端点关闭以避免非筛选 S3 端点长时间阻塞。
- [x] 真实 C2DB：返回 5 条、归一化 5 条，结构/带隙/凸包能/金属性/layer group 等
  真实字段进入审计；金属性冲突按 `REJECT` 处理。
- [x] 真实 NOMAD：返回 10 条、归一化 10 条，8 条 `UNCERTAIN` 发布，凸包能和部分
  金属性保持缺失；没有跨源补值。
- [x] 真实 TQC：返回 5 条、归一化 5 条，4 条 `UNCERTAIN` 发布；TQC 分类、子分类、
  SOC、拓扑指标存在性和费米面交叉诊断真实写入属性。
- [x] 真实 MC3D：返回 5 条、归一化 5 条，4 条 `UNCERTAIN` 发布；结构、公式、原子数、
  三维性真实可判定，缺失电子/热力学字段保持不确定。
- [x] 五来源均完成 `exact_formula` 真实复核：MP、C2DB、NOMAD、TQC、MC3D 均生成
  `EXACT_FORMULA_MATCH` 或明确 `EXACT_FORMULA_MISMATCH` 审计结果；实时窗口变化导致
  的不匹配被正确拒绝，没有误放行。
- [x] 真实凭据只通过进程环境注入，未写入源码、配置、日志或仓库 Artifact；完整离线
  Gate `498 passed, 9 skipped`，`pip check` 和 `git diff --check` 通过。

### 数据库原生 Requirement 编译与 MP adaptive 深筛退役（当前工作区）

- [x] 新增 `SourceRequirement`：用户 Requirement 在选定数据库后按该数据库的
  Agent01 capability catalog 编译；可映射条件进入 `mapped_constraints`，无法表达或
  属于其他数据库的条件保留在 `unmapped_constraints`，不再在查询规划阶段直接拒绝或
  静默丢弃。
- [x] standalone CLI 与 Orchestrator 均写入 source-native Requirement Artifact；查询
  fingerprint 纳入未映射条件，候选评估将跨库未映射条件保留为缺失证据。
- [x] MP adaptive/deep-screen 执行入口从 CLI、Orchestrator 和 Runner 主路径移除；MP
  仍保留普通 Summary 检索与报告 enrichment。历史 `mp_screening` 兼容模型暂不作为
  生产执行路径。
- [x] 当前工作区离线 Gate `500 passed, 9 skipped`，`pip check`、`git diff --check`
  通过；公开 NOMAD release Gate `1 passed`。本机当前没有可用的 DeepSeek/MP API key，
  因此本轮真实 LLM 与 MP Gate 未成功执行。
