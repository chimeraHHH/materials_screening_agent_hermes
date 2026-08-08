# P3.3 Passage & Tag Feedback 执行清单

## Identity

- parent map node：P3 Hermes inspiration companion / Passage & Tag Feedback
- loop/run：`p33-passage-tag-feedback-20260808`
- baseline：`agent/inspiration-generalization@fc1951b`
- plan：[`P33_EXPERIMENT_PLAN.md`](P33_EXPERIMENT_PLAN.md)
- stage：implementation / main-test

## In progress

- [x] 三路只读审计现有 runner、extractor、vectorizer、TagGraph 和测试基线
- [x] 冻结 P3.3 baseline、假设、代码图、主 Gate、失败分类和回退路径
- [ ] 实现 approval-bound bounded document fetch seam

## Fetch implementation

- [ ] 定义 typed request/result/error/attempt/budget contract 和 component snapshot
- [ ] disabled fetcher 保持现有 metadata-only 行为与默认 public fetch=0
- [ ] fixture fetcher 为离线 main Gate 提供 byte-stable URL→body/redirect 映射
- [ ] 网络 fetcher 强制 HTTPS、exact-host allowlist、无 userinfo/非默认端口
- [ ] 禁用自动 redirect，每跳连接前校验并限制 redirect 次数
- [ ] retry/redirect/GET 按物理请求计量，timeout 与总等待有界
- [ ] `Content-Length` 和 streamed `max+1` 同时执行单响应/总字节限制
- [ ] PDF MIME/magic、未知/禁用 content type 不进入正文 extractor
- [ ] transient/permanent/security/budget/fatal 失败分类不冒充 scientific no-match

## Runner integration

- [ ] metadata 足够严格 skip-fetch；不足才调用 fetcher
- [ ] 每个 canonical document 每 run 最多 fetch/process 一次并保留全部 query lineage
- [ ] fetched body 先不可变持久化并验 hash，再执行 JSON/JSON-LD/Highwire/JATS/HTML extraction
- [ ] metadata/body passage 分别指向真实 raw-search/raw-fetch source Artifact
- [ ] fetched bodies/attempts/manifest 纳入 intermediate、bundle 和 stage-result verification
- [ ] fetch ledger 与物理 attempts、accepted bytes、持久化 bytes 可重算
- [ ] vector canonical input 仅含 title/heading/selected passage/normalized tags
- [ ] PDF fulltext reads=0；internal LLM calls/input/output=0

## Feedback implementation

- [ ] 内部 strict feedback schema 不修改 frozen public contracts
- [ ] compiler snapshot、TagGraph pointer/hash、全部输入 pointer 和 fingerprint 固定
- [ ] query planned/executed、attempt/hit/doc/passage/evidence/bridge/cost yield 可重算
- [ ] tag/bridge attribution 明确 inclusive/non-additive；总账以 CostLedger 为准
- [ ] 三条 curated bridge rule 逐条记录 activation/execution/evidence status
- [ ] 无专家 Artifact 时顶层和每条 bridge 的 expert status 均为 UNKNOWN
- [ ] feedback 不含 replacement graph/mutation op，运行前后 TagGraph bytes/SHA 不变
- [ ] feedback compiler 纳入 approval execution component manifest
- [ ] `tag_feedback.json` 纳入 bundle lineage和权威报告

## Pilot / smoke

- [ ] fetch URL/redirect/content-type/PDF/bytes/request/retry unit fault matrix 通过
- [ ] feedback recomputation/tamper/UNKNOWN/graph immutable unit tests 通过
- [ ] 现有 extractor/vectorizer unit baseline 保持通过
- [ ] metadata sufficient 文档证明 fetch attempts=0
- [ ] 单个 HTML body 证明 structured/meta/section passage lineage 与 passage-only vector input
- [ ] prompt injection 只作为 untrusted data，不能改变 policy/tool/fetch/graph

## Main offline Gate

- [ ] 1 direct + 3 bridge query calibration 全路径执行
- [ ] JSON metadata、JSON-LD/Highwire、JATS/XML、普通 HTML 均经 runner seam 覆盖
- [ ] 三条 reviewed bridge rule 全部有 calibration 行和预期状态
- [ ] 至少覆盖 SEARCH_SUPPORTED、EVIDENCE_INSUFFICIENT、NOT_EXECUTED/NOT_PLANNED 语义
- [ ] PDF、host/redirect、unsupported type、request/per-response/total bytes 故障注入通过
- [ ] 同 DOI 多 query inclusive attribution 与单次处理同时成立
- [ ] 所有 vector 唯一回指 selected passage，全文 sentinel 不进入 embedding input
- [ ] 两个独立 workspace 关键 JSON/JSONL/body/vector/report/bundle/stage hash 一致
- [ ] P3.2 Top-5、机制/路线 coverage、exact/strict duplicate 指标不退化

## Validation and release

- [ ] 定向 unit/integration suite 通过并记录精确命令
- [ ] 完整非-live suite、compileall、pip check 通过
- [ ] opt-in live Crossref metadata regression 通过或诚实记录外部依赖阻塞
- [ ] Hermes bundle/profile/schema/approval manifest checks 通过
- [ ] secret scan、diff audit、工作树范围和依赖检查通过
- [ ] P3.3 durable run record 发布
- [ ] P3.3 定期工作报告发布
- [ ] checkpoint commits 只推送 `hermes-origin/agent/inspiration-generalization`
- [ ] `PLAN.md`、`CHECKLIST.md` 与结果同步

## Next

- [ ] 完成 P3.3 后进入最终真实 Hermes + Crossref natural-language release Gate
- [ ] 下一报告检查点：P3.3 离线 main Gate 验收后

## Blocked

- [x] 当前无已确认 blocker；public body fetch 默认保持 disabled，不影响离线 Gate 实施

## Closeout

- [ ] 用 1–2 句总结主实验，claim 分类为 supported/refuted/inconclusive
- [ ] 写明 baseline relation、失败模式、scientific boundary 和最终 release 下一动作
