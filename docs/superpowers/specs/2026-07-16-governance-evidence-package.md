# 治理证据工程包规格（monday 审计第二梯队，Daniel 批"要"）

> 缘起：monday 审计第二梯队三项（1.4 影子测量台 / 1.5 一页运行看板 / 1.6 规则执行台账），
> 共同主题——**把治理从"文档承诺"变成"看得见的证据"**，直击项目北极星"系统看不见=病根"，
> 且是未来一切 AI 放权裁决（裁决题1）的唯一证据来源。
> 三项**都不改任何线上行为、合成数据就能完整跑、无需业务裁决即可开工**（纯工程）。
> 数据源均已现查确认：`llm_calls`（遥测）/ `resolution_memory`（AI提案+人决定+实际结果+质量标签）/
> `agent.evaluate --llm`（真AI过题）/ `action_log`（三账本审计）。

## 依赖顺序与派发

```
三洞修复落地（engine/detect.py + app/actions.py）
  → G-Ledger 规则执行台账（engine 域，与三洞修同域故必须在其后）
  → G-Shadow 影子测量台（agent 域，读引擎输出+resolution_memory）  ∥ 可与 Ledger 并行
  → G-Dashboard 一页运行看板（apps 域，展示前两者+既有遥测产出）  ← 依赖前两者有数据
```

---

## G-Ledger｜规则执行台账（engine 域）

**目标**：每次 `engine.detect` 跑完落一笔可复盘、可 diff、可重放的账。

**数据契约**（新建旁路表 `rule_run_ledger`，**不碰核心对象表、不进真值、绕开 md5 基线**）：
```
run_id            INTEGER PK AUTOINCREMENT
as_of             TEXT      -- 扫描的世界日期
rule_id           TEXT      -- R1..R21（每规则一行；detect 一次 = 21 行）
rule_version      TEXT      -- 规则版本：本轮取本体 version（0.11.3）+ 规则所在模块的语义版本；
                            --   审计 1.6 指出 engine 全线无 rule_version——这里补上，先用本体版本兜底
input_fingerprint TEXT      -- 该规则输入数据的指纹：对该规则读取的源表切片做稳定 hash
                            --   （如 R1 读 shipments+allocations → hash 其 as_of 快照）；口径入 docstring
detected_count    INTEGER   -- 本规则本次产出风险数
status            TEXT      -- ok / error
error             TEXT      -- 异常摘要（若有）
created_at        TEXT
```

**实现**：detect 主流程每个规则块结束时 append 一行；表在 build_ontology 建库时创建（旁路，
不影响 34→35 对象计数、不进 ontology_lint 断言域——它不是本体对象，是运维台账）。

**为什么有价值**：以后"这次扫描和上次差在哪"可 diff（同 as_of 同 rule_version 应同 count，
不同则要么数据变了要么规则改了，一眼定位）；配合三洞修复的幂等，重跑账目稳定。

**验收**：detect 跑两次，rule_run_ledger 每次 append 21 行；同 as_of 两次的 detected_count 逐规则
相同（幂等已修）；故意改一个阈值重跑，对应规则 count 变化在台账可见。

---

## G-Shadow｜影子测量台（agent 域，本包战略价值最高）

**目标**：让 AI 把历史风险案例都跑一遍、给出它会提的处置**建议**，**只记录不生效**，
和"人的真实决定/确定性基准"算一致率——这是"以后要不要给 AI 更多权限"的唯一证据。

**核心设计决策（本规格钉死，执行层不得自选）——"拿 AI 提案跟什么比、什么算一致"：**

比较基准分两档，都做，分别报告：
1. **对 resolution_memory 历史决定**（真金标）：对已关闭且有 `decision`+`quality_label` 的历史风险，
   让 AI 基于当时上下文（脱敏简报，同 UI grounding）产出"它会建议的处置动作"，与人当时的
   `decision` 比。一致 = AI 建议的 proposed_action 与人最终采纳的动作同类
   （reschedule/expedite/accept_delay/dispute/... 同值）。**额外维度**：在人的决定"事后被证明有效"
   （quality_label=effective）的子集里，AI 一致率是多少——这才是"AI 跟对了好决定"的真信号。
