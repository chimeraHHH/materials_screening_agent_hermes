# P3.x Inspiration Generalization 实施与实验计划

- `run_id`: `p3x-inspiration-generalization-20260808`
- 实验层级：`main/test`
- 当前分支：`agent/inspiration-generalization`
- 只读基线：`hermes-origin/main@dcd076d01061f36a2f9826deecefc274c9c3211d`
- 控制清单：[`CHECKLIST.md`](CHECKLIST.md)
- P3.3 主实验计划：[`P33_EXPERIMENT_PLAN.md`](P33_EXPERIMENT_PLAN.md)
- P3.3 执行清单：[`P33_EXPERIMENT_CHECKLIST.md`](P33_EXPERIMENT_CHECKLIST.md)
- 详细产品计划：
  [`plans/subagents/material-screening-inspiration-plan.md`](plans/subagents/material-screening-inspiration-plan.md)

## 1. 选择的路线

保留已经验收的 fixed-request pilot，不改写它的历史结论；在独立 P3.x 轨道把它扩展为
单用户、本机、可复用的 Hermes inspiration beta。首要路线是先打通“受支持自然语言请求 →
冻结审批 → 同一 Gateway run 的 Crossref metadata 搜索 → 可审计 proposal”，再补齐真实
多候选去重/多样性和受限多格式 passage/tag 反馈闭环。

该路线继续只支持 curated flat/narrow-band 问题、operator-owned parent catalog 和白名单结构
变换。它不扩展为任意材料科学 planner，也不做 novelty、prior-art、专利或性质背书。

## 2. 研究问题与假设

- 研究问题：能否在不开放任意路径、任意结构生成、全文读取和科学结论权限的前提下，解除
  单一精确请求与离线 fixture 限制，并以同一 Hermes/Gateway run 完成稳定、低成本、可恢复的
  灵感生成？
- 零假设：解除固定请求或接入真实搜索后，系统无法同时维持严格审批、预算、provenance、
  确定性 replay、运行内去重和科学边界。
- 备择假设：通过确定性 request compiler、受信 catalog、metadata-first adapter、有限重试和
  版本化 TagGraph，可在现有 companion 边界内满足全部 P3.x Gate。
- 最强替代解释：成功仅来自单一 TiS2 fixture 或单候选路径，不能证明通用请求、多候选选择或
  多格式证据链。P3.2/P3.3 Gate 必须专门排除这一解释。

## 3. 完成范围

### P3.1：Generalization & Reliability

- 多种自然语言表述可确定性编译到受支持的 flat/narrow-band target；所有未支持 target、冲突
  约束、无语义字段和超预算请求均 fail closed。
- 保留 fixture factory 作为离线 replay；生产 Hermes profile 改用真实 Crossref factory。
- 同一 approval-bound Gateway run 持久化 Crossref raw response、Passage、EvidenceCard、
  BridgePacket、proposal、bundle 和两套分离成本边界。
- 408/429/明确的瞬时 5xx/网络错误采用有界重试；尊重有上限的 `Retry-After`，4xx/schema
  drift 不重试，每次 attempt 均可审计。
- 同一 DOI/arXiv/URL 的多 query hit 在文档/passage/vector 层只处理一次，同时保留全部 query
  和 raw-hit lineage；外部故障不得伪装为 `SCIENTIFIC_NO_MATCH`。

### P3.2：Candidate Breadth & Diversity

- 引入 SHA-pinned、operator-owned parent catalog；Hermes 不能提交任意文件路径或自由 CIF。
- 冻结一个至少产生 5 个结构有效 proposal 的多 parent/multi-route Gate；相同输出结构合并但
  保留全部路线与证据。
- `top_k=5` 时 exact/strict duplicate 为 0；池中存在多个机制时覆盖至少 2 个 mechanism；
  候选不足时诚实少输出并说明未满足原因。
- `require_diverse_routes` 映射到可验证 policy/报告语义，排序 hash 与 frozen input 可重放。

### P3.3：Passage & Tag Feedback

- 把现有 JSON metadata、JSON-LD/Highwire、JATS/XML 和普通 HTML 抽取器接入统一、可注入、
  受字节/请求/host/content-type 约束的 runner 路径；metadata 足够时不 fetch。
