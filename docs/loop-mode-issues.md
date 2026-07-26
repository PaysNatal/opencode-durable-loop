# Loop 模式问题清单（用于改进）

> 记录日期：2026-07-25
> 来源：用 loop 模式（zeroshot 6.7.1 + zs-learn 包装层，opencode provider）真实跑一个"极简俄罗斯方块"代码生成任务的全过程观察，以及此前的架构诊断。
> 本次实验 run：集群 `silver-glacier-13`，7 个 agent，29,392+ tokens，耗时约 12 分钟，最终 **worker 卡死**，手动 kill。交付物 `tetris.html` 本身质量合格（worker 第一稿就写对了），但 **loop 的验证与学习环节全部没有发生**。

---

## 一、本次真实观察到的问题（有实证）

### 🔴 P0 — Worker 可以无声卡死，没有看门狗
- **现象**：worker 写完 `tetris.html`（20:18）后，状态一直停在 `executing_task`，**token 不再增长、文件 mtime 不再变化**，持续 10+ 分钟，直到我手动 kill。
- **证据**：`zeroshot status` 显示 worker `State: executing_task / Iteration: 1`，两次查询 token 均为 29,392 不变；文件 mtime 固定。
- **影响**：集群永远停在 `running`，不会自愈、不会超时、不会上报。用户只能靠"感觉不对劲"去手动查。对无人值守的超长程任务是致命的——你以为它在跑，其实早就死了。
- **建议**：给 agent 加 **stale 检测**（N 分钟无 token/输出/文件变化 → 标记 stale → 重试或升级告警）。settings 里已有 `staleWarningsBeforeKill: 2`，但本次显然没生效，需要排查该机制为何没覆盖"executing_task 但无进展"这种情况。

### 🔴 P0 — 卡死 → kill 之后，学习层零收获
- **现象**：这次 run 花了 29k+ tokens、12 分钟，但 `.zeroshot/lessons.jsonl` **依然是空的**。
- **原因链**（三个环节叠加）：
  1. `zs-learn run` 的等待循环被我的 bash 超时（330s）杀掉 → 跑在等待之后的 `extract_and_store_lesson` **根本没执行**；
  2. 我手动 kill 集群后，集群状态变成 `killed`，而 `harvest` 只收割 `failed/zombie/corrupted`，**跳过 killed**；
  3. kill 之后集群**立刻从 `zeroshot list --json` 消失**（harvest 直接报 "No clusters found"），连尸体都看不到。
- **影响**：**"卡死/挂起"这一整类失败，loop 永远学不会**。而这恰恰是超长程任务最常见的死法。学习层对最需要学习的失败模式是瞎的。
- **建议**：
  - 把"长时间 running 无进展后被 kill"也归类为可学习的失败（给 killed 区分子类，或单独记一条 `stall` 教训）；
  - kill 后集群记录不要立刻从 list 消失（保留墓碑供 harvest 读取）；
  - `zs-learn` 的教训提取不要只依赖"等待循环正常走到结尾"这一条路径（见 P1）。

### 🟠 P1 — `zs-learn run` 的同步等待不适合超长程任务
- **现象**：`zs-learn run` detach 后会**阻塞轮询**直到集群终态。本次任务约 12 分钟，超过了我 330s 的工具超时，等待循环被强杀。
- **影响**：等待循环一死，"等终态→提教训"整条链就断了（教训丢失，见 P0）。对真正的超长程任务（几小时），没有任何终端/工具能一直挂着等。
- **反向验证**：这恰好证明**即发即走的 `zs-learn loop` + `harvest` 才是超长程任务的正确模型**——detach 保住了集群，harvest 负责事后收割。`zs-learn run` 的阻塞等待只适合短任务。
- **建议**：`zs-learn run` 增加 `--no-wait`（发完即走，交给 harvest）；或者把"等待+提教训"也做成后台守护，不要绑死在前台进程生命周期上。

### 🟠 P1 — "验证者"环节可以被静默跳过
- **现象**：conductor-bootstrap 对 STANDARD 任务会派 `validator-requirements` + `validator-code` 两个验证者。但本次 worker 卡死，**两个 validator 迭代次数为 0，从未运行**，completion-detector 也没判定。
- **影响**：所谓"executor-verifier loop"的验证保证，在 worker 卡死时**悄无声息地没有发生**。交付物是我（编排者）人工验收的，不是 loop 验证的。如果用户信任 loop 的"approved"，会拿到未经验证的结果。
- **建议**：验证者未运行就不允许集群进入"成功"终态；worker 超时未交付时应触发验证/重试流程而不是无限挂起。

