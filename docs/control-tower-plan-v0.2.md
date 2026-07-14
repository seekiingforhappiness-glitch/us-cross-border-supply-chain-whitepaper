# 跨境供应链数智运营原型 · 总体规划 v0.2

场景：跨境订单延误风险识别与处置闭环（控制塔）

版本：v0.2 ｜ 定稿日期：2026-07-06 ｜ 状态：规划定稿，待执行

---

## 0. 文档定位

本文档是项目唯一的执行依据，取代此前 ChatGPT 输出的方案草稿。与仓库现有资产的关系：

- `docs/cross-border-ontology-manual.md`（v0.1，SKU 准入报价）：**不废弃**。其动作定义模板（输入参数/权限/成功状态/失败处理/审计字段）、权限矩阵格式、验收清单直接复用；SKU 准入场景延后为 v0.3。
- `ontology/sku-admission-ontology.json`：其 JSON schema 结构（primaryKeyPolicy、objects、dataQualityRules）作为本版 `control-tower-ontology.json` 的格式模板。
- `美国跨境供应链实战白皮书`：作为领域知识参考（时效、费用、异常类型的合理取值范围）。

## 1. 项目目标与路线

已确认的三个决策：

| 决策项 | 结论 |
| --- | --- |
| 路线 | B：自研原型，学 Ontology / 数字孪生 / Agent / 运营闭环的底层原理，不依赖 Palantir 平台 |
| 首个闭环 | 延误风险控制塔（事件流驱动） |
| 交付形态 | 可演示的完整原型：Streamlit + SQLite + 文档，AI agent 为最后阶段 |
| 节奏 | ≥10 小时/周，6 周计划 + 每周硬验收 |

**项目一句话定义**：一个把跨境履约链路建模为对象-关系-动作网络的运营原型——当运输延误发生时，系统能沿对象图定位受影响的客户订单行，量化影响，生成风险事件与任务，由人执行动作并回写状态，最后由 AI 基于同一套对象和动作做解释与建议。

**不是什么**：不是延误率分析看板，不是聊天机器人，不是真实报关/承运商对接系统。

## 2. MVP 场景闭环

### 2.1 叙事场景（演示脚本原型）

```text
美国客户 C-Best Buy Reseller 下了销售订单 SO-2026-0188（500 件 USB-C 充电器，承诺 8/20 交付）。
公司向深圳供应商下采购订单 PO-2026-0101。
货物拼入 SHP-2026-0099，8/1 从盐田港出发，原 ETA 洛杉矶 8/14。
8/8 系统收到 milestone：船期变更，新 ETA 8/21，延误 7 天。
系统沿对象图传播影响：SHP-0099 → 3 条 SO 行 → 2 个客户 → 其中 SO-0188 承诺日 8/20 将被击穿。
自动创建 RiskEvent（severity=high），生成 Task 分派给物流运营。
运营在处理台看到：延误原因、受影响订单、库存可否兜底、可选动作（加急空运补货 / 与客户改期 / 接受延误）。
AI 给出解释和推荐（proposal-only）。运营选择"改期 + 通知客户"，经理审批，状态回写。
RiskEvent 关闭，全程留审计记录。
```

### 2.2 闭环六步

```text
感知（milestone 事件）→ 传播（对象图影响分析）→ 定级（风险规则）
→ 派发（RiskEvent + Task）→ 处置（人工动作 + 审批）→ 回写（状态 + 审计）
```

六步全部跑通才算 MVP 完成。任何一步缺失，本项目退化为看板。

## 3. 范围边界

### 3.1 做

| 维度 | v0.2 边界 |
| --- | --- |
| 链路 | 中国供应商 → 海运 FCL/LCL → 洛杉矶港 → 美国仓 → B2B 客户订单 |
| 商品 | 消费电子配件（充电器、数据线、耳机、手机壳），10-20 个 SKU 即可 |
| 风险类型 | 仅 3 类：ETA 延误、清关文件缺失（简化为 shipment 属性异常）、承诺日击穿 |
| 动作 | 6 个（见 §7），全部落在延误处置闭环上 |
| 用户角色 | 3 个：物流运营、客户成功、经理（v0.1 的 6 角色砍半） |

### 3.2 不做（与理由）

| 不做 | 理由 |
| --- | --- |
| 空运/快递/铁路 | 一种运输方式足以验证闭环，多方式只增加数据生成成本 |
| Container/Vessel/Port/Carrier/Invoice/CustomsDeclaration 对象化 | 降级为 Shipment 属性（见决策 D3），v0.3 再对象化 |
| 库存与补货决策 | InventoryPosition 仅作为处置时的参考属性，不建补货闭环 |
| 真实清关合规逻辑 | v0.1 场景的领域，不在本闭环 |
| 行级采购分配（POLine） | 见决策 D2，接受已知误差 |
| 真实权限系统 | Streamlit 角色切换器 + 字段可见性规则即可，权限的价值在 spec 不在实现 |
| 费用/成本异常 | v0.3 |

## 4. 关键建模决策日志

这是本规划相对 ChatGPT 草稿的核心修正，执行中不得静默推翻，如需变更须在此追加记录。

**D1 — 影响分析必须到订单行级。**
SalesOrder 拆 header + line 两个对象。理由：一票 shipment 部分覆盖多个订单是常态（草稿自己的噪声清单也如此要求），header 级建模算不出"影响哪个客户各多少量"。这是草稿最大的建模缺陷，v0.2 修正。

**D2 — 采购侧不拆行，接受已知误差。**
PurchaseOrder 保留 header 级，PO→Shipment 为 N:1（一票可含多个 PO）。理由：影响传播的关键路径是 Shipment→SOLine，采购行粒度对本闭环无增益。代价：无法精确回答"某 PO 行在哪个柜"，记录为已知限制。

**D3 — 分配关系建中间对象 ShipmentAllocation。**
SOLine ↔ Shipment 是带属性（分配数量）的 N:M 关系，按 v0.1 手册原则"关系带属性必须建中间对象"，不塞裸 link。这是整个影响传播计算的枢纽表。

**D4 — ShipmentMilestone 必须对象化。**
延误检测的信号源是 milestone 事件流（离港、到港、ETA 变更、清关放行……），它是一等公民对象而非 Shipment 的日志字段。草稿把它留在数据表层，导致"感知"这一步没有建模载体。

