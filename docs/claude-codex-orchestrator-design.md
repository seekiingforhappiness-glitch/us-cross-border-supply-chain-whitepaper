# Claude Code × Codex 协同编排工具 —— 审核意见与最终方案

> 状态：设计定稿（v1）
> 日期：2026-07-07
> 背景：针对 Codex 提出的 "Agent Workbench / Agent Orchestrator" 方案进行审核，输出修正后的可实施方案。

---

## 一、审核结论（TL;DR）

Codex 方案的**核心定位正确**：不做聊天转发器，而是构建"任务契约 + Worktree 隔离 + 证据化验收"的协同层。这个骨架予以采纳。

但有 **三处必须修正的结构性问题**：

1. **契约没有强制执行机制（enforcement）**。`allow_paths / deny_paths` 只写进 YAML 是"纸面约束"，Codex 不一定遵守。契约必须由 runner 在执行后用 `git diff` 机器校验，越界即拒收——否则契约形同虚设。这是原方案最大的漏洞。
2. **验收证据的可信度模型有缺陷**。原方案让 Codex 自己跑验收命令、自己汇报结果，存在"自报成绩"问题（测试被改、结果被美化、命令被跳过）。验收命令必须由 orchestrator 在 worktree 里**独立重跑**，runner 捕获的退出码和日志才算证据；Codex 自跑的结果仅供参考。
3. **MCP 集成方式是重复造轮子，且优先级放错了**。原方案建议"把 Codex 包成 Claude Code 可调用的 MCP 工具"。实际上 Codex CLI 原生提供 headless 模式（`codex exec`，支持 `--json` 事件流、`--output-last-message`、`--cd`、`--sandbox`）和 MCP server 模式（`codex mcp-server`）。MVP 阶段直接用 `codex exec` 从脚本调用即可，无需自建包装层；MCP 留到第二阶段，且直接注册官方 server（`claude mcp add codex -- codex mcp-server`），不自己写。

另有两处**降级**和若干**补充**，详见第二节。

---

## 二、对 Codex 方案的逐条审核

| # | Codex 提议 | 裁定 | 说明 |
|---|-----------|------|------|
| 1 | 任务契约（YAML） | ✅ 采纳，需加固 | 骨架正确。但需补充：契约必须机器校验（见 §5.3）；scope 中需区分"Codex 可改的测试"与"验收基准测试"（后者列入 deny，防止改测试凑绿）；增加 `execution`（超时、沙箱级别、最大轮数）和 `merge`（结果回收策略）字段。 |
| 2 | Worktree 执行器 | ✅ 采纳 | 成熟做法：上下文隔离、Diff 干净、失败可整体丢弃。补充：每个 worktree 绑定独立分支；需处理依赖安装成本（MVP 串行执行回避，后续再考虑共享缓存）。 |
| 3 | Codex MCP 工具 | ⚠️ 修正 | 不自建包装。MVP 用 `codex exec` CLI 调用（脚本可控、日志可捕获、可加 `timeout`）；P2 阶段直接用官方 `codex mcp-server`。Claude Code 侧的"派发能力"用 skill/slash command 实现（`/dispatch`），成本远低于 MCP。 |
| 4 | 上下文打包器 | ⬇️ 降级 | 过度设计。Codex 本身是 agentic CLI，会自己探索仓库；"最小上下文打包"在 MVP 阶段是负资产（打包逻辑本身要维护、容易漏）。降级为"任务简报渲染"：把契约渲染成任务级 `AGENTS.md` 写入 worktree——Codex 原生自动读取该文件，比塞 prompt 更可靠。文件指针（而非文件内容）写进契约的 `context.files`。 |
| 5 | 证据收集器 | ✅ 采纳，改双层 | 字段清单合理。修正为双层：机器可读的 `result.json`（runner 生成，作为验收依据）+ 人类可读的 `review-report.md`（渲染层）。Codex 的自述（`--output-last-message`）只作为 `agent_summary` 字段收录，不作为验收依据。 |
| 6 | 互审循环（2–3 轮） | ✅ 采纳，补状态机 | 轮数上限正确。但原方案没有"失败出口"，需补终止状态机：`ACCEPTED / REJECTED（丢弃 worktree）/ ESCALATED（升级给人）`。另一个关键改进：第二轮起用 `codex exec resume` 续接同一会话，让 Codex 带着上一轮上下文修复，而不是每轮从零开始。 |
| 7 | Hooks 自动化 | ✅ 采纳，收窄 | format / lint / typecheck / test / build 合并进验收命令即可，MVP 不需要独立 hooks 框架。security scan、e2e 放 P1。 |
| 8 | 过夜任务队列 | ✅ 采纳，列前置条件 | 放最后是对的，但无人值守有硬前提：① 每任务硬超时（`timeout(1)` 包裹 `codex exec`）；② 失败策略显式声明（fail-skip vs fail-block，任务间依赖）；③ 磁盘配额（worktree 数量上限）；④ 沙箱审批策略固定为非交互（`--full-auto` + `workspace-write`），绝不用 `danger-full-access`。前置条件不满足不上线。 |
| — | MVP 三件套（task.yaml + worktree-runner + review-report） | ✅ 采纳 | 排序正确，但 runner 必须包含 scope 校验与独立验收，否则 MVP 就把最大的洞带上线了。 |

