# U7 操作台归一评估方案书（纯文档，零代码改动）

日期：2026-07-16 ｜ 来源：波U 规格 `docs/superpowers/specs/2026-07-16-waveU-user-facing.md` U7 条目
｜ 性质：**评估方案书，不含任何代码/配置改动**，本次执行未碰 `engine/`、真值、`agent/`、`data/`
任何一行；对 `data/ontology.sqlite`、`data/simworld.sqlite` 只做只读查询（查询前后 md5 校验见附录）。
本文所有结论候 **Daniel 裁决**，不构成既定路线。

---

## TL;DR

- Streamlit 操作台 11 个工作台，其中 6 个有真实写动作（21 个表单入口，约 320 行表单代码）；
  驾驶舱目前只承接了 **1 个**写动作（`ApproveMitigation` 批准/驳回），其余全部仍只在 Streamlit。
- 后端 API 层没有给"人类"开放的通用写通道——`/actions` 硬编码 AI 身份（`actor="api-caller"`），
  `/decisions` 只认冻结区 4 动作白名单，且这 4 个里驾驶舱只接了 1 个按钮。这意味着"迁移"从来
  不是纯前端搬字段的活，A/B 两个候选都会撞到后端写通道要不要新建的问题。
- `docs/control-tower-plan-v0.2.md` 357-366 行 **V14 裁决已经把这件事的边界画好了**：
  "写路径接缝——所有写操作收敛到单一 Command 通道……不再新增第三条写路"，且路线图上"波2 Command
  写总线"就是要把 `/actions`、`/decisions`、Streamlit 三边统一铺满全部 32 个动作。波2 还没开工。
- `action_log` 真查（见第二节）：样本量小到不能拿来排"高频"——479 行里 455 行是引擎自己的探测
  日志、23 行是 seed 脚本演示数据，人类/系统之外唯一挂了真实 actor 的写动作只有 1 条。诚实结论：
  **现有证据不足以支撑"哪个动作高频"这个问题本身**，B 候选的"高频"判据必须换成别的、更弱但如实
  标注的代理指标。
- 推荐（候裁决）：**C 为主 + B 的"零新写路"子集**——把已有后端支持、只是前端没接的 3 个冻结动作
  按钮（`CloseRiskEvent`/`ApproveQuoteDecision`/`RejectOrRequestMoreInfo`）补进驾驶舱，其余全部
  留给波2 Command 总线一次做对，不提前拆分造返工债。理由与代价见第三、四节。

---

## 一、事实盘点

### 1.1 Streamlit 操作台：11 个工作台一览

数据源：`app/rbac_nav.py`（TAB_LABELS/ROLE_WORKSPACE，权限真源）+ `app/streamlit_app.py` 逐 tab
grep `st.form(`/`st.form_submit_button(`/`st.button(`。

| tab key | 白话标签 | 可见角色数 | 读/写 | 写动作（对应 `app.*_actions` 函数） |
|---|---|---|---|---|
| kpi | 全局概览 | 1（仅 manager） | 只读 | 无 |
| risk | 风险队列 | 4 | 读+写 | `assign_task`（派单 A3）、`close_risk_event`（关闭/误报强制关闭 A6） |
| task | 任务处理台 | 5 | 读+写 | `propose_mitigation`（3 种变体：费用/采购/常规提案 A4）、`approve_mitigation`（审批 A5，**驾驶舱已接**） |
| coord | 协调收件箱 | 4 | 读+写 | `record_outreach`/`record_response`/`escalate_coordination`/`resolve_coordination`/`mark_dead_ended`（CL1 五动作） |
| cost | 费用工作台 | 2 | 只读 | 无（页面横幅明示"只读视图"，处置动作导向任务处理台） |
| po | 采购工作台 | 3 | 读+写 | `assign_task`（采购风险派单，复用 A3） |
| obj | 对象详情 | 7（全角色） | 只读+导航 | 无表单；含对象级交互式 AI 问答（见 1.3） |
| kg | 知识图谱 | 2 | 只读 | 无（本体地图 + 实例邻域图，纯 DOT 可视化） |
| dq | 待核对的数据（DQ） | 2 | 读+写 | `assign_dq_issue`（分派）、`close_dq_issue`（核对关闭） |
| adm | 准入工作台 | 4 | 读+写 | `create_admission_case`(B1)、`run_compliance_precheck`(B2)、`build_logistics_plan`(B3)、`calculate_cost_scenario`(B4)、`approve_quote_decision`(B5，冻结)、`reject_or_request_more_info`(B6，冻结) |
| log | 审计日志 | 3 | 只读 | 无 |

