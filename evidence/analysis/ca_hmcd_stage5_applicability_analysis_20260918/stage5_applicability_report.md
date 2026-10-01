## Material Passport

- Origin Skill: experiment-agent
- Origin Mode: run + validate
- Origin Date: 2026-09-18
- Verification Status: ANALYZED
- Version Label: ca-hmcd-stage5-applicability-v1.1

# CA-HMCD 阶段5：外部基线与适用边界定稿

## 完成状态

- 正式实验：750个30种子轨迹单元，9000条逐周期记录。
- 公平性审计：1800个场景-种子-周期组全部通过。
- 统计：220项配对比较，55个Holm比较族。
- 比较方法：CA-HMCD-FixedK、CA-HMCD-Exact（HiGHS）、Genetic Algorithm、Greedy和External Auction。
- 冻结价值模型、外部评价器和服务端点均未修改。

## 方法选择矩阵

下表依据各方法的样本均值作描述性选择；统计推断仍以配对区间和Holm校正结果为准。

| 场景 | 最高统一目标均值 | 最高独立服务均值 | 最快方法 | 100 ms且命中率>=95%的最高目标均值方法 |
|---|---|---|---|---|
| airport_corridor | CA-HMCD-FixedK | CA-HMCD-FixedK | External-Auction | CA-HMCD-FixedK |
| energy_facility | External-Auction | CA-HMCD-FixedK | External-Auction | External-Auction |
| public_event | CA-HMCD-Exact | CA-HMCD-Exact | External-Auction | CA-HMCD-Exact |
| urban_corridor | Genetic-Algorithm | CA-HMCD-Exact | External-Auction | CA-HMCD-FixedK |
| industrial_zone | Genetic-Algorithm | CA-HMCD-FixedK | External-Auction | CA-HMCD-Exact |

## FixedK与Exact模式

- **airport_corridor**：FixedK-Exact统一目标差 0.0000 (95% BCa CI 0.0000 to 0.0000; Holm P=1)；服务差 0.0000 (95% BCa CI 0.0000 to 0.0000; Holm P=1)；端到端时间差 -12.8668 (95% BCa CI -22.8142 to -7.0743; Holm P=2.199e-05)。
- **energy_facility**：FixedK-Exact统一目标差 0.0000 (95% BCa CI 0.0000 to 0.0000; Holm P=1)；服务差 0.0000 (95% BCa CI 0.0000 to 0.0000; Holm P=1)；端到端时间差 -5.0751 (95% BCa CI -7.0039 to -3.6322; Holm P=1.351e-05)。
- **public_event**：FixedK-Exact统一目标差 -0.0012 (95% BCa CI -0.0039 to 0.0022; Holm P=0.375)；服务差 -0.0004 (95% BCa CI -0.0008 to -0.0001; Holm P=0.05469)；端到端时间差 13.3832 (95% BCa CI 6.8152 to 22.2048; Holm P=0.004834)。
- **urban_corridor**：FixedK-Exact统一目标差 0.0011 (95% BCa CI -0.0012 to 0.0048; Holm P=0.7515)；服务差 -0.0009 (95% BCa CI -0.0028 to -0.0001; Holm P=0.4375)；端到端时间差 4.6686 (95% BCa CI 0.6079 to 10.7466; Holm P=0.3038)。
- **industrial_zone**：FixedK-Exact统一目标差 -0.0009 (95% BCa CI -0.0023 to -0.0003; Holm P=0.375)；服务差 0.0001 (95% BCa CI -0.0002 to 0.0008; Holm P=1)；端到端时间差 9.7971 (95% BCa CI 1.6180 to 22.7337; Holm P=0.9344)。

HiGHS优化的是同一CA-HMCD价值模型。因此，某一场景中Exact模式均值更高时，差异属于候选空间和求解后端，而不表示价值模型被外部方法否定。五个场景中，Exact相对FixedK的统一目标和独立服务差异均未在Holm校正后达到显著水平。

## 截止期边界