**D5 — 六个非核心对象降级为属性。**
Container→`shipment.container_no`；VesselVoyage→`shipment.vessel_voyage`；Port→`shipment.origin_port / destination_port`（枚举）；Carrier→`shipment.carrier_name`；CustomsDeclaration→`shipment.customs_status + missing_docs`；Invoice→不进 v0.2。判断标准：**只有需要独立生命周期、独立动作或独立权限的概念才配当对象。**

**D6 — 合成数据必须自带 ground truth。**
数据生成器注入每一个异常（延误、缺失、重复、状态冲突）时，同步写入 `ground_truth_events` 表。风险引擎的查准/查全率对着这张表算。这是合成数据相对真实数据的唯一优势，必须用足。

**D7 — AI 最后接入，但动作接口提前按工具函数设计。**
每个 Action 的函数签名（输入参数、前置校验、返回结构）在第 1 周就定稿，AI 阶段直接把它们注册为 tools，不重写。

**D8 — 全局仿真时钟 `as_of_date`。**
控制塔是随时间演进的系统：R3 停滞规则、延误检测、UI 世界状态都依赖"今天是几号"。所有引擎评估和 UI 渲染必须以显式 `as_of_date` 参数为准，禁止隐式取系统当前时间。最小实现：固定快照日（如数据窗口第 90 天）；W5 可选加分项：日期滑条回放。数据生成器所有事件必须带时间戳且分布在 4 个月窗口内。

**D9 — W1 建模勘误与授权记录（2026-07-06，人已批准）。**
C1：SOLine 改期后回迁 allocated 继续监控（rescheduled 不作终态），二次击穿再次报警；C2：加急简化为 expedite_flag + 解除行风险，不新建空运 shipment（v0.3 补真实流程）；C3：affected_so_line_ids 用 JSON 列表不建关联表；C4：单 RiskEvent 同时仅 1 个非终态 Task；C5：Shipment 取消 departed 独立状态（departed 事件触发 planned→in_transit）；C6：Task 取消 created 状态。C1、C2 为人直接裁决；C3-C6 为人授权 AI 决定。§5 状态机骨架已按此勘误。详见 ontology manual §8。

**D10 — milestone 规模估算修订（2026-07-06，人已批准）。**
§9 的 shipment_milestones 估算由 1,500-2,500（每票 8-15 条）修订为 600-1,200（每票 5-10 条）。原估算隐含船位流水类事件，与事件枚举（9 类业务事件）不符。理由详见 weekly-notes/W2。

**D11 — 真实字段 Tier 1 采纳（2026-07-06，人已批准）。**
依据 DCSA / EDI X12 315 / 可视化平台调研（docs/field-gap-analysis.md）：Shipment 增 booking_no、mbl_no、carrier_scac、container_type、gross_weight_kg、volume_cbm、incoterm、双港 UN/LOCODE；Milestone 增 event_classifier（ACT/EST）、event_locode；SalesOrderLine 增 unit_price_usd（成交价），影响金额口径由目录价改为成交价。结构性差距 G1（真实世界无全局 shipment_id）记录在案，v0.2 保留源表内部键，单证号级 ER 留 v0.3 决定。

**M1 — 动作边界升级为 demo 实名 actor + maker-checker（2026-07-07，人以"继续"批准）。**
批准范围仅限成熟度升级 Task 1：在不接真实 SSO/权限系统的前提下，为动作层引入 demo
`actor_id` 语义、稳定 ID helper、事务 helper、以及 proposer 与 approver 不得为同一人的
maker-checker 校验。取舍：比原 role-only 更接近成熟控制塔治理，但仍保持 simulation-first；
真实身份目录、行级授权、审计不可篡改存储不在本决策范围内。M2+（MDM、关系 registry、
DQ issue、outbox、业务扩展）仍须另行批准。

**M2 — 任务队列升级为 named owner + SLA state + escalation_level（2026-07-07，人以"继续"批准）。**
批准范围仅限成熟度升级 Task 2：在不接真实用户目录、排班系统或通知系统的前提下，
为 Task 增加演示用 `assignee_user_id`、`assignee_team_id`、`sla_state`、`escalation_level`、
`policy_version` 字段，并提供基于角色/区域的 deterministic owner 选择 helper 与显式
`as_of_date` 的 SLA 状态计算。取舍：比 role-only `assignee_role` 更接近运营队列，但仍保持
simulation-first；真实人力排班、提醒发送、自动升级动作、工作量均衡不在本决策范围内。
M3+（事件信封/血缘、MDM、关系 registry、DQ issue、outbox、业务扩展）仍须另行批准。

**M3 — 源事件信封与 raw lineage 作为模拟 source-truth 基础设施（2026-07-07，人以"继续"批准）。**
批准范围仅限成熟度升级 Task 3：为 milestone 源事件增加 canonical event envelope、
`source_events` 停靠表、`idempotency_key` 与 raw payload 摘要，使对象层事件可追溯回模拟源消息。
取舍：比直接消费 cleaned milestone 更接近成熟控制塔的数据血缘，但仍保持 simulation-first；
不接真实 carrier/API，不新增真实生产集成、DQ issue 工作流、outbox、MDM crosswalk 或业务规则。
M4+（MDM、关系 registry、DQ issue、outbox、AI/redaction 等）仍须另行批准。

**M4 — 跨系统主数据 crosswalk 解析作为 inspectable MDM 基础能力（2026-07-07，人以"继续"批准）。**
批准范围仅限成熟度升级 Task 4：为 customer / sku / vendor 等模拟外部 ID 增加 deterministic
crosswalk resolver、`mdm_crosswalk` 表与 unresolved / ambiguous 计数，使管道可显式暴露“能映射、不能映射、
不能猜”的主数据状态。取舍：比临时名称匹配更接近成熟控制塔的主数据治理，但仍保持
simulation-first；不接企业 MDM 平台，不做自动主数据合并、人工治理队列、真实主数据同步或 M5+
graph traversal / relationship registry。M5+（关系 registry、DQ issue、outbox、AI/redaction 等）仍须另行批准。

**M5 — 通用关系 registry + 可解释 graph traversal（2026-07-07，人以"继续"批准）。**
批准范围仅限成熟度升级 Task 5：在 SQLite 内增加 simulation-first 的 `object_relationships`
registry，并提供只读 graph traversal helper，让风险、费用和 AI 解释可复用同一层对象关系路径。
取舍：比分散 SQL / JSON list 更接近成熟控制塔的可解释对象图，但仍保持轻量；不上图数据库，
不新增业务对象、风险规则、真实主数据治理、DQ issue 工作流或 outbox。M6+（DQ issue、outbox、
AI/redaction、业务扩展等）仍须另行批准。

