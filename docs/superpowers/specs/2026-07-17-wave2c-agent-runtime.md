# 波2-2c as-built：持久 Agent runtime 状态机

日期：2026-07-17 ｜ 依据：V18 第二批授权（Daniel"你来决定"）+ 波2 Command 总线契约
（`2026-07-16-wave2-command-bus.md`）+ Monday 深研运行时吸收（`docs/research/2026-07-16-monday-deep-research-absorb.md`
【二】4 写后复读、3.4 kill switch）。代码：`agent/runtime.py`；回归：`agent/test_runtime.py`（60 项全绿）。

## 一句话（白话）

给 AI 一个"能干长活"的驾驶座：它按剧本一步步处置风险，走到**要人拍板**的地方就停下等审批；
人批完 `--resume` 接着走，并**读回数据库核实副作用真的发生**（写后复读）；中途崩溃可断点续跑而
**绝不重复派单**（写步骤凭幂等键"恰一次"）；有硬预算和防打转护栏；人随时 `--kill` 急停留痕。

## 状态机（图 + 白话）

```text
created ──→ running ──→ waiting_approval
   │           │  ↑              │
   │           │  └──(人批完 resume)──┘        ┌ done              活干完且复读核实
   │           ├──→ done                       │ failed            走不下去（驳回/打转/业务失败）
   │           ├──→ failed                     │ timeout           时间预算耗尽
   │           ├──→ timeout          终态 ─────┤ budget_exhausted  步数/工具次数预算耗尽
   │           └──→ budget_exhausted           └ killed            被人急停
   └──────────────→ killed（任意非终态均可被杀）
waiting_approval ──→ failed（等的任务消失等台账异常）
```

- 迁移唯一入口 `Runtime._transition()`，按 `ALLOWED_TRANSITIONS` 白名单校验，**非法迁移抛
  IllegalTransition**（如 done→running、created→waiting_approval），不吞不糊。
- 终态五个，出边为空（测试逐一断言）。
- **裁量说明（诚实标注）**：任务书行文把三种预算超限都归"budget_exhausted"，但状态机又单列
  timeout——本实现取"时间预算超限=timeout、步数/工具次数超限=budget_exhausted"（否则 timeout
  永不可达），两者都属"预算停"、都落白话 summary。此为规格歧义的实现裁量，已在交付报告列明。

## 两张表（业务库自建 CREATE IF NOT EXISTS，同 commands / llm_calls 模式）

```sql
agent_runs(run_id PK, goal, status CHECK(八态), agent_role, budget_json,
           created_at, updated_at, killed DEFAULT 0, summary)
agent_run_steps(run_id, step_no, kind CHECK(think|tool|command|wait|verify),
                payload_json, result_json, created_at, PRIMARY KEY(run_id, step_no))
```

- 列=任务书原样，无增列。status / kind 上了 SQLite CHECK（脏值直接被库拒）。
- created_at / updated_at 用**真实 UTC**（运行遥测，非业务 as_of；D8 遥测例外，同 egress_gate
  先例）。两表已登记 `pipeline/db_digest.py` 的 `TELEMETRY_TABLES` 豁免（附一行理由），
  业务表确定性门不受影响——登记后 db_digest 两次输出仍一致（9582dcb0…，实测）。
- 白话：这两张表是 runtime 自己的**黑匣子**（飞行记录仪），不是业务对象表；记"AI 这趟活干到哪、
  每步干了什么"，35 对象表结构一根手指都没碰。

## 写步骤=总线，恰一次（本批的承重墙）

- runtime 发起的**任何业务写**都走 `execute_command`（Command 写总线），幂等键
  `run:{run_id}:{step_no}`。通道：AgentSession.dispatch 新增可选参数 `idempotency_key`
  透传给 `_bus`（per-call 设置、finally 复位，同 trace_id 模式；缺省 None=既有调用方语义一字不变）。
- **崩溃窗口**（总线已 commit、黑匣子步骤行未落）：resume 从历史重算出**同一个 step_no**→同一把
  幂等键→总线**重放首次结果**（不重执行）。测试④实证：崩溃现场"任务已建但步骤行缺失"，resume 后
  同 key 命令台账仍只 1 条、重放拿到首次 object_id、该风险仍只有 1 个非终态任务——恰一次。
- 白话：幂等键像"取件码"——第一次凭码取了件，崩溃后再来，柜子直接把**当时那件**给你，
  绝不再造一件。
- think 步（纯读/纯想）按至少一次语义（崩溃重跑无副作用），如实标注。

## 冻结区不可达（红线）

