# Materials Inspiration 标准部署与启动

本文是 `materials-inspiration` Hermes profile 的标准本地部署流程。唯一推荐入口是
仓库根目录的 `./deploy/materials-inspiration`；不要分别手工启动 dashboard、Gateway
worker 或 monitor，否则进程身份、数据库绑定和健康检查不会形成同一部署闭包。

## 1. 部署边界

- Hermes 固定为 `v2026.8.3` / `0.20.0` / commit
  `3c27eb6234bf91b8ceee9e9071591b31e9b148cb`。
- dashboard、monitor 与共享 MCP Hub 只允许绑定 loopback；默认端口为 `9119`、
  `9120` 和 `9121`。
- profile 保留 `materials_inspiration_run`、`materials_run_get`、
  `materials_run_act`、`materials_result_get` 四个审计 MCP tools，并新增独立的
  `materials_research_pipeline_run` 本地科研直跑入口。
- provider key、`API_SERVER_KEY` 只通过当前进程环境传入，不写入仓库、profile、
  workspace Artifact 或运维事件。
- 部署成功只表示本地工程闭环可运行，不表示候选结构获得性质、稳定性或新颖性验证。

## 2. 前置条件

在仓库根目录执行：

```bash
git --version
uv --version
node --version
npm --version
```

要求 Python `3.11`（由 `uv` 创建隔离环境）以及 Node.js `>=22.22.0`。如果机器上有
多个 Node 版本，应先把满足版本要求的 `node`/`npm` 所在目录放到 `PATH` 最前面，
再运行部署命令。不要在 `.external/hermes-agent` 中手工运行 `npm install`。

选择一个有明确容量和备份策略的绝对 workspace 路径。它不能是符号链接；Hermes
home、运维日志、项目数据库和 Artifact 都会被绑定到该 workspace 下。

## 3. 配置运行环境

DeepSeek 直连的推荐配置如下。占位符必须在当前 shell 中替换，不要把真实密钥写进
脚本、`.env`、命令历史示例或版本库：

```bash
export MATERIAL_AGENT_WORKSPACE=/absolute/path/to/materials-workspace
export MATERIAL_AGENT_PROJECT_ID=materials-inspiration
export MATERIALS_HERMES_PROVIDER=deepseek
export MATERIALS_HERMES_MODEL=deepseek-v4-flash
export API_SERVER_KEY='<at-least-32-random-characters>'
export DEEPSEEK_API_KEY='<research-api-key>'
export MATERIALS_CROSSREF_CONTACT_EMAIL='<contact-email>'
export MATERIAL_AGENT_INSPIRATION_SEARCH_PROVIDER='crossref+arxiv+osti'
export MATERIAL_AGENT_INSPIRATION_SEARCH_MAX_RESULTS='20'
export MATERIAL_AGENT_INSPIRATION_RAG_PROVIDER=deepseek
export MATERIAL_AGENT_LLM_MODEL=deepseek-v4-pro
export MATERIAL_AGENT_LLM_API_KEY="$DEEPSEEK_API_KEY"
export MATERIAL_AGENT_SMACT_WORKER_PYTHON="$PWD/.venv-smact/bin/python"
export MATERIAL_AGENT_ML_WORKER_PYTHON="$PWD/.venv-agent02/bin/python"
```

当前固定 Hermes 版本已停用 `deepseek-chat` / `deepseek-reasoner` 的线上模型 ID，
并将它们归一化为 `deepseek-v4-flash`。部署器也执行同一归一化：即使输入旧别名，
写入 profile、UI 默认模型和运行日志的名称都会统一为 `deepseek-v4-flash`。
科研 worker 不复用这个对话模型：当 grounded RAG 显式启用 DeepSeek 时，部署器将
`MATERIAL_AGENT_LLM_MODEL` 固定为审计过的 `deepseek-v4-pro`，避免 UI 模型别名覆盖
科研 JSON provider。CHGNet 则必须使用独立 `.venv-agent02`，不能安装进 Gateway 或
Hermes 环境。
`DEEPSEEK_BASE_URL` 可选；未设置时使用 Hermes 的 DeepSeek provider 默认端点。

若使用 OpenRouter，可改为：

```bash
export MATERIALS_HERMES_PROVIDER=openrouter
export MATERIALS_HERMES_MODEL=openai/gpt-4.1
export OPENROUTER_API_KEY='<provider-api-key>'
```

## 4. 标准部署

首次部署或更新锁定运行时时执行：

```bash
./deploy/materials-inspiration deploy
```

该命令按顺序完成：

