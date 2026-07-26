# Loop 模式修复方案 —— 成熟方案与思想成果对照

> 配套文档：`loop-mode-issues.md`（问题清单）
> 日期：2026-07-25
> 结论：你手搓的这套（zeroshot 编排 + zs-learn 学习层）遇到的每个问题，工业界/学术界都有标准答案。主要集中在两大思想体系：
>   - **Durable Execution（持久化执行，Temporal 为代表）** → 治"卡死 + 进程一死状态全丢"
>   - **从失败学习的 agent 研究（Reflexion / ExpeL / SAMULE）+ 评估者-优化者模式（Anthropic）** → 治"验证被跳过 + 学不会失败"

---

## 一、问题 → 成熟方案 → 具体修法（对照表）

### 问题 1：Worker 卡死、无看门狗（issues P0-1）
**成熟方案：Durable Execution 的心跳机制（Temporal Activity Heartbeats）**
- Temporal 的核心设计：activity 必须周期性发 **heartbeat**；设一个 **Heartbeat Timeout**，心跳一停就判定 activity 失败，按重试策略重试。
- 官方原话（精准命中你的问题）：
  > "Without heartbeat_timeout, a crashed worker holds the activity task **invisibly** until schedule_to_start_timeout expires — delaying recovery by **minutes instead of seconds**."
  > （没有心跳超时，崩溃的 worker 会**隐形地**占着任务，直到 schedule-to-start 超时——把恢复从几秒拖到几分钟。）
- Temporal 的**分层超时模型**（值得照搬）：
  - `Schedule-To-Start`：在队列里等待被领取的超时
  - `Start-To-Close`：开始执行到完成的超时
  - `Schedule-To-Close`：端到端总超时
  - `Heartbeat Timeout`：活性检测（多久没心跳就算死）
- Kubernetes 的 **liveness probe** 是同一思想。
- **对你的具体修法**：
  - 把"token 增长 / 输出 / 文件 mtime 变化"当作**隐式心跳**。
  - 加一个 watchdog：N 分钟无任何进展信号 → 标记 stale → kill+重试 或 升级告警。
  - 你的 `staleWarningsBeforeKill: 2` 是这个机制的雏形，但没覆盖"executing_task 但零进展"——补上这一档。

### 问题 2：进程一死，状态/教训全丢（issues P0-2）
**成熟方案：Durable Execution + 事件溯源（Temporal / Restate / Inngest）**
- Temporal 把 workflow 状态持久化到**持久事件日志**；官方原话：
  > "Even if we crash here, Temporal knows [the state] and will **resume** the workflow with it."
  > （即使在这里崩溃，Temporal 也记得状态，并据此**恢复** workflow。）
  - 崩溃 → **恢复**，而不是从零重来。
- **事件溯源（Event Sourcing）**：不可变事件日志作为唯一事实来源，重放即可重建状态（Temporal 的 workflow history 本质就是 event log）。
- **对你的具体修法**：
  - **把"教训捕获"从前台等待进程里解耦出来**，做成持久副作用：任务一启动就**预写（write-ahead）**一条 `{cluster_id, task, status: pending}` 到持久存储。这样即使 waiter 死了，harvest 也能找到它。
  - **kill 后保留集群墓碑**（不要立刻从 list 消失），让 harvest 读得到。
  - 集群的 sqlite WAL 已经是某种持久状态——但 zs-learn 的"等待+提取"逻辑不持久。把这段逻辑挪进一个**持久后台 worker**，或让 **harvest 成为持久 reconciler**（对账器）。
  - 思想：不要相信"前台进程会活着走到结尾"，一切重要副作用都要有持久化的对账兜底。

### 问题 3：验证者被静默跳过（issues P1）
**成熟方案：评估者-优化者模式（Anthropic《Building Effective Agents》）+ Self-Refine + LLM-as-judge**
- **Anthropic 的 evaluator-optimizer workflow**：一个 LLM 生成输出，**另一个独立的 evaluator LLM 批判它**，循环直到 evaluator 通过。这是一道**显式、强制**的关卡。
  - 参考：anthropic.com/engineering/building-effective-agents；buildingeffectiveagents.com/patterns/evaluator-optimizer/