---

## 三、修正后的总体架构

```
┌─────────────────────────────────────────────────┐
│  Claude Code（主仓库）                             │
│  角色：规划、拆解、写契约、审 Diff/证据、终审        │
│  入口：/dispatch skill → 调用 workbench CLI        │
└──────────────┬──────────────────────────────────┘
               │ task.yaml
               ▼
┌─────────────────────────────────────────────────┐
│  workbench runner（确定性脚本，非模型）             │
│  1. 建 worktree + 任务分支                        │
│  2. 渲染契约 → 任务级 AGENTS.md                    │
│  3. timeout N codex exec --sandbox workspace-write│
│  4. git diff → scope 机器校验（越界即拒收）         │
│  5. 独立重跑验收命令，捕获退出码/日志                │
│  6. 产出 result.json + review-report.md           │
└──────────────┬──────────────────────────────────┘
               │ 证据
               ▼
┌─────────────────────────────────────────────────┐
│  Claude Code 审查                                 │
│  ACCEPTED → push 分支 + 开 draft PR               │
│  RETRY    → 反馈写回，codex exec resume（≤3 轮）   │
│  REJECTED → 删 worktree/分支，证据归档              │
│  ESCALATED→ 停下来等人                            │
└─────────────────────────────────────────────────┘
```

关键原则：**runner 是确定性脚本，不是模型**。所有"必须发生"的事（隔离、校验、验收、留证）都在脚本里，模型只负责"需要判断"的事（规划、写代码、审查）。

---

## 四、任务契约 v1（修正版 schema）

```yaml
# tasks/task-001-login-layout.yaml
id: task-001-login-layout
goal: 修复登录页移动端布局错位

context:
  files:                          # 文件指针，不内联内容，Codex 自己读
    - src/pages/login/LoginPage.tsx
    - src/components/auth/
  notes: |
    复现：viewport 375px 下提交按钮溢出容器。

scope:
  allow:
    - "src/pages/login/**"
    - "src/components/auth/**"
  deny:                           # deny 优先于 allow
    - "package.json"
    - "package-lock.json"
    - "tests/acceptance/**"       # 验收基准测试禁改，防止改测试凑绿

acceptance:                       # 由 runner 独立执行，Codex 自跑结果不作数
  commands:
    - npm test
    - npm run build

constraints:                      # 语义约束，进 AGENTS.md，由 Claude 审查时人工判断
  - 不改认证逻辑
  - 不引入新依赖

execution:
  sandbox: workspace-write        # 禁用 danger-full-access
  timeout_minutes: 30
  max_rounds: 3

merge:
  strategy: draft-pr              # draft-pr（默认）| manual | keep-branch
```

与原方案的差异：新增 `execution` 与 `merge`；`deny` 明确覆盖锁文件与验收基准测试；`context` 只放指针和备注。

---

## 五、执行流程与强制机制

### 5.1 状态机

```
PENDING → RUNNING → EVIDENCE → REVIEW ─┬→ ACCEPTED（push + draft PR）
                                       ├→ RETRY（round+1，resume 会话）… ≤ max_rounds
                                       ├→ REJECTED（删除 worktree，证据归档）
                                       └→ ESCALATED（等待人工决策）
超时 / 命令崩溃 → EVIDENCE（带失败记录），不静默丢失
```

### 5.2 单轮执行（runner 伪代码）

```bash
git worktree add worktrees/$TASK_ID -b agent/$TASK_ID origin/main
render_agents_md task.yaml > worktrees/$TASK_ID/AGENTS.md
timeout ${TIMEOUT}m codex exec \
  --cd worktrees/$TASK_ID \
  --sandbox workspace-write --full-auto \
  --json --output-last-message runs/$TASK_ID/round-$N/agent-summary.md \
  "$(render_prompt task.yaml $FEEDBACK)" \
  > runs/$TASK_ID/round-$N/events.jsonl
```

### 5.3 Scope 机器校验（原方案缺失的核心）

```
changed = git diff --name-only base..HEAD  +  git status --porcelain（未跟踪文件）
violation = changed 中不匹配任何 allow glob，或匹配任意 deny glob 的文件
violation 非空 → verdict = SCOPE_VIOLATION，直接进 REVIEW（默认拒收），
                 违规文件清单写入 result.json
```

### 5.4 独立验收

