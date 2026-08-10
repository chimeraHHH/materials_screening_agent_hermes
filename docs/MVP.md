# MVP：材料筛选最小可用产品

一句话：**自然语言材料需求 → 人工审批门 → 单一真实数据库检索 → 确定性筛选 →
候选报告与下游 manifest**。全链路已在真实数据上验证（2026-08-10，MC3D 实时检索
948 条记录、发布 200 个候选）。

## 一键运行

```bash
scripts/mvp_demo.sh              # 离线 fixture（无网络，验收控制流）
scripts/mvp_demo.sh mc3d         # 真实 Materials Cloud MC3D（免 key）
scripts/mvp_demo.sh nomad        # 真实 NOMAD（免 key；服务慢时会走重试门）
MP_API_KEY=... scripts/mvp_demo.sh materials_project   # 真实 MP（完整判定）
```

脚本会在需求确认门暂停等待回车（保留人工审批语义），随后执行检索、筛选并打印报告。
报告与全部 Artifact 落在 `$MVP_WORKSPACE`（默认 `/tmp/ma-mvp-demo`）。

## MVP 范围内

- Stage 0 需求解析（离线 Parser 默认；DeepSeek Provider 可显式启用）
- Agent01 单来源真实检索：Materials Project、NOMAD、MC3D、C2DB、TQC、NIMS SuperCon
- 确定性筛选：`PASS/REJECT/UNCERTAIN/FAILED`、结构身份/去重、来源 provenance、
  raw response 全量留存
- 持久化审批、跨进程 `resume`、重试门、报告与下游 manifest
- Hermes 灵感生成 companion（已于 PR #3 交付，独立入口）

## MVP 边界（有意为之，不是缺陷）

- **判定诚实性**：来源不暴露的性质不跨库补值。MC3D 无带隙/hull 字段，因此带隙类约束
  下所有候选保持 `UNCERTAIN`；要得到 `PASS/REJECT`，用字段全映射的 Materials Project
  （需 `MP_API_KEY`，注入方式见 README）。
- Agent02 真实 CHGNet 需按 Agent02 Plan 建独立 worker 环境并显式注册；Agent03/04
  仍为 mock 控制链，不产生科学证据。
- 科研 benchmark 轨道（flatband）与生产加固是独立轨道，均不阻塞本 MVP。

## 已知运行注意

- 沙箱/受限网络环境下外部 API 不可达时，系统会正确分类为 `TRANSIENT_EXTERNAL`
  并停在重试确认门，`material-agent retry` 可恢复。
- NOMAD 公共服务偶发慢响应（>30s 超时）；MC3D 通常更快。
