# 准入报价 Ontology 增补手册 v0.3

场景：SKU 准入与报价决策闭环 ｜ 依据：v0.1 手册（领域规格）+ admission-plan-v0.3（决策 E1-E5）
状态：V1 提案稿。与控制塔共享同一 ontology 存储，本文档只写**增量**——控制塔部分见
control-tower-ontology-manual.md，未提及的约定（主键策略、审计、UTC 时间）全部沿用。

---

## 1. 共享对象扩展（E1/E2，全部可空以保护控制塔零回归）

### 1.1 Sku 新增属性

| 属性 | 类型 | 敏感 | 说明 |
| --- | --- | --- | --- |
| sku_status | enum | 否 | **candidate / active**。现有目录 SKU=active；准入案件针对 candidate；approved 后可转 active（两场景的生命周期咬合点） |
| declared_value_usd | number | 否 | 申报价值（准入建模用） |
| package_weight_kg | number | 否 | 包装重量 |
| package_l_cm / package_w_cm / package_h_cm | number | 否 | 包装尺寸 |
| battery_flag / food_contact_flag / children_product_flag | boolean | 否 | 监管触发三标记（驱动 PGA 判定） |
| material / use_case / origin_country | string | 否 | 材质/用途/原产国 |
| platform | enum | 否 | amazon / walmart / tiktok_shop / shopify / other |

active 老 SKU 上述字段可空，语义="当年准入时未数字化"（真实遗留数据形态）。

### 1.2 Customer 新增属性

| 属性 | 类型 | 敏感（可见角色） | 说明 |
| --- | --- | --- | --- |
| business_model | enum | 否 | platform_seller / brand_dtc / trader / service_provider / other |
| sales_channel | string | 否 | 主要销售渠道 |
| ior_capability | enum | 否 | has_ior / needs_partner / unknown——**DDP 门禁的判据（G2）** |
| broker_status | enum | 否 | has_broker / needs_broker / unknown |
| credit_terms | string | **finance/manager** | 账期 |
| risk_tier | enum | **finance/manager** | low / medium / high / critical（区别于控制塔的 tier A/B/C：那是商务分级，这是信用风险） |

### 1.3 Supplier 新增属性

| 属性 | 类型 | 敏感（可见角色） | 说明 |
| --- | --- | --- | --- |
| factory_audit_status | enum | 否 | not_started / pending / passed / failed / waived |
| compliance_docs_status | enum | 否 | missing / partial / provided / verified / rejected |
| uflpa_risk_flag | boolean | **compliance/manager** | UFLPA 风险内部标记 |
| origin_evidence_status | enum | 否 | missing / provided / verified / rejected |

## 2. 新对象（4 个）

### 2.1 AdmissionCase ｜ 主键 admission_case_id ｜ Owner: 销售创建/经理负责

| 属性 | 类型 | 必填 | 敏感 | 说明 |
| --- | --- | --- | --- | --- |
| admission_case_id | string | 是 | 否 | AC-2026-%04d |
| case_title | string | 是 | 否 | |
| customer_id / sku_id | string | 是 | 否 | → Customer / Sku(candidate) |
| request_type | enum | 是 | 否 | new_sku / ddp_quote / dap_quote / plan_review |
| incoterm_candidate | enum | 是 | 否 | FOB / DAP / DDP / tbd |
| target_launch_date | date | 是 | 否 | |
| monthly_order_estimate | number | 是 | 否 | 预估月单量 |
| risk_level | enum [derived] | — | 否 | 合规发现 severity 取最大，动作重算 |
| status | enum | 是 | 否 | 见状态机 §3 |
| decision | enum | 否 | 否 | approve / quote_with_conditions / reject / more_info |
| decision_reason | string | 否 | 否 | 审批/拒接理由，终态必填 |
| conditions | string | 否 | **manager/finance** | 附条件报价的条件 |

### 2.2 ComplianceFinding ｜ 主键 compliance_finding_id ｜ Owner: 合规

| 属性 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| compliance_finding_id | string | 是 | CF-%05d |
| admission_case_id | string | 是 | → AdmissionCase |
| finding_title | string | 是 | |
| finding_type | enum | 是 | hts / pga / labeling / certification / origin / uflpa / ad_cvd / section_301 / platform_rule |
| severity | enum | 是 | low / medium / high / critical |
| hts_candidate | string | 否 | 候选 HTS（模拟值，如 8504.40.95） |
| pga_agency | enum | 否 | CBP / FDA / FCC / CPSC / EPA / USDA / none |
| required_document | string | 否 | 所需文件 |
| evidence_status | enum | 是 | missing / provided / verified / rejected |
| recommendation | enum | 是 | accept / more_docs / dap_only / reject / escalate |