写动作合计：**6 个工作台、21 个表单入口**（`streamlit_app.py` 内 `st.form(` 命中 21 处，行号
872-1521 区间，代码量约 320 行——risk 39 行/task 88 行/po 12 行/dq 12 行/adm 137 行/coord 33 行）。

### 1.2 驾驶舱（Cockpit，`apps/cockpit`）已经承接了什么

视图层（全部只读，`apps/cockpit/src/views/*.tsx`）：`CommandWall` 指挥墙（分区告警总览）、
`ZoneQueue`/`LaneQueue`/`WorkQueue` 三级下钻队列、`ZoneContext` 分区体征卡（今日/累计检测-提案-
批准-驳回，对应 U2/U3）、`ObjectCard` 对象卡（字段+关系导航，U5 域）、`AiWorkflow` AI 故事卡回放
+ 协作流标签（U6）、`RouteMap` 地理航线图、`TopBar`（角色切换/世界切换 U1/时间轴回放 A-2）。

写动作：**只有一个**——`ImpactPanel.tsx` 的 `DecisionButtons` 组件，硬编码调用
`POST /decisions/ApproveMitigation`，仅 manager 角色可点，其余角色置灰（`role === "manager"` 前端
预判，后端仍独立鉴权）。组件注释原文（`ImpactPanel.tsx:415-416`）已经把"部分留在驾驶舱、部分留在
Streamlit"的分工写进了界面提示文案："批准 / 驳回在此直接拍板……复杂处置、关闭风险、准入审批仍可
去 Streamlit 操作台" / "处置动作（批准 / 驳回 / 关闭）在 Streamlit 操作台执行——驾驶舱专注『看清 +
拍板定位』……防单量爆炸"。也就是说：**当前分工不是"还没来得及做"的空白，而是代码里已经写明的
设计意图**，U7 是在评估要不要、以及怎么把这条线往前推。

后端 API 层现状（`apps/api/main.py`+`decisions.py`）：

| 端点 | 身份来源 | 覆盖动作 | 前端接线情况 |
|---|---|---|---|
| `POST /actions/{name}` | 硬编码 `actor="api-caller"`（`main.py` 内 `API_ACTOR` 常量），无 X-Actor | 7 个 `ai_executable=auto` 动作 | 无前端调用方（专供 AI/MCP 面） |
| `POST /decisions/{name}` | X-Actor 必填（真实人类身份） | 4 个 `ai_executable=frozen` 动作 | **仅 1/4**（`ApproveMitigation`） |
| （无） | — | 21 个 `ai_executable=never` 动作 + DQ 2 个非本体动作 | 无任何 HTTP 写端点，只能被 Streamlit 进程内直接函数调用触达 |

### 1.3 只有 Streamlit 有的动作面（逐项列）