1. 校验 Git、`uv`、Node/npm 版本；
2. 校验并同步固定的 Gateway 与 Hermes Python 3.11 环境；
3. 安装 Hermes `slack` extra，并验证 `aiohttp`、`api_server` 和 Slack adapter 可导入；
4. 安装 source-controlled profile，写入规范化后的 provider/model；
5. 构建 dashboard，执行 profile、四个审计工具加一个科研直跑工具、SQLite 和本地运行时 preflight；
6. 启动同时承载 queued worker 与共享 Streamable HTTP MCP Hub 的单一进程，再启动
   dashboard 和 monitor，并记录精确进程身份。

profile 不再为每个 dashboard/TUI session 启动 stdio MCP 子进程，而是通过
`MATERIAL_AGENT_MCP_BASE_URL` 复用唯一的本地 HTTP Hub。因此打开或重连 UI 不会复制
watchdog、Gateway MCP 或 research-pipeline MCP；Hub 仍使用固定的
`.venv-gateway/bin/python` 环境并由标准生命周期管理。

Hermes 的 npm 构建在部分 npm 版本下会只改写 `package-lock.json` 元数据。部署器仅在
受管 checkout 正好位于锁定 commit、且改动全部为“未暂存的 `package-lock.json`”时，
从该 commit 原子恢复锁文件后继续。存在源码改动、暂存改动、未跟踪文件、删除或重命名
时仍会 fail closed；部署器不会清理这些状态。dashboard build、每次 preflight 和正常
`stop` 都执行同一窄检查，因此 UI/TUI 延迟生成的 lockfile 漂移也不会阻塞后续
`health` / `start`。

只准备环境和 profile、不启动常驻进程：

```bash
./deploy/materials-inspiration deploy --prepare-only
```

将 Crossref 当前可用性也作为本次发布硬门槛：

```bash
./deploy/materials-inspiration deploy --require-live-crossref
```

默认部署把 Crossref 视为外部依赖；其瞬时不可用不会掩盖本地 worker、数据库和 MCP
闭包状态。科研入口推荐的 `crossref+arxiv+osti` 模式还会访问官方 arXiv Atom Query API
和 OSTI.GOV v1 JSON API；三者的原始响应分别进入同一 hash/provenance envelope，且不会
跟随 PDF 或全文链接。accuracy-first profile 为每个 provider/query 保留最多 20 条结果；若
环境中存在 `OPENALEX_API_KEY` 且没有显式覆盖 provider 变量，科研入口还会自动加入
OpenAlex。若只需单源排障，可把 provider 临时改为 `crossref`、`arxiv` 或 `osti`。

## 5. 验证与访问

```bash
./deploy/materials-inspiration status
./deploy/materials-inspiration health
./deploy/materials-inspiration metrics
```

默认地址：

- UI：`http://127.0.0.1:9119/?profile=materials-inspiration`
- monitor health：`http://127.0.0.1:9120/healthz`
- monitor readiness：`http://127.0.0.1:9120/readyz`
- Prometheus metrics：`http://127.0.0.1:9120/metrics`
- shared MCP Hub health：`http://127.0.0.1:9121/healthz`

验收时至少确认 `status` 中 `running=true`、`local_ready=true`、`mcp_hub_http=true`、
三个 owned process 均为 true、两个 SQLite integrity 均通过；UI 的默认模型与 dashboard 日志都应显示
`deepseek-v4-flash`。

## 6. 发起 DFT 外科研直跑（无需终端批准）

在 UI 直接用自然语言描述科研目标即可。用户不需要填写 tool 名、workflow、
`submission_id`、run ID 或布尔开关；Hermes 将目标翻译为受支持的非 DFT 请求，服务端
自动生成稳定身份并绑定当前流水线实现版本。首次调用会快速返回 `RUNNING`；后台任务
继续执行，用户稍后说“继续”或“查看进度”即可恢复同一请求：

```text
请从真实二维 TiS2 母体出发，结合 1960—2026 年公开文献与化学先验，生成并评估
TiSe2 窄带结构假说。运行除 DFT 和 many-body 之外的完整流程，包括文献分析、
DeepSeek grounded RAG、SMACT、软化学变换、CHGNet 和 DeepH。请按阶段汇报状态、
原因、候选结构和证据文件；不要把文献支持或结构有效说成物性已经验证。
```

这一入口在程序内部冻结 Agent01 结构化需求并继续执行，不使用
`operator_approval`，也不调用 `materials_run_act`。当前科研状态必须按返回值理解：

- C2DB、Crossref/arXiv/OSTI 元数据检索、LangGraph Inspiration、DeepSeek grounded RAG、SMACT 与软化学
  operator 可以真实执行；