- 默认 PDF 全文读取保持为 0；vectorizer/内部 LLM 只接收 title、section heading、选定 passage
  和 normalized tags，不接收全文。
- 每个 query/tag/bridge 记录 hit、unique-document、EvidenceCard、supported-bridge、字节和成本
  收益；反馈只生成 review artifact，不能在线改写 curated TagGraph。
- 离线校准集覆盖当前 3 条 cross-domain bridge rule，并记录启用、拒绝、证据不足和专家状态；
  无专家判定时必须标为 `UNKNOWN`，不得伪造校准结论。

### 最终 release Gate

- 完整离线、契约、安全、篡改、预算、重试、恢复、多格式、去重和多样性测试通过。
- live Crossref runner 与同一 Gateway lifecycle 通过；真实 Hermes 自然语言 session 使用
  Crossref 而不是 fixture，并完成 `run → user approval/grant → act → result`。
- provenance/引用闭包 100%，默认 PDF 0，内部 LLM 0，Top-K exact/strict duplicate 0，
  novelty/validated-property 声明 0。
- 所有工作报告、run record、代码、测试和 GitHub PR/检查/合并证据一致。

## 4. 明确不在本轨道完成的事项

- 第二公共 provider、远程 embedding/vector database、pairwise reranker；
- 任意 chemistry、LLM 自由生成坐标或在线自改 TagGraph；
- 把 inspiration 升级为冻结四阶段状态机的第五 Stage；
- Postgres、多用户、RBAC、公网部署、后台队列、UI；
- novelty、prior-art、专利判断和任何已验证性质结论。

这些项目可以后续另立版本，但不得写成 P3.x 已完成能力。

## 5. 基线与可比性

- baseline：公开主线上的 P0.3 fixed-request pilot，tree
  `e00aa04d9518310adcbea8f3a5e5a89eac06d503`。
- baseline 已有：4-tool Gateway、审批/恢复、固定 fixture replay、独立 Crossref runner Gate、
  passage-only signed hashing、单次运行候选 identity/MMR、真实 Hermes fixture session。
- baseline 不证明：非精确请求、同一 Hermes run 的 Crossref、provider retry、文档级处理去重、
  真实 Top-5 多样性、多格式 runner 集成或 tag feedback。
- P3.x 不修改 novelty/科学证据语义、Artifact hash 规则、Hermes/材料环境隔离和四阶段
  `StageId`；这些变化会使比较失效，必须先更新 ADR/PLAN。

## 6. 指标与停止条件

必须记录的工程指标：

- request compile：受支持案例通过率、未支持案例拒绝率；
- search：logical queries、HTTP attempts、retries、sleep、response bytes、unique documents；
- passage：raw hits、unique passages、vectorized passages、重复处理节省量；
- bridge/candidate：EvidenceCards、supported bridges、plans、dedup candidates、selected、
  mechanism coverage、exact/strict duplicate；
- 边界：fetch/PDF/内部 LLM 数量、UNKNOWN property、`scientific_conclusion=false`；
- replay：关键 JSON/JSONL/CIF/report/stage-result hashes。

停止条件：P3.1–P3.3 与最终 release Gate 全部通过、工作报告和 GitHub 证据闭合。放弃/重定向
条件：需要开放任意文件/代码、绕过人工审批、引入未经评审科学 registry，或两次相同失败没有
新的代码/环境/证据变化；此时先形成结构化 failure report，再决定新路线。

## 7. 最小代码变更图

| 路径 | 当前角色 | 计划变更 | 主要风险 |
|---|---|---|---|
| `src/material_agent/integration/hermes_service.py` | 固定 fixture service | request compiler、live factory、catalog preparer | 输入被静默忽略、网络审批漂移 |
| `src/material_agent/inspiration/search.py` | 单次 Crossref GET | typed transient error、Retry-After、有界 retry/attempt | 不可控等待、错误重试 |
| `src/material_agent/inspiration/runner.py` | 完整 pipeline | attempt artifact、文档级处理去重、可恢复搜索、fetch seam、feedback | Artifact 幂等和 lineage 丢失 |
| `src/material_agent/inspiration/engine.py` | 单 bridge、单 substitution route | 受 catalog/policy 约束的多 parent/multi-route 生成 | 重复结构或空泛机制 |
| `src/material_agent/inspiration/reporting.py` | 简要权威报告 | query/attempt/raw/passage/bridge/route/dedup/MMR/ledger 细节 | 报告与 authoritative artifact 漂移 |
| `integrations/hermes/` | 固定请求 Skill/profile | 受支持词汇、live service、fail-closed 指引 | Agent 把审批或 UNKNOWN 说错 |
| `tests/` | pilot 回归 | compiler/retry/live lifecycle/diversity/fetch/tag feedback Gate | fixture 冒充 live 能力 |

