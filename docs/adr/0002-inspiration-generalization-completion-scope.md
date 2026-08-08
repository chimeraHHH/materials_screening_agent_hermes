# ADR-0002：灵感生成器通用化完成范围

- 状态：Accepted
- 日期：2026-08-08
- 决策人：项目用户与当前实施代理
- 取代：不取代 ADR-0001；扩展其通过 pilot 验证后的后续范围

## 背景

ADR-0001 和 P0.3 已经证明单用户本机的固定请求纵切：Hermes 可通过四工具 Gateway 停在
真实人工审批点，离线 fixture runner 可生成非空、hash-verified proposal；独立 Crossref
runner 也能保存 metadata-only 证据。但这两个纵切彼此分离，生产 profile 仍只接受一个逐字
固定 goal/constraints，真实候选池通常只有一个。

用户继续授权推进，并要求把灵感生成器作为后续可开发的系统工程，而不是停留在演示脚本。
因此需要新增完成范围，同时避免把“完整”误解为任意科学 planner、多用户服务或 novelty 系统。

## 决策

1. P0.3 fixed-request pilot 保持已完成、只读基线；新增轨道命名为 P3.x Inspiration
   Generalization，不回写或夸大旧运行证据。
2. P3.x 完成目标是“单用户、本机、受控领域的可复用 beta”，仍是 companion capability；
   不修改冻结的 `retrieval → ml → dft → many_body` 状态图。
3. Hermes 继续只负责会话、Skill 和粗粒度 Tool 调度；Requirement、审批、执行、Artifact、
   provenance 和科学边界仍由材料服务独占。
4. 支持范围先固定为 reviewed flat/narrow-band TagGraph、operator-owned parent catalog 和
   `SUBSTITUTE_EQUIVALENT_SITE_V1`。自然语言可以同义改写，但必须确定性编译；未支持 target、
   冲突约束或超预算请求 fail closed，不能静默忽略。
5. 生产 Hermes profile 必须能在同一 approval-bound Gateway run 中使用 Crossref metadata；
   fixture factory 仅用于离线 replay，不能冒充 live 产品链路。
6. 搜索采用有界 transient retry 和 attempt provenance；文献按 DOI/arXiv/URL 做运行内处理去重，
   同时保留所有 query/raw-hit lineage。网络失败不得转换成 scientific no-match。
7. 多格式正文能力通过受限、可注入 fetch seam 接入；metadata 足够时不 fetch，PDF 全文默认
   永久关闭。只有 title、heading、selected passage 和 normalized tags 可进入 vectorizer/内部 LLM。
8. TagGraph 仍由 Git 中的 reviewed registry 管理。模型或运行只能提交 Bridge/feedback artifact，
   不得在线改图；没有专家判断时状态必须为 `UNKNOWN`。
9. P3.x 必须用真实多候选 Gate 验证运行内 exact/strict 去重和机制多样性，不能再用单候选结果
   证明 Top-K 行为。
10. 每个 Gate、重大失败、架构变更和 PR closeout 都提交不可变工作报告，并持续推送公开 GitHub。

## 完成判据

P3.x 只有在 `PLAN.md` 定义的 P3.1、P3.2、P3.3 和最终真实 Hermes+Crossref release Gate
全部通过后才完成。测试或 fixture 的成功不能替代真实 provider/Agent Gate；live 内容可以变化，
但身份、预算、provenance、引用闭包和科学边界必须稳定。

## 不在本决策范围

- novelty、prior-art、专利或“新材料”判断；
- 任意材料性质、任意 parent 文件、自由坐标/代码和未经评审 transformation；
- 第二公共 provider、远程 embedding、vector database 或在线 reranker；
- 顶层第五 Stage、多用户/RBAC/Postgres、公网部署、后台任务平台或 UI。

## 后果

正面：

- “通用”有可测试边界，不再依赖精确 prompt，又不会退化成开放式科学执行；
- Crossref、重试、去重、多样性、网页格式和 tag 反馈都有独立证据 Gate；
- 旧 pilot 证据保持可复现，新能力可以逐里程碑回退。

代价：

- 需要维护 request compiler、parent catalog、attempt/fetch/feedback Artifact 和更多故障注入；
- 支持领域仍窄，不能把 beta 描述成任意材料生成器；
- 专家校准需要真实专家输入，工程系统只能诚实保存 `UNKNOWN`，不能自动补造。
