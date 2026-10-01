## Material Passport

- Origin Skill: academic-research-suite / experiment-agent
- Origin Mode: run + validate
- Origin Date: 2026-09-18
- Verification Status: COMPLETE
- Version Label: ca-hmcd-stage5-completion-v1

# CA-HMCD 第5阶段完成报告

## 1. 完成范围

第5阶段“外部基线与适用边界”已完成，且未修改冻结的价值模型、外部评价器和服务端点。

- 场景：airport corridor、energy facility、public event、urban corridor、industrial zone。
- 方法：CA-HMCD-FixedK、CA-HMCD-Exact（HiGHS）、Genetic Algorithm、Greedy、External Auction。
- 正式实验：750个独立轨迹单元，每个单元12个周期，共9000条周期记录。
- 随机种子：每个场景和方法统一使用30个配对种子。
- 公平性审计：1800个场景-种子-周期组全部通过。
- 统计分析：220项配对比较，划分为55个Holm校正比较族。
- 区间与效应报告：95% BCa置信区间、未校正P值、Holm调整P值、效应量、胜率和有界指标Wilson区间。
- 响应时间：仅在两种方法共同服务的任务-周期上比较。

## 2. 核心结果

### 2.1 不存在普遍最优方法

不同场景和决策约束对应不同选择。按统一目标均值，五个场景的描述性最优方法分别为FixedK、External Auction、Exact、Genetic Algorithm和Genetic Algorithm；这些均值排序不能替代配对统计检验。

### 2.2 FixedK与Exact

Exact在2/5个场景具有更高的统一目标均值，在2/5个场景具有更高的独立服务均值，但所有对应差异在Holm校正后均不显著。因此，不能将Exact或FixedK写成普遍质量最优。

HiGHS求解的是同一CA-HMCD价值模型及完整候选空间，应标记为`CA-HMCD-Exact`，而不是与所提价值模型相互独立的外部方法。`CA-HMCD-FixedK`则是候选压缩和有界搜索配置。

### 2.3 外部基线

- External Auction在五个场景中均具有最低平均端到端时间，范围为8.51--44.65 ms。
- FixedK相对External Auction在五个场景中的独立服务得分均更高，且差异均通过Holm校正；统一目标优势在airport corridor、public event和urban corridor中显著。
- Genetic Algorithm的平均端到端时间为271.49--376.01 ms，未获得相对FixedK的Holm显著统一目标或独立服务优势。
- Greedy在若干场景较快，但在airport corridor、public event、urban corridor和industrial zone中出现可检测的服务或统一目标损失。

### 2.4 截止期边界

在至少95%的周期满足截止期后，按统一目标均值选择：

| 场景 | 50 ms | 100 ms | 200 ms | 500 ms |
|---|---|---|---|---|
| airport corridor | FixedK | FixedK | FixedK | FixedK |
| energy facility | External Auction | External Auction | External Auction | External Auction |
| public event | 无验证方法 | Exact | Exact | Exact |
| urban corridor | Exact | FixedK | FixedK | Genetic Algorithm |
| industrial zone | Exact | Exact | Exact | Genetic Algorithm |

该结果是实验平台上的经验截止期证据，不构成硬实时认证。阶段3 Gate B仍为`FAIL`。

## 3. 论文主张边界

可保留：

- 稳定性项能够减少分配切换，但存在即时服务代价。
- 真实状态修复能够在感知扰动下恢复最终可行性，但存在服务权衡。
- 框架已在受控、工程形态的低空安全仿真场景中验证。
- Exact与FixedK可作为同一框架下的两种求解配置，按场景和截止期选择。

必须删除或收紧：

- “CA-HMCD具有普遍实时优势”。
- “FixedK或Exact在所有场景中质量最优”。
- “兼容性和互补性普遍提高完整目标”。
- “当前冗余惩罚已经获得充分运行机制验证”。
- 任何现场部署、硬实时认证或真实响应装备验证表述。

## 4. 推荐的论文定位

论文的主要贡献应从“提出一个普遍优于精确求解器的实时算法”调整为：

1. 面向低空安全异构响应资源的可解释联盟价值模型。
2. 将稳定性控制、真实状态安全修复和统一外部评价器组成闭环。
3. 提供Exact、FixedK和轻量基线之间可复核的质量-计算代价适用边界。
4. 证明所提框架能够支持按场景、任务负载和截止期选择求解配置，而不是宣称单一求解器通用最优。

## 5. 验收结果

- 第5阶段专用测试：5/5通过。
- 全仓库回归测试：77/77通过。
- 五种方法的确定性复算：通过。
- 正式结果数量、配对设计、源文件哈希和协议指纹：通过。
- 分析报告中的过强Exact主张已修正。

## 6. 主要交付物

- `ca_hmcd_stage5_applicability_protocol_20260918/`
- `ca_hmcd_stage5_applicability_formal_20260918/`
- `ca_hmcd_stage5_applicability_analysis_20260918/stage5_applicability_report.md`
- `ca_hmcd_stage5_applicability_analysis_20260918/stage5_paired_comparisons.csv`
- `ca_hmcd_stage5_applicability_analysis_20260918/stage5_deadline_selection.csv`
- `ca_hmcd_stage5_applicability_analysis_20260918/stage5_claim_freeze.csv`
- `ca_hmcd_stage5_applicability_analysis_20260918/stage5_analysis_manifest.json`
- `test_ca_hmcd_stage5_applicability.py`

## Fallacy Scan

- Pseudoreplication: inference uses complete seed trajectories.
- Multiple testing: 55 Holm families are explicitly registered.
- Solver/model conflation: HiGHS is treated as the Exact backend of the same model.
- Runtime cherry-picking: runtime includes candidate construction and solver execution.
- Null-is-equivalence: nonsignificant differences are not described as equivalence.
- Deadline post-selection: 50, 100, 200 and 500 ms were frozen before execution.
- Generalization overreach: conclusions remain limited to controlled simulation.
