# 控制塔 Ontology 操作手册 v0.2

场景：跨境订单延误风险识别与处置闭环 ｜ 依据：`docs/control-tower-plan-v0.2.md`（决策 D1-D8）

状态：**W1 提案稿，待人审批**。§8 列出的非平凡设计选择未获批准前，不得据此写代码。

---

## 1. 建模总则

1. 主键一律 `*_id`，稳定且不可变，禁用名称做主键（沿用 v0.1 primaryKeyPolicy）。
2. 所有时间戳 UTC ISO8601。所有引擎评估与 UI 渲染接受显式 `as_of_date` 参数（D8）。
3. **事件不可变**：`ShipmentMilestone` 只增不改不删。状态是从事件流派生的快照。
4. **派生属性显式标注**：标 `[derived]` 的属性由管道/引擎重算，任何动作不得直接写入。
5. 乱序事件不回退状态：仅当 `event_time` 晚于该字段最近一次生效事件时间才更新状态（详见 Action A1）。

## 2. 对象字典（11 个对象）

### 2.1 Supplier ｜ 主键 supplier_id ｜ Owner: 运营

| 属性 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| supplier_id | string | 是 | 稳定主键 |
| supplier_name | string | 是 | 标准名（entity resolution 后的规范名） |
| city | string | 是 | 城市 |
| lead_time_days | number | 是 | 常规交期，>0 |

DQ 规则：id 唯一；lead_time_days > 0；源系统别名映射存 pipeline 映射表，不进对象。

### 2.2 Sku ｜ 主键 sku_id ｜ Owner: 运营

| 属性 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| sku_id | string | 是 | 稳定主键 |
| sku_name | string | 是 | 展示名 |
| category | enum | 是 | charger / cable / earbuds / phone_case |
| unit_price_usd | number | 是 | 单价，用于影响金额计算 |

DQ 规则：id 唯一；unit_price_usd > 0。

### 2.3 Customer ｜ 主键 customer_id ｜ Owner: 客户成功

| 属性 | 类型 | 必填 | 敏感 | 说明 |
| --- | --- | --- | --- | --- |
| customer_id | string | 是 | 否 | 稳定主键 |
| customer_name | string | 是 | 否 | 展示名 |
| tier | enum | 是 | **是（仅 cs/manager 可见）** | A / B / C，驱动风险升级 |
| us_state | string | 是 | 否 | 收货州 |

DQ 规则：id 唯一；tier 必填（R1 规则依赖）。

### 2.4 SalesOrder ｜ 主键 so_id ｜ Owner: 客户成功

| 属性 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| so_id | string | 是 | 稳定主键 |
| customer_id | string | 是 | → Customer |
| order_date | date | 是 | 下单日 |
| status | enum [derived] | 是 | open / in_fulfillment / fulfilled / cancelled，由行状态聚合重算 |

DQ 规则：id 唯一；至少 1 条行；status 不得被动作直接写。

### 2.5 SalesOrderLine ｜ 主键 so_line_id ｜ Owner: 客户成功

| 属性 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| so_line_id | string | 是 | 稳定主键 |
| so_id | string | 是 | → SalesOrder |
| sku_id | string | 是 | → Sku |
| qty | number | 是 | >0 |
| promised_delivery_date | date | 是 | 当前承诺日（可被改期动作更新） |
| original_promised_date | date | 是 | 初始承诺日，创建后不可变 |
| reschedule_count | number | 是 | 默认 0，每次批准改期 +1 |
| line_status | enum | 是 | 见状态机 §3.2 |

DQ 规则：qty > 0；promised ≥ order_date；original_promised_date 不可变。

### 2.6 PurchaseOrder ｜ 主键 po_id ｜ Owner: 运营

| 属性 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| po_id | string | 是 | 稳定主键 |
| supplier_id | string | 是 | → Supplier |
| sku_id | string | 是 | 单 SKU 采购（D2 简化） |
| qty | number | 是 | >0 |
| po_date | date | 是 | |
| expected_ready_date | date | 是 | 预计齐货日 |
| status | enum | 是 | placed / ready / shipped / closed / cancelled |

