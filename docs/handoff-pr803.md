# Handoff: Zeroshot PR #803 — opencode reformatOutput 修复

> 日期：2026-07-29
> 状态：**代码已完成并推送到 fork，尚未通知维护者（等用户评审后再沟通）**

---

## 1. 背景

### 原始问题
Zeroshot 用 **opencode** 作为 provider 时，需要输出结构化 JSON 的 agent（如 planner）会失败：
- opencode 的 planner 花整个回合调工具（读文件探索），不输出最终的 JSON 计划块
- `extractJsonFromOutput` 提取不到 JSON → 失败
- 兜底的 `reformatOutput` 是个 **stub**（源码里写着 `STATUS: SDK NOT IMPLEMENTED`），直接抛错
- 两条路都断 → 集群进程退出 → 变 **zombie** → 任务失败

Trivial/SIMPLE 任务不走 planner，不受影响；只有复杂任务才暴露。

### PR 历史
- **2026-07-26**：我们提交了原始修复（commit `5160d72`）——实现 `reformatOutput`，用 opencode CLI 作为重格式化后端
- **2026-07-26**：Greptile bot 自动 review，提了 2 个 P1（硬编码 opencode 二进制、重试不响应取消）
- **2026-07-28**：维护者 **tomdps** 在分支上推了 3 个 commit（合并 main、scope+cancel、reuse task runtime），然后做了正式 review（`CHANGES_REQUESTED`），提了 **4 条结构性修改要求**
- **2026-07-29**：我们实现了这 4 条修改（commit `15ed4dd`），已推送

### PR 链接
- PR: https://github.com/the-open-engine/zeroshot/pull/803
- Fork: https://github.com/PaysNatal/zeroshot (branch: `fix/opencode-reformat-output`)
- Upstream: https://github.com/the-open-engine/zeroshot (base: `main`)

---

## 2. 维护者的 4 条要求及实现

### ① 创建可重入任务执行句柄（进程创建前拥有取消权）

**问题**：`spawnTaskProcess` 在进程 spawn 之后才解析 task ID 和设置 agent 全局。取消/失败窗口期内没有句柄可 kill，重试可能重叠。

**实现**：新建 `src/agent/task-execution-handle.js`
- `TaskExecutionHandle` 类，在 `spawnClaudeTask` 里 **spawn 之前**创建
- `attachProcess(proc)`：绑定进程，cancel 可立即 kill
- `assignTaskId(id)` / `assignPid(pid)`：晚到赋值；若已取消则立即 kill 晚到的进程
- `cancel(reason)`：设标志 + kill 进程（SIGTERM → 3s 后 SIGKILL）
- `settle()`：等进程退出（返回 Promise）
- 在 `spawnTaskProcess`（local）和 `spawnClaudeTaskIsolated`（isolated）两条路径都使用

**改动文件**：
- `src/agent/task-execution-handle.js`（新建，~120 行）
- `src/agent/agent-task-executor.js`：`spawnClaudeTask` 创建 handle 并传给 `spawnTaskProcess`；`spawnTaskProcess` 接受 `handle` + `nested` 参数

### ② 父/格式化器身份分离

**问题**：嵌套 reformat 调用 `agent._spawnClaudeTask()` 走完整的 `spawnClaudeTask` 路径，覆盖 `agent.currentTask/currentTaskId/processPid`，导致父任务的 `TASK_COMPLETED`、`TOKEN_USAGE`、hooks、resume、diagnostics 全部用错 ID。

**实现**：`nested` 标志贯穿整条调用链
- `parseResultOutput` 调 reformat 时传 `{ skipStructuredResultCheck: true, nested: true }`
- `spawnClaudeTask` → `spawnTaskProcess` → `createLogFollower` 都接受 `nested`
- 当 `nested: true` 时：
  - **不写** `agent.currentTask`、`agent.currentTaskId`、`agent.processPid`
  - **不发** `TASK_ID_ASSIGNED`、`PROCESS_SPAWNED` 生命周期事件
  - **不启动** liveness monitoring
  - **不注册** kill handler 到 `agent.currentTask`
