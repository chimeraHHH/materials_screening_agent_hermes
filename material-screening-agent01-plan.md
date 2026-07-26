# Agent 01：Materials Project 材料检索与确定性筛选实施计划

版本：v0.2  
日期：2026-07-25  
最近进度更新：2026-07-26  
工期：总计划 Day 3–5，约 18–21 小时  
依据：`material-screening-agent-system-plan.md` 与 `material-screening-orchestrator-plan.md`

## 0. 当前实施进度

当前状态：**P0 已实现并通过离线测试与真实 Materials Project API 验收；P1 性能和扩展项尚未开始。**

### 0.1 已完成

- [x] 在当前目录初始化独立 Git 仓库，当前分支为 `main`；
- [x] 建立仓库本地 Python 3.11.13 `.venv` 隔离环境；
- [x] 固定直接依赖和完整传递依赖，核心版本为 `mp-api==0.45.15`、`pymatgen==2025.10.7`、`pydantic==2.12.5` 和 `emmet-core==0.86.2`；
- [x] 建立 `material-agent retrieval` CLI、Pydantic 输入输出模型和 StageResultEnvelope；
- [x] 实现 Requirement 校验、Query Plan、query fingerprint、MP 查询参数下推和本地约束复核；
- [x] 实现真实 Materials Project Adapter 与不依赖 API key 的离线 fixture Adapter；
- [x] 实现 database version snapshot；兼容固定版本客户端的 `get_database_version()` 接口；
- [x] 实现受控查询、瞬时错误重试、schema drift 检测、零结果和扫描截断状态；
- [x] 实现 gzip JSONL 原始响应归档、manifest、原子写入、hash 校验和 artifact 路径保护；
- [x] 实现结构解析、source JSON、canonical CIF、稳定 candidate ID、structure ID 和 query ID；
- [x] 实现 summary/structure 一致性检查、CrystalNN + Larsen 维度分析及显式质量标记；
- [x] 实现 band gap、energy above hull、is_metal 等性质的 origin task 解析和降级状态；
- [x] 实现 `PASS/REJECT/UNCERTAIN/FAILED` 决策、闭区间容差和缺失证据处理；
- [x] 实现精确重复标注、非破坏性 StructureMatcher 相似聚类和确定性排序；
- [x] 实现全量审计账本、下游 candidate manifest、JSON/Markdown 报告和幂等结果复用；
- [x] 实现 resume 时对数据库版本、Requirement revision 和 policy 变化的拒绝检查；
- [x] API key 仅通过 `MP_API_KEY` 读取，未写入源码、配置、报告或其他项目产物；
- [x] 清理真实 API 运行产物、Python/pytest 缓存和安装元数据；运行目录可在下次执行时自动重建。

### 0.2 验证状态

- 自动化测试：`27 passed`，覆盖 unit、contract、integration 和离线 E2E；
- 清理运行产物后再次以禁止生成 bytecode/pytest cache 的方式执行全量测试，仍为 `27/27` 通过；
- 固定 Si/O 用例已使用真实 Materials Project API 验收：
  - Materials Project database version：`2026.04.13`；
  - 数据库返回并规范化 81 条候选；
  - 81 条全部通过 Si/O、0.5–1.0 eV band gap、energy above hull ≤ 0.05 eV/atom 和非金属约束；
  - 约束违规、结构文件缺失、结构文件为空、规范化失败、来源未解析均为 0；
  - 405 项候选性质 origin 状态全部为 `RESOLVED`；
  - 未发生扫描截断或发布截断。

上述真实 API 验收数据只用于联调确认。其原始响应、候选 CIF、manifest 和筛选报告已按项目清理要求删除，不进入 Git；需要审计复现时使用相同 Requirement 和数据库版本重新运行。

### 0.3 尚未完成

- [ ] P1：真正的逐页 cursor checkpoint；
- [ ] P1：大规模 StructureMatcher 性能优化；
- [ ] P1：并行结构分析；
- [ ] P1：robocrystallographer dimensionality 交叉验证；
- [ ] P1：MP Similarity endpoint 对照；
- [ ] P1：多数据库 Adapter；
- [ ] P1：科学 silver set 扩充；
- [ ] P1：用户可配置的扫描上限提升审批；
- [ ] 将 Agent 01 详细计划链接补入总系统计划对应章节；
- [ ] 创建项目首次 Git 提交；当前源码、测试和计划文件尚未提交。

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