- 若 TiS2 原始 CIF 没有氧化态，入口只接受“其余结构检查均通过、仅
  `charge_or_oxidation=UNKNOWN`”的方案，先做 Ti⁴⁺/S²⁻ 显式价态标注并交给 SMACT；
- CHGNet 使用独立 worker 执行真实 checkpoint/lock/Si health 校验，但当前受审模型卡
  只覆盖 3D 单质 Si；Ti/Se 与二维结构会以 `ELEMENT_COVERAGE_UNKNOWN`、
  `DIMENSIONALITY_NOT_BULK` 停在 applicability Gate，不会启动越域弛豫；
- DeepH 只有在 CHGNet 发布 QC 通过的 L2 弛豫结构且存在匹配模型/overlap binding 后
  才能运行。`BLOCKED` 不能解读为 ML 计算成功；
- DFT 与依赖 DFT 的 many-body 始终为 `SKIPPED`。

若 UI 页面断开，标准 dashboard 会给重连保留 10 秒；超过该窗口后每 2 秒运行的
reaper 会终止 detached TUI 及其 Python gateway，避免多个浏览器 token 长期堆积进程。

## 7. 发起审计 Run 与人工批准

在 UI 中提交需求后，Run 会停在 `INTERACTION_REQUIRED`。先向用户展示完整 interaction、
物理检索请求上限、零正文/PDF/内部模型预算和 execution-manifest SHA，再由独立本地
operator 发放一次性 grant：

```bash
.venv-gateway/bin/python -m material_agent.integration.operator_approval \
  --workspace "$MATERIAL_AGENT_WORKSPACE" \
  --project "$MATERIAL_AGENT_PROJECT_ID" \
  --run-id '<inspiration-run-id>' \
  --confirmation-reference 'user-confirmation:<ticket-id>'
```

然后在 UI/MCP 中对同一个 Run 调用 `materials_run_act`。成功调用会原子消费 grant、
创建 durable queue job 并立即返回 `RUNNING`；独立 worker 完成执行。拒绝、取消、故障
恢复的完整合同见 [Hermes 集成说明](../integrations/hermes/README.md)。

## 8. 停止、重启与重新部署

```bash
./deploy/materials-inspiration stop
./deploy/materials-inspiration start
```

`stop` 会先捕获 dashboard 的精确后代进程身份，再终止所有 TUI Gateway 子进程及其
独立进程组，最后停止 dashboard、共享 Hub/worker 和 monitor；因此 watchdog 或 MCP
不会因重新分组而遗留为孤儿。重启后，同一 canonical 科研请求会从持久化
Orchestrator checkpoint 恢复；已有终态结果则直接幂等返回。

`start` 复用已安装环境和 profile，但仍执行完整 preflight。需要重建环境、profile 或 UI
时先 `stop`，再执行 `deploy`；部署器拒绝覆盖仍由它管理的运行中进程。

## 9. 常见故障

- `Node >=22.22.0`：修正 `PATH` 后重新运行；Node 检查发生在任何部署写入之前。
- `Hermes checkout contains ... state`：检查 `.external/hermes-agent` 的 Git 状态。只有
  npm 产生的未暂存 `package-lock.json` 漂移可自动恢复，其他内容必须由操作者审查。
- `aiohttp` / `api_server` / Slack import 失败：重新执行 `deploy`。标准 lock 已包含 Hermes
  `slack` extra；bootstrap 会在启动前验证三者，不再允许带缺失依赖的运行时通过。
- UI 显示 `MCP Servers (0)` 或日志出现 `No module named material_agent`：确认安装 profile
  使用的是本仓库当前版本，并重新执行 `deploy`；标准部署必须保留 `.venv-gateway/bin/python`
  虚拟环境入口，不能改成 `readlink` 后的底层解释器。
- UI 与日志模型名不同：重新执行 `deploy` 以重装 profile，并确认环境中的 model 是
  `deepseek-v4-flash`；旧别名只作为输入兼容，不再持久化。
- 端口被占用：先用 `status` 确认是否为当前 deployment；若是，执行 `stop`。若不是，
  释放端口或在所有生命周期命令中一致传入新的全局 `--dashboard-port` / `--monitor-port`。
- `health` 本地通过但 Crossref 不可用：查看 `external_crossref_ready`；只有发布 smoke
  需要用 `--require-live-crossref` 将其提升为硬失败。

不要在排障输出中粘贴 provider key 或 `API_SERVER_KEY`。如密钥曾进入聊天、终端回显或
日志，应在 provider 控制台轮换后再继续测试。
