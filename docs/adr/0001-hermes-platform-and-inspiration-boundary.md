# ADR-0001：Hermes 平台与灵感生成器边界

- 状态：Accepted
- 日期：2026-08-08
- 决策人：项目用户与当前实施代理

## 背景

仓库已有 LangGraph Orchestrator、SQLite 业务状态、Artifact Store、审批、恢复和四个科学
Agent 边界。后续工作需要通过 Hermes 统一 Skill、Tool、Provider、subagent 和用户交互，
并新增一条能够搜索跨领域证据、生成受约束材料候选的“灵感生成器”链路。

若直接把 Hermes 嵌入主 Python 环境或让它接管科学阶段，将形成两个状态真源、两套审批和
依赖冲突。若完全不引入 Hermes，则无法达到用户要求的长期 Agent 工程规范。

## 决策

1. Hermes 作为上层 Agent 平台和 MCP host；`materials_screening_agent` 作为领域执行服务。
2. Hermes 与材料项目使用独立进程/环境，通过版本化窄接口通信。
3. Hermes 首个兼容基线固定为 release `v2026.8.3`、resolved commit
   `3c27eb6234bf91b8ceee9e9071591b31e9b148cb`、package `0.20.0`。
4. Requirement、审批、Run/Stage、科学 evidence、Artifact 与 backend 状态继续由现有系统
   独占；Hermes Session/Memory 只保存交互与非权威偏好。
5. 灵感生成器首版作为 companion capability，不立即修改冻结的四阶段 `StageId`。
6. 灵感生成器可生成白名单 operator 的结构 proposal，但 proposal 固定不构成性质证据。
7. 本阶段不实现 novelty/prior-art 判定，也不输出“新材料”结论。
8. Hermes tool call 不能代表人工审批；关键动作必须获得真实用户确认，失败时 fail closed。

## 后果

正面：

- Hermes 能规范 Skill、Tool 和开发入口，同时不破坏现有科学状态与复现性；
- 两个 Python 依赖树隔离；
- 灵感生成器可以独立演进、测试和回退；
- 未来可在真实 Gate 通过后决定是否升级为顶层 Stage。

代价：

- 需要维护 Gateway 契约和跨进程兼容测试；
- 本地阶段仍只适合单用户、单 Hermes 实例；
- Hermes 运行成功不能替代材料引擎的科学验证；
- profile、Skill、Tool Schema 和运行版本都必须进入 provenance。

## 不采用的方案

- **将 Hermes 作为主项目依赖直接 import：** 精确依赖冲突且耦合内部 API。
- **由 Hermes 替换 LangGraph/SQLite：** 会丢失冻结审批、幂等和 Artifact 真源。
- **让 Hermes 直接调用底层 DFT/ML tools：** 会绕过现有计划、输入 hash 和审批边界。
- **只复制 Hermes 设计而不运行 Hermes：** 不满足用户要求的长期规范化目标。