| Requirement 约束 | MP 查询下推 | 本地复核 |
|---|---|---|
| `include_elements` | `elements=[...]` | 必须全部出现 |
| `exclude_elements` | `exclude_elements=[...]` | 不得出现任一元素 |
| 完整 band gap 范围 | `band_gap=(min,max)` | 按闭区间复核 |
| 仅 band gap 上限 | `band_gap=(0,max)` | 按用户边界复核 |
| 仅 band gap 下限 | v1 不构造虚假上限，留给本地筛选 | 本地复核 |
| 完整 hull energy 范围 | `energy_above_hull=(min,max)` | 按闭区间复核 |
| 仅 hull energy 上限 | `energy_above_hull=(0,max)` | 本地复核 |
| `is_metal` | `is_metal=...` | 本地复核 |
| `max_num_sites` | `num_sites=(1,max)` | 以解析后结构位点数复核 |
| `dimensionality` | 不下推 | 本地结构算法 |
| 高级科学目标 | 不下推 | 生成证据缺口 |

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
- 已新增独立 `material-screening-agent01-plan.md`；总计划的 Agent 01 小节仍需补充该文档链接和“确定性筛选归 Agent 01、ML 归 Agent 02”的职责说明，不覆盖原总计划。

## 8. 下一步实施规划与 Orchestrator 进入 Gate

本节基于 2026-07-26 的仓库只读扫描，作为 Agent 01 从“离线主链已运行”进入“真实可验收、接口可冻结”阶段的执行顺序。

### 8.1 当前实现基线

当前仓库已经具备：

- 独立 Git 仓库，但 `main` 尚无首次提交；
- Python 3.11.13 仓库本地 `.venv`；
- 锁定依赖且 `pip check` 通过；
- Agent 01 查询、Adapter、规范化、结构处理、筛选、排序、去重聚类、报告、CLI 和本地 Artifact Store；
- 固定 Si/O 离线 fixture；
- unit、contract、integration 和 offline E2E 共 27 个测试，当前全部通过。

当前实现还不能视为 Agent 01 完成，原因包括：

- 尚未通过真实 Materials Project API 验收；
- `RetrievalStageInput` 中的 Requirement URI/hash 尚未作为真实输入完整性边界校验；
- 权威候选 manifest 尚未按 Stage Run 隔离；
- 完成 operation 的 artifact 损坏后，当前恢复语义仍可能重新执行，而不是停止并报告不一致；
- 计划要求的单位、结构、失败注入和边界测试尚未全部覆盖；
- Agent 02 和 Orchestrator 将消费的公共输出契约尚未冻结。

### 8.2 执行顺序

下一步固定按以下顺序实施，不先展开完整 LangGraph Orchestrator：

1. 建立当前通过测试状态的 Git 基线，确认 `.venv`、`workspace/`、环境文件、密钥和缓存未进入版本控制。
2. 修复 Requirement 输入、快照、manifest 和 operation artifact 的完整性与不可变语义。
3. 补齐单位、结构、筛选、排序、API 错误、恢复和报告测试。
4. 使用环境变量 `MP_API_KEY` 执行固定 Si/O 真实查询。
5. 冻结 Agent 01 对 Agent 02 和 Orchestrator 的公共输出契约。
6. 生成一份由冻结契约校验过的 Agent 01 输出 fixture。
7. Agent 01 通过本节 Gate 后，再实现 Orchestrator 的真实 `Stage 0 → Agent 01 → Report` 链路。

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
prepare(context) -> RetrievalQueryPlan
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

### 8.8 Agent 01 完成 Gate

只有以下条件全部满足，才进入完整 Orchestrator 实施：

- 依赖锁文件可在 Python 3.11 环境复现；
- `pip check` 通过；
- 所有离线 unit、contract、integration 和 E2E 测试通过；
- 真实 MP Si/O release test 通过；
- Requirement 和全部输出 artifact 的 hash 校验闭环；
- run-scoped manifest 和结构 lineage 可供 Agent 02 使用；
- resume 不重复查询、不覆盖已完成证据；
- 报告漏斗、状态和证据措辞符合本计划；
- 公共 StageRunner、Candidate、PropertyValue 和 StageResultEnvelope 契约冻结；
- README 包含离线、真实查询、测试、artifact 布局和已知限制；
- 系统总 Plan 补充 Agent 01 详细计划链接和职责边界。

通过 Gate 后的下一项工作固定为 Orchestrator P0：

> Requirement 确认与冻结 → ExecutionPlan → Agent 01 → Envelope 校验 → Report → checkpoint/resume

Agent 02、Agent 03 和 Agent 04 可在公共契约冻结后使用 fixture 并行开发，但不阻塞 Orchestrator P0 的真实 Agent 01 主链。