runner 在 worktree 内逐条执行 `acceptance.commands`，记录每条的退出码、耗时、完整日志路径。任一非零 → `verdict = FAILED`。Codex 会话中自己跑过什么，一律不计入。

---

## 六、证据格式

### result.json（机器可读，验收依据）

```json
{
  "task_id": "task-001-login-layout",
  "round": 1,
  "branch": "agent/task-001-login-layout",
  "base_commit": "abc123",
  "head_commit": "def456",
  "changed_files": ["src/pages/login/LoginPage.tsx"],
  "scope_violations": [],
  "acceptance": [
    {"cmd": "npm test", "exit_code": 0, "duration_s": 41, "log": "runs/.../npm-test.log"}
  ],
  "verdict": "PASSED | FAILED | SCOPE_VIOLATION | TIMEOUT | CRASHED",
  "agent_summary": "runs/.../agent-summary.md"
}
```

### review-report.md（人类可读，渲染层）

按序呈现：verdict → diff stat → scope 校验结果 → 验收命令表格 → Codex 自述 → 已知风险 → 建议下一步。Claude Code 审查时读 `result.json` + `git diff`，report 供人快速浏览。

---

## 七、两侧集成方式

**Claude Code 侧**：一个 `/dispatch` skill（`.claude/skills/dispatch/`），职责是：把用户意图写成 task.yaml → 调 `workbench run` → 读 result.json 和 diff → 给出 ACCEPTED/RETRY/REJECTED 判断 → RETRY 时把审查意见作为 feedback 传入下一轮。不需要 MCP。

**Codex 侧**：零改造。契约经 AGENTS.md 注入（Codex 原生读取），多轮修复用 `codex exec resume` 续接会话保留上下文。

**P2 才考虑 MCP**：届时直接 `claude mcp add codex -- codex mcp-server`，用官方实现。

---

## 八、分阶段实施计划

### Phase 0 — MVP（1–2 天）

| 交付物 | 内容 |
|--------|------|
| `tasks/*.yaml` | 契约 schema v1 + 示例 |
| `bin/workbench` | `run <task>`：worktree → AGENTS.md → codex exec（带 timeout）→ **scope 校验** → **独立验收** → result.json + review-report.md |
| `/dispatch` skill | Claude Code 派发与验收入口 |
| 目录约定 | `worktrees/`、`runs/`（git-ignored） |

验收标准：一个真实小任务全流程跑通；故意越界的任务被机器拒收；故意失败的测试导致 verdict=FAILED。

### Phase 1 — 互审循环与质量门（3–5 天）

- 状态机完整实现（RETRY 用 resume，max_rounds 熔断，ESCALATED 出口）
- ACCEPTED 自动 push 分支 + 开 draft PR
- lint / typecheck / format 并入验收命令；`runs/` 状态持久化，支持断点续跑

### Phase 2 — MCP 与过夜队列（1 周＋）

- 注册官方 `codex mcp-server`
- 串行任务队列：显式依赖声明、fail-skip/fail-block 策略、磁盘配额、结束通知
- 上线前置条件：§二 第 8 条四项全部满足

### 目录结构

```
agent-workbench/
├── bin/workbench                 # run | review | accept | reject | queue
├── tasks/                        # 任务契约
├── runs/                         # 证据（git-ignored）
│   └── task-001/round-1/{result.json, events.jsonl, agent-summary.md, logs/}
├── worktrees/                    # worktree 挂载点（git-ignored）
├── templates/{AGENTS.task.md, review-report.md}
└── .claude/skills/dispatch/SKILL.md
```

---

## 九、风险与开放问题

| 风险 | 缓解 |
|------|------|
| Codex 改验收基准测试凑绿 | 基准测试路径进 deny；diff 触及测试文件时 report 里标红 |
| 验收命令本身不可靠（flaky test） | FAILED 且 Claude 判断为 flaky 时允许一次重跑，重跑记录留痕 |
| worktree 依赖安装成本高 | MVP 串行执行；P1 评估 pnpm/共享缓存或 worktree 池复用 |
| 两个模型意见冲突死循环 | max_rounds 熔断 → ESCALATED，永远有人工出口 |
| 无人值守下的凭证/网络面 | 沙箱固定 workspace-write；过夜任务禁止需要新增凭证的操作 |

开放问题（P1 前决定即可）：① 验收命令在 worktree 内直接跑还是容器内跑（更强隔离 vs 更多配置）；② 多任务是否允许 scope 重叠（MVP：不允许，重叠即拒绝入队）。

---

## 十、一句话方案

> 采纳 Codex 的"契约 + 隔离 + 证据"骨架；把契约从纸面约束升级为 **runner 机器校验**，把验收从 Codex 自报升级为 **orchestrator 独立重跑**，把集成从自建 MCP 简化为 **`codex exec` CLI + Claude Code skill**；按 MVP → 互审循环 → MCP/过夜队列 三阶段落地。