**M6 — 数据质量停车记录升级为可运营 DQ issue（2026-07-07，人以"继续"批准）。**
批准范围仅限成熟度升级 Task 6：把模拟管道中的 unresolved / parking 记录转为 `dq_issues`
运营队列，支持 assign / close 两个处置动作并写审计，使“数据断链”从 build-time 诊断升级为
可分派、可解释、可关闭的运营工作。取舍：更接近成熟控制塔的数据质量运营，但仍保持
simulation-first；不真实修复源系统、不回写外部系统、不新增噪声类型、风险规则、真实主数据合并、
outbox 或 AI/redaction。M7+（outbox、AI/redaction、业务扩展等）仍须另行批准。

**M7 — 模拟集成写回 outbox（2026-07-07，人以"继续"批准）。**
批准范围仅限成熟度升级 Task 7：在 SQLite 内增加 simulation-first 的 `integration_outbox`，
用 deterministic idempotency key 记录已批准业务动作的模拟写回请求，并支持标记成功，使“状态已回写”
从口头假设变成可审计、可重放、可去重的内部队列。取舍：更接近成熟控制塔与外部系统集成的安全边界，
但不接真实 ERP/TMS/QMS、不发送网络请求、不新增真实 retry worker、不改变既有业务规则或 KPI。
M8+（业务扩展决策、AI/redaction、成熟工作台等）仍须另行批准。

**P1 — 采购(procurement)业务域第一个纵向切片（2026-07-08，Daniel 经 AskUserQuestion 批准）。**
联网调研（SAP Ariba/Coupa/Dynamics 365/UC Berkeley SoD/Alibaba Trade Assurance）后，Daniel 批准把
跨境采购(P2P)接入控制塔，走 model-first。核心：采购异常与延误/费用异常同构，**全复用**
`RiskEvent→Task→动作→审计` + maker-checker + 对象工作台+agent，不新增治理机制。
Daniel 的两个业务裁决：① **加采购单行 PoLine**（支持一单多 SKU + 分批收货 + 逐行三方对账；
**本项覆盖 D2 的单 SKU PO 约束**，为采购稽核核心能力）；② 第一版范围 = **三方对账主线 4 类**
（R7 供应商交期延误 / R8 短装 / R9 QC 不合格 / R10 价量不符）。
controller 默认取舍（Daniel 未否决，随此记录）：RiskEvent 锚点由"货运锚定"泛化为**可空
po_id/supplier_id**（与"加字段不联表"哲学一致，唯一触碰核心对象处）；SupplierInvoice **独立对象**
（不复用货运 Invoice，避免污染费用场景）；采购动作归现有 **ops/finance/compliance**（暂不新增 buyer 角色）；
容差/阈值全进 `config/*.yaml`。
MVP 对象：`PoLine`、`GoodsReceipt(+行,含 qc_status/defect_ppm)`、`SupplierInvoice(+行)`；
MVP 动作：`RecordGoodsReceipt`/`MatchSupplierInvoice`（摄入→检测）+ proposed_action 扩展
（expedite_po/raise_supplier_claim/dispute_supplier_invoice/accept_receipt_variance）。
**推迟（富化，另行批准）**：R11-R13(超收货开票/预付款敞口/资质过期)、PurchasePayment、SupplierQualification、
RFQ、单一来源(R14)/maverick(R15)。取舍：先把一条三方对账主线端到端跑通验证模式，再富化。

**P2 — 采购富化 R11-R13（2026-07-08，Daniel 批准，承 P1 推迟清单）。**
把 P1 推迟的三条采购风险从"另行批准"转为落地：**R11 开票超收货量、R12 预付款敞口、R13 供应商资质过期**。
新增对象：`PurchasePayment`（挂 PO：payment_type[deposit/balance/full]、amount_usd、paid_date、exposure_status；
R12 事实源）、`SupplierQualification`（挂 Supplier：cert_type、evidence_status、valid_from、valid_to、status；R13 事实源）。
R11 复用既有 supplier_invoice_lines + goods_receipt_lines（无新对象）。新增处置动作（proposed_action 扩展）：
`escalate_prepayment`/`hold_balance_payment`（R12）、`request_supplier_docs`/`suspend_supplier`（R13）。全复用
RiskEvent→Task→治理闭环 + po_id/supplier_id 锚点 + 既有 PO 工作台；两个新对象走**自动标准视图**（不建富工作台）。
容差/阈值（敞口金额、超期天数、资质过期宽限）入 `config/*.yaml`。取舍：补齐跨境采购最吃重的预付款/资质两条
（研究里异常 G/H），仍不做 RFQ/单一来源/maverick。铁律不变：引擎禁读真值、真值存 data/truth/、既有 R1-R10 R/P=1.000
不得扰动、agent 不越权。

**W1 — 仓储(warehouse/inventory)业务域第一个纵向切片（2026-07-08，Daniel 经 AskUserQuestion 批准）。**
联网调研（Dynamics 库存/ATP/IRA/FBA/cycle count）后批准仓储接入控制塔，model-first，全复用 RiskEvent→Task→治理骨架。
Daniel 两个业务裁决：① 库存粒度 = **SKU×仓库**（不到 lot；R21 批次过期因此推迟）；② 第一版 = **库存准确主线 + 现货救延误**。
controller 默认取舍（未否决）：角色复用 ops(仓库执行)/finance(仓储费)/compliance(隔离过期)/manager(调整/替代审批)，暂不加 wh_ops；
阈值(安全库存/IRA目标/容量%)入 config；RiskEvent 锚点加可空 `warehouse_id`。
MVP 对象：`Warehouse`(type overseas/bonded/domestic/FBA/3PL、capacity)、`InventoryPosition`(sku×warehouse；桶
available/reserved/in_transit/quarantine)、`InventoryReservation`(open→allocated→released→fulfilled/backordered)、
`CycleCount`(scheduled→counted→variance→reconciled)。MVP 规则：**R16 stockout**(available≤safety_stock)、
**R17 unfulfillable**(SOL.open 且 ATP<ordered_qty；ATP=available+在途−reserved)、**R18 shrinkage**(|counted−system|>tol 或 IRA<阈)。
MVP 动作：`Putaway`(GoodsReceipt→InventoryPosition)、`ReserveInventory`/`ReleaseReservation`、
`SuggestSubstitution`(延误→现货拆单先发+余量改期，过 A5 审批)、`RecordCycleCount`→`AdjustInventory`(过 A5)。
**连接点**：①采购 GoodsReceipt.accepted_qty→Putaway→InventoryPosition.available ②Reservation.allocated→驱动
SalesOrderLine open→allocated ③Shipment 延误(R1-R3)→查目的仓现货→SuggestSubstitution（业务问题落点）。
**推迟（另行批准）**：R19 呆滞、R20 超库容、R21 批次过期(需 lot)、R22 错分配、wh_ops 子角色。铁律不变：引擎禁读真值、
真值存 data/truth/、既有 R1-R13 R/P=1.000 不得扰动、agent 不越权。