DQ 规则：id 唯一；expected_ready_date ≥ po_date。

### 2.7 Shipment ｜ 主键 shipment_id ｜ Owner: 运营

| 属性 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| shipment_id | string | 是 | 稳定主键 |
| mode | enum | 是 | ocean_fcl / ocean_lcl |
| container_no | string | 否 | 降级属性（D5），可空（噪声） |
| vessel_voyage | string | 否 | 降级属性，可空（噪声） |
| carrier_name | string | 否 | 降级属性，可空（噪声） |
| origin_port | enum | 是 | yantian / shekou / ningbo |
| destination_port | enum | 是 | los_angeles / long_beach |
| destination_warehouse | string | 是 | 美国仓名称（不对象化） |
| etd | date | 是 | 预计/实际离港日 |
| eta_initial | date | 是 | 首次 ETA，创建后不可变 |
| eta_current | date | 是 | 当前 ETA，仅 A1 可更新 |
| ata | date | 否 | 实际到港日 |
| customs_status | enum | 是 | not_filed / filed / hold / released |
| missing_docs | list | 是 | ⊆ {commercial_invoice, packing_list, bill_of_lading, isf}，空列表=齐 |
| expedite_flag | boolean | 是 | 默认 false，批准加急后置 true |
| delay_days | number [derived] | — | eta_current − eta_initial |
| status | enum | 是 | 见状态机 §3.1 |

DQ 规则：eta_initial ≥ etd；eta_current 只能由 A1 更新；delay_days 派生不可写。

### 2.8 ShipmentMilestone ｜ 主键 milestone_id ｜ Owner: 系统（不可变事件）

| 属性 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| milestone_id | string | 是 | 稳定主键 |
| shipment_id | string | 是 | → Shipment |
| event_type | enum | 是 | booking_confirmed / departed / eta_change / transshipment / arrived / customs_filed / customs_hold / customs_released / delivered |
| event_time | datetime | 是 | 业务发生时间 |
| new_eta | date | 否 | 仅 eta_change 必填 |
| source_system | enum | 是 | carrier_edi / forwarder_portal / manual |
| payload | json | 否 | 原始报文 |
| ingested_at | datetime | 是 | 摄入时间（≠event_time，可乱序） |
| is_duplicate | boolean [derived] | — | 管道判重标记，重复事件保留但不触发副作用 |

DQ 规则：append-only；eta_change 必带 new_eta；(shipment_id, event_type, event_time, source_system) 组合重复即标 is_duplicate。

### 2.9 ShipmentAllocation ｜ 主键 allocation_id ｜ Owner: 系统（D3 枢纽对象）

| 属性 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| allocation_id | string | 是 | 稳定主键 |
| shipment_id | string | 是 | → Shipment |
| so_line_id | string | 是 | → SalesOrderLine |
| allocated_qty | number | 是 | >0 |

DQ 规则：同一 so_line 的 Σallocated_qty ≤ line.qty；分配的 sku 必须在该 shipment 所载 PO 的 sku 集合内。

### 2.10 RiskEvent ｜ 主键 risk_event_id ｜ Owner: 系统创建 / 运营处置

| 属性 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| risk_event_id | string | 是 | 稳定主键 |
| type | enum | 是 | delay_breach / docs_missing / stalled |
| rule_id | enum | 是 | R1 / R2 / R3 |
| severity | enum | 是 | medium / high / critical（tier=A 升一级） |
| shipment_id | string | 是 | → Shipment |
| affected_so_line_ids | json list | 是 | 受影响行（见 §8 选择 C3） |
| affected_value_usd | number [derived] | — | Σ(allocated_qty × unit_price) |
| detected_at | date | 是 | 检出时的 as_of_date |
| root_cause | string | 是 | 规则给出的机器可读原因 |
| status | enum | 是 | 见状态机 §3.3 |
| resolved_at | date | 否 | |
| outcome | enum | 否 | mitigated / accepted_delay / false_alarm / escalated |
| resolution_summary | string | 否 | 关闭时必填 |

