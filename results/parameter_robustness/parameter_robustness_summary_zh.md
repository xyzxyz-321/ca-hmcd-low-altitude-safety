# CA-HMCD 54 项参数稳健性结果报告

日期：2026-09-17

## 1. 试验口径

“54 项”指 6 个模型系数、每个系数 3 个注册水平、每个水平 3 种方法形成的
54 个参数-方法配置，并非 54 个彼此独立的模型参数。

- 系数：`alpha_cost`、`beta_time`、`gamma_unserved`、
  `lambda_redundancy`、`lambda_switch`、`synergy_scale`
- 方法：CA-HMCD、External-Auction、Greedy
- 每个配置：30 个配对随机种子，12 个滚动决策周期
- 总记录：1,620 条完整种子轨迹
- 独立推断单位：一条完整的 12 周期种子轨迹
- 主要终点：与求解器状态解耦的独立服务质量得分

每个系数构成一个预先定义的 Holm 比较族，包含 3 个水平乘以 2 个对照方法，
即每族 6 个比较。全试验共 36 个配对比较、6 个 Holm 比较族。每个比较报告
均值配对差、10,000 次 BCa 配对 bootstrap 95% 置信区间、未校正 P 值、
Holm 调整后 P 值、配对 Cohen's dz、秩二列相关和胜率。

## 2. 汇总结果

| 系数 | 注册水平 | CA-HMCD 服务得分 | 得分极差 | 相对对照优势范围 | Holm 显著 |
|---|---|---:|---:|---:|---:|
| `alpha_cost` | 0.06 / 0.12 / 0.24 | 0.366 / 0.348 / 0.313 | 0.0531 | 0.0083 至 0.0276 | 6/6 |
| `beta_time` | 0.04 / 0.08 / 0.16 | 0.357 / 0.348 / 0.334 | 0.0234 | 0.0153 至 0.0203 | 6/6 |
| `gamma_unserved` | 0.10 / 0.20 / 0.40 | 0.329 / 0.348 / 0.369 | 0.0392 | 0.0144 至 0.0229 | 6/6 |
| `lambda_redundancy` | 0.06 / 0.12 / 0.24 | 0.348 / 0.348 / 0.348 | 0.0004 | 0.0162 至 0.0181 | 6/6 |
| `lambda_switch` | 0.05 / 0.10 / 0.20 | 0.375 / 0.348 / 0.294 | 0.0806 | 0.0043 至 0.0352 | 6/6 |
| `synergy_scale` | 0.50 / 1.00 / 1.50 | 0.348 / 0.348 / 0.349 | 0.0015 | 0.0162 至 0.0191 | 6/6 |

36/36 个 CA-HMCD 相对 External-Auction 或 Greedy 的服务得分差值均为正，
并在各自预先定义的六成员比较族内通过 Holm 校正。最弱比较为
`lambda_switch = 0.20` 时 CA-HMCD 相对 External-Auction：
均值差 0.0043，95% CI [0.0013, 0.0112]，Holm P = 0.0305，
配对 dz = 0.353，种子级胜率 65%。

## 3. 解释边界

结果支持“在注册的一因子合成扫描范围内，相对对照排序保持稳定”，但不支持
“绝对性能不受参数影响”。`lambda_switch` 和 `alpha_cost` 引起的绝对服务
得分变化最大；`lambda_redundancy` 和 `synergy_scale` 的变化较小。

该分析属于受控合成敏感性试验。它不能替代基于独立公开轨迹的外部验证，
也不能给出工程部署所需的参数标定值。部署系数仍需通过专家标定、响应资源
实测或硬件在环试验确定。

## 4. 完整结果位置

- 54 行配置统计：
  `CA-HMCD_Supplementary_Table_S9_Parameter_Configurations_20260917.csv`
- 36 行完整配对统计：
  `CA-HMCD_Supplementary_Table_S10_Parameter_Paired_Statistics_20260917.csv`
- 6 个参数 Holm 家族：
  `ca_hmcd_parameter_robustness_analysis_20260917/holm_family_registry_6.csv`
- 1,620 条种子级记录：
  `ca_hmcd_parameter_robustness_analysis_20260917/seed_level_records.csv`
- 全论文统一统计注册表：
  `CA-HMCD_Reproducibility_Package_20260917/results/statistical_registry/complete_statistical_registry_516.csv`
- 全论文统一 Holm 家族注册表：
  `CA-HMCD_Reproducibility_Package_20260917/results/statistical_registry/complete_holm_family_registry_141.csv`

统一注册表共包含 516 项比较和 141 个预先定义的 Holm 比较族，覆盖正式公开
回放、无剪枝对照、外部全候选基线和参数稳健性四个证据模块。