**P3 — 采购富化 2：RFQ 询价 + R14 单一来源 + R15 maverick（2026-07-08，Daniel 请求批准；P1 推迟清单项）。**
Daniel 批准把 P1 推迟的 RFQ/单一来源/maverick 落地，model-first，全复用既有 RiskEvent→Task→治理骨架。
新增对象：`RFQ`(询价，draft→sent→quoting→evaluating→awarded→closed)、`RFQLine`、`Quote`(供应商报价，
invited→submitted→shortlisted→awarded/rejected/expired)。新增规则：**R14 single_source**（某 active SKU 仅 1 个
approved 供应商 且该供应商近 N 天有 R7/R9 事件 → 断供风险）、**R15 maverick_spend**（supplier_invoice 无匹配
approved PO / 非 approved 供应商 → 绕流程采购）。锚点复用 supplier_id/po_id（不加核心列）。
新增动作：`initiate_second_source`（R14→建 RFQ 启动第二来源）、`block_non_po_payment`/`backfill_po`（R15），
走既有 assign→propose→approve 闭环 + maker-checker（approve 仍仅 manager）。
默认取舍：容差/近期窗口(N 天)入 config；RFQ/RFQLine/Quote 走**标准视图**（不建富工作台）。
铁律不变：引擎禁读真值、真值存 data/truth/、既有 R1-R18 R/P=1.000 不得扰动、agent 不越权。

**P4 — 新增专职「采购 procurement」角色（2026-07-09，Daniel 经 AskUserQuestion 亲批）。**
动因：系统已有完整采购业务域（R7-R15、采购工作台、三方对账），但 6 角色里无专职采购——采购处置此前
折叠进 ops(收货/交期)+finance(供票/价量)+manager(审批)。Daniel 判断真实跨境公司采购/寻源是独立职能，
面客 demo 应补齐。范围（最小、守护城河）：① 新角色 `procurement`，落地页=采购工作台，工作台 `[po, task, obj]`；
② `ops` 去掉 po「回归纯物流」；③ 权限仅给 `ProposeMitigation` +procurement（采购处置提案是其核心活）；
④ **AssignTask/CloseRiskEvent/ApproveMitigation 全不动**——`ApproveMitigation` 仍仅 manager、`CloseRiskEvent`
仍仅 ops，形成「采购提案→经理审批→运营关闭」三方职责分离（比原来分离更强）。roster 加 u-proc-us/cn。
铁律不变：agent 审批类工具从未注册（procurement agent 亦无）、越权动作层挡回、R1-R18 R/P=1.000 与 truth md5
逐字节未动、maker-checker 不削弱。三处 EXPECTED_ROLE_PERMS 基线 + rbac/data_scope/smoke 契约同步更新（明批非削弱）。

**CL1 — 协调回路（coordination loop）第一个纵向切片（2026-07-09，Daniel 经 AskUserQuestion 亲批）。**
动因：用真实数据（两轮中立调研：4 岗位 + 跨境异常处理流程）做 A/B/C 缺口分析——系统现有功能 vs
第一性原理必要功能表（见 `docs/notes-decision-rights-org-design.md §10`）vs 真实异常。**最响的信号**：
真实世界里异常处理最难、最耗时的活是**跨外部主体的协调/催办/谈判/扯皮**（"驻厂催货"/"让客户去开证行认
不符点"/"业务员指挥不动产线要老板出面"/"滞港费责任扯到打官司"），而系统核心闭环 检测→派单→提案→审批→关闭
**完全没建**这个多方协调回路（grep coordination/协调/催办/谈判 = 0 命中）。这是**逻辑缺口**（作用于所有域、
乘数效应），优先级高于"再加一个同形状的覆盖域"（海关/信用证/D&D 是覆盖缺口，记待办不在本切片）。
与 §10 第一性原理一致：谈判/协调是一等必要功能、人的黏核，系统偏漏。
范围（最小、复用骨架）：① 新对象 `CoordinationThread`（协调线程）锚在 Task 上：对手方类型(supplier/
forwarder/customs_broker/bank/customer)+引用、ask、状态机(open→awaiting→responded→resolved/
escalated/dead_ended、逾期 overdue)、催办次数、升级级别、内部协调人；② 新动作（走动作层+审计+
permission-aware）：open_coordination/record_outreach/record_response/escalate_coordination/
resolve_coordination/mark_dead_ended；③ 浮现 overdue（外部版 SLA）。**刻意不做**：不做真发消息/CRM，
只建协调的状态+台账；对手方用轻引用，不建完整 Counterparty 对象图（留后续）。
铁律不变：R1-R18 不动（新对象+动作，不碰检测）、truth md5 逐字节不变（协调是运营态，seed_demo_ops 幂等造，
不碰 datagen 真值）、maker-checker 不削（既有 4 权限键不动、ApproveMitigation 仍仅 manager、协调动作是新权限组）、
agent 不越权（协调写动作**不注册为 agent 工具**，agent 仍只读+提案）。demo：延误 RSK-0031 对工厂/客户各起一条协调。

