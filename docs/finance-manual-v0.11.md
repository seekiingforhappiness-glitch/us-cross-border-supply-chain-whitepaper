# finance-manual v0.11 — 资金流域规格（V8-② 获批，四问裁决 2026-07-14）

> 裁决依据：决策日志 V8-② 及补记（Daniel："1.同意；2.做；3.先做，我看看再给意见"——
> 解读为：四问推荐全同意 / 资金流开工 / R20 预警线由编排层代定初值候调。解读已明示可纠。）
> 本规格是执行期依据；本体将升 0.11.0。

## 1. 对象（新增 1 + 扩展 1）

**Payment（资金往来单，收付一本子——问 1 裁决）**

| 字段 | 类型 | 说明 |
|---|---|---|
| payment_id | string PK | `PAY-` 前缀 |
| direction | enum: in / out | in=客户回款；out=付供应商货款或物流商费用 |
| counterparty_type | enum: customer / supplier / vendor | vendor=物流/费用商 |
| counterparty_id | string | 对应主键 |
| ref_type | enum: sales_order / supplier_invoice / invoice | 应收挂订单（问 2 裁决）；应付挂两类发票 |
| ref_id | string | 单据主键 |
| amount_usd | number | 金额 |
| due_date | date | 应收付日（out 由 Supplier.payment_terms_days 自动推算——问 3 裁决） |
| paid_date | date, optional | 实收付日；空=未结 |
| status | enum: scheduled / paid | 状态机唯一迁移 scheduled→paid（RecordPayment 触发）；overdue 为派生标记（as_of > due_date 且未 paid），非状态（沿 D9 先例：delayed 是派生不是状态） |
| as_of_date / created_at | | 惯例字段（D8） |

**Supplier 扩展**：+`payment_terms_days`（integer，账期天数；datagen 赋 30/45/60 档）。

## 2. 关系（新增 3 条）

| linkType | 源→目标 | 基数 | 承载 |
|---|---|---|---|
| payment_settles_supplier_invoice | Payment→SupplierInvoice | N:1 | payments.ref_id（ref_type 判别） |
| payment_settles_invoice | Payment→Invoice | N:1 | 同上 |
| payment_collects_order | Payment→SalesOrder | N:1 | 同上 |

（ref_type+ref_id 单列承载三关系——storage 声明用 column+判别式；lint D 类核对按 ref_type 分支。）

## 3. 动作（五要素入本体；enforcement 标注按桥2 惯例）

- **RecordPayment（A9 RecordPurchasePayment 转正合并，语义扩为收付通用）**：
  executors=[finance, system]；前提=ref 单据存在且金额>0、同 ref 未重复全额结清；
  效果=Payment.status→paid + paid_date + 审计；失败=拒绝并审计；enforcement=role_dict。
  注意：本系统是运营原型不接银行——"付款"是**记录事实**不是执行转账，故无需 ExecutePayment
  冻结区扩充；AI 照旧不可达（exposed_as_tool=false, ai_executable=never）。
- **ProposeCollection（新，催收提案）**：executors=[cs, finance]；AI 可提
  （exposed_as_tool=true, ai_executable=auto，走既有提案流 maker-checker）；
  前提=存在 overdue 派生的 in 向 Payment；效果=生成催收任务提案候人批。

## 4. 规则（R19-R21；真值由 datagen 注入受控）

| 规则 | 触发 | severity | KPI 目标 |
|---|---|---|---|
| R19 逾期应收 | direction=in 且 as_of > due_date+7 且未 paid | high（>$50K critical） | P/R=1.000 |
| R20 现金水位预警 | 未来 14 天 Σout.scheduled − Σin.scheduled > **$500,000**（初值代定候调：≈半月应付的 50%，推导见 V8 补记；datagen 定标使正常期不触发） | critical | P/R=1.000 |
| R21 付款异常 | 同 ref 重复 paid 记录，或 paid 金额 ≠ 单据金额（容差 $0.01） | high | P/R=1.000 |

全部 as_of 安全（D8）；不修改既有 R1-R18 与其真值。

## 5. 数据生成（datagen/finance.py，独立随机流零扰动——沿 admission 先例）

- out 向：为全部 supplier_invoices 与物流 invoices 生成 Payment（due=issue_date+账期；
  ~85% 已 paid，其余 scheduled 含少量注入逾期）
- in 向：为 sales_orders 生成回款（due=order_date+对客账期常量 30；~80% paid）
- 噪声注入（真值表 `expected_finance_risks`）：逾期应收 ×8（R19）、构造一个 14 天窗口
  净流出击穿 $500K 的密集应付段（R20 ×1-2）、重复付款 ×3 + 金额不符 ×4（R21）
- 固定种子；`datagen.verify` 增第 8 段资金流断言；同种子逐字节复现

## 6. 波及面（一次验收点内完成）

本体 0.11.0（对象/关系/动作/规则/敏感字段——amount 类对非 finance/manager 脱敏沿
sensitiveFieldRules）→ 生成器重跑（Pydantic/DDL 自动）→ build_ontology 建表 →
engine/finance_rules.py + evaluate_finance → 桥1 闸门自动覆盖新声明 → MCP/API 自动可见
（工具枚举与路由从本体派生，零手写）→ 驾驶舱钱区两个【候批】指标转【直投】→
sim 侧补灌活世界资金流（S3 备忘一并处理）。

## 7. 断言清单（骨架，执行单细化）

FA1 对象/关系/动作/规则声明齐备且 lint --strict 0；FA2 R19-R21 P/R=1.000；
FA3 RecordPayment 权限矩阵（finance 可/ops 拒且留审计）；FA4 ProposeCollection 提案流
maker-checker 走通；FA5 AI 不可达 RecordPayment（288 注入集扩充）；FA6 同种子复现；
FA7 既有十评估器零回归 + 真值 md5 不变；FA8 驾驶舱钱区新指标与手工 SQL 对照。