| 场景 | 50 ms | 100 ms | 200 ms | 500 ms |
|---|---|---|---|---|
| airport_corridor | CA-HMCD-FixedK | CA-HMCD-FixedK | CA-HMCD-FixedK | CA-HMCD-FixedK |
| energy_facility | External-Auction | External-Auction | External-Auction | External-Auction |
| public_event | No verified method | CA-HMCD-Exact | CA-HMCD-Exact | CA-HMCD-Exact |
| urban_corridor | CA-HMCD-Exact | CA-HMCD-FixedK | CA-HMCD-FixedK | Genetic-Algorithm |
| industrial_zone | CA-HMCD-Exact | CA-HMCD-Exact | CA-HMCD-Exact | Genetic-Algorithm |

上表仅在至少95%的实测决策周期满足截止期的方法中选择统一目标最高者。它不构成硬实时认证。

## 论文主张冻结

| ID | 决策 | 允许进入正文的表述 |
|---|---|---|
| C5.1 | **REJECT** | The evaluated experiments did not establish a real-time computational advantage over full-candidate HiGHS. |
| C5.2 | **RETAIN_WITH_SCOPE** | CA-HMCD-Exact provides the complete-candidate reference backend. In the registered tests, it did not show a Holm-significant external-objective or service advantage over FixedK; backend selection should therefore follow the scenario- and deadline-specific evidence. |
| C5.3 | **REJECT** | CA-HMCD-FixedK is a bounded-computation approximation whose quality-runtime trade-off must be stated explicitly. |
| C5.4 | **RETAIN** | The stability term reduces allocation switching at a measurable immediate-service cost. |
| C5.5 | **RETAIN** | True-state repair restores final feasibility under perception disturbance, with an explicit service trade-off. |
| C5.6 | **REJECT** | Compatibility and complementarity are context-dependent components with service-level evidence but no universal complete-objective advantage. |
| C5.7 | **REJECT** | The redundancy term remains a modeling safeguard; its operational effect was not supported under the registered tests. |
| C5.8 | **RETAIN_WITH_SCOPE** | The framework is supported by controlled engineering-shaped simulation, not by field deployment evidence. |

## 解释边界

- 质量-时间Pareto非支配记录共29条；完整记录保存在`stage5_pareto_front.csv`。
- 阶段3 Gate B仍然有效：当前证据不支持“实时优势”或“普遍比HiGHS更快”的表述。
- 阶段4的稳定性和真实状态修复证据不因HiGHS质量更高而失效；它们属于价值模型和安全控制机制，而不是求解器速度主张。
- 工程场景仍为受控模拟。公开轨迹和真实观测模式不能替代响应侧实装测试。

## Fallacy Scan

- Coverage: 11/11 fallacy types checked.
- Pseudoreplication: inference uses one complete seed trajectory.
- Multiple testing: endpoint-specific Holm families are explicit.
- Metric circularity: independent service excludes optimization terms.
- Solver/model conflation: HiGHS is labelled CA-HMCD-Exact.
- Runtime cherry-picking: end-to-end time includes candidate construction.
- Deadline post-selection: four deadlines were frozen before execution.
- Null-is-equivalence: nonsignificance is not called equivalence.
- Bounded-outcome misuse: Wilson intervals are reported.
- Common-support bias: response time uses jointly served task-periods.
- Causal overreach: claims are limited to controlled interventions.
- Generalization overreach: no field-deployment claim is made.

## Reproducibility

- Formal execution used the locked Python 3.12.14, NumPy 2.5.3 and SciPy 1.18.1 environment.
- Every shard is bound to the protocol fingerprint and code hashes.
- Verification status is `ANALYZED`; deterministic spot re-runs and repository regression tests are reported separately.

## Source fingerprints

- Protocol: `e3e9bba35f532aa93bea35f5153a68396e965aa5ff5b093d5633168db757d373`
- Execution metadata SHA-256: `55f52dbec0d7d71cf1c04889aa2ca473831dc6872029bef0b5a8d50c02d1eedf`
