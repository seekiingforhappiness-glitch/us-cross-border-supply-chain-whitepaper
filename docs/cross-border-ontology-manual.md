# 跨境供应链 Ontology 项目操作手册

版本：v0.1

适用范围：第一版只覆盖“SKU 准入与报价决策”闭环，不建设全量企业供应链大脑，不接入真实 ERP/WMS/平台 API，不处理密钥、生产数据或客户隐私数据。

## 1. 核心原则

本项目采用 Palantir Ontology 的落地思路：先围绕一个高价值决策闭环建模，再逐步扩展对象、动作、权限和 AI 辅助能力。第一版的目标不是把所有表复制成对象，而是让业务团队能围绕一个 SKU 回答：

- 这个 SKU 能不能接？
- 需要哪些合规资料？
- 应该走 FOB、DAP 还是 DDP？
- 头程、清关、海外仓、尾程怎么设计？
- 保守、中性、乐观三套成本情景下是否可盈利？
- 哪些风险要拒接、加价、实报实销或升级审批？

最小可用闭环：

1. 销售或方案人员创建 `AdmissionCase`。
2. 合规人员维护 `ComplianceFinding`，确认 HTS、PGA、IOR、资料缺口和拒接风险。
3. 运营人员创建 `LogisticsPlan`，给出头程、清关、仓配、尾程方案。
4. 财务人员创建 `CostScenario`，计算三情景成本和风险准备金。
5. 经理执行 `ApproveQuoteDecision` 或 `RejectOrRequestMoreInfo`。
6. AI 只输出结构化建议，不自动执行高风险写回。

## 2. Use Case Canvas

| 项目 | 第一版定义 |
| --- | --- |
| 业务场景名称 | SKU 准入与报价决策 |
| 核心业务问题 | 对一个跨境 SKU 判断是否可接、怎么走、怎么报价、风险谁承担 |
| 目标 KPI | SKU 预审周期、资料补齐率、报价准确率、异常风险命中率、审批一次通过率 |
| 主要用户角色 | 销售、合规、运营、财务、经理 |
| 关键对象 | `Sku`、`Customer`、`Supplier`、`AdmissionCase`、`ComplianceFinding`、`LogisticsPlan`、`CostScenario` |
| 关键关系 | 案件关联 SKU 和客户；SKU 关联供应商；案件产生合规发现和物流方案；物流方案产生成本情景 |
| 关键动作 | 创建案件、合规预审、构建物流方案、计算成本情景、审批报价、拒接或补资料 |
| 输入数据源 | 先使用人工录入或 CSV 样例；后续可接 ERP、WMS、OMS、平台账单、承运商账单 |
| 外部系统写回 | v0 不写回；后续通过受控 action 写回 CRM、报价单、工单或 ERP |
| 权限/合规要求 | 合同价、成本、供应商评级、客户毛利等敏感字段按角色隔离 |
| 上线最小范围 | 单个业务团队，单个目的国美国，单个准入报价流程 |
| 不做事项 | 不做真实清关申报，不自动通知客户，不自动审批 DDP，不自动写回生产系统 |
| 成功验收标准 | 一个 SKU 能完整走完资料收集、合规预审、物流方案、成本情景、报价审批 |

## 3. 对象模型

### 3.1 `Sku`

业务定义：一个准备进入美国市场或需要报价的商品。

主键：`sku_id`

Title Key：`sku_name`

核心属性：

- `sku_name`：商品名称。
- `category`：品类。
- `material`：材质。
- `use_case`：用途。
- `origin_country`：原产国。
- `declared_value_usd`：申报价值。
- `package_weight_kg`、`package_length_cm`、`package_width_cm`、`package_height_cm`：包装重量和尺寸。
- `battery_flag`、`food_contact_flag`、`children_product_flag`：监管触发标记。
- `platform`：Amazon、Walmart、TikTok Shop、Shopify 或其他。

敏感属性：采购价、供应商内部评分、利润目标。