| # | 动作/能力 | 所在 tab | 本体分类 | 后端可复用性 |
|---|---|---|---|---|
| 1 | `CloseRiskEvent`（关闭风险/误报强制关闭） | risk | frozen | **后端已有** `/decisions/CloseRiskEvent`，纯前端缺口 |
| 2 | `ApproveQuoteDecision`（准入审批） | adm(b5) | frozen | **后端已有** `/decisions/ApproveQuoteDecision`，纯前端缺口 |
| 3 | `RejectOrRequestMoreInfo`（拒接/补资料） | adm(b6) | frozen | **后端已有** `/decisions/RejectOrRequestMoreInfo`，纯前端缺口 |
| 4 | `AssignTask`（派单，risk+po 两处入口） | risk, po | auto（exposed_as_tool） | 无人类写端点；`/actions` 存在但身份硬编码为 AI，不可直接复用 |
| 5 | `ProposeMitigation`（3 种参数变体：费用/采购/常规） | task | auto | 同上 |
| 6 | `CreateAdmissionCase`/`RunCompliancePrecheck`/`BuildLogisticsPlan`/`CalculateCostScenario`（准入 b1-b4） | adm | auto | 同上 |
| 7 | `AssignDqIssue`/`CloseDqIssue`（DQ 分派/关闭） | dq | **不在 32 动作本体内**（`app/dq_actions.py` 自成一套 `ALLOWED_ROLES`，无 `permission_key`） | 无端点，且无法直接套用 `/decisions` 的"声明驱动白名单"模式，需要单独设计 |
| 8 | `RecordOutreach`/`RecordResponse`/`EscalateCoordination`/`ResolveCoordination`/`MarkDeadEnded`（协调 5 动作） | coord | never（`ManageCoordination`） | 无端点；每个动作都带状态机合法转移校验（`coordination_actions._transition`），非法转移由后端拦截 |
| 9 | 对象级交互式 AI 问答（"询问（Opus 4.8 作答）"） | obj（`object_workbench.py:61`） | 不是表单动作，是权限感知的 agent 对话 | 驾驶舱的 `AiWorkflow` 只有历史 `llm_calls` 的故事卡回放，没有实时提问入口；这是能力形态差异，不是"少几个按钮"的事 |
| 10 | 知识图谱（本体地图 + 实例邻域图可视化） | kg | 纯呈现层，`st.graphviz_chart` | 驾驶舱 `ObjectCard` 只有单对象的 links 列表导航，没有图可视化；**类型级本体地图这一层实际上已经在"透视镜"`apps/builder-console` 里存在**（面向建造者而非操作者），是否要在驾驶舱重做一份是独立问题，不属于"操作台迁移"范畴 |

### 1.4 关键架构约束：V14"写路径接缝"与波2 Command 总线

这一条决定了 A/B 两个候选的真实工作量和风险，不是我方臆断，原文见
`docs/control-tower-plan-v0.2.md:357-366`（V14，Daniel 一字裁"C"）：

> ② 写路径接缝——所有写操作收敛到单一 Command 通道（dispatch/decisions 双门归一的演进方向），
> **不再新增第三条写路**。

以及路线图（`docs/research/2026-07-16-monday-uplift-gap-diagnosis.md:100-104`，路线丙已被采纳为
当前执行序、见 `plan-v0.2.md:350-352`"波1→波2→波3"）：

> 波2「Command 写总线 + 持久 Agent runtime」：统一写信封（command_id/幂等键/乐观锁 version/审批绑
> 提案指纹）经桥式代码生成铺满 **32 动作**，`/actions`、`/decisions`、Streamlit 全走它……

现状是：波1（AI 可信度可测可放权）已交付（见 STATUS.md 2026-07-16 记录），**波2 尚未开工**。
这意味着：表 1.3 里第 4-8 项（AssignTask/ProposeMitigation/准入 b1-b4/DQ 两项/协调五项，合计
13 个动作）如果现在就要在驾驶舱接出写按钮，**必须先给它们造一条新的人类写通道**——而这条通道
将来大概率会被波2 的 Command 总线整体替换掉。提前造 = 明知要推倒的返工债；不提前造，这些动作
就没法在候选 A/B 里落地成"能点的按钮"，只能停在只读展示。

第 1-3 项（3 个冻结动作）不受此约束——它们的后端通道（`/decisions`）已经存在且已验证
（`ApproveMitigation` 走的就是同一条路），补前端按钮**不产生新写路**，是唯一能绕开这个架构
约束、立刻可做的部分。这也是第四节推荐意见的主要依据。

---

## 二、`action_log` 真实分布（诚实纪律：只读真查，SQL 与结果如实附上）

### 方法

按 AGENTS.md 红线与 `apps/api/test_api.py` 的隔离模式，**未直接查询工作库**，而是先把
`data/ontology.sqlite`、`data/simworld.sqlite` 复制到 scratchpad 临时目录再查询；查询前后对工作库
做了 md5 校验，两次一致（`dc91b3959060...`/`7d87becf4e89...`，与开工前基线相同，工作库未被触碰）。

