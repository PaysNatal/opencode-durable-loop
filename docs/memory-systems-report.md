# 记忆系统分析与改进报告：memorix vs agent-knowledge

> 日期：2026-07-25
> 触发：评估两套记忆系统是否完全重叠，决定取舍，并为 loop 模式的"教训记忆"选型。

---

## 一、现状盘点（实测）

| 维度 | **agent-knowledge** (compiled-memory-mcp) | **memorix** |
|---|---|---|
| 当前数据 | **全空**：sources 0 / entities 0 / concepts 0 / syntheses 0 / reports 0 | 未知（见下方"关键问题"，当前目录无法查询） |
| 是否在用 | **否**（空的，从未摄取） | 是（用户已用大模型接入） |
| 数据模型 | 摄取式知识库：source → 自动抽 **claim/entity** → 检索 | 经验式记忆：类型化 **observation**（gotcha/decision/problem-solution/trade-off…） |
| 独有能力 | UMSF 事件钩子实时捕获、`ak_dream` 巩固循环、自动 claim/entity 抽取、source 管理 | **code graph**、mini-skills、project context（任务透镜 brief）、sessions、orchestration、reasoning |
| 检索方式 | `ak_query`（语义检索 claims） | `memorix_search`（按类型/状态/时间，支持 project/global scope） |
| 作用域 | 独立 vault | **git 项目作用域**（绑定 git 仓库） |

### ⚠️ 关键问题：memorix 在当前环境被禁用
实测 `memorix_search` / `memorix_codegraph_status` 均返回：
> "No git project could be resolved from `/Users/locodocoww/Documents/opencode`. project-scoped tools are disabled until a git-backed project is detected."

- memorix **必须有 git 仓库**才能工作；当前工作目录不是 git 仓库 → memorix 在这里**完全不可用**。
- 本环境也**没有 `memorix_session_start` 工具**可用来重新绑定项目。
- **这直接影响"只保留 memorix"的可行性**：如果 loop 继续在非 git 目录跑，memorix 根本接不上。

---

## 二、重叠度判定：**非完全重叠**

- **目的层面重叠**：两者都是"为 agent 存储并可检索的知识"。
- **机制层面不同**：
  - **agent-knowledge = 摄取式知识库**：你喂它文档/source，它自动抽 claim/entity。适合**外部知识/文档沉淀**。
  - **memorix = 经验式工作记忆**：agent 主动存"我踩了个坑/做了个决策"，绑定项目与代码图。适合**经验/教训/决策**。
- **对 loop 的"教训记忆"需求**：memorix 的类型化 observation（`gotcha` / `problem-solution`）**天然契合 Reflexion 式经验记忆**；agent-knowledge 的自动抽取更适合做文档库，不是为"失败教训"设计的。

**结论：不是完全重叠**（机制与定位不同），因此按你的要求出本报告，而非直接删除。

---

## 三、推荐方案

### 主记忆：保留 **memorix**（符合你的偏好 + 在用 + 类型契合 loop 教训）
但**必须先解决 git 作用域问题**，否则 memorix 在 loop 里接不上。三个方案：

| 方案 | 做法 | 评价 |
|---|---|---|
| **A（推荐）** | 建一个专门的 git 仓库当"记忆库"（如 `~/memory-vault`，`git init`），loop 固定绑定它 | 干净、可控、loop 教训集中管理 |
| B | loop 固定在某 git 项目里跑 | 最简单，但教训散落在各项目 |
| C | 用 memorix 的 `scope=global` | search 支持 global，但 **store 仍需项目绑定**，需进一步验证 |

### agent-knowledge：**暂禁用而非删除**（`enabled: false`）
- 当前为空，移除零数据损失。
- 但它的"自动 claim 抽取 + 钩子实时捕获 + dream 巩固"是 memorix 没有的独有能力——将来若要做**文档知识库**可再启用。
- 建议先在 `opencode.jsonc` 里 `enabled: false`，保留选项，观察一段时间再决定是否彻底删。

### zs-learn → memorix 集成（在方案 A 落地后）
- `extract_and_store_lesson` 改为调用 `memorix_store`（`type=gotcha`/`problem-solution`，带上 `filesModified`/`concepts`）。
- 教训注入（`build_lesson_context`）改为 `memorix_search`（按 type + status=active 检索）。
- 这样教训就进了你已接入大模型的 memorix，质量和可检索性都上一个台阶。

---

## 四、测试结论与步骤

**本环境无法测试 memorix**（非 git 目录 + 无 session_start 工具）。需在 git 工作区验证。建议测试步骤：

```bash
# 1. 建记忆库
mkdir -p ~/memory-vault && cd ~/memory-vault && git init

# 2. 在 opencode 里把工作区切到 ~/memory-vault（或 memorix_session_start 绑定它）
# 3. 测试存/取：
#    memorix_store 一条 gotcha → memorix_search 能检索到 → memorix_detail 能读全
# 4. 确认 codegraph_status 不再报 "No git project"
```

---

## 五、行动清单（优先级）

1. **P0**：建 `~/memory-vault` git 仓库，解决 memorix 作用域问题（否则一切免谈）。
2. **P0**：在 git 工作区测试 memorix 存/取正常。
3. **P1**：`opencode.jsonc` 里把 agent-knowledge 设 `enabled: false`（暂禁，不删）。
4. **P1**：zs-learn 的教训存取改用 memorix（依赖 P0 落地）。
5. **P2**：观察一段时间后决定是否彻底移除 agent-knowledge。