DQ 规则：同一 (shipment, type) 不允许并存两个非终态事件（A2 负责合并升级）。

### 2.11 Task ｜ 主键 task_id ｜ Owner: 运营/经理

| 属性 | 类型 | 必填 | 敏感 | 说明 |
| --- | --- | --- | --- | --- |
| task_id | string | 是 | 否 | 稳定主键 |
| risk_event_id | string | 是 | 否 | → RiskEvent |
| title | string | 是 | 否 | |
| assignee_role | enum | 是 | 否 | ops / cs / manager |
| priority | enum | 是 | 否 | P1 / P2 / P3 |
| due_at | date | 是 | 否 | |
| proposed_action | enum | 否 | 否 | expedite / reschedule / accept_delay |
| proposal_params | json | 否 | **est_cost_usd 仅 ops/manager 可见** | 按动作类型定 schema，见 A4 |
| approval_status | enum | 否 | 否 | pending / approved / rejected |
| approved_by_role | enum | 否 | 否 | |
| action_taken | string | 否 | 否 | 最终执行的动作摘要 |
| status | enum | 是 | 否 | 见状态机 §3.4 |

DQ 规则：一个 RiskEvent 同时最多 1 个非终态 Task（见 §8 选择 C4）。

## 3. 状态机

只有列出的迁移是合法的；引擎与 UI 必须拒绝其余迁移。

### 3.1 Shipment（5 态）

```text
planned --A1(departed)--> in_transit --A1(arrived)--> arrived
arrived --A1(customs_filed/hold/released)--> customs --A1(delivered)--> delivered
```

说明：plan 骨架中的 "departed" 独立状态取消，departed 事件直接触发 planned→in_transit（见 §8 选择 C5）。delayed 不是状态，是 delay_days>0 的派生标记。

### 3.2 SalesOrderLine（5 态）

```text
open --分配(数据构建)--> allocated --A2--> at_risk
at_risk --A5(approve reschedule/expedite)--> allocated   ← 回迁，非终态
at_risk --交付确认--> fulfilled
allocated --交付确认--> fulfilled
open/allocated --取消--> cancelled
```

说明：plan 骨架把 rescheduled 列为终态，本稿改为"改期后回迁 allocated + reschedule_count+1"（见 §8 选择 C1，需批准）。终态仅 fulfilled / cancelled。

### 3.3 RiskEvent（5 态）

```text
open --A3(AssignTask)--> acknowledged --A4(ProposeMitigation)--> mitigating
mitigating --A6(Close, outcome=mitigated/accepted_delay/false_alarm)--> resolved
open/acknowledged/mitigating --A6(Close, outcome=escalated)--> escalated
```

说明：escalated 通过 A6 的 outcome 参数到达（v0.2 无独立 Escalate 动作），表示移交本系统边界外处理。

### 3.4 Task（4 态）

```text
assigned --A4(Propose)--> in_progress
in_progress --A5(approved)--> done
in_progress --A5(rejected)--> assigned   ← 驳回退回，提案留痕
assigned/in_progress --取消(风险关闭为 false_alarm 时联动)--> cancelled
```

说明：plan 骨架的 created 状态删除——A3 创建任务时必带 assignee，created 永远不会被观测到（见 §8 选择 C6）。

## 4. 关系模型

与 plan v0.2 §6 一致，此处仅补 DQ：

| Link | Cardinality | 完整性规则 |
| --- | --- | --- |
| customer_places (Customer→SO) | 1:N | SO 必有存在的 customer |
| so_has_line (SO→SOLine) | 1:N | 孤儿行禁止 |
| line_for_sku (SOLine→Sku) | N:1 | |
| supplier_provides (Supplier→Sku) | 1:N | v0.2 单一供应商 |
| po_from_supplier (PO→Supplier) | N:1 | |
| po_shipped_by (PO→Shipment) | N:1 | 一票多 PO（D2） |
| shipment_has_milestone | 1:N | append-only |
| allocation_joins (Allocation→Shipment/SOLine) | N:1 / N:1 | §2.9 DQ |
| risk_on_shipment (Risk→Shipment) | N:1 | |
| risk_affects_line (Risk→SOLine) | N:M | 经 affected_so_line_ids |
| task_handles_risk (Task→Risk) | N:1 | 最多 1 个非终态 Task |