## 8. 执行设计与证据梯度

- minimum：P3.1 静态 transport + Gateway E2E 通过，现有 baseline 全绿。
- solid：P3.1 live Crossref、P3.2 Top-5、多格式/反馈 P3.3 离线 Gate 均有持久化 Artifact。
- maximum：真实 Hermes+Crossref release session、完整回归、secret/diff/dependency 检查、公开
  GitHub PR 合并和 release record。只有达到 solid 后才执行 maximum。
- smoke：按模块运行定向 unit/contract/integration；不把 smoke 当 release 结果。
- main：完整非 live suite、opt-in live Crossref、隔离 MCP、Hermes profile/bundle verifier 和
  一次经用户授权的真实 Hermes session。

运行和工作报告放在 `docs/runs/` 与 `docs/reports/`。任何实机证据必须包含精确命令、版本、
branch/SHA、run/session ID、Artifact/hash、成本与失败；credential 只存在 ignored runtime，
不得读取、打印或提交。

## 9. 回退与恢复

- Crossref 不可用：记录 `external_dependency_blocked`，保留 retry/attempt 证据；fixture 只能验证
  契约，不能替代 live Gate。
- Hermes provider 不可用：先完成不依赖 provider 的 Gate，记录真实阻塞；不得伪造自然语言 run。
- live 响应发生变化：用身份/契约/闭包指标验收，不固定科学内容；每次 raw bytes 单独 hash。
- 多候选 scientific catalog 不足：仅提交明确标注的工程 fixture，不能声称专家接受或真实性质。
- 新路径破坏 baseline：停止扩张，回到最近一个已提交、可执行、可解释的 checkpoint 调试。

## 10. 修订记录

| 时间（CST） | 变更 | 原因 | 可比性影响 |
|---|---|---|---|
| 2026-08-08 17:57 | 从 `hermes-origin/main@dcd076d` 建立 P3.x 计划 | 用户继续授权，fixed pilot 不能代表完整 beta | 保留 pilot 为只读 baseline；新 Gate 独立计量 |
| 2026-08-08 18:37 | P3.1 Generalization & Reliability Gate 通过 | compiler、retry/attempt、同轮去重、生产 Crossref Gateway、报告、Skill 与 live lifecycle 均有闭合证据 | 仍只有 pinned parent/route 和单候选；不提前声称 P3.2/P3.3 或最终 Hermes release 完成 |
| 2026-08-08 19:24 | P3.2 Candidate Breadth & Diversity Gate 通过 | SHA-pinned catalog、6→5 exact merge、Top-5、机制/路线审计和双工作区 replay 均通过 | 工程校准结构不构成性质证据；P3.3 fetch/feedback 与最终真实 Hermes release 仍未完成 |
| 2026-08-08 19:41 | 冻结 P3.3 主实验契约 | 三路只读审计确认 pure extractors 可复用，缺口集中在受限 fetch seam、runner lineage 与 immutable feedback | 保持 public schema、P3.2 catalog、默认 public fetch=0 和科学边界不变 |
| 2026-08-08 20:23 | P3.3 Passage & Tag Feedback Gate 通过 | metadata-first skip-fetch、三格式离线 body seam、selected-passage-only vector、immutable feedback、共享 DOI/预算/瞬时故障和 replay 均闭合 | public body fetch 因 DNS preflight/connection 未绑定而继续为 0；P3.x 只剩 exact-user-approved 真实 Hermes release 与 GitHub closeout |
| 2026-08-08 20:42 | 真实 Hermes submit Gate 与知情审批修复通过 | 首个 pending prompt 错称 offline，保持 0 grant/0 result；修复后 v2 明示 Crossref 公网 metadata、8 attempts、body/PDF/model=0，真实 Hermes 单次 run 后停止 | final act/result 仍必须由用户在看到 exact interaction 与 manifest 后重新授权；不得使用先前泛化授权代签 |