- isolated 路径同理（`spawnClaudeTaskIsolated` 里的 3 处 agent 全局赋值都条件化）
- 父身份在嵌套执行期间**不可变**——不是 snapshot/restore，是根本不碰

**改动文件**：`src/agent/agent-task-executor.js`（~15 处条件化编辑）

### ③ 复用已验证的恢复对象

**问题**：`evaluateStructuredSuccess` 调 `_parseResultOutput`（可能触发 reformat 模型调用），但**不缓存**结果。`buildCompletionResult` 返回原始 `state.output`。下游 hooks 再次解析 → **第二次模型调用**，且可能产出不同结果。

**实现**：
- `evaluateStructuredSuccess`：`state._cachedParsedResult = await agent._parseResultOutput(state.output)`
- `buildCompletionResult`：返回值新增 `parsedResult: state._cachedParsedResult || null`
- 下游 hooks 和 `{{result.*}}` 替换消费 `parsedResult`，不再二次解析

**改动文件**：`src/agent/agent-task-executor.js`（`evaluateStructuredSuccess` + `buildCompletionResult`）

### ④ 取消身份端到端保留

**问题**：`REFORMAT_CANCELLED` 必须始终是取消，不能在任何路径变成 "missing required JSON block"。

**实现**：
- `parseResultOutput` 的 catch 块：`if (reformatError.code === 'REFORMAT_CANCELLED') throw reformatError;`（在维护者的 commit 里已实现）
- `reformatOutput` 在每次 attempt 前后检查 `isCancelled()`，取消后**不重试**
- 我们新增了回归测试验证：error.code 是 `REFORMAT_CANCELLED`，error.message 不含 "missing required JSON block"

**状态**：维护者的 commit 已处理核心路径；我们补了测试覆盖。

---

## 3. 分支上的 commit 历史

```
15ed4dd  fix(opencode): task-execution lifecycle for output recovery   ← 我们的（4 条修改 + 9 个测试）
d431a04  fix(opencode): reuse task runtime for output recovery         ← 维护者 tomdps 的
d4235fb  fix(opencode): scope and cancel output reformatting           ← 维护者 tomdps 的
76058b6  Merge branch 'main' into review-pr803                         ← 维护者合并 main
5160d72  fix(opencode): implement reformatOutput so structured-output agents recover  ← 我们的原始修复
```

**注意**：分支基于较旧的 main。upstream/main 已推进（#811 reuse provider sessions、#816 pass --dir to opencode、#823 fail closed on Keychain 等）。合入前可能需要 rebase。

---

## 4. 文件变更清单（vs upstream/main）

| 文件 | 变更 | 说明 |
|------|------|------|
| `src/agent/task-execution-handle.js` | **新建** | 可重入任务执行句柄 |
| `src/agent/agent-task-executor.js` | 修改 | handle 集成、nested 标志、cached parsedResult |
| `src/agent/output-reformatter.js` | 修改 | 注入式 runReformat + isCancelled（维护者改的） |
| `src/agent-wrapper.js` | 修改 | 移除 providerSession 字段（维护者改的） |
| `tests/task-execution-lifecycle.test.js` | **新建** | 9 个回归测试 |
| `tests/output-reformatter.test.js` | 修改 | 断言更新（options 多了 nested:true） |
| `tests/opencode-nested-model-provenance.test.js` | 修改 | 断言更新（同上） |

---

## 5. 测试状态

**32 passing, 0 failing**（23 已有 + 9 新增）

新增测试（`tests/task-execution-lifecycle.test.js`）：
- ✅ pre-ID 取消（handle 创建后、task ID 到达前取消）
- ✅ 晚到进程 kill（已取消的 handle 绑定进程时立即 kill）
- ✅ taskId/pid 不可变赋值
- ✅ settle 等进程退出
- ✅ 嵌套 reformat 后父 currentTaskId/processPid/currentTask 不变
- ✅ buildCompletionResult 返回 cached parsedResult，_parseResultOutput 只调一次
- ✅ REFORMAT_CANCELLED 穿过 parseResultOutput 不变 missing-JSON
- ✅ reformatOutput 取消后不重试