### 2.3 LogisticsPlan ｜ 主键 logistics_plan_id ｜ Owner: 运营

| 属性 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| logistics_plan_id | string | 是 | LP-%05d |
| admission_case_id | string | 是 | → AdmissionCase |
| plan_name | string | 是 | |
| route_type | enum | 是 | express / air_freight / ocean_fcl / ocean_lcl / warehouse_fulfillment |
| incoterm | enum | 是 | FOB / DAP / DDP |
| origin_port_locode / destination_port_locode | enum | 是 | 沿用控制塔 LOCODE 枚举（D11 对齐） |
| us_warehouse_region | enum | 是 | west / central / east / platform |
| last_mile_method | enum | 是 | UPS / FedEx / USPS / OnTrac / LTL / platform |
| estimated_transit_days | number | 是 | |
| sla_risk | enum | 是 | low / medium / high |
| operational_notes | string | 否 | |

### 2.4 CostScenario ｜ 主键 cost_scenario_id ｜ Owner: 财务

| 属性 | 类型 | 必填 | 敏感 | 说明 |
| --- | --- | --- | --- | --- |
| cost_scenario_id | string | 是 | 否 | CS-%05d |
| logistics_plan_id | string | 是 | 否 | → LogisticsPlan |
| scenario_type | enum | 是 | 否 | conservative / base / optimistic |
| quote_price_usd | number | 是 | **finance/manager** | 报价（v0.1 缺此字段无法算毛利，V1 补充，见 §8-N1） |
| product_cost_usd 等 9 项成本 | number | 是 | **finance/manager** | product / first_mile / international_freight / duty_tax / customs_brokerage / warehouse / last_mile / returns_allowance / risk_buffer |
| gross_margin_usd | number [derived] | — | **finance/manager** | quote − Σ成本 |
| gross_margin_rate | number [derived] | — | **finance/manager** | margin / quote |

## 3. AdmissionCase 状态机（7 态）

```text
draft --RunCompliancePrecheck--> in_precheck --BuildLogisticsPlan--> plan_ready
plan_ready --CalculateCostScenario--> priced
priced --ApproveQuoteDecision--> approved / quote_with_conditions   （终态）
任意非终态 --RejectOrRequestMoreInfo(reject)--> rejected             （终态）
任意非终态 --RejectOrRequestMoreInfo(more_info)--> needs_more_info
needs_more_info --RunCompliancePrecheck--> in_precheck              （补资料回流，非终态）
```

与控制塔 SOLine 的 C1 同理：needs_more_info 不是终态，回流后继续走。终态仅
approved / quote_with_conditions / rejected。

## 4. 新增关系

| Link | Cardinality | 说明 |
| --- | --- | --- |
| case_has_sku (AC→Sku) | N:1 | 案件针对 candidate SKU |
| case_for_customer (AC→Customer) | N:1 | **复用控制塔同一 Customer 对象** |
| case_has_finding (AC→CF) | 1:N | |
| case_has_plan (AC→LP) | 1:N | 一案件可多方案 |
| plan_has_scenario (LP→CS) | 1:N | 一方案三情景 |

（v0.1 的 sku_supplied_by N:M 由目录 Sku.supplier_id 承载，v0.3 维持单一供应商简化——D2 同理。）

## 5. 动作五要素（6 个，B1-B6）

统一沿用 app/actions.py 模式：权限→前置→成功→失败→审计，全部写 action_log，
返回 {ok, object_id, side_effects, error}。签名即 AI 工具接口（D7 同理）。

### B1 CreateAdmissionCase — 销售

```python
create_admission_case(con, customer_id, sku_id, request_type, incoterm_candidate,
                      target_launch_date, monthly_order_estimate, actor, role)
```
前置：customer/sku 存在且 sku_status=candidate。成功：AC=draft。
失败：对 active SKU 建案 → 拒绝（已在售商品走复核流程属 v0.4）。

### B2 RunCompliancePrecheck — 合规

```python
run_compliance_precheck(con, admission_case_id, findings: list[dict], actor, role)
```
前置：AC ∈ {draft, in_precheck, needs_more_info}；每条 finding 过 §2.2 schema；
finding_type=hts 时必带 hts_candidate。成功：写入/更新 CF，AC→in_precheck，
risk_level=各 finding severity 取最大。失败：schema 不过 → 整体拒绝（不落半截）。