Owner：销售提供事实，合规确认监管触发，运营确认物流约束，财务确认成本口径。

### 3.2 `Customer`

业务定义：发起准入或报价需求的客户、卖家或品牌方。

主键：`customer_id`

Title Key：`customer_name`

核心属性：

- `customer_name`：客户名称。
- `business_model`：平台卖家、品牌独立站、贸易商、服务商。
- `sales_channel`：主要销售渠道。
- `target_market`：目标市场，第一版默认美国。
- `ior_capability`：客户是否具备 Importer of Record 能力。
- `broker_status`：是否已有报关行。
- `credit_terms`：账期。
- `risk_tier`：客户风险等级。

敏感属性：账期、信用等级、历史赔付、毛利要求。

Owner：销售与财务。

### 3.3 `Supplier`

业务定义：SKU 的生产商、供货商或贸易供货主体。

主键：`supplier_id`

Title Key：`supplier_name`

核心属性：

- `supplier_name`：供应商名称。
- `country`：所在国家或地区。
- `factory_audit_status`：验厂状态。
- `compliance_docs_status`：合规文件状态。
- `uflpa_risk_flag`：UFLPA 风险标记。
- `origin_evidence_status`：原产地证据状态。
- `lead_time_days`：常规交期。

敏感属性：采购条款、供应商评级、风险备注、联系人。

Owner：采购、合规。

### 3.4 `AdmissionCase`

业务定义：围绕一个 SKU 的准入、方案和报价决策案件。

主键：`admission_case_id`

Title Key：`case_title`

核心属性：

- `case_title`：案件标题。
- `status`：`draft`、`in_precheck`、`plan_ready`、`priced`、`approved`、`rejected`、`needs_more_info`。
- `request_type`：新 SKU 准入、DDP 报价、DAP 报价、方案复核。
- `incoterm_candidate`：FOB、DAP、DDP 或待定。
- `target_launch_date`：目标上线日期。
- `monthly_order_estimate`：预估月单量。
- `risk_level`：low、medium、high、critical。
- `decision`：approve、reject、more_info、quote_with_conditions。
- `decision_reason`：审批或拒接理由。

敏感属性：报价底线、风险准备金、内部审批意见。

Owner：销售创建，经理最终负责。

### 3.5 `ComplianceFinding`

业务定义：合规预审中发现的监管、税号、文件或拒接风险。

主键：`compliance_finding_id`

Title Key：`finding_title`

核心属性：

- `finding_title`：发现标题。
- `finding_type`：HTS、PGA、labeling、certification、origin、uflpa、ad_cvd、section_301、platform_rule。
- `severity`：low、medium、high、critical。
- `hts_candidate`：候选美国 HTS。
- `pga_agency`：CBP、FDA、FCC、CPSC、EPA、USDA 或其他。
- `required_document`：所需文件。
- `evidence_status`：missing、provided、verified、rejected。
- `recommendation`：可接、补资料、只做 DAP、拒接、升级。

敏感属性：合规风险备注、拒接依据、客户争议记录。

Owner：合规。

### 3.6 `LogisticsPlan`

业务定义：面向一个案件的跨境履约路径方案。

主键：`logistics_plan_id`

Title Key：`plan_name`

核心属性：

- `plan_name`：方案名称。
- `route_type`：express、air_freight、ocean_fcl、ocean_lcl、ocean_parcel、warehouse_fulfillment。
- `incoterm`：FOB、DAP、DDP。
- `origin_port`、`destination_port`：起运和目的港。
- `us_warehouse_region`：美西、美中、美东或平台仓。
- `last_mile_method`：UPS、FedEx、USPS、OnTrac、LTL、平台仓配。
- `estimated_transit_days`：预计总时效。
- `sla_risk`：SLA 风险。
- `operational_notes`：操作备注。

敏感属性：承运商合同价、服务商内部评价。

Owner：运营。

### 3.7 `CostScenario`

业务定义：一个物流方案下的报价、成本和利润情景。

主键：`cost_scenario_id`

Title Key：`scenario_name`

