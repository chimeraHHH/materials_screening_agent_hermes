# Prompt1 scientific-validation-loop 工作报告（2026-08-23）

> 2026-08-25 更新：本报告前半部分保留 2026-08-23 的历史 DFT 路由实验，仅用于说明
> 契约演进。当前生产政策已经固定为 `ML_ONLY`：不运行 DFT，也不以 DFT 作为 fallback；
> 只消费数据库中已发布的能带/CIF或可信预计算图，并运行本地判据与真实 ML。证据上限为
> L2，面向速度优先的候选筛选，不表述为第一性原理证明。

## 测试目标

复用历史二维过渡金属平带请求及其三个候选：biaxially strained Pd3P2S8、
Li-intercalated Pd3P2S8、Li-deficient LiP2PdS6。验证：

1. 全部候选进入统一契约，而非只取第一个候选；
2. DeepSeek 拥有模型/计算与 observable 的科学路线选择；
3. 本地 policy 只审计适用域、预算、权重、backend 和 evidence ceiling；
4. 模型结果先进入 research memory，再驱动淘汰、operator revision 或升级计算。

## 实现与离线结果

- 新增 `scientific-validation-loop-v1` 的 `HypothesisCandidate`、`OperatorResult`、
  `DeepSeekModelRouteProposal`、`ModelTaskPlan`、`ScientificEvidence`、
  `DeepSeekFeedbackProposal` 和 `FeedbackCycleResult`。
- 历史 graph 的三个候选全部成功投影，顺序仍为 strain、Li-intercalation、Li-vacancy；
  每个候选携带八项未验证 hard constraints 和 hash-bound parent CIF。
- 多候选 route 测试中，DFT band/PDOS task 在 supplied capability ready 时批准；当前
  Si-only CHGNet 被元素+二维域阻断；缺权重 Uni-HamGNN 被 backend/weight/evidence Gate
  阻断；DeepSeek 提出的未注册模型被 `MODEL_CAPABILITY_NOT_REGISTERED` 阻断。
- 反馈纵切把三条显式测试 evidence 写入 append-only memory 后，分别接受
  `ELIMINATE`、typed `MODIFY_OPERATOR` 和 `REQUEST_HIGHER_EVIDENCE`。另一个负向测试证明
  mock/NONE 且未写 memory 的 contradiction 不能淘汰候选。

上述 execution evidence 是测试契约数据，不是 Pd 材料的真实计算结果，不得引用为科学
结论。

## 真实 DeepSeek route 与 feedback Gate

使用 Keychain 中科研专用凭据做 opt-in live route，前四次均 fail closed：

1. 3-call tool budget 不足；
2. 扩至 12 calls 后耗尽 8-round budget；
3. 显式传入 output schema 后，provider 返回 malformed tool-argument JSON；
4. inspection tool 改为无参数完整 snapshot 后，超过 900 秒 walltime。

针对前三项已将单轮 completion 固定为 32768、默认 tool budget 扩至 12、inspection 改为
无参数批量读取，并把 required output schema 显式放入请求。进一步定位到原生 agent 使用
`model_validate(json.loads(content))`，导致 strict tuple 字段永久拒绝合法 JSON array；改为
`model_validate_json(content)` 后第五次 route live 通过，receipt SHA-256 为
`c4ba46f4096f449db8efa2e023247a7e6733692dd26426055e46ccda82e8a117`。

DeepSeek 为三个候选各提出一条 `vasp-hse-soc-band-pdos-v1` 路线，并覆盖 bandwidth、
Fermi 邻接、轨道来源、连通子晶格、无一阶交点和无色散带穿越费米面六项 observable。
确定性 policy 没有改选 ct-UAE、CHGNet、DeepH 或 Uni-HamGNN，而是因 capability snapshot
中真实 DFT backend 为 `UNAVAILABLE`，将三条任务全部阻断为
`MODEL_BACKEND_NOT_READY`。

随后使用明确标记为 fixture/`NONE` 的三条 memory evidence 发起真实 feedback 回合，用于
验证模型是否建议 revision/escalation。provider 在返回 chunked response 时中断，重试后仍为
`IncompleteRead`；该回合 fail closed，没有 feedback receipt，也没有候选状态变化。离线
feedback 纵切仍证明即使 DeepSeek 建议淘汰，fixture/NONE evidence 也会被确定性审计拒绝。

## 验证

定向跨模块测试：`65 passed`。全仓 Ruff、`pip check`、`git diff --check` 通过。完整离线
pytest 在历史 flat-band/spglib 压力用例长期运行时于 42:01 人工终止；终止前结果为
`339 passed, 19 skipped` 且未出现失败，因此不能表述为全量 Gate 通过。未执行真实 CHGNet、
Uni-HamGNN、DeepH、DFT 或实验验证。

## 2026-08-25 纯数据库/ML 完整回归

历史联邦检索结果中实际保存了三个 C2DB 候选的 CIF 与 PBE 无 SOC 能带压缩包：
GaP2PdS6、P2Pd3S8 和 LiP2PdS6。新执行链为：

`CIF 二维/价态/连通性检查 → 数据库能带导入 → 本地平带严格判据 → research memory → DeepSeek feedback`

该链不运行 DFT、不生成 overlap/电子图，也不调用 GPU。C2DB 图的纵轴是相对 VBM，执行器
不再错误地把 0 eV 当作费米能；对于数据库标记为绝缘体的候选，筛选约定使用数据库带隙
中点作为化学势参考，并把该约定写入 Artifact。三条候选的 9 个 typed tasks 全部通过执行
与审计：

| 候选 | 最近目标带 | 带宽 / eV | 距化学势 / eV | 关键结论 |
|---|---:|---:|---:|---|
| GaP2PdS6 | 32 | 0.36910 | 0.04319 | 连通且无一阶交点，但带宽超过 0.05 eV |
| P2Pd3S8 | 53 | 0.57983 | 0.86174 | 带宽超限、存在一阶交点、不是费米面附近第一条带 |
| LiP2PdS6 | 87 | 0.26774 | 0.04205 | 连通且无一阶交点，但带宽超过 0.05 eV |

三者的保存能带均没有轨道投影，因此“过渡金属或金属-配体杂化贡献”仍为 unresolved，
没有被伪造为通过。尽管如此，带宽是足以否决的硬条件；速度优先 policy 允许真实数据库/
真实 ML 的明确反证把候选移出当前搜索池，但不把负筛选升级成确定材料性质证明。

真实 `deepseek-v4-pro` 自主生成 9 节点路线并全部通过确定性审计。路由为 2 rounds、
22,399 total tokens、0 transport retries，receipt SHA-256 为
`339139df7fb6c08374a2ab25cff17248990e706d98b7abd161f5b2cae250db03`。执行证据写回
memory 后，真实 feedback 回合为 2 rounds、22,590 total tokens、0 transport retries，
receipt SHA-256 为
`3d4bfb34be4645b74a18d4b4ef37d6fde8e78c365ca561b5b1358b295d556bdf`；三项淘汰均以
`SPEED_FIRST_REAL_ML_SEARCH_POOL_ELIMINATION_ACCEPTED` 通过审计。feedback cycle 为
`feedback-cycle-66ede8c0a8784dad30ec77e8`，model task plan 为
`model-task-plan-46a2bd01e42e70988368570e`。

这次结论只淘汰三个数据库母相，不外推到应变、插层、空位或元素替换后的新结构。由于
能带无 SOC 且无轨道 projector，也不能据此判断 SOC、拓扑不变量或轨道来源。