**J1-J6+合流 — 产品化转向裁决与两线合流（2026-07-10~12，Daniel 逐项裁决；详见
`docs/superpowers/specs/2026-07-12-control-tower-product-design-v2.md`（v2.1，已批准）与
`2026-07-12-merge-resolution-v2.2.md`（合流决议）；证据底座：`docs/product-foundations-primer.md` 教材
+ `docs/research/` 三份深调研与六路对抗评审）。**
动因：Daniel 决定将项目转向"能实际落地使用的产品"（多角色 AI 智能体/治理式进化/越用越强）。三天探讨
（定位四轮+教材十题+六路对抗评审）后逐项裁决：**J1** LLM 开发期默认 OpenAI、provider 接口做实（附随护栏
已确认：出境白名单摘除闸门第一天建＋国产 provider 适配真实跑通评估集一次）——合流后修订为 claude_cli
订阅通道为主力（本仓库已打通），闸门要求不变；**J2** 不砍范围，保留完整系统协同（六路评审的"致死点修复
清单"全部纳入，见 v2.1 §16：运维骨架/确认队列防毒化/断货模型影响公式/对外触达=AI 起草人发出/评估分级
闸门/宪法 10c 修正为"管理员动作留痕+每周披露"等）；**J3** 审批渠道跟设计伙伴实际工具；**J4** 双轨验证
（构建轨×Mom Test 访谈轨，第 6-8 周按预写判据汇合裁决三岔路）；**J5** 合规整体后置到商业化决策点
（例外已确认：ICP 备案+一页纸试点协议在接真实数据前完成）；**J6** 叙事修正（"可以带走但别处用不起来"/
"1 人干 3 人的活"/新卖点"新专员上手包"）。**合流裁决（07-12 深夜）**：两线并行三天后互相发现，Daniel
裁决方案甲——本仓库（codex 线 v0.9.0）为主干，产品化成果并入；能力排序维持路线图 B→C1→A（B 已完成，
下一步 C1）；新产品 repo 推迟至第二家非亲友付费客户出现。**反目标"不接真实数据/真实企业系统"的推翻，
生效于 A 能力首次摄入真实业务数据之时**——在此之前本仓库仍是仿真世界，诚实边界不变。教训入账：两线
并行三天才互相发现，接管自检新增第 0 步（git branch -a + 各活跃分支 STATUS 对照）。

**C1 — 处置记忆→更好提案（2026-07-12 深夜，Daniel 经 AskUserQuestion 亲批三裁决点）。**
提案全文：`docs/superpowers/plans/2026-07-12-c1-resolution-memory-proposal.md`。裁决：相似=规则类型精确匹配+
**同航线**为第二维度（处置手段相似度最高且首年样本密度够）；提案先例区块=**最相似 1 条讲透+一行汇总统计**
（审批预算友好）；质量标签=**专员关闭异常时顺手三选一**（有效/部分有效/无效——一致率成绩单与评估集的原料，
人打标签 AI 不自评）。范围：resolution_memory 表（决定时写入+关闭时回填）、同域只读检索、propose 先例区块
（数字运行时现算可回查、禁缓存禁编造）、DecisionRecord 血缘雏形（四件套+场外依据字段）、关闭表单质量标签。
四红线不动：R1-R18 全 P/R=1.000、真值 md5 逐字节不变、maker-checker/FORBIDDEN 零修改（AI 仅获只读检索工具）、
agent 不越权。阶段门 G1：提案可回放+删除先例不暗引。

**J4-v2 — 商业验证判据变更（2026-07-13，Daniel 授权"其他的你来决定"后由主会话代行入账）。**
背景：Daniel 无法执行陌生访谈，改以四路线上证据替代（`docs/research/2026-07-13-online-evidence-synthesis.md`）。
变更：原判据后半条（≥3 家能说出具体损失与动作）由线上证据满足（22 案例远超）；前半条（陌生付费承诺）
**改挂产品可演示后**——设计伙伴象征付费+4 周真实使用（弱信号）+ 用真实 demo 向 3-5 个陌生对象（含 1-2 家
货代，兼验白牌备胎假设）展示并开价；三岔路裁决点顺延至"伙伴 4 周验收+demo 展示后"。残余风险如实记录：
付费验证推迟 4-8 周，对冲=构建轨服务自家真实业务（自用价值兜底）。证据要点入账：痛点强支持（9/9 延误
案例无系统告警）；"货主无动作空间"假设修正（常规延误有销售端动作空间，预警=可折算成钱的决策窗口）；
产品形态信号（独立跟踪 SaaS 难成立，以 ERP 增强/货代增值被感知）；"可举证审计链"获司法定价（迟通知
18 天=40% 责任、时效仅 1 年）；定价证据排序（追回分成 20-30% 最强）。设计输入三条已并入待办：免箱期+
诉讼时效倒计时、司法语言进销售材料、定价模式证据排序。

**V4 — 产品形态重构决议：驾驶舱范式+模拟世界引擎（2026-07-14，Daniel 经费曼对齐后确认，95% 信心收口）。**
缘起：UI 多轮修补后 Daniel 判"远远达不到想要的效果"，裁定回归起点用费曼提问法重新对齐。三轮对话收口的
裁决：①**前台=分级驾驶舱**（全员同构"小全景"，颗粒度随角色缩放；异常与待决策自动上浮；层层下钻；
重点突出+高信息密度+真实动态，拒绝华而不实）②**AI 完全透明且可视化**（工作过程全程可见，是页面主角
而非角落按钮）③**后台=建造者透视镜**（完全独立应用：本体分层立体展示/宪法/进化资产/运行账本/血缘回放，
"每个视图回答一个问题"，否决数据结构直接投影式图谱）④**模拟世界引擎为重投入板块**（Daniel 原话：投入
比当前认知多得多——连续时间流的活世界，多维度多场景异常谱系，标准="懂行者十分钟找不出破绽"，
不是画面好看是画面真实）⑤视觉锚点=Palantir Ontology 等距分层图的感觉 ⑥技术路径：Python 核心与
合成验证世界一行不丢，+FastAPI 服务层+React 双应用（驾驶舱/透视镜），Streamlit 降级为过渡操作台
⑦顺序：模拟引擎设计提案（§3 候批）∥透视镜 v1 先行 → 模拟引擎实现 → API → 驾驶舱。
方法论教训入账：机制对齐≠体验对齐——产品形态必须用"画面复述"对齐，选项框式提问会框住裁决人。

**V5 — 本体运行时化决议：三座桥并入 API 层 + MCP PoC（2026-07-13，Daniel 三项全批）。**
缘起：Daniel 看透视镜 v2 后判"还缺点什么"+"实体/关系/动作在系统里如何存在、大模型如何调用，我总感觉
不托底"。诊断（代码核验）：本体 JSON（34 对象/53 关系/31 动作五要素俱全）**只被展示层引用，引擎/动作/
AI 层零引用**——本体是图纸不是发动机，两张皮靠纪律不靠机制；主通道 claude_cli 为单发合成，**模型在朗读
预取简报而非真调用工具**。外部调研（Palantir OSDK/Action Types、SQLModel/schema-driven、MCP 工具标注、
Anthropic tool-use 工程实践）结论：业界通行**混合模式**——结构性低频部分用"生成"（Palantir 亦非热更新），
权限/工具暴露用"运行时解释"。裁决：①**三座桥并入 API 层实施**（不改 V4 顺序，改 API 层的实现方式）：
桥1 一致性闸门（校验器断言本体↔表结构↔权限字典↔工具清单，不一致阻断发布，先出两张皮差异基线）；
桥2 声明驱动（actions 增 ai_executable/exposed_as_tool 字段，ROLE_PERMS/FORBIDDEN/TOOL_DEFS 从本体生成）；
桥3 结构生成（properties→Pydantic 校验+DDL）。②**MCP PoC 立即做**：本体暴露为 MCP server，验证
claude_cli 订阅通道真工具调用（成=不托底根治且不加 API 成本；安全模式不变：冻结区工具不注册、写动作
只提案）。③透视镜 v3（全局搜索/影响分析/动作↔工具联动/依赖与使用板块）**跟在 API 层后**——届时镜子
照的是真机器。新顺序：桥1+MCP PoC（并行，立即）→ API 层=桥2+桥3+MCP 正式化 → 前台驾驶舱 → 透视镜 v3。