核心属性：

- `scenario_name`：保守、中性、乐观或自定义。
- `scenario_type`：conservative、base、optimistic。
- `product_cost_usd`：商品成本。
- `first_mile_cost_usd`：国内段成本。
- `international_freight_usd`：国际干线成本。
- `duty_tax_usd`：关税税费。
- `customs_brokerage_usd`：清关费用。
- `warehouse_cost_usd`：海外仓成本。
- `last_mile_cost_usd`：尾程成本。
- `returns_allowance_usd`：退货准备。
- `risk_buffer_usd`：风险准备金。
- `gross_margin_usd`：毛利。
- `gross_margin_rate`：毛利率。

敏感属性：成本明细、利润、风险准备金、报价底线。

Owner：财务。

## 4. 关系模型

| Link Type | 源对象 | 目标对象 | 含义 | Cardinality |
| --- | --- | --- | --- | --- |
| `case_has_sku` | `AdmissionCase` | `Sku` | 一个准入案件围绕一个 SKU | N:1 |
| `case_for_customer` | `AdmissionCase` | `Customer` | 一个准入案件属于一个客户 | N:1 |
| `sku_supplied_by` | `Sku` | `Supplier` | 一个 SKU 由一个或多个供应商供货 | N:M |
| `case_has_compliance_finding` | `AdmissionCase` | `ComplianceFinding` | 一个案件有多条合规发现 | 1:N |
| `case_has_logistics_plan` | `AdmissionCase` | `LogisticsPlan` | 一个案件可有多个物流方案 | 1:N |
| `plan_has_cost_scenario` | `LogisticsPlan` | `CostScenario` | 一个物流方案有多个成本情景 | 1:N |

关系设计原则：

- 关系名必须表达业务语义，不使用 `related_to`、`has` 这类模糊名称。
- 如果关系本身有属性，例如影响金额、责任比例、有效期，后续应建中间对象，不塞进裸 link。
- 多对多关系必须有稳定 join 数据源或中间对象，不能靠页面临时拼接。

## 5. 动作闭环

### 5.1 `CreateAdmissionCase`

业务含义：创建一个 SKU 准入与报价案件。

输入参数：

- `customer_id`
- `sku_id`
- `request_type`
- `target_launch_date`
- `monthly_order_estimate`
- `incoterm_candidate`

权限：销售可创建；经理、合规、运营、财务可查看。

成功状态：创建 `AdmissionCase`，状态为 `draft`。

失败处理：缺少客户、SKU、目标市场、基础商品事实时返回补资料错误。

审计字段：`actor_id`、`created_at`、`source`、`reason`。

### 5.2 `RunCompliancePrecheck`

业务含义：对案件执行合规预审。

输入参数：

- `admission_case_id`
- `hts_candidate`
- `pga_agency`
- `required_documents`
- `severity`
- `recommendation`

权限：合规可提交；经理可覆盖或升级。

成功状态：创建或更新 `ComplianceFinding`，案件状态进入 `in_precheck`。

失败处理：HTS、监管触发点或资料缺口缺失时不得标记为预审完成。

审计字段：`actor_id`、`checked_at`、`evidence_url`、`reason`。

### 5.3 `BuildLogisticsPlan`

业务含义：为案件创建可报价的物流履约方案。

输入参数：

- `admission_case_id`
- `route_type`
- `incoterm`
- `origin_port`
- `destination_port`
- `us_warehouse_region`
- `last_mile_method`
- `estimated_transit_days`
- `sla_risk`

权限：运营可创建；经理可要求重做。

成功状态：创建 `LogisticsPlan`，案件状态可进入 `plan_ready`。

失败处理：DDP 方案必须已有 IOR、HTS、税费和合规预审结果；否则只能暂存为草案。

审计字段：`actor_id`、`planned_at`、`assumptions`。

### 5.4 `CalculateCostScenario`

业务含义：为物流方案创建保守、中性、乐观成本情景。

输入参数：