`data/simworld.sqlite`（"sim 世界"，14 个月合成模拟数据）**没有 `action_log` 表**——它是只读合成
数据集，不经过 `app.actions` 写路径。人类/系统写动作的审计日志只存在于 `data/ontology.sqlite`
（"验证世界"，工程验收用的主操作库）。

### SQL 与结果

```sql
SELECT action, COUNT(*) c FROM action_log GROUP BY action ORDER BY c DESC;
```

| action | 次数 |
|---|---|
| MatchInvoice | 287 |
| CreateRiskEvent | 168 |
| AssignTask | 20 |
| OpenCoordination | 3 |
| ApproveMitigation | 1 |
| **总计** | **479** |

```sql
SELECT action, role, actor, COUNT(*) c FROM action_log GROUP BY action, role, actor ORDER BY c DESC;
```

| action | role | actor | 次数 |
|---|---|---|---|
| MatchInvoice | system | engine | 287 |
| CreateRiskEvent | system | engine | 168 |
| AssignTask | system | seed_demo_ops | 20 |
| OpenCoordination | system | seed_demo_ops | 3 |
| ApproveMitigation | manager | u-manager-us | 1 |

`as_of_date` 全部落在同一天（2026-08-08）——说明这份日志是"一次性种子/评估跑批"的快照，不是
跨时间积累的真实使用历史。

### 诚实解读

1. **455/479（95%）是引擎自身的探测日志**（`MatchInvoice`+`CreateRiskEvent`，actor=`engine`），
   跟"操作台上人点了什么按钮"完全无关，必须从"高频动作"讨论里剔除。
2. 剩下 24 行里，23 行 actor 是 `seed_demo_ops`——**datagen 的演示种子脚本**跑出来的，不是真实
   操作员反复点击积累的使用频率；只有 1 行（`ApproveMitigation`）挂着人类 actor `u-manager-us`。
3. **表 1.3 列出的 13 个"只在 Streamlit"的动作类型里，`CloseRiskEvent`/`ProposeMitigation`/DQ
   两项/准入 b1-b6/协调五项——全部 0 条记录**。不是"低频"，是这份日志里**从未被调用过**（至少
   自上次库重建以来）。
4. 唯一有一点点信号的是 `AssignTask`：24 条非引擎记录里占 20 条（83%），但来源是种子脚本按
   demo 剧本批量派单，不是运营人员的真实使用习惯，**不能倒推"AssignTask 是高频动作"这个结论**，
   只能说"在这批 demo 数据里它是被脚本调用最多的一个"。
5. 结论：**当前证据量级不足以支撑"哪个动作面是高频"这个问题本身**——不是"频率不够高"，而是
   "根本没有可统计的样本"。第三节候选 B 的"高频"判据因此必须换成如实标注的代理指标（架构可
   行性/存量规模/角色覆盖面），不能假装用这份日志排出一个可信的频率榜。

作为补充上下文（非频率，是**当前队列存量**，同样只读实查，与"高频"是两回事、不可混用）：
`dq_issues` 未关闭 11 条、`coordination_threads` 活跃 3 条（sim 世界合成数据里是 28 条）、
`admission_cases` 共 40 条、`tasks` 未完成 17/20 条、`risk_events` 全部 168 条仍是 open（一条
都还没被人工关闭过）——这组数字说明这份验证库本身也还没经历过多少"人工处置"的洗礼，间接印证
了上面第 1-4 点的判断：这不是一个能读出"运营高频动作"的数据源。

---

## 三、三候选方案

### 候选 A：全迁驾驶舱

**做法**：把表 1.3 全部 10 类缺口（3 个冻结动作补按钮 + 13 个需要新写通道的动作 + kg 可视化 +
对象级 AI 问答）都在驾驶舱做出对应 UI，Streamlit 操作台功能上可退役。

