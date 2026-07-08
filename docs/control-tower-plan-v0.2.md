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