---

## 6. 本地文件位置

| 内容 | 路径 |
|------|------|
| **fork clone（工作目录）** | `/tmp/zeroshot-fork/`（注意：/tmp 重启后会丢） |
| **我们的 loop 项目** | `~/Documents/opencode-durable-loop/`（GitHub: PaysNatal/opencode-durable-loop） |
| **zeroshot 全局安装（已打补丁）** | `/usr/local/lib/node_modules/@the-open-engine/zeroshot/` |
| **zeroshot 补丁备份** | `<zeroshot>/src/agent/output-reformatter.js.bak` |
| **opencode 会话数据库** | `~/.local/share/opencode/opencode.db` |
| **memorix 记忆库** | `~/memory-vault/`（git 仓库） |

**⚠️ `/tmp/zeroshot-fork/` 重启后会丢。** 如需继续工作，重新 clone：
```bash
git clone https://github.com/PaysNatal/zeroshot.git
cd zeroshot
git checkout fix/opencode-reformat-output
git remote add upstream https://github.com/the-open-engine/zeroshot.git
```

---

## 7. 待办 / 下一步

### 立即
- [ ] **用户评审代码**（特别是 `agent-task-executor.js` 的 ~15 处条件化编辑和 `task-execution-handle.js`）
- [ ] 评审通过后，**在 PR 上回复维护者**（告知 4 条修改已完成，邀请 re-review）

### 维护者可能要求的后续
- [ ] **Rebase 到最新 upstream/main**（upstream 已推进 #811 #816 #823 等）
- [ ] 维护者可能要求 `TaskExecutionHandle` 也用于 isolated 路径的 `buildIsolatedLifecycleHandle`（目前 isolated 的 `agent.currentTask` 条件化了，但没用 handle 类）
- [ ] 可能要求 `evaluateStructuredSuccess` 在 `state._cachedParsedResult` 已存在时**跳过**重新解析（当前每次都会调 `_parseResultOutput` 并覆盖缓存）
- [ ] 可能要求更完整的 isolated 路径取消测试

### 合入后
- [ ] `npm update @the-open-engine/zeroshot` 拉取包含修复的版本
- [ ] 验证本地补丁不再需要（`apply-zeroshot-patch.sh` 应报 "already applied"）
- [ ] 更新 `opencode-durable-loop/README.md` 里的补丁说明

---

## 8. 关键技术细节（供评审参考）

### reformatOutput 的两个坑（原始修复发现的）
1. **重格式化提示必须禁用工具**：opencode 看到输出里提到的文件名会去调工具读文件，导致 >120s 挂起。加了 "Do NOT use any tools" 指令后 ~15s 完成。
2. **必须用 spawn + stdin ignored**：`execFile` 给子进程的 stdin 是保持打开的 pipe，opencode 会等 stdin EOF 而无限挂起。用 `spawn` 设 `stdio: ['ignore','pipe','pipe']` 解决。

### 维护者的架构改进（d4235fb + d431a04）
- `reformatOutput` 不再自己 spawn 进程，改为接受 `runReformat` 回调（由 executor 注入）
- `isCancelled` 回调让 reformat 参与 agent 取消
- 重格式化只限 opencode provider（其他 provider 不走这条路）

### nested 标志的传播链
```
parseResultOutput
  → agent._spawnClaudeTask(prompt, { skipStructuredResultCheck: true, nested: true })
    → spawnClaudeTask(agent, context, options)        // 创建 handle, nested = options.nested
      → spawnTaskProcess({ ..., handle, nested })     // 条件化 agent.currentTaskId
      → followClaudeTaskLogs(agent, taskId, options)  // 传 nested
        → createLogFollower({ ..., nested })          // 条件化 agent.currentTask
```

### buildCompletionResult 的新返回结构
```js
{
  success: boolean,
  output: string,          // 原始输出（不变）
  parsedResult: object|null, // 新增：已验证的解析结果（来自缓存）
  error: string|null,
  tokenUsage: object,
}
```