**工作量（估算，非测量）**：
- 3 个冻结动作补按钮：复用 `DecisionButtons` 模式，约 3-5 人天。
- 13 个动作新建人类写通道（后端）+ 对应表单（前端）：参考 A-1（仅 1 个动作、后端通道已有
  设计模式）当时是独立一个波次的工作量，13 个动作里还包含 DQ（无本体框架可套）、协调（状态机
  校验）、准入多步骤（8-9 字段表单如 B4 成本情景）这些复杂度更高的类型，估算 15-25 人天
  （后端约 5-8、前端约 6-10、测试约 4-6，重叠有出入，量级仅供参考）。
- kg 可视化如需在驾驶舱重做：新增图形渲染能力，`AGENTS.md §7` "栈锁定……不上图数据库"的精神
  上是否允许待裁决，工作量未估（范围本身有争议，建议先裁是否要做再估）。
- 对象级 AI 问答如需迁移：需要把 agent 会话能力接入驾驶舱前端，是新能力不是"迁移"，工作量未估。
- **仅前两项（补 3 按钮 + 13 动作新通道）合计约 18-30 人天**，其余两项范围未定不计入。

**风险**：
1. **直接撞 V14 接缝②"不再新增第三条写路"**——13 个动作的新写通道若现在建，本质上就是在
   `/actions`（AI 面）、`/decisions`（人类冻结面）之外再开一批，除非明确定性为"波2 Command 总线
   的提前铺垫"（那就不是 U7 一份文档能单方面拍板的范围，需要单独裁决）。
2. DQ 动作不在本体框架内，接入方式没有现成模式可抄，设计成本和踩坑概率都更高。
3. 协调 5 动作每个都带状态机合法转移校验，UI 端要正确处理"非法转移"这类错误态，工作量不只是
   拼表单。
4. `po`/`dq` 这类低可见角色数（2-3 个角色）的工作台全额迁移，投入产出比存疑。
5. 若 13 个动作先按 ad hoc 方式建好，等波2落地时大概率要推倒重来——即"返工债"是可预期的，不是
   小概率风险。

**收益**：单一入口，长期用户心智负担最低，是"操作台归一"字面意义上的终态。

**对 V14 路线契合度**：**低**——除非重新定性为波2的提前分片执行（超出本次评估授权范围）。

---

### 候选 B：只迁高频动作面

**"高频"判据的诚实前提**：如第二节所示，`action_log` 真实分布**不足以给出统计意义上的高频排序**
——不能假装有一份可信的频率榜。因此把候选 B 拆成两个证据基础完全不同的子集：

**B-1（结构性零成本子集，强烈推荐）**：3 个冻结动作（`CloseRiskEvent`/`ApproveQuoteDecision`/
`RejectOrRequestMoreInfo`）补前端按钮。判据不是"频率高"，而是"**后端已经具备、不产生新写路、
架构零风险**"——这是唯一不依赖任何频率假设、纯靠既有事实就能证成的迁移范围。
- 工作量（估算）：约 3-5 人天（`CloseRiskEvent` 有误报/常规两条子路径，`ApproveQuoteDecision`
  需要从关联的 logistics_plan/cost_scenario 里选值，比单纯批准/驳回复杂一些；`RejectOrRequestMoreInfo`
  相对简单）。
- 风险：低——复用已验证的 `/decisions` + `DecisionButtons` 模式，是 A-1 工作的直接延伸，不是
  新范式。
- 对 V14 契合度：**高**——零新写路，是"两门"框架内的收尾工作，不是扩张。

**B-2（"疑似高频"扩展子集，证据薄弱，不推荐单独立项）**：如果一定要从 13 个 never/auto 动作里
选一个，`action_log` 里唯一有哪怕一点点信号的是 `AssignTask`（seed 脚本调用占比 83%）——但如
第二节所述，这个信号来自演示脚本而非真实使用，**不构成可靠的"高频"证据**。若仍要做，它会立刻
撞上候选 A 同样的"新写路"问题（只是范围缩小到 1 个动作）。
- 工作量（估算）：约 3-6 人天（新写通道 2-4 人天 + 前端 1-2 人天）。
- 风险：与候选 A 第 1 条相同，只是量级更小；且"用弱证据驱动一次架构决策（开一条新写路）"本身
  是需要避免的模式。
