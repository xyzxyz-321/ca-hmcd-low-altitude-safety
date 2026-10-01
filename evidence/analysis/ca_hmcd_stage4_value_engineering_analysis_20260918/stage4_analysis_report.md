## Material Passport

- Origin Skill: experiment-agent
- Origin Mode: run + validate
- Origin Date: 2026-09-18
- Verification Status: ANALYZED
- Version Label: ca-hmcd-stage4-value-engineering-v1

# CA-HMCD 阶段4：价值模型与工程机制验证

## 完成状态

- 正式实验：2,070 个30种子轨迹单元，24,840 条逐周期记录。
- 配对审计：7,560 个状态-周期组，全部通过。
- 候选池：完整价值模型生成的共享 K=12 候选池；节点上限 12,000。
- 推断：280 项配对比较，84 个明确注册的 Holm 比较族。
- 冻结模型、外部评价器和服务评价端点均未修改。

## 决策门结果

| 假设/机制 | 判定 | 证据摘要 |
|---|---|---|
| H4.1 compatibility | **CONTEXT_DEPENDENT** | 0/5 scenarios supported both objective and service: none; 3/5 supported at least one endpoint |
| H4.2 complementarity | **CONTEXT_DEPENDENT** | scale-0 identity=False; scale-2 objective difference=0.007444, 95% CI [-0.002561, 0.019586]; scale-2 service difference=0.004857 |
| H4.3 redundancy | **NOT_SUPPORTED** | 0/3 diagnostics supported: none; service difference=0.000000 |
| H4.4 stability | **SUPPORTED** | 4/4 non-zero volatility levels supported switch reduction: [0.05, 0.1, 0.2, 0.3] |
| H4.5 true-state repair / Gate C | **SUPPORTED** | supported non-zero noise levels: [0.05, 0.1, 0.2, 0.3]; service trade-off levels: [0.05, 0.1, 0.2, 0.3]; RepairOn cell count=5 |
| H4.6 engineering transfer | **SUPPORTED** | all CA-HMCD engineering trajectories feasible=True; supported scenarios 4/5: airport_corridor, public_event, urban_corridor, industrial_zone |

## 关键效应

- 互补机制（scale=2）：0.0074 (95% BCa CI -0.0026 to 0.0196; Holm P=0.7312; dz=0.242)。
- 互补机制的独立服务端点（scale=2）：0.0049 (95% BCa CI 0.0033 to 0.0070; Holm P=7.388e-05; dz=0.952)。
- 冗余机制（同类资源对比例）：0.0000 (95% BCa CI 0.0000 to 0.0000; Holm P=1; dz=0.000)。
- 稳定性机制（volatility=0.30，切换次数）：-3.4583 (95% BCa CI -3.8472 to -3.0528; Holm P=8.996e-06; dz=-3.096)。
- 安全修复（noise=0.30，可行率）：0.7944 (95% BCa CI 0.7444 to 0.8444; Holm P=6.562e-06; dz=5.688)。
- 安全修复的即时服务代价（noise=0.30）：-0.0240 (95% BCa CI -0.0386 to -0.0106; Holm P=0.005848; dz=-0.610)。

## 解释边界

1. 本阶段把候选身份、状态轨迹、随机种子和外部评价器固定，但候选池仍是 K=12 压缩池，因此结论针对已注册的实际决策流程，不等同于全组合空间中的纯解析证明。
2. 工程场景由低空安全任务需求结构驱动，响应资源仍为模拟资源；因此可支持机制合理性和半合成工程证据，不能替代实装系统试验。
3. 互补、冗余和稳定性分别报告独立结构指标与服务结果；若结构指标改变但总体服务未改善，只能声称机制生效，不能声称总体性能普遍提高。
4. 修复模块的判据是恢复真实状态下的可行性，而不是恢复到无感知误差时的目标值。

## 统计完整性

- 独立实验单位为一个完整的12周期随机种子轨迹，未把周期记录错误地当作独立样本。
- 每项比较分别列示未校正 P 值、Holm 调整后 P 值、95% BCa区间、Cohen dz、秩二列相关和胜率。
- 可行率、覆盖率和三项 [0,1] 结构指标另报 Wilson 区间。
- 响应时间仅在两种方法共同服务的任务-周期上比较。

## Fallacy Scan

- Coverage: 11/11 fallacy types checked.
- Pseudoreplication: controlled by seed-level aggregation.
- Multiple testing: controlled by explicit endpoint-specific Holm families.
- Dichotomization: continuous effects and intervals retained.
- Null-is-equivalence: unsupported results are not described as equivalent.
- Causal overreach: claims limited to controlled interventions.
- Selection bias: all registered seeds and cells included.
- Post-hoc subgrouping: only registered scenarios and factor levels analysed.
- Metric circularity: independent service endpoint excludes model terms.
- Bounded-outcome misuse: bounded intervals reported separately.
- Missing-data bias: complete-cell and common-task counts audited.
- Generalization overreach: field-deployment claims explicitly excluded.

## 复现状态

- 形式化运行、哈希绑定、分片完整性和配对状态均已验证。
- 本报告标记为 `ANALYZED`；正式全量独立二次重算不在本阶段内，因此不将状态夸大为完整外部复现。

## Material Passport Sources

- Protocol fingerprint: `39b34649ec56edcab972d4c033ab6640023a3e1265c15add37b2e49fbdc38743`
- Execution metadata SHA-256: `065e1d8c6066678590199f5094bbf94f88aee3cedd73848466e7099620f4a318`
- Trend records: 4
