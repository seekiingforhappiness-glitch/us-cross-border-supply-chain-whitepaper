# 费用对账 Ontology 增补手册 v0.4

场景：费用/账单对账闭环 ｜ 依据：cost-plan-v0.4（F1-F5）｜ 状态：X1 提案稿
只写增量；未提及的约定（主键、审计、UTC、as_of）全部沿用 v0.2/v0.3。

---

## 1. 新对象（4 个）

### 1.1 Container ｜ 主键 container_no ｜ Owner: 运营（F2，偿还 D5 技术债）

| 属性 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| container_no | string | 是 | ISO 6346 含校验位（沿用 W2 生成器） |
| shipment_id | string | 是 | → Shipment（1:N，一票多柜） |
| container_type | enum | 是 | 40HC / 40GP / 20GP |
| is_primary | boolean | 是 | 迁移标记：v0.2 shipment 原柜号字段对应的柜 |
| free_days | number | 是 | 免箱期（5-7 天），DET 计费与归因的基准 |
| gross_weight_kg / volume_cbm | number | 是 | 按柜拆分 |

零回归约定：shipment.container_no 等原字段保留（=主柜），控制塔读法不变；
Container 表只增不改。DQ：每票 ≥1 柜且恰一个 primary；校验位合法。

### 1.2 Invoice ｜ 主键 invoice_id ｜ Owner: 财务

| 属性 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| invoice_id | string | 是 | INV-2026-%05d |
| vendor_type | enum | 是 | carrier / forwarder / warehouse / last_mile |
| vendor_name | string | 是 | 开票方（船司名/货代名…） |
| vendor_invoice_no | string | 是 | 对方票号（真实对账的引用键） |
| shipment_id | string | 是 | → Shipment |
| issue_date | date | 是 | |
| currency | enum | 是 | USD（F 反目标：单币种） |
| total_usd | number | 是 | =Σ行金额（DQ 对账） |
| status | enum | 是 | 见状态机 §2 |

### 1.3 InvoiceLine ｜ 主键 invoice_line_id ｜ Owner: 财务

| 属性 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| invoice_line_id | string | 是 | IL-%06d |
| invoice_id | string | 是 | → Invoice |
| charge_code | enum | 是 | OFT 海运费 / THC 码头操作 / DOC 文件费 / FSC 燃油附加 / CUS 清关费 / DTY 关税 / WHS 仓库操作 / STO 仓储 / LMD 尾程派送 / DET 滞箱 / DEM 滞港 / CHS 车架 / ACC 地址更正 |
| container_no | string | 否 | 柜级费种（THC/DET/DEM/CHS）必填 |
| qty / unit_price_usd / amount_usd | number | 是 | amount = qty × unit_price（DQ） |

### 1.4 ExpectedCost ｜ 主键 expected_cost_id ｜ Owner: 系统（F3 基准）

| 属性 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| expected_cost_id | string | 是 | EC-%06d |
| shipment_id | string | 是 | → Shipment |
| charge_code | enum | 是 | 同上；**DET/DEM/ACC/CHS 无基准**（计划外费种，R6 的判据） |
| container_no | string | 否 | 柜级基准按柜展开 |
| baseline_usd | number | 是 | 来自费率卡（航线×柜型×费种） |
| source | string | 是 | "rate_card"（v0.4 唯一来源） |

## 2. Invoice 状态机（4 态）

```text
received --MatchInvoice(无异常)--> approved                （终态：照付）
received --MatchInvoice(有异常)--> under_review
under_review --A5 批准 dispute--> disputed                  （终态：进入争议流程，本系统边界外追款）
under_review --A5 批准 accept_charge / rebill_customer--> approved
```

## 3. 风险规则 R4-R6（F1：复用 RiskEvent，type 扩展）

聚合键沿用 A2 语义：(shipment_id, type) 合并。新增字段 affected_invoice_line_ids（可空）。
affected_value_usd = **异常金额**（超收部分/重复金额/计划外金额），不是账单总额。

| 规则 | type | 逻辑 | severity |
| --- | --- | --- | --- |
| R4 费率超收 | rate_overbilling | line.amount > baseline×(1+tol)，tol=5%（配置） | 超收 >20% high，否则 medium |
| R5 重复计费 | duplicate_charge | 同 (shipment, charge_code, container) 出现 >1 行（跨账单或同账单），首行外均计重复 | high |
| R6 计划外费用 | unplanned_charge | 行费种无 ExpectedCost 基准（DET/DEM/ACC/CHS） | 金额>500 high，否则 medium |

**F4 跨场景归因**：R6 检出 DET/DEM 且 shipment.delay_days>0 时，root_cause 必须包含
延误事实（"源于 ETA 延误 N 天"+ 关联既有延误 RiskEvent id 若存在）——统一本体的叙事兑现。

## 4. 动作扩展（复用为主）

- **IngestInvoice / MatchInvoice**（系统）：X2 数据生成视为已摄入；引擎 detect 阶段执行
  match（写审计 actor=engine），无异常发票直接 approved。
- **A4 ProposeMitigation 提案类型扩展**（PARAM_SCHEMAS 增三项）：
  - `dispute`：{reason, disputed_amount_usd}
  - `accept_charge`：{reason}
  - `rebill_customer`：{rebill_amount_usd, incoterm_basis}
- **A5 审批新增 G4 incoterm 责任门禁**（F5，P4 勘误对齐 Shipment 实际枚举 FOB/CIF/DDP）：
  rebill_customer 仅当受影响行的费种 ⊆ 该票 incoterm 的可转嫁集合：
  - DDP → ∅（门到门全我方，rebill 一律拒绝）
  - CIF → {DTY, CUS, WHS, STO, LMD, DET, DEM, CHS, ACC}（目的港起买方责任）
  - FOB → {OFT, FSC, THC, DOC, DTY, CUS, WHS, STO, LMD, DET, DEM, CHS, ACC}（装船起买方责任）
- **A5 批准的发票侧回写**：dispute→invoice.status=disputed；accept/rebill→approved。
- A3/A6 原样复用；风险队列/任务处理台 UI 自动出现新类型（F1 的直接收益）。

## 5. 权限增量

费用异常处置提案（dispute/accept/rebill）：finance（与 ops 并列加入 A4 执行者）；
审批仍仅 manager。Invoice/InvoiceLine/ExpectedCost 金额字段：全角色可见
（对账数据本就是财务工作对象；成本敏感规则只约束 CostScenario——注意区分）。
AI 角色（ops）可读发票用于解释，rebill/dispute 提案工具向 AI 开放与否：**开放 propose，
禁止 approve**（与 v0.2 E5 一致）。

## 6. 勘误与补充（append-only）

- **P1**：RiskEvent 增可空字段 affected_invoice_line_ids（F1 最小侵入）
- **P2**：type 枚举扩展 rate_overbilling / duplicate_charge / unplanned_charge；
  rule_id 枚举扩展 R4/R5/R6
- **P3**：A4 执行者扩展为 ops/cs/finance（原 ops/cs）——费用提案属财务
- **P4**：G4 矩阵键对齐 Shipment.incoterm 实际枚举（FOB/CIF/DDP）；plan §3-F5 的 DAP
  表述系笔误（DAP 是准入 LogisticsPlan 的枚举，不是控制塔 Shipment 的）