- 对 V14 契合度：**中低**——如果要做，建议明确定性为"波2的一个抢跑分片"并让 Daniel 知晓这个
  定性，而不是当作独立的驾驶舱 UI 任务。

**收益**：B-1 立刻缩小"批准/驳回"这类高敏感决策留在两个系统的割裂感（4 个冻结动作里 3 个还要
去 Streamlit，体验上不一致）；B-2 收益证据不足，不单独评估。

---

### 候选 C：不迁——Streamlit 长期作为内部操作台，驾驶舱纯指挥

**做法**：维持现状定性——驾驶舱专注"看清 + 拍板定位"（`ImpactPanel.tsx` 注释原文，见 1.2），
Streamlit 继续承载全部日常处置动作，不做任何迁移，直到波2 Command 总线把 32 个动作的写路径统一
之后，再一次性把驾驶舱需要的写入口铺出来（而不是现在分批、用不同的临时方案各自搭一次）。

**工作量**：0（本文档本身即 U7 的交付物）。

**风险**：
1. 双系统心智负担持续存在——用户要记住"批准去驾驶舱、其余去 Streamlit"，`ImpactPanel.tsx` 的
   提示文案已经在替用户做这个心智切分，但这终究是个将就。
2. 如果波2 排期持续后延，"候波2"在实践中会变成无限期搁置，驾驶舱"动作区"的提示文案会显得像
   空头支票——这正是 Monday 审计曾经点过名的问题（画面承诺了但实现搬回过一次，见 V13 记录）。
   **这一条风险需要 Daniel 知晓并接受，不是可以忽略的小事。**

**收益**：零工程返工风险；与 V14"不再新增第三条写路"红线完全对齐；把工程投入留给波2 一次做对
全部 32 个动作，避免"先建 13 条 ad hoc 写路、再被 Command 总线推倒重建"的浪费。

**对 V14 路线契合度**：**最高**——本来就是路线丙"波1→波2→波3"排期的隐含前提，候选 C 不是"什么
都不做"，而是"不提前打乱既定顺序"。

---

### 三候选对比汇总

| | A 全迁 | B-1 冻结3按钮 | B-2 AssignTask 等 | C 不迁 |
|---|---|---|---|---|
| 工作量（估算，人天） | 18-30+（kg/AI问答未计） | 3-5 | 3-6 | 0 |
| 是否新增写路 | 是（13 处） | 否 | 是（1 处） | 否 |
| 证据基础 | 无频率证据支撑范围选择 | 架构事实（后端已具备） | 弱信号（种子脚本非真实使用） | 不依赖频率证据 |
| V14 接缝②契合度 | 低 | 高 | 中低 | 最高 |
| 主要风险 | 返工债 + 范围膨胀 | 无（低复杂度扩展工作） | 同 A 但量级小 | 提示文案空转/双系统心智负担 |

---

## 四、推荐（候 Daniel 裁决）

**推荐：候选 C 为主体路线，叠加候选 B-1 作为唯一现在就做的增量。**

理由：
1. B-1 是三个候选里**唯一不依赖任何频率假设、纯靠既有架构事实（后端通道已存在）就能证成**的
   动作——`/decisions` 白名单和 `DecisionButtons` 模式已经被 `ApproveMitigation` 验证过一次，
   补齐另外 3 个是同一模式的直接复用，零新写路、风险最低、见效最快。
2. 除 B-1 外的全部迁移工作（表 1.3 第 4-10 项）都会撞上 V14 裁决明确画下的红线——"不再新增第三
   条写路"，而波2"Command 写总线"就是为了一次性、系统性解决这个问题（含幂等键/乐观锁/审批绑
   指纹，覆盖全部 32 动作）而不是逐个动作现造轮子。提前分头造会产生可预期的返工债。
3. `action_log` 现有证据（第二节）明确不足以支撑"哪些动作高频"这个候选 B 原本设想的判据——用
   一份样本量个位数、且主要来自种子脚本而非真实使用的日志去驱动"开一条新写路"这个不小的架构
   决策，证据强度不够。

