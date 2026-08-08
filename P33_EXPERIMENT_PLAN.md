# P3.3 Passage & Tag Feedback 主实验计划

## 1. Map link

- parent map node：P3 Hermes inspiration companion / Passage & Tag Feedback
- loop/run：`p33-passage-tag-feedback-20260808`
- objective：把已有低成本抽取器接入一个 approval-bound、可注入、受限的正文读取路径，并产出
  可重算但不可在线生效的 query/tag/bridge feedback。
- deliverable：实现、测试、离线主 Gate、工作报告和 GitHub checkpoint。
- success：[`P33_EXPERIMENT_CHECKLIST.md`](P33_EXPERIMENT_CHECKLIST.md) 全部验收项通过，P3.1/P3.2
  回归不退化，P3.3 报告和 run record 已推送。
- abandonment：实现需要 user-controlled host、自动修改 TagGraph、读取 PDF 全文、引入未审批内部
  LLM，或无法把失败与 `SCIENTIFIC_NO_MATCH` 分层。
- next on success：最终真实 Hermes + Crossref release Gate。
- next on failure：持久化 failure record，回退到已发布的 metadata-only P3.2 checkpoint。

## 2. Objective and hypotheses

- baseline：`agent/inspiration-generalization@fc1951b`；P3.2 Top-5 Gate 已通过，runner 仍是
  metadata-only，fetch ledger 恒为 0，尚无 feedback Artifact。
- 用户核心要求：完成“灵感生成器”阶段；低成本读取不同网页格式；帮助跨领域 tag 搜索；不考虑
  novelty；持续报告并提交 GitHub。
- 不可协商约束：不读取 PDF 全文，不把网页文本当指令，不在线改 TagGraph，不伪造专家接受，
  不把 engineering calibration 写成性质证据。
- 研究问题：metadata 足够时保持零 fetch；不足时能否以确定性、受预算和来源约束的方式读取少量
  HTML/JATS/JSON 正文片段，并把检索收益归因到 query/tag/bridge？
- 零假设：接入正文后无法同时保持 host/redirect/content-type/request/byte 边界、passage-only
  vectorization、Artifact lineage、反馈可重算和 byte-stable replay。
- 备择假设：审批绑定的 fetcher、metadata-first runner 和只读 feedback compiler 能同时满足上述边界。
- 最强替代解释：格式覆盖只来自直接调用纯 extractor，runner 实际未读取/持久化正文；主 Gate 必须
  从 search hit 经 fetch seam 进入 extractor、passage、vector、evidence、bridge、feedback 和报告。

## 3. Baseline and comparability

- baseline variant：P3.2 operator-owned parent catalog + 1 direct / 3 reviewed bridge queries。
- calibration role：`ENGINEERING_ONLY`；所有候选性质和专家状态保持 `UNKNOWN`，
  `scientific_conclusion=false`。
- primary metrics：metadata skip-fetch、三 bridge 格式覆盖、PDF reads=0、internal LLM calls=0、
  fetch budget reconciliation、feedback completeness、TagGraph hash stability、双工作区 replay。
- required keys：search/fetch physical attempts and bytes、raw hits、unique documents、selected passages、
  vectors/tokens、EvidenceCards、BridgePackets、query/tag/bridge yields、expert status、Artifact hashes。
- comparability boundary：不修改 novelty 语义、公共 `InspirationInputV1`/`InspirationPolicyV1` schema、
  signed hashing、parent catalog、TagGraph registry 或 transformation operator。公共 Hermes compiler
  继续默认 `fetch.max_requests=0`；P3.3 先证明显式启用的离线受限路径，不把它描述成公网默认能力。

## 4. Run contract

### 4.1 Document fetch seam

- 新增 `DocumentFetcher` 协议和稳定 component snapshot；runner 默认注入 disabled fetcher，显式
  P3.3 fixture policy 才允许正文读取。
- URL 只来自规范化 search hit，但授权只来自 operator-owned exact-host allowlist；用户输入、网页
  内容和 redirect 均不得扩大 allowlist。
