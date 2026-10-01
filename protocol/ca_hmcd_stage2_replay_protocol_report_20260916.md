# CA-HMCD 第二阶段：公开数据回放实验协议

日期：2026-09-16

## 完成状态

第二阶段已经完成。当前成果是冻结后的实验协议、回放文件与运行清单，
尚不包含算法结果。后续批量运行必须直接读取本阶段生成的注册表，不再临时
重新抽样或改变负载组成。

## 1. 数据角色与证据边界

- UZH-FPV提供真实三维位置轨迹，用于验证真实运动学条件下的滚动分配。
- Anti-UAV410提供真实图像平面运动、尺度和可见性变化，用于验证观测模式
  与并发任务负载。
- 保护区配准、多个独立视频的并发组合、风险与响应时间窗、十个异构响应
  资源、资源效能和处置结果仍为仿真构造。
- 因此，实验应称为“public-data-informed semi-synthetic replay”，不能称为
  现场部署验证或真实反无人机系统试验。

## 2. 数据分割与非复用规则

UZH-FPV的16条有效公开轨迹全部作为独立外部运动源。Anti-UAV410严格采用
官方分割：train仅用于开发，val仅用于参数校准，test仅用于正式评价。
120条test序列均被分配一次且仅一次，train和val中没有任何序列进入正式
工作负载。

## 3. 固定工作负载

| 数据集 | 任务负载 | 工作负载数 | 每个负载的配对种子 | 运行单元数 |
|---|---:|---:|---:|---:|
| UZH-FPV | 1 | 16 | 30 | 480 |
| Anti-UAV410 | 2 | 10 | 30 | 300 |
| Anti-UAV410 | 4 | 8 | 30 | 240 |
| Anti-UAV410 | 6 | 6 | 30 | 180 |
| Anti-UAV410 | 8 | 4 | 30 | 120 |
| 合计 | - | 44 | - | 1,320 |

所有工作负载统一采用12个滚动决策周期、10个响应资源、top-12候选保留和
每周期4,000个搜索节点。所有比较算法读取相同回放文件、相同活动任务、
相同到达偏移、相同资源数和相同随机种子。

Anti-UAV410的固定到达偏移为：负载2使用`(0, 2)`；负载4使用
`(0, 1, 2, 3)`；负载6使用`(0, 0, 1, 2, 3, 4)`；负载8使用
`(0, 0, 1, 1, 2, 2, 3, 4)`。这些偏移只构造滚动到达，不改变源视频内部
的观测顺序。

## 4. 场景分层和平衡

Anti-UAV410 test序列按预先声明的优先级分为四类：visibility 42条、
fast-motion 19条、scale/small 13条和nominal 46条。各负载层按全体测试集
比例分配，避免将负载强度与观测难度混杂。

| 任务负载 | Nominal | Visibility | Fast motion | Scale/small | 合计 |
|---:|---:|---:|---:|---:|---:|
| 2 | 8 | 7 | 3 | 2 | 20 |
| 4 | 12 | 11 | 5 | 4 | 32 |
| 6 | 14 | 12 | 6 | 4 | 36 |
| 8 | 12 | 12 | 5 | 3 | 32 |

## 5. 统计单位

30个随机种子是同一固定工作负载内的配对重复，用于控制算法和仿真资源
随机性，不是30条独立真实轨迹。UZH-FPV的独立运动源单位为原始飞行轨迹
（16个cluster）；Anti-UAV410的主要推断单位为预注册组合工作负载
（28个cluster），同时保留全部父视频标识。算法比较应使用配对分析，并在
置信区间或重采样中按上述cluster处理，避免伪重复。

## 6. 审计结果

以下约束均已自动检查并通过：

- 16条UZH-FPV轨迹全部且仅分配一次；
- 120条Anti-UAV410 test序列全部且仅分配一次；
- train和val序列进入正式负载的数量为0；
- 每个工作负载恰有30个配对种子；
- 44个回放均为12个决策周期且序列标识唯一；
- 回放中的任务数与注册负载一致；
- 到达偏移与预注册模式一致；
- 资源数、候选上限和节点预算在全部工作负载中一致；
- 四类Anti-UAV410观测场景在每个负载层内按配额平衡；
- 1,320个运行种子全局唯一。

## 7. 论文可用英文表述

### Public-data-informed replay protocol

We constructed a preregistered semi-synthetic replay protocol using UZH-FPV
and Anti-UAV410 as complementary sources of task-side evidence. The 16
quality-controlled UZH-FPV trajectories supplied measured three-dimensional
motion. For Anti-UAV410, the official training split was restricted to
development, the validation split to calibration, and all 120 test sequences
to evaluation. Each test sequence was assigned to exactly one composed
workload. The resulting evaluation set comprised ten two-task, eight
four-task, six six-task, and four eight-task workloads. Nominal, visibility,
fast-motion, and scale/small-target sequences were proportionally balanced
within each load group to reduce confounding between concurrency and
observation difficulty.

Each workload was replayed over 12 rolling decision periods with ten response
resources, top-12 candidate retention, and a 4,000-node search budget. Thirty
paired stochastic replications were registered for every fixed workload. All
methods received the same replay states, task-arrival offsets, resource count,
and random seed in each comparison. Random seeds were treated as nested
replications rather than independent public trajectories. Inference was
clustered by original UZH-FPV trajectory or by preregistered Anti-UAV410
workload, with all parent-video identities retained for traceability.

Public data supplied motion and observation patterns only. Protected-zone
registration, concurrent composition of independent Anti-UAV410 videos,
task-risk and response-window parameters, heterogeneous response resources,
and allocation outcomes remained simulated. We therefore interpret this
experiment as public-data-informed semi-synthetic validation rather than field
deployment evidence.

## 8. 输出文件

- `ca_hmcd_replay_protocol.py`：协议生成器；
- `test_ca_hmcd_replay_protocol.py`：协议自动化测试；
- `ca_hmcd_replay_protocol_20260916/source_registry.csv`：426个数据源及分割；
- `ca_hmcd_replay_protocol_20260916/scenario_registry.csv`：场景注册表；
- `ca_hmcd_replay_protocol_20260916/workload_registry.csv`：44个固定工作负载；
- `ca_hmcd_replay_protocol_20260916/workload_task_manifest.csv`：136条任务映射；
- `ca_hmcd_replay_protocol_20260916/experiment_run_registry.csv`：1,320个运行单元；
- `ca_hmcd_replay_protocol_20260916/protocol_config.json`：冻结参数与分层配额；
- `ca_hmcd_replay_protocol_20260916/protocol_audit.csv`：协议审计结果；
- `ca_hmcd_replay_protocol_20260916/replays/`：44个标准化回放CSV。