### B3 BuildLogisticsPlan — 运营

```python
build_logistics_plan(con, admission_case_id, plan: dict, actor, role)
```
前置：AC ∈ {in_precheck, plan_ready}；**DDP 门禁（G1 前半）**：incoterm=DDP 要求
(a) customer.ior_capability=has_ior，(b) 存在 hts_candidate 非空的 CF。
成功：建 LP，AC→plan_ready。失败：DDP 缺 IOR/HTS → 拒绝并注明缺项。

### B4 CalculateCostScenario — 财务

```python
calculate_cost_scenario(con, logistics_plan_id, scenario: dict, actor, role)
```
前置：LP 存在；AC ∈ {plan_ready, priced}；**DDP 成本门禁**：DDP 方案的情景要求
duty_tax_usd>0 且 customs_brokerage_usd>0 且 risk_buffer_usd>0。
成功：建 CS（毛利现算现存），AC→priced。失败：DDP 缺税费/风险金 → 拒绝。

### B5 ApproveQuoteDecision — 仅经理

```python
approve_quote_decision(con, admission_case_id, approved_logistics_plan_id,
                       approved_cost_scenario_id, decision, decision_reason,
                       conditions, actor, role)
```
前置（三重硬门禁，plan §3-E4）：
- **G1**：存在 severity=critical 且 evidence_status≠verified 的 CF → 禁批
- **G2**：批准的 LP 为 DDP 且 customer.ior_capability≠has_ior → 禁批
- **G3**：AC≠priced 或指定 CS 不存在 → 禁批
- **M1 maker-checker**：若可从所选成本情景推断报价提案 actor，则审批 actor 不得与其相同
成功：AC→approved / quote_with_conditions，写 decision/reason/conditions；
若 approve 则 sku_status: candidate→active（E1 生命周期咬合）。
失败：任一门禁触发 → 拒绝并审计（这是准入侧的 B4/B5 断言）。

### B6 RejectOrRequestMoreInfo — 合规(more_info) / 经理(reject)

```python
reject_or_request_more_info(con, admission_case_id, decision, missing_documents,
                            rejection_reason, actor, role)
```
前置：AC 非终态；reject 必填 rejection_reason；more_info 必填 missing_documents。
角色门禁：reject 仅 manager；more_info 仅 compliance/manager。
成功：AC→rejected / needs_more_info。

## 6. 权限矩阵（6 角色 = 控制塔 3 + 新增 3）

| | sales | compliance | ops | finance | manager | cs |
| --- | --- | --- | --- | --- | --- | --- |
| B1 建案 | ✓ | — | — | — | — | — |
| B2 合规预审 | — | ✓ | — | — | — | — |
| B3 物流方案 | — | — | ✓ | — | — | — |
| B4 成本情景 | — | — | — | ✓ | — | — |
| B5 审批 | — | — | — | — | ✓ | — |
| B6 补资料/拒接 | — | more_info | — | — | ✓ | — |
| A3-A6（控制塔） | — | — | 原有 | — | 原有 | 原有 |

字段级规则（并集，UI 与 AI 同规）：成本/报价/毛利 → finance/manager；
credit_terms/risk_tier → finance/manager；uflpa_risk_flag → compliance/manager；
Customer.tier(ABC) → cs/manager（原规则不变）。渲染空值必须显示"无权查看"。

## 7. AI 增量（E5，V4 实现）

新查询工具 get_admission_context(admission_case_id)（按角色脱敏）；
确定性 admission briefing 输出 v0.1 §7 schema（risk_level / missing_documents /
hts_candidates / recommended_route / pricing_risks / needs_human_approval / confidence）；
**approve_quote_decision / reject_or_request_more_info 永不注册给 AI**。
红线（v0.1 §7 禁止行为）：不编造 HTS；不把 null 解释为无风险；不越权读成本。

## 8. 本手册相对 v0.1 的勘误与补充（append-only）

- **N1**：CostScenario 补 quote_price_usd——v0.1 有毛利字段但无价格字段，毛利不可算
- **N2**：LogisticsPlan 港口改用 LOCODE 枚举，与控制塔 D11 对齐
- **N3**：quote_with_conditions 从 decision 值升为独立终态（v0.1 §3.4/§5.5 两处表述不一致，取 §5.5）
- **N4**：ocean_parcel 路线枚举删除（v0.2 运输语义无此模式，避免死枚举）
- **N5**：approved 时 sku_status candidate→active（v0.1 未定义准入通过后的 SKU 去向）
