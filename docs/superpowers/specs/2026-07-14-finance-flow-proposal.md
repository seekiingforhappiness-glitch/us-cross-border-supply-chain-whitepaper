# 资金流扩范围·建模提案（V8-② 获批方向，建模粒度候 Daniel 四问）

> 你批了"资金流：做"。方向定了，但**建几个对象、怎么建**是建模粒度决策（§3 归你）。
> 下面先给我推荐的最小闭环方案，再问四个业务问题——你答完，我写正式 manual 并派工。

## 推荐方案（最小闭环，一个验收点）

**新对象 1 个：Payment（资金往来单）**——收付统一建模：
`payment_id / direction（in=客户回款 out=付供应商或物流商）/ counterparty_type+id /
ref_type+ref_id（挂到 SupplierInvoice、物流 Invoice 或 SalesOrder）/ amount_usd /
due_date（该收付日）/ paid_date（实收付日）/ status: scheduled→paid，逾期=derived`

**新关系 3 条**：payment_settles_supplier_invoice / payment_settles_invoice /
payment_collects_order（应收挂订单，见问 2）

**新规则 3 条（候选，KPI 目标值你定）**：
- R19 逾期应收：客户回款超期 N 天 → 风险事件（催收任务派 cs/finance）
- R20 现金水位预警：未来 14 天 应付合计 − 预期回款 > 阈值 → 风险事件（finance）
- R21 付款异常：重复付款 / 金额与单据不符 → 风险事件（finance，maker-checker 拦截线）

**数据生成**：datagen 新模块 finance.py（固定种子；注入逾期/重复付/金额不符噪声）+ 真值表
`expected_finance_risks`；模拟世界 sim 侧同步补灌资金流实例（活世界的钱也要流动）。

**动作**：RecordPayment（finance 执行，A9 RecordPurchasePayment 从 placeholder 转正合并）；
AI 只读+提案（催收提案），付款执行永远人做（冻结区扩充：ExecutePayment 若建即入 frozen）。

**驾驶舱回填**：钱区两个【候批】指标（应收/应付水位、现金流投影）转【直投】。

## 四个业务问题（答完即开工）

**问 1（一个本子还是两个本子）**：'客户该给我的钱'和'我该付出去的钱'，你日常是当一类东西
管（一张资金往来表分收/付两栏），还是完全两套（应收一套、应付一套）？
→ 我推荐**一个本子**（Payment 单对象带 direction），字段全同、规则共用，系统更简单。

**问 2（客户的钱按什么追）**：客户欠的钱，你按"这张订单该收多少"追，还是先开发票按票追？
→ 跨境电商对客通常无发票，我推荐**按订单追**（应收挂 SalesOrder）。

**问 3（账期要不要建进系统）**：给供应商的货款一般有账期（如月结 30 天）。要系统自动按
账期推算"哪天该付谁多少"（供应商对象加 payment_terms_days 字段），还是每笔付款日期人工录？
→ 我推荐**建账期字段自动推算**（这正是现金水位预警 R20 的数据来源）。

**问 4（预警线是多少）**：R20 现金水位——"未来 14 天净流出超过多少美元"该报警？
（这是 KPI 目标值，按规矩必须你定；拍脑袋一个数即可，之后可调。）

---
答完四问 → 我写 finance-manual（正式规格）→ 派工（datagen/引擎/闸门/驾驶舱回填一条链）。