## 5. 动作五要素（6 个 Action）

所有动作写 `action_log(actor, role, action, target_object_id, params_json, as_of_date, timestamp, result)`。函数签名即 W6 的 AI 工具接口（D7），返回统一结构 `{ok, object_id, side_effects[], error}`。

### A1 IngestMilestone

```python
ingest_milestone(shipment_id, event_type, event_time, source_system,
                 new_eta=None, payload=None, as_of_date=...) -> Result
```

- 执行者：系统
- 前置：shipment 存在；eta_change 必带 new_eta；四元组重复则写入并标 is_duplicate=true，**跳过全部副作用**
- 成功：追加 milestone；若 event_time 晚于该字段最近生效事件：更新 eta_current / customs_status / 状态机迁移；触发该 shipment 的风险检测（R1-R3，as_of=event_time 所在日）
- 失败：未知 shipment → 拒绝；非法状态迁移（如 delivered 后又 departed）→ 写入事件但不迁移状态，返回 warning 并计入数据质量报告
- 审计：source_system、是否触发规则、是否判重

### A2 CreateRiskEvent

```python
create_risk_event(shipment_id, rule_id, type, severity,
                  affected_so_line_ids, root_cause, as_of_date, actor="system") -> Result
```

- 执行者：系统（引擎）、运营（手工）
- 前置：affected_so_line_ids 非空（delay_breach 时）；同 (shipment, type) 已有非终态事件 → 不新建，合并：取更高 severity、并集受影响行，返回既有 id
- 成功：RiskEvent=open；受影响 SOLine → at_risk；计算 affected_value_usd
- 失败：行与 shipment 无 allocation 关联 → 拒绝
- 审计：rule_id、合并或新建

### A3 AssignTask

```python
assign_task(risk_event_id, assignee_role, priority, due_at, actor) -> Result
```

- 执行者：系统、运营
- 前置：RiskEvent ∈ {open}；无非终态 Task
- 成功：Task=assigned；RiskEvent → acknowledged
- 失败：已有非终态 Task → 拒绝并返回其 id；RiskEvent 已终态 → 拒绝
- 审计：assignee_role、priority

### A4 ProposeMitigation

```python
propose_mitigation(task_id, proposed_action, proposal_params, actor_role) -> Result
# params schema:
#   expedite:     {new_mode:"air", est_cost_usd, expected_new_eta}
#   reschedule:   {new_promise_date, notify_customer:true}
#   accept_delay: {reason}
```

- 执行者：运营、客户成功
- 前置：Task=assigned；params 满足对应 schema；reschedule 的 new_promise_date > 原承诺日
- 成功：Task → in_progress，approval_status=pending；RiskEvent → mitigating
- 失败：schema 校验不过 → 拒绝，Task 状态不变
- 审计：proposed_action、params（est_cost_usd 按 §6 脱敏规则记录）

### A5 ApproveMitigation

```python
approve_mitigation(task_id, decision, comment, actor_role="manager") -> Result
```

- 执行者：**仅经理**
- 前置：approval_status=pending
- 成功（approved）按动作类型回写：
  - reschedule：受影响 SOLine 的 promised_delivery_date=new_promise_date，reschedule_count+1，at_risk→allocated
  - expedite：shipment.expedite_flag=true，受影响 SOLine at_risk→allocated（简化，见 §8 选择 C2）
  - accept_delay：行保持 at_risk，等待交付；风险可以 accepted_delay 关闭
  - 共同：Task→done，action_taken 写摘要
- 成功（rejected）：Task→assigned，approval_status=rejected，提案参数保留在 action_log
- 失败：非 manager 调用 → 拒绝并审计越权尝试
- 审计：decision、comment、approved_by_role

### A6 CloseRiskEvent

```python
close_risk_event(risk_event_id, outcome, resolution_summary, actor_role) -> Result
```