- `logistics_plan_id`
- `scenario_type`
- `product_cost_usd`
- `international_freight_usd`
- `duty_tax_usd`
- `warehouse_cost_usd`
- `last_mile_cost_usd`
- `returns_allowance_usd`
- `risk_buffer_usd`

权限：财务可创建和编辑；销售只能查看非敏感报价结果。

成功状态：创建 `CostScenario`，案件状态可进入 `priced`。

失败处理：DDP 情景缺少税费、清关、风险准备金时不得提交审批。

审计字段：`actor_id`、`calculated_at`、`assumption_version`。

### 5.5 `ApproveQuoteDecision`

业务含义：经理审批报价决策。

输入参数：

- `admission_case_id`
- `approved_logistics_plan_id`
- `approved_cost_scenario_id`
- `decision`
- `decision_reason`
- `conditions`

权限：经理。

成功状态：案件状态变为 `approved` 或 `quote_with_conditions`。

失败处理：存在 `critical` 合规发现、缺少成本情景、DDP 缺少 IOR 时禁止批准。

审计字段：`actor_id`、`approved_at`、`decision_reason`。

### 5.6 `RejectOrRequestMoreInfo`

业务含义：拒接案件或要求客户补充资料。

输入参数：

- `admission_case_id`
- `decision`
- `missing_documents`
- `rejection_reason`
- `next_owner`

权限：合规可要求补资料；经理可拒接。

成功状态：案件状态变为 `rejected` 或 `needs_more_info`。

失败处理：拒接必须填写明确原因；补资料必须列出缺失项。

审计字段：`actor_id`、`decided_at`、`reason`。

## 6. 权限矩阵

| 角色 | 可见对象 | 敏感属性 | 可执行动作 | 验收测试 |
| --- | --- | --- | --- | --- |
| 销售 | `Customer`、`Sku`、自己创建的 `AdmissionCase`、非敏感报价摘要 | 不可见成本底线、供应商评级、内部风险准备金 | `CreateAdmissionCase` | 尝试查看财务成本应失败或为空 |
| 合规 | `Sku`、`Supplier`、`AdmissionCase`、`ComplianceFinding` | 可见合规风险备注，不可见报价底线 | `RunCompliancePrecheck`、`RejectOrRequestMoreInfo` 中的补资料 | 可更新 HTS 和文件状态，不能审批报价 |
| 运营 | `AdmissionCase`、`LogisticsPlan`、必要 SKU 属性 | 不可见客户信用和利润底线 | `BuildLogisticsPlan` | DDP 缺合规预审时不能提交正式方案 |
| 财务 | `LogisticsPlan`、`CostScenario`、必要案件上下文 | 可见成本和利润字段 | `CalculateCostScenario` | 能提交三情景成本，不能批准最终报价 |
| 经理 | 全部对象，按业务域限制 | 可见审批所需敏感字段 | `ApproveQuoteDecision`、`RejectOrRequestMoreInfo` | 高风险 DDP 必须经理审批 |
| AI 助手 | 跟随调用用户或最小权限服务账号 | 不得绕过用户权限读取敏感字段 | 只生成 proposal，不直接执行高风险 action | 提示词诱导越权时不得返回敏感信息 |

## 7. AI Logic 护栏

第一版 AI 只做结构化建议，不自动审批、不自动发客户通知、不自动写回生产系统。

输入：

- `AdmissionCase`
- 关联 `Sku`
- 关联 `Customer`
- 关联 `ComplianceFinding`
- 关联 `LogisticsPlan`
- 关联 `CostScenario`

输出 schema：

```json
{
  "risk_level": "low | medium | high | critical",
  "missing_documents": ["string"],
  "hts_candidates": ["string"],
  "recommended_route": "string",
  "pricing_risks": ["string"],
  "needs_human_approval": true,
  "confidence": 0.0
}
```

禁止行为：

- 不编造 HTS、税率、监管结论。
- 不绕过权限读取成本、客户信用、供应商内部评分。
- 不自动批准 DDP、高风险品类、高成本方案或拒接决定。
- 不向客户发送外部通知。
- 不把 `null` 解释为“没有风险”；`null` 可能是权限不可见。