- 审批/关闭/报价裁决四动作对 runtime **任何形态不可达**：写面=Toolbox 既有 7 写工具
  （`ALL_WRITE_PERM`），全部经 `AgentSession.dispatch` 单门（FORBIDDEN_TOOLS 最先拦截+审计）；
  runtime 源码零 import 动作函数。
- 静态断言测试（⑥）锁死：runtime.py 全部字符串常量对四个冻结工具名做**子串级**检查为零命中；
  AST 抽取剧本计划的工具名字面量（{"tool": …}）无 approve/close/quote 字样；写调用 ⊆ 7 写工具。

## 硬预算 + 防打转（budget 语义）

- `budget_json = {max_steps, max_tool_calls, max_seconds, spent_seconds}`，缺省 12 步 / 8 次工具 /
  300 秒。每步执行前检查：`spent_seconds ≥ max_seconds → timeout`；`步数 ≥ max_steps` 或
  `(tool|command 步将执行且工具次数 ≥ max_tool_calls) → budget_exhausted`。超限一律落白话 summary。
- `spent_seconds` 只累计**真实执行时长**（monotonic 计时，跨 resume 持久于 budget_json）——
  **等人审批的时间不吃预算**（pending 时 resume 不记步不计时，人慢不该烧掉 AI 的预算）。
- loop guard（防打转）：tool/command 子序列末 **N=3** 步 (工具, 参数指纹) 完全相同 → 自动停
  （failed+白话"原地打转"）。指纹复用总线 `fingerprint_params`（同一把尺）。测试③实证恰 3 次后停。

## 等审批三分支（写后复读）

提案提交成功 → 记 wait 步（含等待的 task_id）→ run 进 `waiting_approval`。`resume(run_id)` 查
任务 approval_status：

- **pending** → 如实返回"仍在等"，不推进、不耗预算；
- **approved** → 回到 running，执行 **verify 步=写后复读**：读回任务行核实
  `approval_status=approved ∧ status=done ∧ action_taken 非空`（副作用真实落库）→ done；
- **rejected** → verify 观测到驳回 → failed，白话原因"提案被人工驳回（任务退回 assigned，
  参数留痕于审计）"。

## kill switch（急停留痕）

`python3 -m agent.runtime --kill RUN_ID`：置 `killed=1`；未终态则迁 killed 并落白话 summary。
**任何 resume/step 前都先查杀令**（跨进程 commit 后可见）；已终态的 run 也置 killed=1 留痕
（"有人下过杀令"这个事实本身可查）。重复 kill 幂等。

## think 步与 LLM 降级

think 步走 `agent.llm_agent` 现有通道（`probe_cli_availability` 先行，不出境探测；可用才
`answer_over_context` 单发）。不可用/异常/`--no-llm` → **优雅降级为确定性剧本说明**，result 里
`mode` 如实标注 `llm` / `deterministic` + 降级原因——绝不把脚本产物假装成模型说的。
测试全程 `llm="off"`（零出境，遵守限流纪律）。

## 确定性剧本（V1 范围）

`--start "处置 RSK-xxxx"` → think → tool:get_risk → command:assign_task（P1/P2 按 severity，
due=as_of+3d，ops）→ command:propose_mitigation（费用类→accept_charge，其余→accept_delay，
均为审批路径无危险副作用的最小可批集）→ wait → （人批）→ verify → done。
planner **只依据 goal+已落账历史**计划下一步（不猜库状态），断点续跑天然复现同 step_no/同键。
任何一步业务失败（如目标风险已有在办任务）→ failed+白话原因，不接管既有任务（诚实边界）。

## 入口（全部实测）

```bash
python3 -m agent.runtime --start "处置 RSK-0007" [--db PATH] [--role ops] [--no-llm]
                         [--max-steps N] [--max-tool-calls N] [--max-seconds N]
python3 -m agent.runtime --resume RUN_ID / --list / --kill RUN_ID
```

## 明确不做（本批边界）

驾驶舱/Streamlit 接线（runtime 本批纯后台，UI 下批）；多 run 并发调度器；LLM 自主规划
（planner 本批确定性剧本，think 步已留 LLM 通道）；接管既有任务；engine/真值/EXPECTED_*/
本体 JSON/app/actions.py/app/command_bus.py 零改动。

## 验收（真实输出见交付报告）

`python3 -m agent.test_runtime` 60 项全绿（迁移合法性/预算三停/loop guard/崩溃恢复恰一次/
kill/冻结区静态断言/等审批三分支/遥测登记守护/CHECK 约束）；pytest apps/api 80 passed；
test_agent_security 288 注入全绿；ontology_lint --strict 零差异；db_digest 两次一致（9582dcb0…）；
test_mcp_server 全绿（tools.py 改动的额外保险）。