2. **对确定性引擎/种子基准**：对 agent.evaluate 的金标题（已有正确答案），真 AI 过题的通过率
   （复用 agent.evaluate --llm，不重造）。

**一致率口径**：分母=可比案例数（有明确人决定/金标的），分子=AI 建议与之同类数。
**必须分规则/分航线/分严重度切片报告**（不能只给一个总数——放权是分域的，证据也要分域）。

**红线**：
- **纯只读、零线上行为改变**：AI 在此只"生成建议供比对"，不经过任何写路径、不产生 Task/提案、
  不碰 dispatch 的写工具——它跑的是"假如让我提，我会提什么"，落到台账不落到业务库。
- 合成数据完整可跑；LLM 不可用时优雅降级（跳过 LLM 档，只报确定性档）并如实标注。
- 结果落一张旁路表 `shadow_run`（run 批次+每案 AI建议/人决定/是否一致/切片维度），不碰核心对象。
- **绝不编造一致率**：无 LLM 或样本不足时如实报"样本 N 太小/LLM 不可用"，不给假数字
  （规则5：自信的具体≠真实——一致率是要拿去做放权决策的数，必须一手实测）。

**交付形态**：`agent/shadow_bench.py`（命令行可跑，`python3 -m agent.shadow_bench [--llm]`），
输出结构化报告 + 落 shadow_run 表。--llm 缺省走确定性档（无 API key 也能出确定性基准部分）。

**验收**：合成数据上完整跑一轮（确定性档必出、LLM 档有 key 则出）；一致率分规则/航线/严重度
切片；shadow_run 表有留痕；重跑同种子同结果；报告里明确标注"这不改任何线上 AI 行为，是影子测量"。

---

## G-Dashboard｜一页运行看板（apps 域）

**目标**：把散在 CLI stdout 的治理证据聚成一屏——控制室视角。

**聚合内容**（全部现查现算，来源如实标注，无数据如实"暂无"）：
- **AI 调用遥测**（llm_calls）：按 call_type/provider/status 的调用数、token/耗时 p50/p95、
  降级次数——审计 1.5 指出 llm_calls "只写不展示"，这里给它第一个 UI 出口
- **安全对抗**：288 注入结果概览（从 test_agent_security 的产出或现跑摘要）、越权 denied 计数
  （action_log 里 result=denied 的聚合）
- **金标评估**：agent.evaluate 通过率（scripted 档常驻；LLM 档若跑过则展示）
- **影子一致率**（G-Shadow 产出）：分域一致率卡片
- **规则台账**（G-Ledger 产出）：最近 N 次 detect 的规则 count 趋势/diff

**落点**：优先挂进**透视镜 builder-console**（它本就是"给建造者/监督者看系统内部"的后台，控制室
天然属于它；避免污染驾驶舱的运营者视角）；作为新视图（第15视图）。数据经其 export_data.py 导出
或新增聚合端点——沿其既有静态导出模式，零新依赖。

**红线**：零新 npm 依赖；数字全真零编造；无数据格如实"暂无/需先跑 shadow_bench"；控制台零报错。

**验收**：透视镜新视图渲染五类证据；每类与手工现查一致；缺数据档如实降级提示；既有14视图零回归。

---

## 全包红线（三项共用）

- 不改任何线上业务行为（AI 放权、规则逻辑、动作语义一律不动）——本包只"测量与展示"，不"改变"。
- 旁路表（rule_run_ledger/shadow_run）不进本体对象域、不进 ontology_lint 断言、不进真值 md5 基线。
- 全局红线6条全量回归 + 真值 md5 不变（本包不应触碰任何真值相关路径，若触碰即设计错误）。
- 诚实优先：一致率/通过率/趋势全部一手实测，样本不足或 LLM 不可用如实标注，绝不给好看的假数。