**V9 — 驾驶舱首屏质感批评（2026-07-14，Daniel 原话："你给的是示意图还是真实的，怎么感觉很 low?"）。**
答复：真实应用非示意图（React+API 现查数据）。批评成立，诊断四点：①全景实现为五排横铺点阵+
交叉细线，无等距空间层次，偏离 V4⑤"Palantir 等距分层图"锚点（最大 low 源）②体征带用系统
emoji 图标（廉价感）③命令中心质感 token 缺失（网格/辉光/面板层次）④模拟世界缺灌域致画面空
（供应商无数据/AI 今日 0 检）。根因：B2 任务书只给功能交互规格未给视觉基准，未沿用 Streamlit
视觉升级"先概念图定审美"先例。处置：B3 视觉升级单（等距 2.5D 分层+节点聚合组块+几何 SVG 图标
+质感 token，零依赖零假数据不变）∥ F2 补灌单（sim 资金流+采购/准入域）。**画面质感标准入账：
"懂行者十分钟找不出破绽"（V4④）同样适用于视觉——截图对比验收，不达即再迭代。**

**V8 补记 — 资金流四问裁决（2026-07-14，Daniel 对四问汇报回复原话同 V8："1.同意；2.做；
3.先做，我看看再给意见"）。** 编排层解读（已明示可纠）：①四问推荐方案全同意——Payment 单对象
收付一本子（问1）/应收挂订单（问2）/Supplier.payment_terms_days 账期自动推算（问3）；
②资金流按 finance-manual-v0.11 开工；③问4 预警线未拍数——按"先做我看看"授权代定初值候调
（先例 J4-v2）：**R20=未来 14 天净流出 > $500,000**（推导：验证世界收入月均 ~$3M、物流费月均
~$165K、供应商货款为应付大头，取≈半月应付的 50% 即回款覆盖率跌破一半时触发；datagen 定标）。

**V8 — 画面二稿三裁决（2026-07-14，Daniel 亲批"1.同意；2.做；3.先做，我看看再给意见"）。**
①七掌控区全部【直投】项上画面（无删减）。②**资金流扩范围获批**——应收/应付/付款计划入本体
（新对象+数据生成+规则，一次 §3 变更）；建模粒度提案由编排层出、业务语义裁决点仍归 Daniel。
③首屏画面级实现（公司体征带+小全景+AI 工作流+协作流预留）即刻开工，Daniel 看实物再迭代
（画面迭代权保留，不视为终稿验收）。执行序：画面线先行（B1 API 聚合层→B2 前端画面），
资金流建模提案并行候裁。

**V7 — 驾驶舱画面一稿反馈三点（2026-07-14，Daniel 原话入账）。**
①**信息密度不足**："优点接近了，但还不足够，信息密度不够！整体画面给我的感觉没什么内容
（不是信息堆砌，而是我感觉还有很多重要的信息需要展现出来，但我一时也想不出来）……就是这个
画面我能够对整个公司的运营能够全面掌控的感觉。这也许是因为我们前面把范围圈定过小有关系。"
——处置：画面二稿以"全面掌控的信息版图"盘点回应（现有本体可直投 vs 需扩范围候批两栏，
替他把"想不出来的重要信息"想出来供勾选）；范围扩展项（如资金流对象）仍走 §3 人批，不擅扩。
②**多渠道人际协作后期接入**："系统除了 agent 之间交流，也是不同角色的交流，当前想法是接
飞书、企业微信、slack、邮箱等。这些可以放到后期再接进来。"——处置：入账为后期路线项
（协调域 CoordinationThread 是其本体承载雏形），驾驶舱画面预留"协作流"位置但不实现。
③**可视化分级原则**："必要的地方，重点要让可视化作出来，作出智能化的感觉出来。保持非必要
的华丽彰显（克制）。"——处置：与 V4①"重点突出+拒绝华而不实"合并为设计原则——智能感
可视化集中在 AI 工作流/异常上浮/影响传播三处（必要的华丽），其余区块信息密度优先、视觉克制。

**V6 — API 层收官三裁决（2026-07-14，Daniel 亲批，原文"1.补；2.可以；3.了解"）。**
背景：API 层五单（M1-M5）交付时三项业务语义按最保守读法先行、挂账候裁（STATUS 2026-07-14 条）。
裁决：①**裁1=补**：`risk_affects_sku`（RiskEvent→Sku N:M）补正式承载列 `affected_sku_ids`
（json 列，与 risk_affects_line 等三条 N:M 同规），declared_only 豁免退场；R14 复用
affected_po_line_ids 装 sku_id 的历史 hack 的退役与否以"评估器全绿+真值不动"为唯一约束，
执行中核实。②**裁2=可以**：MCP server 开放 6 个 exposed 写提案工具（assign_task /
propose_mitigation / create_admission_case / run_compliance_precheck / build_logistics_plan /
calculate_cost_scenario）——调用必须走 agent/tools.py 既有 dispatch（同一套 ROLE_PERMS 白名单
+ FORBIDDEN 拦截 + 审计），不建第二写路径；冻结区照旧永不注册；maker-checker 不变（工具只产
提案，审批仍人做）。③**裁3=了解**：AI 问答默认真查库通道（44-105s）维持，config/llm.yaml
一行可回退旧朗读档——不再视为待裁项。

## 5. 对象模型骨架（11 个对象）

完整属性字典是第 1 周交付物，此处定骨架和主键策略（沿用 v0.1：`*_id` 稳定主键，禁用名称做主键）。

