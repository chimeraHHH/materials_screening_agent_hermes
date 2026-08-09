# Inspiration Production Hardening Plan

## 1. Map Link

- parent_map_node: P3.x local beta release
- loop_id: `production-hardening-20260809`
- node_objective: 将本机窄域 beta 加固为可自动验收的生产实现
- node_deliverable: 五个 Gate 的代码、测试、运行证据和文档
- success_condition: 用户列出的五项要求逐项有当前源码与自动测试证明
- abandonment_condition: 只有出现必须由用户决定的科学阈值、外部凭据或部署范围时暂停对应分支；其余工作继续
- next_on_success: production release review
- next_on_failure: 保留最后可用 checkpoint，记录失败层并修复，不缩小目标

## 2. Objective

- run id: `production-hardening-20260809`
- experiment tier: `main/test`
- selected idea: 先封闭网络、manifest、审批、证据关系和完整性五个信任边界，再把同步调用升级为持久任务执行；随后开放受限证据、补齐可复现运维，最后扩展检索与科学评测。
- baseline: `537ddd446df71c2ffaab25c2a1801e2be2df3c00`
- user constraints: 保留完整五阶段目标；不得用较窄兼容方案替代；不做 novelty
- research question: 能否在保留现有确定性 replay 与科学边界的同时，补齐生产安全、恢复、可观测和科学质量契约？
- null hypothesis: 加固会破坏既有 hash/replay/审批或仍留下无法自动验收的关键边界。
- alternative hypothesis: 每个边界都能通过版本化契约、持久状态和故障注入形成自动发布 Gate。

## 3. Gate Contract

### Gate A — Safety and integrity

1. Redirect 每一跳在连接前验证；跨域跳转不会产生第二个请求。
2. Execution manifest 绑定完整构建/源码身份与全部执行组件，审批后漂移 fail closed。
3. Approve/reject/cancel 均可由独立 operator 决策签发、消费并审计。
4. 否定、反事实和不确定 passage 不得产生 `SUPPORT`；无法判定时降为 `CONTEXT/UNKNOWN`。
5. 在线 `verified=true` 只在完整 Artifact 闭包通过时返回。

### Gate B — Durable execution

1. `approval consumed + job created` 同一持久事务。
2. Worker claim/lease/heartbeat，过期 lease 可安全恢复，活跃 lease 不可重复执行。
3. 阶段 checkpoint 允许恢复且不覆盖 immutable Artifact。
4. 单调时钟 deadline/cancellation 贯穿外部请求与本地阶段。
5. 确定性科学账本与真实运维 walltime/resource ledger 分离并关联。

### Gate C — Readable bounded result

1. 只读接口绑定当前 run/result hash，不接受任意 URI/path。
2. 返回长度受限的 report excerpt、title、DOI/URL、年份、passage excerpt 和 evidence relation。
3. 所有内容从已验证闭包投影；超长、缺失或篡改 fail closed。

### Gate D — Operations and production E2E

1. 幂等 bootstrap/deploy/start/stop/status，显式 Node/Python/model/provider Gate。
2. liveness/readiness 覆盖 Gateway、MCP、worker、Artifact/DB 写入能力。
3. 结构化日志、correlation/run ID、核心 metrics 与错误分类。
4. 自动 E2E 覆盖 Hermes profile → production MCP → operator decision → bounded provider fixture/live seam → terminal result → closure verification。

### Gate E — Scientific usefulness

1. 有界 rows/pagination、来源质量字段和边际收益停止提升召回。
2. 专家 Tag review/promotion/reject/rollback，版本化图和不可变审计。
3. 语义向量实际参与近重复、证据排序和多样性；模型/version/hash 固定。
4. `top_k>=3` 的正/负/歧义/跨体系 gold set 与专家盲评，冻结 precision/recall、错误类比率、接受率和多样性指标。

## 4. Minimal Code-change Map

| Path | Planned change | Acceptance evidence |
| --- | --- | --- |
| `src/material_agent/inspiration/search.py` | per-hop redirect policy | transport adversarial tests |
| `src/material_agent/gateway/companion.py`, `inspiration/*` | complete build/component manifest | source-drift test |
| `src/material_agent/gateway/authorization.py`, `integration/operator_approval.py` | generic exact-action decisions | approve/reject/cancel E2E |
| `src/material_agent/inspiration/passages.py`, `evidence.py` | conservative relation classifier | negation/uncertainty gold cases |
| `src/material_agent/gateway/service.py` | full online closure verification | intermediate tamper tests |
| `src/material_agent/gateway/` | job/outbox/lease/checkpoint/deadline/ops ledger | crash/concurrency/timeout tests |
| `src/material_agent/gateway/models.py`, projector | bounded evidence/report DTO | projection and tamper tests |
| `integrations/hermes/scripts/` | deployment, health, metrics, E2E | fresh workspace release test |
| `src/material_agent/inspiration/` | recall, expert tags, semantic ranking/eval | frozen evaluation package |