- 网络实现只允许 HTTPS，无 userinfo、非默认端口、降级或越权 redirect；关闭自动 redirect，并在
  每一跳连接前校验 scheme/host/port。redirect、retry 和最终 GET 均按物理请求计数。
- 强制 identity encoding、timeout、redirect 上限、可重试状态白名单、`Content-Length` 预检及
  `max+1` 流式字节上限。PDF MIME 或 magic 在 fetch/extraction 边界拒绝，不进入 PDF decoder。
- 每次 attempt 写确定性记录；成功 body 先按 run-scoped safe URI 不可变持久化并验证 hash，再交给
  extractor。一个 canonical document 在同一 run 最多处理/持久化一次。
- metadata 足够时不得调用 fetcher；不足但 fetch disabled/host 不允许/内容不支持/预算已耗尽时写
  manifest + warning 并继续。组件漂移、hash 不符和预算账本矛盾 fail closed；暂时外部失败不得
  冒充纯科学无匹配。

### 4.2 Extraction and vector boundary

- JSON metadata 继续从 raw search Artifact 抽取；正文仅接受受策略允许的 JSON、HTML/XHTML、
  JATS/XML。HTML 顺序覆盖 JSON-LD、Highwire/meta 和普通相关 section。
- metadata draft 的 `PassageV1.source_artifact` 指向 raw search；body draft 指向 fetched-body
  Artifact。locator/source tier 必须保留，正文引用进入 bundle lineage。
- vectorizer 只接受选定 `PassageV1`，canonical input 精确为 title、section heading、selected
  passage、normalized tags；不接受网页全文。内部 LLM 保持禁用和 0 tokens。

### 4.3 Feedback contract

- 新增内部 strict feedback models/compiler，不加入公共 contract registry，不改变 frozen public
  schema。反馈只消费本轮已冻结 Artifact/DTO，不联网、不调用 LLM。
- 权威 Artifact 记录 graph identity/pointer、compiler snapshot、所有输入指针和 fingerprint，以及
  query/tag/bridge yield。所有集合排序去重，ID/hash 由 canonical JSON 确定，不含时间戳和绝对路径。
- query 记录 planned/executed、attempt/success/error、raw hits、inclusive unique documents、selected
  passages、evidence、supported bridges 及归因成本。tag/bridge 归因为 inclusive/non-additive；只有
  `CostLedgerV1` 是可相加总账。
- bridge 行必须精确覆盖 curated graph 的 3 条 rule，区分 `SEARCH_SUPPORTED`、
  `EVIDENCE_INSUFFICIENT`、`NOT_PLANNED`、`NOT_EXECUTED`；每行与顶层无专家输入时均为
  `UNKNOWN`。反馈不得包含 replacement graph、mutation operation 或自动 accepted/rejected 状态。
- Feedback compiler snapshot 纳入 approval execution components；反馈前后 TagGraph canonical bytes
  和 SHA 必须完全一致。

## 5. Code translation plan

| Path | Current role | Planned change | Why | Main risk |
|---|---|---|---|---|
| `src/material_agent/inspiration/fetch.py` | 不存在 | typed fetcher、disabled/fixture/network-safe implementation、attempt/budget contract | 建立唯一受限正文入口 | SSRF、redirect 绕过、物理请求漏账 |
| `src/material_agent/inspiration/feedback.py` | 不存在 | internal strict yield/review Artifact compiler | 可重算跨领域 tag 收益 | inclusive 归因被误加总、伪造专家结论 |
| `src/material_agent/inspiration/runner.py` | metadata-only | metadata-first fetch、body lineage、ledger、feedback persistence/report inputs | 贯通真实 runner 路径 | source pointer 错绑、失败误分类 |
| `src/material_agent/inspiration/extractors.py` | pure multi-format extractors | 最小清理/必要的通用 dispatch，不引入 I/O | 复用已测逻辑 | metadata/body tier 混淆 |
| `src/material_agent/inspiration/reporting.py` | 权威 Markdown 报告 | 增加 fetch/feedback/UNKNOWN 边界摘要 | 人可审计 | 报告漂移于权威 JSON |
| `src/material_agent/integration/hermes_service.py` | approval-bound components | 绑定 fetcher/feedback compiler；默认公共 fetch 仍关闭 | 审批覆盖实现 | 默认能力被静默扩大 |
| `tests/unit/test_inspiration_fetch.py` | 不存在 | URL/redirect/MIME/PDF/bytes/request/retry 故障矩阵 | 安全与预算 Gate | fixture 未覆盖真实传输语义 |
| `tests/unit/test_inspiration_feedback.py` | 不存在 | 可重算、三桥状态、UNKNOWN、immutable graph/tamper | feedback contract Gate | representative-hit 归因漂移 |
| `tests/integration/test_inspiration_runner_multiformat.py` | 不存在 | 全路径多格式、prompt injection、budget、replay Gate | 排除纯 extractor 替代解释 | 测试数据与生产路径脱节 |