| # | Object | 主键 | 关键属性（摘） | 状态字段 | Owner 角色 |
| --- | --- | --- | --- | --- | --- |
| 1 | Supplier | supplier_id | name, city, lead_time_days | — | 运营 |
| 2 | Sku | sku_id | name, category, unit_weight, unit_price_usd | — | 运营 |
| 3 | Customer | customer_id | name, tier, default_promise_buffer_days | — | 客户成功 |
| 4 | SalesOrder | so_id | customer_id, order_date, status | status | 客户成功 |
| 5 | SalesOrderLine | so_line_id | so_id, sku_id, qty, promised_delivery_date, line_status | line_status | 客户成功 |
| 6 | PurchaseOrder | po_id | supplier_id, sku_id, qty, po_date, expected_ready_date, status | status | 运营 |
| 7 | Shipment | shipment_id | po_ids, mode, container_no, vessel_voyage, carrier_name, origin_port, destination_port, etd, eta_initial, eta_current, customs_status, missing_docs, status | status | 运营 |
| 8 | ShipmentMilestone | milestone_id | shipment_id, event_type, event_time, new_eta, source_system, payload | — | 系统 |
| 9 | ShipmentAllocation | allocation_id | shipment_id, so_line_id, allocated_qty | — | 系统 |
| 10 | RiskEvent | risk_event_id | type, severity, shipment_id, affected_so_line_ids, detected_at, root_cause, status | status | 系统创建/运营处置 |
| 11 | Task | task_id | risk_event_id, assignee_role, action_taken, priority, due_at, status | status | 运营/经理 |

对象状态机（第 1 周细化，此处定终态集合）：

```text
Shipment:   planned → in_transit → arrived → customs → delivered
            （departed 事件触发 planned→in_transit；delayed 是派生标记，不是状态。D9/C5）
SOLine:     open → allocated → at_risk →（改期/加急批准后回迁 allocated）→ fulfilled / cancelled
            （改期非终态，二次击穿再次报警。D9/C1）
RiskEvent:  open → acknowledged → mitigating → resolved / escalated
Task:       assigned → in_progress → done / cancelled（驳回退回 assigned。D9/C6）
```

## 6. 关系模型

| Link | 源 → 目标 | Cardinality | 说明 |
| --- | --- | --- | --- |
| supplier_provides | Supplier → Sku | 1:N | v0.2 简化为单一供应商 |
| customer_places | Customer → SalesOrder | 1:N | |
| so_has_line | SalesOrder → SalesOrderLine | 1:N | |
| line_for_sku | SalesOrderLine → Sku | N:1 | |
| po_from_supplier | PurchaseOrder → Supplier | N:1 | |
| po_shipped_by | PurchaseOrder → Shipment | N:1 | 一票多 PO（D2） |
| shipment_has_milestone | Shipment → ShipmentMilestone | 1:N | 事件流 |
| allocation_joins | ShipmentAllocation → Shipment / SalesOrderLine | N:1 / N:1 | 影响传播枢纽（D3） |
| risk_on_shipment | RiskEvent → Shipment | N:1 | |
| risk_affects_line | RiskEvent → SalesOrderLine | N:M | 存 affected_so_line_ids 或关联表 |
| task_handles_risk | Task → RiskEvent | N:1 | |

影响传播查询（系统的灵魂，一条链）：

```text
ShipmentMilestone(eta_change) → Shipment → ShipmentAllocation
→ SalesOrderLine(promised_delivery_date) → SalesOrder → Customer
```

## 7. 动作闭环（6 个 Action）

每个动作按 v0.1 手册模板在第 1 周写全五要素（输入参数/权限/成功状态/失败处理/审计字段）。骨架：

| Action | 执行者 | 触发对象 | 效果 | 是否需审批 |
| --- | --- | --- | --- | --- |
| IngestMilestone | 系统 | Shipment | 写入 milestone，更新 eta_current，触发风险检测 | 否 |
| CreateRiskEvent | 系统/运营 | Shipment | 创建 RiskEvent，标记受影响 SOLine 为 at_risk | 否 |
| AssignTask | 系统/运营 | RiskEvent | 生成 Task 并派给角色 | 否 |
| ProposeMitigation | 运营 | Task | 提交处置方案：expedite / reschedule / accept_delay 三选一 + 参数 | 提交后待审批 |
| ApproveMitigation | 经理 | Task | 批准或驳回；批准则执行状态回写（改承诺日/标记加急/接受） | 是 |
| CloseRiskEvent | 运营 | RiskEvent | 记录结果与耗时，关闭事件 | 否，但校验 Task 全部终态 |

审计规则（全局）：所有 Action 写 `action_log`（actor, role, action, target_object, params, timestamp, result）。演示时审计流水是必展示项。

## 8. 风险规则 v0

只做规则引擎，不做 ML。三条规则：

| 规则 | 逻辑 | severity |
| --- | --- | --- |
| R1 延误传导 | eta_current + 清关缓冲(3d) + 尾程缓冲(2d) > promised_delivery_date | 击穿 ≤3d 为 medium，>3d 为 high，客户 tier=A 升一级 |
| R2 文件缺失 | missing_docs 非空 且 距 ETA <7d | high |
| R3 静默停滞 | in_transit 状态下连续 N 天无新 milestone（N=5） | medium |

评估口径见 §10。缓冲天数参数化，放配置文件，不硬编码。

## 9. 模拟数据规范

规模（较草稿砍半，够用即可）：

| 表 | 记录量 | 备注 |
| --- | --- | --- |
| suppliers | 10 | |
| skus | 20 | |
| customers | 15 | 含 tier A/B/C |
| sales_orders / so_lines | 300 / 800 | |
| purchase_orders | 250 | |
| shipments | 120 | 覆盖 4 个月时间窗 |
| shipment_milestones | 600-1,200 | 每票 5-10 条业务事件（D10 修订：原估含位置流水，与事件枚举不符） |
| allocations | 900 | |
| injected_noise_log | 与注入的数据质量噪声 1:1 | 重复/乱序/空值/名称不一致等，引擎**不应**为其创建 RiskEvent |
| expected_risk_events | 与注入的应检风险 1:1 | 延误击穿、文件缺失、停滞等，引擎**必须**检出，KPI 只对这张表算（D6） |

必须注入的噪声（每类给出注入比例，写进生成器配置）：