**需要 Daniel 裁决的具体问题**（本文档不代为决定）：

1. 是否同意把"补齐 3 个冻结动作按钮（B-1）"作为一个独立、立即可做的小任务单独立项？
2. 候选 B-2（`AssignTask` 等 13 个动作里的任意子集）是否值得作为波2的"抢跑分片"提前做——如果
   做，需要接受"小范围返工债 vs 提前见效"这个明确的 trade-off，而不是当作与波2无关的独立任务。
3. 波2 Command 总线目前有没有明确排期？如果暂时没有，"候波2"在事实上等同于无限期搁置整个
   候选 A/B-2，这个含义需要 Daniel 知晓——如果时间线太远，或许需要重新评估是否值得为 1-2 个
   高价值动作（如 `AssignTask`）单独破例，而不是死等。
4. kg 知识图谱可视化、对象级 AI 问答这两项能力形态差异（非表单动作）是否需要驾驶舱侧覆盖，
   还是维持"驾驶舱看指挥、透视镜看结构、Streamlit 做处置"三工具分工——本文档倾向维持现状但
   未展开评估，因为它们不是候选 A/B/C 框架下的"迁移"问题，更像独立的产品定位问题。

---

## 附录：证据来源与核验记录

**读过的文件**（只读，零修改）：
- `AGENTS.md`、`docs/superpowers/specs/2026-07-16-waveU-user-facing.md`、`STATUS.md`（节选）
- `app/streamlit_app.py`（全量结构 grep + 逐 tab 关键区段精读）、`app/rbac_nav.py`（全文）、
  `app/dq_actions.py`（节选）、`app/coordination_actions.py`/`app/admission_actions.py`（函数签名）、
  `app/object_workbench.py`/`app/standard_object_view.py`（按钮上下文）、`app/knowledge_graph.py`（节选）
- `apps/cockpit/src/views/ImpactPanel.tsx`（全文相关段）、`api.ts`、`App.tsx`、`WorkQueue.tsx`、
  `LaneQueue.tsx`、`ZoneQueue.tsx`、`CommandWall.tsx`、`AiWorkflow.tsx`、`RouteMap.tsx`、
  `ObjectCard.tsx`、`ZoneContext.tsx`（onClick/写调用 grep）
- `apps/api/main.py`（全量路由 + `/actions` 实现）、`apps/api/decisions.py`（全文）、
  `apps/api/cockpit.py`（路由清单）
- `ontology/control-tower-ontology.json`（32 动作 `ai_executable`/`exposed_as_tool` 分类全表）
- `docs/control-tower-plan-v0.2.md`（V14 决议原文，357-390 行）、
  `docs/research/2026-07-16-monday-uplift-gap-diagnosis.md`（路线丙/波2 定义，85-119 行）
- `apps/api/test_api.py`（DB 只读隔离模式，用作本次 SQL 查询的操作规范）
- `apps/builder-console/README.md`（确认"透视镜"与"驾驶舱"的定位差异）

**查过的库**（只读，复制到 scratchpad 临时目录后查询，不碰工作库）：
- `data/ontology.sqlite`：`action_log` 全表分布（第二节 SQL 原文+结果）、`dq_issues`/
  `coordination_threads`/`admission_cases`/`tasks`/`risk_events` 存量计数
- `data/simworld.sqlite`：确认无 `action_log` 表、`coordination_threads` 计数（28，核对 waveU
  规格 U6 条目"sim 28 条"描述一致）、`sim_event_log` 抽样确认为合成世界事件而非操作日志

**红线校验**：
```
开工前 md5: ontology.sqlite=dc91b39590607847763d74545ea29468 simworld.sqlite=7d87becf4e89b9e2349bdac6fce88b65
收工前 md5: ontology.sqlite=dc91b39590607847763d74545ea29468 simworld.sqlite=7d87becf4e89b9e2349bdac6fce88b65
```
两次一致，工作库未被本次评估触碰。本次未修改 `engine/`、`agent/`、本体 JSON、任何冻结区/权限
语义代码；未 `git commit`；未做浏览器验证；未新增依赖；未重启/触碰任何运行中进程。