## 6. Execution design

- minimum smoke：fetch/feedback 定向 unit tests + 原 extractor/vectorizer tests。
- pilot：一个 metadata 足够文档必须 0 fetch；一个 metadata 不足 HTML 文档必须从 body Artifact 产出
  passage/vector，且 prompt-injection 文本不能改变任何策略或组件。
- main offline Gate：执行 1 direct + 3 bridge；至少覆盖同一 HTML 中 JSON-LD/Highwire、JATS/XML、
  普通 HTML，三条 reviewed bridge 均有 calibration 行；另有 evidence-insufficient、PDF、预算和
  redirect/host 故障注入。两个独立 workspace 的关键 Artifact bytes/hash 相同。
- regression：完整非-live suite；随后执行 opt-in live Crossref metadata regression。P3.3 离线正文
  fixture 不替代最终真实 Hermes + Crossref release Gate。
- expected outputs：`fetch_attempts.jsonl`、`fetch_manifest.jsonl`、`fetched_documents/*`、
  `passages.jsonl`、`passage_vectors.jsonl`、`tag_feedback.json`、ledger、report、bundle、stage result、
  P3.3 run record/work report。
- stop condition：清单全绿、Artifact/ledger 可重算、P3.1/P3.2 回归全绿并推送 checkpoint。

## 7. Commands and durable locations

- smoke：`.venv/bin/python -m pytest -q tests/unit/test_inspiration_fetch.py tests/unit/test_inspiration_feedback.py tests/unit/test_inspiration_extractors.py tests/unit/test_inspiration_vectorizer.py`
- integration：`.venv/bin/python -m pytest -q tests/integration/test_inspiration_runner_multiformat.py tests/integration/test_inspiration_runner.py tests/integration/test_inspiration_runner_public.py`
- main：`.venv/bin/python -m pytest -q`
- validation：`.venv/bin/python -m compileall -q src tests`、`.venv/bin/python -m pip check`、Hermes
  bundle/profile verifiers 和 opt-in live Crossref command（精确命令写入 run record）。
- durable run artifacts：ignored `workspace/p33-*`；审计摘要在 `docs/runs/`，定期报告在
  `docs/reports/`。任何 credential 不读取、不打印、不提交。

## 8. Fallback and recovery

- 网络正文 fetcher 不满足逐跳验证：保持 public compiler fetch=0，只发布 fixture seam 与未启用状态。
- 某格式不可稳定抽取：manifest 标为 unsupported/insufficient，不能把整页送给 vectorizer/LLM。
- query budget 导致未执行：feedback 标 `NOT_EXECUTED`，不能写成 evidence insufficient。
- 暂时网络错误：保留 attempt Artifact 并分类 operational failure；offline fixture 只能证明契约。
- feedback 无法从权威 lineage 重算：不发布该 schema，先补 sidecar/来源，不以 reason string 猜测。
- 回归退化：停止扩张，基于最近 pushed checkpoint 修复，不改写 P3.1/P3.2 历史证据。

## 9. Revision log

| Time (CST) | Change | Reason | Comparability impact |
|---|---|---|---|
| 2026-08-08 | 冻结 P3.3 主实验契约 | 三路只读审计完成，开始 substantial edits 前建立 run contract | 保持 P3.2 baseline、公共 schema 与默认 public fetch=0 |
