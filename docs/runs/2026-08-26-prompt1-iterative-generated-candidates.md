# Prompt1 母相淘汰后的迭代结构搜索（ML-only）

- 日期：2026-08-26
- 生产运行：`prompt1-iterative-search-r5`
- 搜索结果：`scientific-candidate-search-4b904d292a3561f3748254cb`

## 结论

三个 C2DB 母相被真实数据库能带淘汰后，Hermes 没有结束任务，而是让
DeepSeek 原生推理调用动态 operator compiler。短预算恢复器采用本轮已经由
DeepSeek 提出、且通过 registry/parameter/geometry prior 的全部新结构计划，
实际生成 4 个 hash-pinned CIF。随后本地脚本完成二维性、周期空隙、过渡金属
连通子图和常见价态筛选。

最终得到 3 个可继续电子筛选的 `GENERATED_STRUCTURE_CANDIDATE`，另 1 个因
Ni 被推断为不常见 +1 价而失败。没有候选被升级为 `ML_SCREENING_CANDIDATE`，
因为当前平台没有适用于任意新 CIF 的真实能带/Hamiltonian 模型。运行终态为
`CAPABILITY_GAP`，而不是错误地声称满足 50 meV 平带条件。

全程 `ML_ONLY`；没有运行 DFT，也没有 DFT fallback。

## 母相真实失败基线

| 母相 | C2DB 近费米带宽 | 相对 50 meV 阈值 |
|---|---:|---:|
| LiP2PdS6 | 267.74 meV | 5.35 倍 |
| GaP2PdS6 | 369.10 meV | 7.38 倍 |
| P2Pd3S8 | 579.83 meV | 11.60 倍 |

## r5 生成结构

| 状态 | candidate | 化学式 | DeepSeek 操作 | 本地结构结论 |
|---|---|---|---|---|
| 保留作电子筛选 | `candidate-e13a4f2d6c068ba6fb6831b1` | P2PdS6 | 从 LiP2PdS6 移除完整 Li 等价类 | 2D；29.75 Å 周期空隙；Pd 子图周期连通；Pd(II) 常见 |
| 保留作电子筛选 | `candidate-4d1da7581a2927217b120513` | NiP2(PdS4)2 | P2Pd3S8 中一类 Pd(II)→Ni(II) | 2D；29.76 Å 周期空隙；Ni/Pd 子图周期连通；Ni(II)/Pd(II) 常见 |
| 保留作电子筛选 | `candidate-9fb96f86e6afc42c22bf147e` | Ni2P2PdS8 | P2Pd3S8 中另一完整 Pd 等价类→Ni(II) | 2D；29.76 Å 周期空隙；Ni/Pd 子图周期连通；Ni(II)/Pd(II) 常见 |
| 修改 operator | `candidate-897e9020ca962882a3026e92` | GaNi(PS3)2 | GaP2PdS6 的 Pd(II)→Ni(II) | 2D、连通，但推断 Ni(I)，命中 `UNCOMMON_TRANSITION_METAL_VALENCE` |

以上三个“保留”只代表结构硬门通过和 DeepSeek 反馈保留，不代表平带通过。
结构 executor 的化学 prior 仍把四个结构标为 `REQUIRES_REVIEW`，主要原因是
CIF 无显式净电荷、SMACT/显式电荷不能给出无歧义的形式电荷闭合。

DeepSeek 对失败的 GaNi(PS3)2 没有直接淘汰，而是提出下一步 Ga(III)→Zn(II)
以恢复 Ni(II) 的最小修改。该修改已写入反馈契约，但由于电子模型能力缺口，
r5 没有继续执行第二轮。

## 已覆盖与未覆盖

已由真实本地执行覆盖：

- 新 CIF 的 hash、lineage、不可变写入与 round-trip；
- operator registry、参数模型、完整对称等价类和最小距离；
- 二维性与周期空隙；
- 过渡金属贡献子图的周期连通 proxy；
- 常见/混合价态规则筛选；
- 孤立真空插层硬拒绝。

仍缺少适用于新 CIF 的电子模型，因此明确 unresolved：

- `bandwidth_le_50_mev`；
- `flat_band_first_near_fermi`；
- `no_first_order_crossing`；
- `dispersive_band_no_fermi_crossing`；
- `tm_or_ligand_hybrid_orbital_character`。

## 自动修复与预算

- 单轮 completion ceiling：32768 tokens；
- operator：最多 4 rounds / 12 tool calls / 100k total tokens；
- route 与 feedback：最多 4 rounds，检查工具每次 run 只允许调用一次；
- operator 若在完成 compiler 后发生 token/网络失败，只采用该轮已经通过 compiler
  的结构计划，不产生新科学参数；本次 r5 触发
  `ALL_NEW_COMPILED_STRUCTURE_PLANS_AFTER_REASONING_FAILURE`；
- feedback 失败时仍会保存生成结构、DAG evidence 和显式失败 receipt；
- 搜索控制器支持 child→下一轮 parent、结构 hash 去重和全局候选/成本预算。

## 主要 artifact

- 完整搜索结果：
  `output/prompt1-c2db-live-full-v2/scientific_loop/prompt1-iterative-search-r5/search-results/scientific-candidate-search-4b904d292a3561f3748254cb.json`
- operator planning audit：
  `output/prompt1-c2db-live-full-v2/scientific_loop/prompt1-iterative-search-r5/operator-planning-audit.json`
- feedback cycle：
  `output/prompt1-c2db-live-full-v2/scientific_loop/prompt1-iterative-search-r5/cycles/feedback-cycle-9f5972213fd90164d380f576.json`
- 生成 CIF：
  `str_861b0af6e831ec44d203e48c.cif`、
  `str_6857542f2614d1bc573d7a13.cif`、
  `str_d885365b25152fc48164face.cif`、
  `str_a4afcb76c580735694e549ca.cif`。

## 后续稳定性加固

2026-08-26 在不改变科学门槛、不加入 DFT 的前提下完成以下改造：

- operator reasoner 不再接收完整 evidence、feedback、parent 和编译计划对象，
  改为目标相关摘要；完整 hash-pinned plan 只保留在本地 audit/checkpoint；
- compiler 工具只返回 plan ID、operator/spec ID、prior 结论以及是否生成 CIF，
  避免大计划对象在 DeepSeek 多轮上下文中反复累积；
- 每次 compiler 调用后立刻写 content-addressed、不可变 planning checkpoint；
- 达到 `min(剩余候选数, 剩余成本, max_plans_per_reasoning)` 后动态关闭工具，
  强制进入最终选择，避免在已有足够候选时继续消耗 completion；
- 新增显式 checkpoint 恢复入口。恢复时重验完整 typed audit 与 registry hash，
  只复用之前已由 DeepSeek 编译的结构计划，不再次生成科学参数；
- 默认 run ID 改为 UTC 微秒级唯一值；显式复用已有 run ID 会在写入前失败；
- 每次运行先把输入 research result 冻结到本次 run 目录，避免外部临时文件
  消失后无法复现；最终 manifest 记录 checkpoint、最终 audit、压缩 payload
  字节数和冻结输入 URI；
- 单轮 completion ceiling 保持 32768。它是上限而非预分配，不再继续增大；
  真正的时延和 token 控制改由压缩上下文、最多 4 个计划/轮和动态停止承担。

回归验证：38 个 Prompt1/operator/search/structure 定向测试全部通过；另验证
重复 run ID 在任何科学执行前 fail-fast。当前尚未把这次稳定性改造重新计为一轮
新的在线科学结果，因此本报告上半部分仍以已完成的 r5 真实运行作为结论基线。