上线阈值：

- 建立至少 30 条测试案例。
- 高风险漏判率必须为 0 才能进入业务试用。
- 工具调用失败必须返回可解释错误，不得静默通过。
- 高风险动作始终需要人工审批。

## 8. 90 天推进路线

### 第 1-2 周：场景收敛

交付物：

- 完成 Use Case Canvas。
- 明确 SKU 准入报价的用户角色和审批边界。
- 输出第一版对象关系图。
- 确认哪些字段属于敏感字段。

验收标准：

- 能明确谁在什么时候对哪个对象做什么决定。
- 所有对象都有业务 owner。

### 第 3-4 周：数据与主键

交付物：

- 建立样例 CSV 或手工录入模板。
- 确认 `*_id` 主键策略。
- 定义核心字段空值、重复、格式规则。

验收标准：

- 主键唯一。
- 核心字段完整。
- 不用商品名、客户名、供应商名做主键。

### 第 5-6 周：Ontology MVP

交付物：

- 创建七个核心 object types。
- 创建六条核心 link types。
- 完成 role/action 权限矩阵。

验收标准：

- 业务用户能读懂对象名称和关系名称。
- 从 `AdmissionCase` 能跳到 SKU、客户、合规发现、物流方案和成本情景。

### 第 7-8 周：动作与应用

交付物：

- 实现六个动作的表单、状态更新和审计字段。
- 形成一个准入报价页面原型。
- 明确失败处理和重复提交处理。

验收标准：

- 一个 SKU 能完整走到审批结果。
- 每个动作都有权限、成功状态、失败处理和审计字段。

### 第 9-10 周：AI 辅助

交付物：

- 建立 AI Logic proposal-only 输出。
- 建立测试集和评估标准。
- 在页面中展示 AI 建议，但不自动写回高风险动作。

验收标准：

- AI 输出结构化字段。
- 越权和高风险场景被拦截。
- 人工审批仍是最终决策入口。

### 第 11-12 周：生产加固

交付物：

- 完成权限复核。
- 建立 schema change review。
- 建立上线培训、回滚和复盘流程。

验收标准：

- 非管理员账号验收通过。
- 高风险变更有迁移计划和回滚计划。
- 上线后有 owner、指标和复盘机制。

## 9. 验收清单

数据验收：

- 每个对象都有稳定主键。
- 核心字段空值率可解释。
- 对象数量和源数据数量差异可解释。

模型验收：

- 每个对象都有主键、业务定义、核心属性、敏感属性、owner。
- 每条关系都有源对象、目标对象、业务含义和 cardinality。
- 没有按源系统命名对象，例如 `ERP_SKU`、`CRM_Customer`。

动作验收：

- 每个动作都有输入参数、权限、提交条件、成功状态、失败处理和审计字段。
- DDP、高风险品类、高成本方案、拒接决定必须经理审批。
- 动作表达完整业务操作，不做大量单字段编辑动作。

权限验收：

- 至少使用销售、合规、运营、财务、经理五类角色测试。
- 敏感字段不可见时，页面不得把空值解释为“无风险”。
- AI 助手不得拥有超过业务角色的读取和执行权限。

AI 验收：

- AI 输出 proposal，不直接高风险写回。
- 有测试集、评估标准和人工复核流程。
- 高风险漏判、越权读取、编造监管结论均视为阻断上线问题。

## 10. 后续扩展

第一版稳定后，再按价值逐步扩展：

- 异常控制塔：清关查验、港口滞箱、海外仓错发、尾程延误。
- 库存补货：库龄、断货风险、平台库存限制、补货计划。
- 客户方案库：按行业、平台、SKU 类型复用方案。
- 账单审计：尾程附加费、仓租、退货、赔付和利润复盘。
- 外部系统集成：CRM 报价单、ERP 订单、WMS 库存、承运商账单。
