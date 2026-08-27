# Agent02 CHGNet Linux/CUDA 平台发布报告

日期：2026-08-24（Asia/Shanghai）

## 结论

Agent02 的真实 CHGNet `0.3.0` 已从裸上游 GPU probe 纳入 Hermes 正式平台契约，并在
WHU `NVIDIA L40S` 上通过完整发布门禁：

`health → immutable plan → no-shell JSON worker → CIF/NPZ Artifact → L2_ML_SCREENED`

这证明当前已审核的三维元素 Si 路径可以由 Hermes 在 CUDA 上真实执行。它不证明模型
适用于二维过渡金属、平带、SOC、拓扑、磁性基态或有限温度稳定性，也不扩大 L2 的科学
适用域。

## 冻结身份

| 项目 | 值 |
| --- | --- |
| Host | `WHUServer-L40S` |
| GPU | 单张 `NVIDIA L40S`，物理索引 `6` |
| Python | `3.11.15` |
| Torch / CUDA runtime | `torch 2.10.0` / CUDA 12.8 wheel stack |
| CHGNet package / model | `0.4.2` / checkpoint `0.3.0` |
| Checkpoint SHA-256 | `d14ab7c0f093efe64b60a7bcd540bca10e74fb7f46c86108a079af60524659d1` |
| CUDA lock SHA-256 | `5001cfffd380e8ea1f61bb0e2a8590456f22dca2039af2b82638908aa5d89f56` |
| Environment fingerprint | `5270360d01dec48a56f92cc40a833961cf4bfad5cb2657233b00de68a496db00` |
| 轻量控制面环境 | `/home/huayiming/Workspace/lab_codex/materials_screening_agent_gpu/hym_material_agent_platform_main_env`（887 MB） |
| CUDA worker 环境 | `/home/huayiming/.conda/envs/hym_material_agent_chgnet_cuda` |

独立环境按 `requirements-agent02-cuda.lock` 校验，`pip check` 返回
`No broken requirements found.`。轻量 Hermes 环境也通过 `pip check`，包含
`langgraph/material_agent`，但明确不安装或导入 Torch、CHGNet、ASE。

## Health 与数值 parity

固定 Si health 结构同时在 CPU 和 CUDA 上执行。结果为 `PASS`：

| 指标 | 实测绝对差 | 冻结上限 | 判定 |
| --- | ---: | ---: | --- |
| energy | `4.76837158203125e-7 eV/atom` | `2e-4 eV/atom` | PASS |
| maximum force component | `4.194676876068115e-6 eV/Å` | `2e-4 eV/Å` | PASS |
| maximum stress component | `2.956390380859375e-5 GPa` | `2e-3 GPa` | PASS |
| maximum site magmom component | `2.1606683731079102e-7 μB` | `2e-4 μB` | PASS |

Health 只报告 `available_devices=[cpu,cuda]`，worker 内部看到的 CUDA device count 为
`1`。CUDA 失败不会自动回退 CPU。

## 端到端门禁与产物

真实测试 `tests/real_ml/test_chgnet_cuda.py` 在 L40S 上返回：

```text
1 passed in 5.84s
```

候选结果通过 `PASS`、`L2_ML_SCREENED`、relaxation QC、实际设备 `cuda`、运行 provenance
和 Artifact root-relative path 校验。产物为：

| Artifact | SHA-256 |
| --- | --- |
| `forces.npz` | `5d1f6fb4b7d1af7691012ae897a9de4fc50ba90c0f37c9ede54af6b72d7ce781` |
| `relaxed.cif` | `cccb572b2250bb223ce7f2bd79e5148222558d9d799d485ce87b7cc55e8276cd` |
| `site_magnetic_moments.npz` | `d33f05c74633f76ee91d653da4546cf9997fce002b01f9d8e1cede0cb22ec754` |

测试结束后索引 `6` 的 GPU 回到空闲状态；worker 没有常驻占卡。

## 平台控制面

- `portable` 和 `cuda` 使用不同、hash-bound 的 package lock 和 model spec；
- Hermes 主控制面与 CUDA worker 使用两个独立环境，主进程没有重型 ML import；
- production factory 仅在显式配置合法 worker、profile 和单一 GPU 索引后注册；
- worker 进程不经 shell，继承的是最小白名单环境；
- lock 文件、已安装 CHGNet/Torch/Pymatgen/ASE/NumPy 和关键 CUDA wheel 版本同时校验；
- health、plan、handshake、执行身份和 lineage 的 profile/device/lock 必须一致；
- CUDA OOM、设备错误、版本漂移和 parity 越界均失败，不降级为 CPU 或 Fake evidence；
- relaxed CIF、force NPZ 和 magmom NPZ 都在返回前计算并复核 SHA-256。

在分环境部署中，正式 production factory 探针返回：

```json
{"stage":"ml","agent_id":"agent02","registered":true,"is_mock":false,"required_inputs":["requirement","candidate_manifest","policy","registry","health"],"requires_approval":false,"supports_external":false,"unavailable_reason":null}
```

第一次 pytest 调用因发布目录缺少 `run-artifacts` 父目录，在 fixture setup 阶段停止；没有
加载模型或占用 GPU。创建专用父目录后同一命令通过。该问题属于部署初始化，未改变代码、
阈值或科学输入。

本地 Agent02 相关 unit/contract/integration/companion 回归为 `198 passed`，改动文件的
Ruff、主环境 `pip check` 和 `git diff --check` 均通过。全仓 pytest 在已完成
`334 passed, 20 skipped` 后，仍耗在既有 `test_flatband_research_analysis.py` 的大型
Pydantic/spglib 压力轨，运行 10 分 56 秒后人工中止；全仓 Ruff 也仍有 33 项位于既有
Hermes/flat-band 脚本的历史债务。二者均不在本次 CUDA 改动文件中，未误报为通过。

## 剩余科学边界

本次完成的是平台执行、身份、数值 parity、故障语义和 Artifact 证据链。CHGNet v1 的
已审核发布域仍仅为周期性三维元素 Si。若要把 ML 用于用户的二维过渡金属平带材料路线，
仍需独立完成目标元素/维度 benchmark、OOD/不确定性校准，并接入能带/SOC/拓扑专用模型
或 DFT；结构预弛豫本身不能验证平带、铁磁居里温度或拓扑不变量。
