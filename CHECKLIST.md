# P3.x Inspiration Generalization 控制清单

## Identity

- parent map node：P3 Hermes inspiration companion
- loop/run：`p3x-inspiration-generalization-20260808`
- branch：`agent/inspiration-generalization`
- stage：implementation / main-test

## In progress

- [x] 审计 public main、现有计划、源码、测试和发布证据
- [x] 区分可信完成的 fixed-request pilot 与新增 P3.x beta 范围
- [x] 提交并推送 P3.x scope、ADR、PLAN/CHECKLIST 和首份工作报告

## P3.1 — Generalization & Reliability

- [x] 实现严格、确定性的受支持 request compiler
- [x] 保留离线 fixture factory，新增同一 Gateway run 的 Crossref factory
- [x] 为 transient HTTP/network error 实现有界 retry 与 `Retry-After`
- [x] 持久化并计量每次 search attempt
- [x] 在文档/passage/vector 层去重并保留所有 raw-hit/query lineage
- [x] 外部故障与 scientific no-match 分层正确
- [x] 权威报告覆盖 query/attempt/raw/passage/bridge/route/dedup/MMR/ledger
- [x] 更新 Hermes profile、Skill、SOUL 与 bundle verifier
- [x] P3.1 unit/contract/integration/live Gate 通过
- [x] 发布 P3.1 工作报告并提交、推送 GitHub

## P3.2 — Candidate Breadth & Diversity

- [x] 冻结 operator-owned、SHA-pinned parent catalog 和 provenance
- [x] 多 parent/multi-route engine 仍只使用白名单 operator
- [x] 构造至少 5 个结构有效 proposal 的可复现 Gate
- [x] 验证同结构多路线合并与完整 lineage
- [x] 验证 Top-5 exact/strict duplicate 为 0
- [x] 验证候选可用时至少覆盖 2 个 mechanism
- [x] 映射并报告 `require_diverse_routes` 的真实语义
- [x] 排序与关键 Artifact hash 可重放
- [x] 发布 P3.2 工作报告并提交、推送 GitHub

## P3.3 — Passage & Tag Feedback

- [x] 冻结 P3.3 主实验计划、baseline、失败分类和执行清单
- [x] 把 JSON metadata、JSON-LD/Highwire、JATS/XML、HTML 抽取接入统一 runner seam
- [x] 实现 host/redirect/content-type/bytes/request 约束和 metadata-first skip-fetch
- [x] 默认 PDF 全文 0，向量/内部 LLM 输入仅含选定 passage 包
- [x] 记录 query/tag/bridge yield、字节和成本 feedback artifact
- [x] feedback 不能在线修改 curated TagGraph
- [x] 离线校准覆盖现有 3 条 bridge rule，并显式记录 UNKNOWN 专家状态
- [x] P3.3 故障注入、预算、prompt-injection 和 replay Gate 通过
- [x] 发布 P3.3 工作报告并提交、推送 GitHub

## Final release

- [x] 完整主环境测试通过
- [x] 隔离 Gateway/MCP 和三个环境 dependency checks 通过
- [x] live Crossref runner 与 Gateway lifecycle 通过
- [x] standalone completed-run verifier 通过 review-fixed 46-case representative static-production-path Gate
- [x] exact v2 terminal workspace 通过 pinned-Gateway verifier，summary/hash/no-write 已写入 final run record
- [x] 真实 Hermes 自然语言 Crossref run 经用户授权完成；`-z` 绕过 host resume 的偏差已诚实记录
- [x] provenance、hash、成本、去重、多样性和科学边界指标闭合
- [ ] Skill/profile/tool schema、secret、diff、GitHub checks 通过
- [ ] 最终 run record 与 work report 发布
- [ ] PR review 后合并 public `main`
- [ ] final release SHA/PR/check/anonymous-read 证据记录

## Next

- [x] 下一步始终与 [`PLAN.md`](PLAN.md) 的当前里程碑一致
- [x] P3.1 结论：GO；详见 `docs/reports/2026-08-08-1837-inspiration-progress.md`
- [x] P3.2 结论：GO；详见 `docs/reports/2026-08-08-1924-inspiration-progress.md`
- [x] P3.3 结论：GO；详见 `docs/reports/2026-08-08-2023-inspiration-progress.md`
- [x] 最终 Hermes pending interaction 与知情审批修复报告已发布
- [x] exact-user-approved result 检查点已形成 final v2 run record
- [ ] 下一次报告检查点：最终 PR merge 与 anonymous-read closeout
- [x] exact approval 后完成 `act → result`：保持同一 Gateway run/manifest/grant；原计划复用同一 Hermes host session，但 `-z` 实际创建 one-shot act session，原 session 随后只读恢复

## Blocked

- [x] 用户已精确批准 v2 interaction/manifest；原审批 blocker 已解除

## Closeout

- [ ] 用 1–2 句总结工程结论
- [ ] 将 claim 分类为 supported / refuted / inconclusive
- [ ] 写明 baseline relation、comparability、failure mode 和 next action
