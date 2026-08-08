# Inspiration 工作报告制度

`docs/reports/` 保存工程进度、决策和 GitHub 发布状态；`docs/runs/` 保存具体实机命令、session、
Artifact 和 hash 证据。工作报告不可覆盖：同一报告需要补充时新增文件，或在发布前的同一提交中
完成；已经进入 public `main` 的报告不改写历史事实。

## 节奏

- 每个独立 Gate、重大失败或架构决策后提交一份报告；
- 连续 2 个活跃小时没有 Gate 时提交 interim report；
- 每个 PR 合并前后分别记录验证与发布状态；
- 每个可独立验证的代码切片提交，最长 60 分钟不积压未提交改动；
- 每 1–3 个提交或最长 2 个活跃小时推送一次公开 topic branch。

文件名使用 `YYYY-MM-DD-HHMM-inspiration-progress.md`（Asia/Shanghai）。

## 最小内容

- 报告区间、CST 时间、branch、base/covered-through SHA、PR；
- 当前 scope、Definition of Done 与 honest status；
- 本期文件/提交和精确测试命令；
- pass/skip/warning/耗时、run/session/artifact/hash/成本；
- 失败、调试、已知风险和下一 Gate；
- `scientific_conclusion=false`、target property `UNKNOWN`、不做 novelty 的边界；
- push/check/review/merge/anonymous-read 等外部证据。