- **Self-Refine**（Madaan et al., NeurIPS 2023）：迭代式自我反馈 + 改进。
- **Reflexion 的 evaluator**：在编码任务里用**单元测试作为评估者**——"环境提供二元反馈"（可执行的真值，不是 LLM 的主观意见）。
- **对你的具体修法**：
  - loop 必须有**强制质量门**：集群**不允许**进入"成功"终态，除非验证者**真的跑过并通过**。"validator 迭代 0 次"绝不能算成功。（类比 Temporal：activity 的结果必须被真正取回。）
  - 对代码任务，验证者应**真的运行代码/测试**（可执行验证），而不是只听 LLM 说"我觉得对了"。本次 tetris.html 是人工验收的，loop 自己没验。

### 问题 4：重试对错误类型无感知（issues K4）
**成熟方案：带错误分类的重试策略（Temporal RetryPolicy / AWS Step Functions）**
- Temporal：`RetryPolicy(maximum_attempts=N, non_retryable_error_types=["InvalidCardError"])`——把错误分成**可重试**（瞬时：网络、超时）vs **不可重试**（永久：认证失败、非法输入）。不可重试 → **快速失败**，不浪费重试。
- AWS Step Functions：`Retriable` vs `NonRetriable` 错误，`Catch` / `Retry` 字段。
- **对你的具体修法**：
  - 在编排层做错误分类。"Not logged in" / 认证错误 → **不可重试** → 快速失败 + 告警（而不是重试 3 次）。网络抖动 → 带退避重试。
  - 你的 zs-learn `classify_failure` 是起点，但它只在**事后**用于记教训，没用于**重试决策**。把分类前移到重试逻辑。

### 问题 5：从失败学习——教训系统本身（issues K8 + zs-learn 的整个立意）
**成熟方案：Reflexion + ExpeL + SAMULE**
- **Reflexion**（Shinn et al., NeurIPS 2023，arXiv:2303.11366）——"从失败学习"的奠基之作：
  > "reinforce language agents **not by updating weights, but instead through linguistic feedback**"
  - agent 反思一条失败轨迹 → 产出一段**语言化的自我反思（verbal self-reflection）** → 存入**情景记忆（episodic memory）** → 下一次尝试以这些反思为条件。
  - **这正是 zs-learn 的"lessons"想做的事**——但 Reflexion 用 **LLM 生成反思**（丰富、有因果），而不是关键词启发式。
- **ExpeL**（Zhao et al., AAAI 2024，《ExpeL: LLM Agents Are Experiential Learners》）：更进一步——跨**大量轨迹（成功+失败）**提炼**可复用的 insight/规则**，累积成增长的经验库。
  - 关键差异：ExpeL **从成功里也学**（"什么管用"），zs-learn 只从失败学。
- **SAMULE**（Ge et al., EMNLP 2025）：多层级反思合成，专门针对"**错误分析不充分**"——即更擅长搞清楚**为什么**失败。直接改进 zs-learn 脆弱的关键词分类。
- **对你的具体修法**：
  - 用 **LLM 生成的反思（Reflexion 式）**替换关键词启发式的 `classify_failure`/教训提取：把失败转录喂给 LLM，问"哪里错了、为什么、下次该怎么做"→ 存为教训。质量远超 grep 关键词。
  - **从成功里也学**（ExpeL），不只从失败学。

### 问题 6：无 remediation / 单点故障（issues K5, K3）
**成熟方案：熔断器 + 降级/故障转移（resilience 模式）**
- **熔断器（Circuit Breaker**，resilience4j / Hystrix）：别一直锤一个已知挂掉的依赖；跳闸、降级。
- **Provider 故障转移链**：opencode 挂了 → 退回 pi → ……
- **对你的具体修法**：错误分类（问题 4）喂给 remediation 策略：provider X 认证错误 → 尝试重认证 或 切到 provider Y；反复失败 → 熔断 + 给人类发**可操作**告警。

### 问题 7：整体架构（aspirational）
**成熟方案：LangGraph（持久化 agent 编排）**
- LangGraph：基于图的 agent 编排，带**循环（cycles）**、**checkpointing/持久化**（治崩溃恢复）、**human-in-the-loop**（中断/恢复）、**time travel**。本质上是"给 agent 用的 durable execution"。
- Anthropic 的 **workflows vs agents** 之分：workflow = 预定义路径（像你的 conductor-bootstrap），agent = LLM 动态控制流程。你的系统是 workflow。
- **对你的具体修法（如果要重构）**：把 loop 建在 durable agent 框架（LangGraph）或 durable execution 引擎（Temporal 包住 agent 调用）上，心跳、持久化、重试、checkpoint 全都白送。