### 🟡 P2 — 可观测性差，卡死时几乎没有调试手段
- **现象**：`<cluster>-daemon.log` 全部是 **0 字节**；`zeroshot logs <id>` 不易非交互使用；`get-log-path` 对集群 id 报 "Task not found"。排查卡死只能反复 `zeroshot status` 看状态字段。
- **影响**：出了问题看不到 agent 在干什么、卡在哪一步，定位全靠猜。
- **建议**：daemon log 真正写入内容；提供 `zeroshot logs <cluster-id>` 的快照（非 -f）模式；`get-log-path` 支持集群。

### 🟡 P2 — 单文件小任务也走 7-agent 全流程，开销大
- **现象**：一个单 HTML 文件任务，触发了 junior-conductor → planner → worker → 2 validator → completion-detector 共 7 个 agent，29k+ tokens、12 分钟。
- **影响**：成本与延迟高。planner 单独就消耗 14.8k tokens（几乎和 worker 一样多）。
- **建议**：对 TRIVIAL/SIMPLE 任务走更轻的 config（跳过 planner/双 validator）；目前系统里只有 conductor-bootstrap 一个 config，需要补充轻量 config。

### 🟢 P3 — 多行任务串的 shell 引号很脆
- **现象**：我第一次用单引号内联多行任务串，直接 `unmatched '` 报错；改成写临时文件 + `$(cat ...)` 才成功。
- **影响**：长任务描述（loop 模式的常态）很难安全地通过命令行传递。
- **建议**：`zs-learn run` 支持 `-` 从 stdin 读任务（zeroshot 原生支持 `zeroshot run -`，但 zs-learn 没透出），或支持 `zs-learn run @file`。

---

## 二、已知潜在问题（此前诊断，仍未解决）

| # | 问题 | 严重度 | 说明 |
|---|------|--------|------|
| K1 | **exit:0 ≠ 成功** | 🔴 | `zeroshot run` 即使所有任务失败也返回 0，自动化无法据此判断。 |
| K2 | **裸词误 spawn 集群** | 🔴 | `zeroshot help`/`clusters` 等不是子命令，会被当成任务直接起集群（我探测时就误起过 2 个 zombie）。 |
| K3 | **executor 与 verifier 同底座，单点故障** | 🟠 | claude 未登录时编排层+执行层+验证层一起死。现已切 opencode 缓解，但底座一挂全挂的结构没变。 |
| K4 | **重试对错误类型无感知** | 🟠 | 永久性错误（如认证失败）也重试 3 次，纯浪费。 |
| K5 | **无 remediation 机制** | 🟠 | 不会自动重认证、不会 provider failover、不会给人类发可操作告警，权限墙=静默死胡同。 |
| K6 | **AskUserQuestion 被屏蔽** | 🟠 | 无人值守设计屏蔽了向用户求助，叠加 K5 导致遇到问题只能死。 |
| K7 | **`--worktree` 需要 git 仓库** | 🟡 | `~/Documents/opencode` 非 git repo，本次无法用 worktree 隔离。 |
| K8 | **教训提取是关键词启发式** | 🟢 | `classify_failure` 基于截断输出的关键词匹配；`auto_resolve` 用 >50% 词重叠，都偏脆弱。 |
| K9 | **教训按 CWD 隔离** | 🟢 | `.zeroshot/lessons.jsonl` 绑定当前目录，跨目录不迁移。 |

---

## 三、改进优先级建议

1. **先修 P0 的看门狗**：没有 stale 检测，超长程任务就是赌博。让 `staleWarningsBeforeKill` 真正覆盖"executing 但无进展"。
2. **修 P0 的学习盲区**：stall/kill 要能被学到；kill 后保留集群墓碑；教训提取解耦于前台等待进程。
3. **`zs-learn run` 加 `--no-wait`**（P1），超长程任务统一走"发后即走 + harvest"。
4. **验证者未运行不得判成功**（P1），保住 loop 最核心的承诺。
5. 其余（可观测性、轻量 config、stdin 任务、K 系列）按表推进。

---

## 附：本次 run 事实

- 集群：`silver-glacier-13`（已 kill 并清理）
- agent：junior-conductor / senior-conductor / planner / worker / validator-requirements / validator-code / completion-detector
- tokens：29,392（conductor 14,501 + planning 14,891），worker 写文件后卡死，validator 0 次迭代
- 交付物：`tetris.html`（15,839 字节）——worker 第一稿即合格，JS 语法校验通过，代码审查通过（7-bag、墙踢、消行、DPR 适配、幽灵方块均正确）
- 教训文件：空（本次失败未被学习，正是 P0-2 的实证）
