# opencode-durable-loop

把 [Zeroshot](https://www.npmjs.com/package/@the-open-engine/zeroshot) 的多 agent 执行循环
（executor → verifier → 失败重试 → 教训累积）包进 [Temporal](https://temporal.io) 持久化执行，
让**超长程、无人值守**的编码任务获得：崩溃可恢复、原生心跳看门狗、错误分类重试、跨项目失败学习。
默认 provider 为 **opencode**（附带一个让 opencode 能正常输出结构化 JSON 的关键补丁）。

---

## 为什么需要它

直接用 `zeroshot run` 跑超长程任务有几个痛点，本项目逐一解决：

| 痛点 | 本项目的解法 |
|---|---|
| 进程/终端一关，任务状态全丢 | Temporal 持久化 workflow，崩溃/重启后自动恢复 |
| agent 卡死（零进展）无人知晓，集群永远 `running` | activity **心跳 + heartbeat_timeout**，卡死自动 kill 并记教训 |
| 认证/权限等永久错误被无脑重试 3 次 | **错误分类**：infra 错误（auth/permission/missing）快速失败，不浪费重试 |
| 失败教训只存本地、换个项目就丢 | 教训写进 **memorix 集中记忆库**，跨项目共享、按任务相关性注入 |
| **opencode 的 planner 输出不了结构化 JSON → 集群变 zombie** | 附带 **zeroshot 补丁**，实现 `reformatOutput` 兜底（详见下文）|

---

## 架构

```
                        dloop "task" --detach            ← 入口（bash 包装）
                              │
              确保 Temporal server + worker 在跑
                              │
        ┌─────────────────────▼─────────────────────┐
        │            Temporal Server                  │   ← 持久化执行引擎
        │   (localhost:7233, sqlite, namespace=loop)  │     崩溃可恢复
        └─────────────────────┬─────────────────────┘
                              │
        ┌─────────────────────▼─────────────────────┐
        │   loop_worker.py  →  LoopWorkflow           │   ← Temporal workflow
        │                                             │
        │   循环 max_iterations 次：                    │
        │     ① inject_lessons    从 memorix 注入教训   │
        │     ② run_cluster       跑 zeroshot 集群      │
        │        (心跳 + 卡死检测 + 错误分类重试)         │
        │     ③ analyze_and_capture 验证门 + 记教训      │
        │     → 成功返回 / infra 错误停 / 失败带教训重试   │
        └─────────────────────┬─────────────────────┘
                              │
        ┌─────────────────────▼─────────────────────┐
        │   Zeroshot 集群（多 agent 执行器）            │   ← 实际干活
        │   conductor → planner → worker → validator  │
        │   （provider: opencode，已打 reformat 补丁）   │
        └─────────────────────┬─────────────────────┘
                              │
        ┌─────────────────────▼─────────────────────┐
        │   memorix 记忆库 (~/memory-vault, git)       │   ← 跨项目失败学习
        └───────────────────────────────────────────┘
```

### 组件
- **`dloop`** — 入口包装。自动拉起 Temporal server + worker，启动 workflow。
- **`loop/`** — Temporal 持久化 loop 核心（Python）。
  - `loop_workflow.py` — `LoopWorkflow` + 3 个 activity（注入/跑集群/分析记教训）。
  - `loop_worker.py` — Temporal worker（轮询 `loop-queue`）。
  - `loop_start.py` — client（启动 workflow，支持 `--detach`）。
  - `models.py` / `zeroshot_lib.py` / `memorix_lib.py` / `lessons_lib.py` / `classify.py` — 数据模型与封装。
- **`zs-learn`** — 早期的 bash 版失败学习包装（轻量备选，无 Temporal 持久化）。
- **`patches/`** — zeroshot 的 opencode 修复补丁。
- **`config/loop-agent.txt`** — opencode 里 `loop` agent 的提示词。

---

## 前置依赖

- [Temporal CLI](https://docs.temporal.io/cli)（`brew install temporal`）— 提供本地 dev server
- [Zeroshot](https://www.npmjs.com/package/@the-open-engine/zeroshot)（`npm i -g @the-open-engine/zeroshot`）
- [opencode](https://opencode.ai)（默认 provider，需可用）
- Python 3.10+（`temporalio` SDK）
- `jq`
- [memorix](https://www.npmjs.com/package/memorix)（可选，跨项目教训记忆；没有则降级到本地 `lessons.jsonl`）

---

## 安装

```bash
git clone <this-repo> opencode-durable-loop
cd opencode-durable-loop
./scripts/install.sh
```

`install.sh` 会：检查依赖 → 装 `temporalio` → **打 zeroshot 补丁** → 把 `dloop`/`zs-learn` 链接到 `~/.local/bin` → 建 memorix 记忆库 `~/memory-vault`。

确保 `~/.local/bin` 在 `PATH` 里。

---

## 使用

### 跑一个超长程任务（推荐）
```bash
dloop "把登录模块重构为支持 OAuth，加上测试" --detach
```
- `--detach`：发后即走。workflow 在 Temporal 里**持久运行**，关掉终端也不影响。
- `--` 之后的参数原样传给 zeroshot，例如：
  ```bash
  dloop "修复支付 webhook 的 bug" --detach -- --worktree --provider opencode
  ```

### 监控 / 取结果（任意终端、任意时刻）
```bash
temporal workflow list --namespace loop
temporal workflow describe --workflow-id <id> --namespace loop
temporal workflow result  --workflow-id <id> --namespace loop   # 阻塞等结果
```

### dloop 选项（`--` 之前）
| 选项 | 说明 | 默认 |
|---|---|---|
| `--project-dir DIR` | 项目目录（zeroshot 的 cwd + 本地教训） | `$PWD` |
| `--max-iterations N` | executor-verifier 最大迭代次数 | 3 |
| `--run-timeout-min N` | 单次集群运行超时（分钟） | 30 |
| `--heartbeat-timeout-sec N` | 心跳超时（超过无心跳判卡死） | 90 |
| `--memory-vault DIR` | memorix 记忆库 | `~/memory-vault` |
| `--detach` | 发后即走（持久后台运行） | 关（前台等结果） |

### 轻量备选（无 Temporal）
```bash
zs-learn run "task" --worktree --provider opencode      # 前台
zs-learn loop                                            # 交互式 REPL
```

---

## ⚠️ 关键：zeroshot 的 opencode 补丁

### 问题
zeroshot 的 planner agent 需要输出一个结构化 JSON 计划块。但 **opencode 作为 provider 时，planner 会花整个回合调工具（读文件探索），始终不输出最终的 JSON 文本**。zeroshot 提取不到 JSON，集群进程异常退出 → 变成 `zombie`，任务失败。

zeroshot 本来设计了一个兜底机制 `reformatOutput`（用一个 LLM 把非 JSON 的输出转成 schema 要求的 JSON），但它**只是个 stub**——源码里直接写着 `STATUS: SDK NOT IMPLEMENTED`，调用就抛 `"SDK not implemented for provider opencode"`。于是兜底也失效。

> 注意：trivial 任务（如简单问答）**不走 planner**，所以不受影响；只有复杂任务（需要 planner）才暴露这个 bug。

### 修复（`patches/zeroshot-opencode-reformat.patch`）
实现 `reformatOutput`，用 **opencode CLI 作为重格式化后端**。两个关键坑：

1. **重格式化提示必须禁用工具**——否则 opencode 看到输出里提到的文件名，又去调工具读文件，导致重格式化卡死（实测：加禁用工具指令后 ~15s 完成；不加则 >120s 挂起）。
2. **必须用 `spawn` 并忽略 stdin**——`execFile` 给子进程的 stdin 是保持打开的 pipe，opencode 会等 stdin EOF 而无限挂起；改用 `spawn` 设 `stdio:['ignore','pipe','pipe']` 解决。

补丁修改 `src/agent/output-reformatter.js`，`install.sh` 会自动应用（幂等，已打则跳过）。

### ⚠️ 升级会被覆盖
补丁打在全局 npm 包上。**`npm update @the-open-engine/zeroshot` 会覆盖它**。
此修复已提交 upstream PR：[the-open-engine/zeroshot#803](https://github.com/the-open-engine/zeroshot/pull/803)。
- **合并后**：`npm update` 即原生包含修复，无需再打补丁。
- **合并前**：升级后需重跑 `./scripts/apply-zeroshot-patch.sh`。
（原始文件备份在 `<zeroshot>/src/agent/output-reformatter.js.bak`。）

---

## 配置（环境变量）

| 变量 | 说明 | 默认 |
|---|---|---|
| `DLOOP_LOOP_DIR` | loop 代码目录 | `<repo>/loop` |
| `DLOOP_TEMPORAL_ADDR` | Temporal 地址 | `localhost:7233` |
| `DLOOP_NAMESPACE` | Temporal 命名空间 | `loop` |
| `DLOOP_DB_DIR` | Temporal dev server sqlite 目录 | `~/.temporal-dev` |
| `DLOOP_MEMORY_VAULT` | memorix 记忆库 | `~/memory-vault` |
| `DLOOP_BIN_DIR` | 可执行文件链接目录 | `~/.local/bin` |

---

## 失败学习怎么工作

1. **注入**：每次跑任务前，用**当前任务**去 memorix 检索相关教训（`entity=loop`），注入到提示词开头。
2. **捕获**：任务失败/卡死时，把教训存进 memorix（`gotcha`/`problem-solution` 类型）。
3. **解决**：任务成功且过验证门时，自动 resolve 相关教训（不再注入）。
4. **跨项目**：因为统一写进 `~/memory-vault`，项目 A 的教训会自动帮到项目 B。
5. **降级**：若 memorix 不可用（非 git 仓库等），自动降级到项目本地 `.zeroshot/lessons.jsonl`。

> 新项目用 loop 会**自动**走 memorix——loop 通过 `cwd=~/memory-vault` 定位记忆库，与任务项目本身是否为 git 仓库无关。

---

## 项目结构

```
opencode-durable-loop/
├── README.md
├── requirements.txt              # temporalio
├── dloop                         # 入口包装（自动起 server+worker）
├── zs-learn                      # bash 版失败学习包装（轻量备选）
├── loop/                         # Temporal 持久化 loop 核心
│   ├── loop_workflow.py          # LoopWorkflow + 3 个 activity
│   ├── loop_worker.py            # Temporal worker
│   ├── loop_start.py             # client（启动 workflow）
│   ├── models.py                 # 数据模型
│   ├── zeroshot_lib.py           # zeroshot CLI 异步封装
│   ├── memorix_lib.py            # memorix 集成
│   ├── lessons_lib.py            # lessons.jsonl 降级存储
│   └── classify.py               # 错误分类（task-logic / infra）
├── patches/
│   └── zeroshot-opencode-reformat.patch   # opencode 结构化输出修复
├── config/
│   └── loop-agent.txt            # opencode loop agent 提示词
├── scripts/
│   ├── install.sh                # 一键安装
│   └── apply-zeroshot-patch.sh   # 应用 zeroshot 补丁
├── tests/
│   └── test_memorix_lib.py       # memorix 往返测试
└── docs/
    ├── loop-mode-issues.md       # 问题清单（诊断记录）
    ├── loop-mode-fixes.md        # 修复方案（成熟方案对照）
    └── memory-systems-report.md  # 记忆系统分析
```

---

## 排错 / FAQ

**Q: workflow 一直 `Started`/`?`，是不是卡了？**
先用 `temporal workflow describe --workflow-id <id> --namespace loop` 看真实 `Status`
（`Running`/`Completed`/`Failed`）。注意字段是 **`Status`** 不是 `State`。

**Q: 集群变 `zombie`，失败信息 "missing required JSON block"？**
说明 zeroshot 补丁没生效（或被升级覆盖）。重跑 `./scripts/apply-zeroshot-patch.sh`。

**Q: 集群变 `zombie`，失败信息 "Not logged in"？**
对应 provider 没登录/没配置。检查 `zeroshot providers`。

**Q: memorix 教训没生效？**
确认 `~/memory-vault` 是 git 仓库（memorix 要求 git 作用域）。否则会自动降级到本地 `lessons.jsonl`。

**Q: 想看某个 workflow 的完整执行历史？**
`temporal workflow show --workflow-id <id> --namespace loop`。

---

## 状态与边界

- Temporal 用的是 **dev server**（单节点、sqlite）。生产/高可用请用 `brew services start temporal` 或 Temporal Cloud。
- worker 以 nohup 后台进程运行；生产建议用 launchd/systemd 服务。
- 心跳重连复用集群（避免重试时重复启动）已实现，但 worker 活动中崩溃的场景难以构造测试。
- memorix 当前用 BM25 全文检索（embedding 未开）；想要更好语义匹配可设 `MEMORIX_EMBEDDING=api`。