---

## 二、关键参考文献（按主题）

**Durable Execution（治卡死 + 治状态丢失）**
- Temporal 官方文档：Activity Execution / Detecting Activity Failures / Retry Policy
  - docs.temporal.io/activity-execution
  - docs.temporal.io/encyclopedia/detecting-activity-failures
- 概念文：《Durable Execution: Why Temporal and Workflow Engines Are Replacing Queues and Cron Jobs》

**从失败学习（治教训系统）**
- **Reflexion: Language Agents with Verbal Reinforcement Learning** — Shinn et al., NeurIPS 2023, arXiv:2303.11366（奠基）
- **ExpeL: LLM Agents Are Experiential Learners** — Zhao et al., AAAI 2024（跨轨迹提炼可复用 insight，从成功+失败学）
- **SAMULE: Self-Learning Agents Enhanced by Multi-level Reflection** — Ge et al., EMNLP 2025（多层级反思，改进错误分析）
- **Self-Refine: Iterative Refinement with Self-Feedback** — Madaan et al., NeurIPS 2023

**验证 / 评估者模式（治验证被跳过）**
- **Building Effective AI Agents** — Anthropic（evaluator-optimizer workflow）
  - anthropic.com/engineering/building-effective-agents
  - buildingeffectiveagents.com/patterns/evaluator-optimizer/
- claude-cookbooks/patterns/agents（Anthropic 参考实现）

**编排框架（整体架构参考）**
- LangGraph — langchain.com/langgraph；docs.langchain.com（workflows-agents、checkpointing/persistence）

**一个重要反直觉发现（设计验证器时必读）**
- 自我反思类方法有局限：纯靠 LLM 自我反思（无外部真值信号）可能无法真正纠错，甚至越改越差。
  - 相关：《Encouraging Divergent Thinking in LLMs through Multi-Agent Debate》(EMNLP 2024) 指出 reflection-style 方法的缺陷；
  - 《Large Language Models Cannot Self-Correct Reasoning Yet》(Huang et al., ICLR 2024) 论证：**没有外部/ground-truth 反馈时，自我纠正不可靠**。
  - **启示**：验证器应尽量用**可执行的外部信号**（跑测试、类型检查、真的运行代码），而不是只听 LLM 自评。Reflexion 自己也是用环境反馈（测试结果）而非纯自省。

---

## 三、给你的优先级建议（改进现有系统，不重写）

按"投入产出比"排序：

1. **心跳/看门狗**（治 P0-1，你实际踩到的 bug）：基于进展信号的活性检测，最高价值。让 `staleWarningsBeforeKill` 真正覆盖"executing 但零进展"。
2. **持久化教训捕获（reconciler 模式）**（治 P0-2）：预写 pending-run 记录 + harvest 作持久对账器 + kill 后留墓碑。把"学习"和"前台进程存活"解耦。
3. **LLM 生成反思替换关键词启发式**（Reflexion 式，治教训质量）：教训质量数量级提升。
4. **强制验证门**（治 P1）：验证者没跑就不许判成功；代码任务要真跑代码/测试。
5. **错误分类驱动重试**（治 K4）：可重试 vs 不可重试，认证错误快速失败。
6. **熔断 + provider 故障转移**（治 K5/K3）。

**如果是从零开始**：用 Temporal（持久化）+ 评估者-优化者 agent 循环 + Reflexion 式记忆；或直接用 LangGraph（打包了上述大部分能力）。

---

## 四、一句话总结

你的 loop 缺的不是创意，而是两块成熟基础设施：
- **可靠性**上，缺 durable execution 的"心跳 + 持久状态 + 错误分类重试"（Temporal 那一套）；
- **质量/学习**上，缺"强制评估门 + LLM 反思式学习"（Anthropic evaluator-optimizer + Reflexion/ExpeL 那一套）。
把这两块补上，loop 模式就从"能跑的 demo"变成"能托付超长程任务的系统"。
