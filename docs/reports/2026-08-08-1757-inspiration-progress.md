# Inspiration progress report — 2026-08-08 17:57 CST

## Verdict

状态：`IN_PROGRESS`。

公开主线上的 P0.3 fixed-request pilot 证据可信且已完成，但它只证明“单用户、本机、精确请求、
离线 Hermes fixture + 独立 Crossref runner”。它不证明可复用自然语言请求、同一 Hermes run
的真实 Crossref、provider retry、真实 Top-5 多样性、多格式 runner 集成或 tag 反馈闭环。
本报告因此建立新的 P3.x completion track，不撤销旧 pilot，也不把 roadmap 冒充当前能力。

## 时间与 Git 基线

- 审计/规划区间：2026-08-08 17:45–17:57 CST；
- public repository：`chimeraHHH/materials_screening_agent_hermes`（PUBLIC）；
- base：`hermes-origin/main@dcd076d01061f36a2f9826deecefc274c9c3211d`；
- base tree：`e00aa04d9518310adcbea8f3a5e5a89eac06d503`；
- branch：`agent/inspiration-generalization`；
- covered-through：尚未产生本轨道首个 commit；
- PR：尚未创建。

本地旧 `main@909d074` 落后 public main 27 个提交，已明确不作为开发基线。新分支直接从
`hermes-origin/main` 建立，原始上游 `wuleyan2004/materials_screening_agent` 仅保留 fetch remote，
不得作为本项目 push 目标。

## Intake audit 与信任分级

### Trusted

- PR #1/#2 已合并；两个 PR 头提交的 GitGuardian check 成功；public main tree 与本地
  closeout tree 一致。合并提交自身没有 check-run，因此不把它描述为“merge commit checks passed”。
- 已发布的 `docs/runs/2026-08-08-hermes-inspiration-pilot.md` 可匿名读取，记录固定 Hermes
  session、MCP/审批、Artifact hash、Crossref live Gate、成本和已知 Broken pipe。
- P0.3 的 strict DTO、4-tool Gateway、Artifact/hash、fixture replay、passage-only vectorization、
  substitution、run-internal identity/MMR 与独立 Crossref runner 有源码和测试对应。

### Needs new verification

- 新分支修改后的完整 regression；
- 同一 Hermes/Gateway lifecycle 的 Crossref；
- transient retry、attempt ledger、文档级处理去重、恢复语义；
- 多 parent/multi-route Top-5 与多格式/tag feedback；
- 最终真实 Hermes 自然语言 Crossref session。

### Stale or out of scope

- 主计划中 Agent02、DFT、多体、服务器和 gold-set 的未勾选项不属于 Inspiration blocker；
- 历史 Day-14 checkbox 与已经发布的 P3 evidence 冲突，作为 stale tracking，不重复执行；
- 多用户/RBAC/Postgres、公网服务、任意 chemistry、第二 provider、远程 embedding、novelty 和
  prior-art 不属于 P3.x。

## 代码缺口

1. `HermesFixturePreparer.supports()` 要求 goal 与 constraints 逐字段等于一个常量；
2. 生产 profile 始终加载 `create_hermes_fixture_service`，Crossref 只在直接 Python tests 中组装；
3. Crossref 每 query 只发一次请求，HTTP 429 被压成普通 `HTTP_ERROR`，没有 `Retry-After`、
   attempt 或 backoff；
4. Runner 虽统计 unique documents，却仍按重复 hit 抽取和向量化；
5. HTML/JATS/JSON-LD extractor 只有库级测试，Runner 当前不 fetch body；
6. 生产 engine 只选最小 bridge、固定一个 parent，实机只有一个 candidate；
7. TagGraph 有 3 条 reviewed bridge rule，但没有 yield/feedback artifact 或校准集；
8. `material-agent inspire` 仍未实现，权威 report 对 query/attempt/raw/locator/route/MMR 的展示不足。

## 本期变更与控制面

本报告随以下新控制面一起提交：

- `PLAN.md`：P3.1–P3.3 run contract、metrics、stop/fallback、代码变更图；
- `CHECKLIST.md`：逐 Gate 状态；
- `docs/adr/0002-inspiration-generalization-completion-scope.md`：冻结 local beta 边界；
- `docs/reports/README.md`：报告 cadence 与最小字段；
- 原 Inspiration plan 将新增 P3.x milestone 和 completion checklist。

## 本期验证

只读审计代理执行的定向 Inspiration/Gateway 回归：

```text
.venv/bin/python -m pytest -q \
  tests/unit/test_inspiration_search.py \
  tests/unit/test_inspiration_extractors.py \
  tests/unit/test_inspiration_vectorizer.py \
  tests/unit/test_inspiration_selection.py \
  tests/integration/test_inspiration_runner_public.py \
  tests/unit/test_gateway_persistence.py \
  tests/integration/test_gateway_runner_e2e.py

56 passed, 1 skipped in 0.40s
```

工具观测 wall time 为 1.9s；唯一 skip 是实际 stdio 需要 combined Gateway MCP environment。
该结果只确认旧 baseline 没有在审计期间失效，不能作为新实现验收。

主代理开发前 preflight：

```text
.venv/bin/python --version
Python 3.11.15

.venv/bin/pip check
No broken requirements found.

git status --porcelain
<empty>
```

首个 scope commit 尚未生成，因此本报告不填写虚构 commit、push、PR、耗时或新的实机
Artifact。提交后在下一份报告记录精确 SHA 和公开分支状态。

## 科学与成本边界

- novelty、prior-art、专利或“新材料”判断：0；
- proposal 的 `scientific_conclusion` 必须为 `false`；
- target property 必须保持 `UNKNOWN`，直到后续计算/实验；
- 默认 PDF 全文读取：0；
- 只允许 selected passage package 进入 vectorizer/内部 LLM；
- Hermes host provider usage 与 Gateway materials-service ledger 分开报告；
- 本期没有新 provider run，因此没有新增 search/token/cost 值。

## 风险与下一 Gate

- 最大近期风险是把“支持同义请求”误做成“静默忽略输入”；request compiler 必须对每个字段
  给出确定性语义或明确拒绝。
- live retry 不能无限等待或主动制造公共限流；使用注入 sleeper/transport 做故障测试，live Gate
  只观察真实 provider 行为。
- raw response 变化会与 immutable URI/recovery 冲突；P3.1 必须先冻结 attempt/checkpoint 语义。
- 多样性不能再由单候选推断；P3.2 必须使用真实多候选 fixture/run。

下一 Gate：提交并推送本次 scope/report checkpoint，然后实现 P3.1 request compiler、typed retry
和同一 Gateway run 的静态 Crossref E2E。