```text
ETA 多次变更（30% shipment 至少 1 次，10% 达 3 次以上）
milestone 重复上报（5%）
milestone 乱序到达（5%）
状态冲突：shipment 状态与最新 milestone 矛盾（8%）
文件缺失（10% shipment）
供应商名称在 PO 系统与运输系统写法不一致（20% supplier，练 entity resolution）
关键字段空值：vessel_voyage / carrier（10%）
一票延误击穿多客户承诺日（人为设计 ≥15 个这样的案例，演示用）
```

Entity resolution 练习**限时 1 周内嵌在 W3**，产出映射表即止，不造框架。

## 10. 评估与验收 KPI

全部可测，对着 ground truth 算：

| 指标 | 目标 | 测法 |
| --- | --- | --- |
| 风险查全率 Recall | ≥95%（高危漏判 = 0） | 检出 RiskEvent / expected_risk_events |
| 风险查准率 Precision | ≥85% | 检出的 RiskEvent 与 expected_risk_events 匹配率（自动计算），另人工抽验 30 条防评估脚本本身出错；被 injected_noise_log 触发的误报单独统计 |
| 影响定位准确率 | 100% | 抽 15 个设计案例，受影响 SOLine 集合与注入时的预期完全一致 |
| 闭环完整性 | 100% | 每个 resolved RiskEvent 必须能追溯完整链：milestone→risk→task→action→审计 |
| 演示脚本 | ≤5 分钟走完 §2.1 全流程 | 录屏验收 |
| AI 阶段 | 10 个评估问题全部基于对象引用回答，0 编造 | 评估集见 W6 |

草稿中的 "SLA Impact Avoided""Manual Search Time Saved" 等指标已删除——模拟环境不可测。

## 11. 六周执行计划

原则：每周产出物可验收；进度落后砍范围（按 §3.2 优先级反向砍），不砍闭环完整性；每周最后 1 小时写复盘笔记。

### W1 建模定稿（约 10h）
- 11 个对象完整属性字典 + 状态机图
- 6 个 Action 五要素全量定义（含函数签名，D7）
- 3 角色 × 对象/动作权限矩阵
- `ontology/control-tower-ontology.json`（沿用 v0.1 JSON 格式）
- 把 §2.1 演示脚本改写为逐步断言清单 `docs/demo-assertions.md`（先写测试再建模，后续每周验收对断言打勾，不对感觉打勾）
- **验收**：从 milestone 到 close 的每一步，都能指出"哪个对象的哪个状态被哪个动作改变"；断言清单覆盖闭环六步

### W2 数据生成（约 10h）
- 生成器：`datagen/`，配置驱动（规模、噪声比例、随机种子）
- 输出 `data/raw/*.csv` + `mock_source.sqlite` + `ground_truth_events`
- 15 个演示用设计案例（含 §2.1 主案例）确定性生成，不靠随机
- **验收**：同一种子可复现；ground truth 与注入异常严格 1:1

### W3 管道与对象层（约 10h）
- 清洗 + 标准化 + supplier 名称 entity resolution（限时）
- 建 `ontology.sqlite`：对象表、link 表、allocation 表
- 数据质量报告（空值率、去重量、映射命中率）
- **验收**：影响传播链 SQL 一次 join 走通；对象数与源数据差异可解释

### W4 风险引擎与影响传播（约 10h）
- R1-R3 规则实现，RiskEvent 生成，受影响 SOLine 标记
- 对 ground truth 跑评估，输出 precision/recall 报告
- **验收**：KPI 表前三项达标；不达标先修规则再修数据，禁止改指标

### W5 动作闭环与控制塔 UI（约 12h）
- Streamlit：风险队列、Shipment/SO 详情页（含对象关系跳转）、任务处理台、审批视图、角色切换器
- 6 个 Action 落地为表单 + 状态回写 + action_log
- 时间不够时按此顺序砍：① 独立审批视图（并入任务处理台）② 角色切换器 ③ 详情页合并为一个通用对象页。**不可砍底线：风险队列 + 任务处理台 + 动作状态回写 + action_log**
- **验收**：非开发者按 §2.1 脚本 5 分钟内独立走完闭环

### W6 AI 协同层与收尾（约 12h）
- 把 6 个 Action + 3 个查询函数（查对象、查影响链、查历史）注册为 LLM tools
- 场景：解释某 RiskEvent 根因 / 推荐处置方案（proposal-only，复用 v0.1 手册 §7 护栏与输出 schema）
- 10 题评估集 + 越权/编造测试
- 终版 README、架构图、录屏 demo、项目复盘
- **验收**：AI 回答全部可溯源到对象 ID；诱导越权时拒绝

## 12. 技术栈与仓库结构

技术栈（从简，禁止中途换）：Python 3.11+，pandas，SQLite，Streamlit，规则引擎纯 Python，AI 阶段任选一家 LLM API（tool-use 即可）。不用 Airflow/dbt/图数据库——本项目学的是建模，不是工具链。

```text
数智供应链/
├── docs/
│   ├── cross-border-ontology-manual.md      # v0.1 保留
│   ├── control-tower-plan-v0.2.md           # 本文档
│   └── weekly-notes/                        # 每周复盘
├── ontology/
│   ├── sku-admission-ontology.json          # v0.1 保留
│   └── control-tower-ontology.json          # W1 产出
├── datagen/                                 # W2
├── pipeline/                                # W3
├── engine/                                  # W4 规则与影响传播
├── app/                                     # W5 Streamlit
├── agent/                                   # W6
└── data/                                    # raw / cleaned / ontology.sqlite（gitignore）
```

## 13. 主要风险与应对

| 风险 | 概率 | 应对 |
| --- | --- | --- |
| 数据生成器越写越大（最常见死法） | 高 | W2 硬时限；生成器只服务 §9 清单，多一种噪声都不加 |
| UI 打磨吞掉 W5-W6 | 高 | UI 验收标准是"走通脚本"，不是好看；美化放项目结束后 |
| Entity resolution 造轮子 | 中 | 映射表 + 简单规则即止，超 1 周立即用硬编码映射兜底 |
| 影响传播粒度返工 | 低（D1-D3 已消） | 若仍返工，说明 W1 建模验收没做实，回 W1 |
| AI 阶段发散成聊天机器人 | 中 | 只允许调用已注册 tools；评估集不过不算完成 |

## 14. v0.3 展望（不承诺，仅记录方向）

- 把 v0.1 的 SKU 准入闭环实现掉，两个闭环共享 Supplier/Sku/Customer 对象——验证 ontology 复用性，这是 Palantir 模式的核心卖点
- Container/Carrier/Invoice 对象化 + 费用异常闭环
- 规则引擎升级为风险评分模型（有 ground truth，可做监督学习）