## 5. Execution Design

- minimum evidence: Gate A targeted unit/integration tests and unchanged baseline tests pass.
- solid evidence: Gates A–D pass failure injection and automated production E2E.
- maximum evidence: Gate E benchmark thresholds plus a fresh supervised live release run.
- smoke strategy: run only target module tests after each isolated edit.
- full run: complete offline pytest Gate, three environment dependency checks, bundle verifier and production E2E.
- stop condition: all five Gates and completion audit are proven from current state.
- strongest alternative hypothesis: tests only prove fixtures and do not cover real host/model/operator boundaries; production E2E and explicit live seam must address it.

## 6. Runtime and Records

- command/log record: `RUNLOG.md`
- checklist: `CHECKLIST.md`
- summary/metrics/claim validation are created as evidence matures.
- `bash_exec/artifact` are unavailable in this session; commands use repository-native execution and are recorded here rather than fabricating skill-native artifacts.

## 7. Revision Log

| Time (CST) | Change | Reason |
| --- | --- | --- |
| 2026-08-09 | Created five-Gate production hardening contract | User promoted audited gaps into an implementation objective |
| 2026-08-09 | Completed stable-tree engineering audit | Offline `950 passed`, live Crossref `3 passed`, isolated MCP `2 passed`, three dependency Gates and bundle/compile/diff passed |
| 2026-08-09 | Kept release at NO-GO | Queued worker is not active in the default profile, full provider deployment is unverified, hard mid-stage recovery is incomplete, and no real expert scientific gold set exists |
| 2026-08-09 | Activated queued production path but retained NO-GO | Queued profile, managed worker, deadline supervisor, operations metrics and CI workflow are active; parent hard-kill child cleanup, provider E2E, platform enforcement and scientific gold remain open |

## 8. Production Activation Pass

- sub-run id: `production-queue-activation-20260809`
- accepted implementation baseline: the stable tree that passed `950` offline
  tests, `3` live Crossref tests, and `2` isolated MCP stdio tests.
- selected idea: make durable queued execution the one installed production path,
  and treat the worker plus queue health as required runtime components rather
  than optional library code.
- research question: can the installed Hermes profile return `RUNNING` from
  `materials_run_act`, execute exactly once in a separately supervised worker,
  preserve the manifest/closure result, and remain observable/recoverable across
  process restarts?
- null hypothesis: profile/lifecycle activation either leaves a synchronous path,
  loses exact approval/outbox binding, permits an unmonitored worker, or breaks
  replay/closure compatibility.
- alternative hypothesis: the exact queued factory, worker identity, shared state
  roots, queue metrics, and restart behavior are all enforced by configuration,
  readiness, and automated E2E.
- stop condition: no production profile or lifecycle path can be healthy without
  the queued factory and live managed worker; an installed-profile E2E proves
  submit -> approve -> `RUNNING` -> worker -> verified terminal result -> restart.
- abandonment condition: none for local implementation; a real provider smoke may
  remain an external credential Gate, but fixture/static production activation
  must still be completed.

### Minimal activation change map

| Path | Required change | Acceptance evidence |
| --- | --- | --- |
| `integrations/hermes/**` | pin queued service factory in profile, probes, docs, and verifier | bundle/profile drift tests and real MCP tool discovery |
| `production_runtime.py` | own worker lifecycle and exact shared workspace/project | start/stop/restart and orphan cleanup tests |
| `production_monitor.py`, `production_ops.py` | require worker and queue health; export backlog/lease/failed metrics | health/metrics failure-injection tests |
| `queued_gateway.py` | provide a bounded worker control/heartbeat seam if process identity alone is insufficient | idle/active worker health and signal tests |
| production E2E | exercise installed queued profile and separate worker | request returns `RUNNING`; terminal result closes without request-thread execution |

### Evidence ladder

- minimum: bundle verifier pins queued factory; worker lifecycle and missing-worker
  readiness tests pass.
- solid: installed-profile static Crossref E2E proves asynchronous transition,
  exact-one runner execution, closure verification, and process restart recovery.
- maximum: Node 22 plus real provider deploy/start/health and one bounded live
  Crossref approval run, followed by clean stop and secret/no-residual checks.
