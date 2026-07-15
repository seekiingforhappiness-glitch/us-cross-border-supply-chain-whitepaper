# v0.4 规划 — 费用/账单对账闭环（第三个业务场景）

版本：v0.4 ｜ 起草：2026-07-06 ｜ 状态：**已批准（F1-F5，2026-07-07"继续开发"），执行中**
本文件即 v0.4 决策日志载体：F1-F5 为 append-only 决策，变更走 AGENTS.md §3 协议。
上游：统一 ontology 0.3.0 + 决策 D1-D11 / E1-E5（全部继续有效）

## 1. 目标与验证命题

跨境物流的钱大量漏在账单里：滞箱滞港费、重复计费、费率超收、莫名其妙的附加费。
本场景实现**账单进来 → 对基准匹配 → 异常检出 → 争议/接受/转嫁处置 → 回写**的闭环。

复用命题升级（比 v0.3 更激进）：
> 费用异常**不建平行闭环**——直接复用控制塔的 RiskEvent→Task→提案→审批→关闭机制。
> 第三个场景的边际成本应低于第二个（v0.3 约 1/3，v0.4 目标 ≤1/4）。

反目标：不做真实费率/汇率（USD 单币种、模拟费率卡）；不做发票 OCR；不做付款执行。

## 2. 业务闭环

```text
承运商/货代/仓库/尾程账单到达（IngestInvoice）
→ 按 费率卡基准+shipment 上下文 匹配（MatchInvoice）
→ R4 费率超收 / R5 重复计费 / R6 计划外费用（滞箱费等）→ 生成 RiskEvent（复用 A2）
→ AssignTask（复用 A3）→ 财务提处置方案：dispute 争议 / accept 接受 / rebill 按贸易术语转嫁客户
→ 经理审批（复用 A5）→ 关闭（复用 A6），发票状态回写
```

## 3. 集成决策（F1-F5，批准后并入决策日志）

**F1 — 费用异常复用 RiskEvent/Task 闭环（本方案的灵魂）。**
新增规则 R4（费率超收：金额>基准×(1+容差)）、R5（重复计费：同柜同费种跨账单重复）、
R6（计划外费用：无基准的费种，如 detention/demurrage）。风险队列、任务处理台、审批流、
审计**零新建**——费用异常和延误风险出现在同一个队列里，按同一套机制处置。
新增的只有处置方案类型：dispute / accept_charge / rebill_customer（扩展 A4 的 PARAM_SCHEMAS）。

**F2 — Container 对象化（偿还 D5 技术债）。**
新对象 Container（container_no 主键，Shipment 1:N）。滞箱费按柜计费，没有柜对象就没法
精确对账——这正是当初"降级为属性"的代价到期。零回归策略：v0.2 shipment 保留原字段作为
"主柜"（primary），既有数据 1:1 迁移；约 15 票 FCL 升级为 2-3 柜（多柜数据只被费用场景消费）。

**F3 — 基准来自模拟费率卡（RateCard→ExpectedCost）。**
按 航线×柜型×费种 生成合同费率，ExpectedCost 按票展开。真实感字段沿用 D11
（费种代码用行业惯例：OFT/THC/DET/DEM/CHS/DOC/FSC…），金额量级参照白皮书。

**F4 — 滞箱费与延误跨场景归因。**
延误船的滞箱费是"合法但可归因"的费用：R6 检出的 detention 异常，root_cause 引用
该船的延误事实（延误天数、原 RiskEvent id）——统一本体的第三次兑现：
控制塔的历史直接解释费用异常的成因，AI 简报可以讲出"这笔滞箱费源于 8/8 的 ETA 延误"。

**F5 — incoterm 责任三档规则。**
rebill 判定只做：DDP=全部我方；DAP=目的港交货前我方、之后客户；FOB=国际段起客户。
不做合同条款级精细划分（真实世界靠合同，模拟世界三档足够教学）。

## 4. 新增建模（X1 细化，此处定骨架）

| 对象 | 主键 | 要点 |
| --- | --- | --- |
| Container | container_no | shipment_id FK、type、tare/gross、free_days（免箱期） |
| Invoice | invoice_id | vendor_type(carrier/forwarder/warehouse/last_mile)、invoice_no、shipment_id、issue_date、total_usd、status: received→under_review→disputed/approved→closed |
| InvoiceLine | invoice_line_id | invoice_id、charge_code、container_no(可空)、qty、unit_price_usd、amount_usd |
| ExpectedCost | expected_cost_id | shipment_id、charge_code、container_no(可空)、baseline_usd、source(rate_card) |

动作：IngestInvoice(系统)、MatchInvoice(系统/财务，产出差异)、DisputeCharge 等并入 A4 提案类型；
Invoice 状态机随 X1 定稿。RiskEvent 扩展：type 增 rate_overbilling / duplicate_charge /
unplanned_charge；新增 affected_invoice_line_ids 字段（可空，费用场景专用）。

## 5. 四段执行计划

- **X1 建模对齐**：manual v0.4 增补 + 统一 JSON 0.4.0 + 断言清单（~20 条，
  含设计案例：超收/重复/滞箱归因/rebill-DDP/rebill-FOB/合法账单不误报）
- **X2 数据**：费率卡 + ~300 账单/~1000 行 + Container 迁移与多柜升级 + 注入异常与
  ground truth（独立随机流 seed+2000）+ pipeline 建表 + **八项零回归**
- **X3 引擎/动作/UI**：R4-R6（as_of 安全）+ 提案类型扩展 + 费用工作台（发票明细+异常同队列）
  + 无头闭环测试
- **X4 AI/收官**：get_invoice_context + 费用简报（含 F4 跨场景归因叙事）+ 评估 +5 题
  （红线：诱导"直接把争议账单标记为已批准"）+ 复盘更新

## 6. KPI

| 指标 | 目标 |
| --- | --- |
| R4-R6 查全/查准（对 ground truth） | Recall≥95%（高额异常漏判=0）、Precision≥85% |
| 设计案例（含"合法账单不误报"反例） | 100% |
| incoterm 责任判定（设计案例） | 100% |
| 复用性 | RiskEvent/Task/审批/审计零新建；八评估器零回归 |
| 跨场景归因 | 滞箱异常 100% 关联到对应延误事实（F4） |

## 7. 风险

| 风险 | 应对 |
| --- | --- |
| Container 迁移打破控制塔（最大） | 主柜字段保留，Container 表只增不改；X2 全量回归为硬门 |
| 费率卡做成定价引擎 | 一张静态表，费种×航线×柜型，不做时效性/合同版本 |
| RiskEvent 字段膨胀 | 只加 affected_invoice_line_ids 一个可空字段，其余复用 |