- 执行者：运营
- 前置：该风险所有 Task 均终态；outcome=mitigated 时必须存在 approved 提案；outcome=false_alarm 时联动取消非终态 Task 后关闭
- 成功：RiskEvent → resolved（或 escalated），写 resolved_at / resolution_summary
- 失败：存在非终态 Task 且 outcome≠false_alarm → 拒绝
- 审计：outcome、resolution_summary

## 6. 权限矩阵（3 角色）

| | ops 物流运营 | cs 客户成功 | manager 经理 |
| --- | --- | --- | --- |
| 读对象 | 全部（Customer.tier 除外） | 全部（proposal_params.est_cost_usd 除外） | 全部 |
| A1 IngestMilestone | —（系统） | — | — |
| A2 CreateRiskEvent | ✓（手工） | — | — |
| A3 AssignTask | ✓ | — | — |
| A4 ProposeMitigation | ✓ | ✓ | — |
| A5 ApproveMitigation | — | — | ✓ |
| A6 CloseRiskEvent | ✓ | — | — |

字段级规则仅两条（刻意最小化，权限价值在 spec 不在实现）：Customer.tier 对 ops 隐藏；est_cost_usd 对 cs 隐藏。UI 渲染空值时必须显示"无权查看"，不得显示为空白（v0.1 手册教训：null ≠ 无风险）。

## 7. 风险规则接口（引擎在 W4 实现，此处锁定入参）

```python
detect_risks(ontology_db, as_of_date, config) -> list[RiskCandidate]
# R1 延误传导: shipment.eta_current + config.customs_buffer_days(3)
#              + config.lastmile_buffer_days(2) > line.promised_delivery_date
#              (沿 allocation 找行; 击穿≤3d=medium, >3d=high, tier=A 升一级)
# R2 文件缺失: missing_docs 非空 且 (eta_current − as_of_date) < 7d → high
# R3 静默停滞: status=in_transit 且 as_of_date − 最近 milestone.event_time ≥ 5d → medium
```

## 8. 非平凡设计选择（需人批准后生效）

| # | 选择 | 理由 | 代价 |
| --- | --- | --- | --- |
| C1 | SOLine 的 rescheduled 不作终态，改期后回迁 allocated | 改期后的行仍需履约，终态会使其脱离后续监控 | 与 plan §5 骨架不一致，plan 需同步勘误 |
| C2 | expedite 不新建 shipment 对象，仅置 expedite_flag 并解除行风险 | 真实加急=新建空运 shipment+重分配，会把 W5 工作量翻倍 | 演示时需口头说明简化；v0.3 再补 |
| C3 | affected_so_line_ids 用 JSON 列表，不建关联表 | 本规模（几百行）无查询压力，省一张表 | 无法 SQL 索引反查"某行涉及的风险"，UI 需扫描 |
| C4 | 一个 RiskEvent 同时只允许 1 个非终态 Task | 闭环追溯简单，演示清晰 | 无法并行多任务处置；真实场景不成立 |
| C5 | Shipment 砍掉 departed 独立状态（6 态→5 态） | departed 与 in_transit 无行为差异 | 与 plan 骨架不一致，需勘误 |
| C6 | Task 砍掉 created 状态 | A3 创建即分配，created 不可观测 | 同上 |

批准方式：在 STATUS.md 对应项打勾或答复"C1-C6 批准/其中某项改为…"。批准后我把 C1/C5/C6 勘误追加进 plan §4 决策日志（走 AGENTS.md §3 变更协议）。

## 9. W1 验收自检（对照 plan §11-W1）

- [x] 11 对象完整属性字典（§2）
- [x] 4 个状态机 + 合法迁移表（§3）
- [x] 6 动作五要素 + 函数签名（§5）
- [x] 3 角色权限矩阵（§6）
- [ ] `ontology/control-tower-ontology.json` — 待 C1-C6 批准后定稿
- [ ] `docs/demo-assertions.md` — 已产出草稿，待人验收
- [ ] 人工验收："从 milestone 到 close 每一步能指出哪个对象的哪个状态被哪个动作改变" → 见 §3+§5 交叉引用，请按 demo-assertions 走查
