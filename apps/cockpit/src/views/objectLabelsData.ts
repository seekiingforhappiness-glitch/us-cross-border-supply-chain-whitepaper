// U5：对象卡白话铺满 35 类 —— 数据文件（objectLabels.ts 的配套数据，被其 import 后合并使用）。
// 本文件不含渲染/查表逻辑（逻辑仍集中在 objectLabels.ts，单一权威不拆两处），只承载三类静态数据：
//
// ① OBJECT_TYPE_DESC —— 35 类对象的"一句话白话"（这是什么，对象卡抬头展示）。
// ② FIELD_LABELS_EXT —— 12 类核心对象之外，另外 23 类对象的字段中文名（核心 12 类字段名仍在
//   objectLabels.ts::FIELD_LABELS 里，不搬动，避免已审过的内容被重新触发审查）。
// ③ FIELD_GROUPS —— 全 35 类对象的字段分组（标识/业务/金额/时间/状态），供 ObjectCard 分组渲染。
// ④ 新 23 类对象需要的枚举翻译补充（ENUM_CN_EXT 通用词典追加项 / FIELD_ENUM_OVERRIDES_EXT 按字段
//   精确覆盖 / FIELD_ENUM_DOMAIN_EXT 标准值域 / CHARGE_CODE_CN 费用代码表）——与 objectLabels.ts
//   原有的 GENERIC_ENUM_CN / FIELD_ENUM_OVERRIDES / FIELD_ENUM_DOMAIN 分开存放但同一套查找逻辑
//   合并使用（objectLabels.ts 里对应函数已改为"先查原表，查不到再查 _EXT 表"）。
//
// ── 生成方式说明（"数据尽量由本体 JSON 描述生成不手抄"的落地）───────────────────────────
// - FIELD_GROUPS：程序化规则驱动生成，不是每个字段手工敲的分组标签。规则＝
//   ①字段名 == 该对象 ontology 声明的 statusField，或字段名含"status"子串，或属于人工核验过的
//     语义状态词表（severity/priority/tier/risk_tier/risk_level/sla_risk/sla_state/outcome/
//     decision/recommendation/escalation_level）→ 状态；
//   ②该字段在 ontology 里的属性 type 是 "date"/"datetime" → 时间（少数语义是时间但 type 声明为
//     string 的字段——仅 Shipment.last_event_time 一例，ontology description 明写"Derived 事件戳"
//     ——已核实后单独登记，非猜测）；
//   ③字段名以 "_usd" 结尾，或等于 gross_margin_rate → 金额；
//   ④字段名 == 该对象 ontology 声明的 primaryKey / titleKey，或以 "_no" 结尾，或以单数 "_id"
//     结尾（复数 "_ids" 不算——那是"受影响对象清单"类业务内容，不是这条记录自身的身份标识）→ 标识；
//   ⑤以上都不命中 → 业务（兜底）。
//   规则跑完后人工复核一遍，3 处判定改写（有理由，非拍脑袋）：
//   Customer.ior_capability（机械规则会分到业务，但与同对象的 broker_status/tier/risk_tier 同属
//   "客户资质分级"语境，归到状态更一致）、Supplier.uflpa_risk_flag 与 Shipment.expedite_flag
//   （字段名本身即"风险标记/当前是否处于加急状态"，是记录当下情况的标记而非中性业务属性，归状态）。
//   全 35 类字段跑完与 ontology 属性总数（353）逐一核对相等，无遗漏无重复（见 PR 交付说明）。
// - OBJECT_TYPE_DESC：ontology JSON 只有 17/35 个对象有顶层 description，且这些 description 是
//   写给工程师看的英文/决策记号（含 rule id、decision code，如 "P1: purchase-order line. Covers
//   D2's single-SKU-PO constraint..."），不是给业务人员看的白话，故 35 句白话为手写而非直接转录；
//   手写时逐条核对了：该对象在 ontology 里的 description（如有）、docs/cross-border-ontology-
//   manual.md §3（v0.1 稿，AdmissionCase/ComplianceFinding/LogisticsPlan/CostScenario 4 类已有
//   "业务定义"可改写复用，已按当前字段现状调整措辞，不逐字照抄过时描述如 route_type 的
//   ocean_parcel——现已不在枚举内）、docs/control-tower-ontology-manual.md §2（11 个早期核心对象
//   的状态机/DQ 规则上下文）、objectLabels.ts::LINK_CN（对象间关系）。凡描述里点名某条风险规则
//   （如"触发断货风险"），均已对照 ontology 属性 description 原文有明确依据才写（例：
//   InventoryPosition.available_qty 的 description 原文"R16: available_qty <= safety_stock =
//   stockout"），没有一手依据的机制细节不写、不猜。
// - 枚举翻译：能复用 objectLabels.ts 已有 GENERIC_ENUM_CN 的一律复用（如 draft/sent/rejected 等
//   同名同义 token），只在下方登记"新增"或"同名不同义需要精确覆盖"的两类。费用代码表
//   CHARGE_CODE_CN 镜像自 app/ux_copy.py::CHARGE_CODE_CN（Python/TS 语言边界无法 import，同
//   objectLabels.ts 里 ROLE_CN 的处理方式：复制+注明出处），并补齐该 Python 表缺失的 3 个新费种
//   （ISF/INSP/PSS——v0.11.3 补充，ontology InvoiceLine.charge_code 的 description 原文已写明
//   "ISF (US Importer Security Filing, has a rate card), INSP (customs inspection/exam fee),
//   PSS (peak-season surcharge)"，据此翻译，非猜测；此为已知的一处 Python/TS 两表口径漂移，
//   不在本任务改动范围，已在交付报告里另行提出）。
// - 行业通用代码（incoterm 三字码、UN/LOCODE、集装箱 ISO 类型、承运人 SCAC、last_mile_method 里
//   的快递公司专名 UPS/FedEx/USPS/OnTrac）刻意不翻译、不登记标准值域——完全比照 objectLabels.ts
//   头部注释里 Shipment.container_type/incoterm/carrier_scac/*_locode 的既有先例（这些码本身不
//   是"脏数据"，翻译反而会掩盖它们是可跨系统核对的原始编码这一事实）。

// ═══════════════════════════ ① 对象类型白话（35 类全覆盖，键序与 objectLabels.ts::OBJECT_TYPE_CN 一致）═══════════════════════════
export const OBJECT_TYPE_DESC: Record<string, string> = {
  Supplier: "SKU 的实际生产商或供货方，记录验厂、合规文件、UFLPA 风险等准入资质。",
  Sku: "一款计划或已在美国市场销售的商品，记录品类、报关要素（材质/用途/含电池等）和单价。",
  Customer: "下单或申请准入的买家/卖家/品牌方，记录业务模式、进口资质和信用等级。",
  SalesOrder: "客户下的一笔订单，状态由其下所有订单行的状态汇总得出，不能被动作直接改写。",
  SalesOrderLine: "订单里的一行商品明细（某 SKU、数量、承诺交期），是延误风险和改期动作的落点。",
  PurchaseOrder: "向供应商下的一份采购订单（单 SKU），记录采购数量、下单日和预计齐货日。",
  Shipment: "一票国际运输（海运整柜/拼箱），记录订舱号、船名航次、港口、清关状态和到港日期，是延误检测的核心对象。",
  ShipmentMilestone: "货件运输过程中的一条事件记录（订舱确认/离港/到港/清关/签收等），只增不改，是货件状态推算的原始依据。",
  ShipmentAllocation: "把某票货件的舱位分配给某条销售订单行的记录，是判断'这票货延误会影响哪些客户订单'的桥梁。",
  RiskEvent: "系统按规则自动检出的一次异常（延误、单证缺失、超收、资质过期等），记录严重度、受影响对象和处置状态。",
  Task: "处置一个风险事件的工作单，记录负责人、优先级、截止日和处置方案的审批流程。",
  AdmissionCase: "围绕一个 SKU 的新品准入、报价或方案复核申请，从提交到审批的完整决策记录。",
  ComplianceFinding: "准入预审中发现的一项监管/归类/文件/资质风险点（如 HTS 归类、UFLPA、标签要求），带处置建议。",
  LogisticsPlan: "为一个准入案件设计的跨境运输路径方案（运输方式、起运港、目的仓、尾程配送），是报价的基础。",
  CostScenario: "一个物流方案下的报价与成本拆解（保守/基准/乐观三档），逐项列出各段成本并算出毛利。",
  Container: "货件所装载的具体集装箱（柜号、柜型、免用箱天数），一票货件可能装多个柜；免用箱天数是滞箱费（DET）判责的依据。",
  Invoice: "货代/船公司/仓库/尾程商就某票货件开来的费用账单，含多个费用行。",
  InvoiceLine: "费用发票上的一条具体收费（费用代码、数量、单价、金额），用于核对是否'费率超收'或'重复计费'。",
  ExpectedCost: "某票货件各费用项的标准预期金额（费率卡口径），是判断发票'费率超收'或'计划外费用'的对照基准。",
  PoLine: "采购单下的一行明细（某 SKU、数量、约定单价），支持一张采购单包含多个 SKU、多次分批收货，以及逐行三方对账。",
  GoodsReceipt: "针对一张采购单的一次收货记录（收货日期），一张采购单可能对应多张收货单，支持分批到货。",
  GoodsReceiptLine: "收货单上对应某条采购单行的逐行实收明细（收货/验收/拒收数量、质检结果），是判断'短装'和'质量不合格'风险的依据。",
  SupplierInvoice: "供应商就某张采购单开来的应付账款发票（区别于物流费用发票），与采购单、收货单三方对账，是识别'价量不符'风险的基础。",
  SupplierInvoiceLine: "供应商发票上对应某条采购单行的收费明细，单价与采购单价的差异是判断'价量不符'风险的依据。",
  PurchasePayment: "针对某张采购单的一笔付款（定金/尾款/全款），定金在收货前打出即形成'预付款敞口'风险。",
  Payment: "一笔收款或付款记录（收客户货款，或付供应商/物流商费用），记录金额、应付日和实付日，是逾期应收和资金水位监控的基础。",
  SupplierQualification: "供应商持有的一项资质证书（验厂/ISO9001/UFLPA 可追溯性等），有有效期，过期未续且仍有在途采购单即触发'供应商资质过期'风险。",
  Warehouse: "货物存放/发货的节点（保税仓/海外仓/国内仓/FBA/第三方仓），库存、可承诺量和盘点都以仓库为范围。",
  InventoryPosition: "某个 SKU 在某个仓库的库存快照（可用/已预留/在途/隔离），可用量低于安全库存即触发'断货'风险；可承诺量（ATP）= 可用 + 在途 − 已预留。",
  InventoryReservation: "把某条销售订单行的需求锁定到某条库存记录上的占用记录，锁定量超过库存可承诺量（ATP）即触发'不可履约'风险。",
  CycleCount: "对某条库存记录的一次实物盘点（系统账面数 vs 实际清点数），两者差异即为'盘点差异'风险和库存损耗的依据。",
  RFQ: "为某个 SKU 发起的一次寻源询价，一旦有供应商中标即认证为该 SKU 的认可供应商，认可供应商不足会触发'单一来源断供'风险。",
  RFQLine: "询价单上对应某个 SKU 及询价数量的明细行。",
  Quote: "供应商针对某询价单提交的报价，一旦中标（awarded）即成为该 SKU 的认可供应商，用于判断'单一来源断供'和'绕流程采购'豁免。",
  CoordinationThread: "为处置某个风险事件而与外部对接方（供应商/货代/报关行/银行/客户）发起的一轮跟进沟通（诉求→跟进→回复→升级），记录沟通轮次和下次跟进截止时间。",
};

// ═══════════════════════════ ② 字段中文名：核心 12 类之外的另 23 类 ═══════════════════════════
// 命名沿用 objectLabels.ts::FIELD_LABELS 已确立的内部约定："_id"→"关联X/所属X/XX编号"、
// "_usd"→"XX（USD）"、"_date"/"_at"→"XX日期/XX时间"、"as_of_date"统一"数据快照日期"、
// "created_at"统一"创建时间"（与 Payment.created_at 现有译法一致，不再另创新词）。
export const FIELD_LABELS_EXT: Record<string, Record<string, string>> = {
  ShipmentMilestone: {
    milestone_id: "动态编号", shipment_id: "关联货件", event_time: "事件发生时间",
    new_eta: "更新后的预计到港日", ingested_at: "系统摄入时间", event_type: "事件类型",
    event_classifier: "事件分类（实际/预计）", event_locode: "事件发生地代码",
    source_system: "数据来源系统", is_duplicate: "重复标记",
  },
  ShipmentAllocation: {
    allocation_id: "分配编号", shipment_id: "关联货件", so_line_id: "关联订单行",
    allocated_qty: "分配数量",
  },
  AdmissionCase: {
    admission_case_id: "案件编号", case_title: "案件标题", customer_id: "关联客户",
    sku_id: "关联 SKU", risk_level: "风险等级", status: "案件状态", decision: "审批决定",
    target_launch_date: "目标上线日期", request_type: "申请类型",
    incoterm_candidate: "拟定贸易术语", monthly_order_estimate: "预估月单量",
    decision_reason: "决定理由", conditions: "附加条件",
  },
  ComplianceFinding: {
    compliance_finding_id: "发现编号", admission_case_id: "关联准入案件",
    finding_title: "发现标题", severity: "严重度", evidence_status: "证据状态",
    recommendation: "处置建议", finding_type: "发现类型", hts_candidate: "拟定 HTS 编码",
    pga_agency: "涉及监管机构", required_document: "所需文件",
  },
  LogisticsPlan: {
    logistics_plan_id: "方案编号", admission_case_id: "关联准入案件", plan_name: "方案名称",
    sla_risk: "时效风险", route_type: "运输方式", incoterm: "贸易术语",
    origin_port_locode: "起运港代码", destination_port_locode: "目的港代码",
    us_warehouse_region: "美国仓所在区域", last_mile_method: "尾程配送方式",
    estimated_transit_days: "预计时效（天）", operational_notes: "操作备注",
  },
  CostScenario: {
    cost_scenario_id: "情景编号", logistics_plan_id: "关联物流方案", scenario_type: "情景类型",
    quote_price_usd: "报价（USD）", product_cost_usd: "商品成本（USD）",
    first_mile_cost_usd: "国内段成本（USD）", international_freight_usd: "国际运费（USD）",
    duty_tax_usd: "关税税费（USD）", customs_brokerage_usd: "清关费用（USD）",
    warehouse_cost_usd: "海外仓成本（USD）", last_mile_cost_usd: "尾程成本（USD）",
    returns_allowance_usd: "退货准备金（USD）", risk_buffer_usd: "风险准备金（USD）",
    gross_margin_usd: "毛利（USD）", gross_margin_rate: "毛利率",
  },
  Container: {
    container_no: "集装箱号", shipment_id: "关联货件", container_type: "集装箱类型",
    is_primary: "主柜标记", free_days: "免用箱天数", gross_weight_kg: "毛重（kg）",
    volume_cbm: "体积（CBM）",
  },
  InvoiceLine: {
    invoice_line_id: "发票行编号", invoice_id: "所属发票", container_no: "关联集装箱号",
    unit_price_usd: "单价（USD）", amount_usd: "金额（USD）", charge_code: "费用代码",
    qty: "数量",
  },
  ExpectedCost: {
    expected_cost_id: "基准编号", shipment_id: "关联货件", container_no: "关联集装箱号",
    baseline_usd: "标准费用基准（USD）", charge_code: "费用代码", source: "基准来源",
  },
  PoLine: {
    po_line_id: "采购单行编号", po_id: "所属采购单", sku_id: "关联 SKU", line_status: "行状态",
    expected_ready_date: "行预计齐货日", as_of_date: "数据快照日期", created_at: "创建时间",
    unit_price_usd: "约定单价（USD）", qty: "采购数量", currency: "币种",
  },
  GoodsReceipt: {
    grn_id: "收货单编号", po_id: "关联采购单", status: "收货状态", received_date: "收货日期",
    as_of_date: "数据快照日期", created_at: "创建时间",
  },
  GoodsReceiptLine: {
    grn_line_id: "收货行编号", grn_id: "所属收货单", po_line_id: "关联采购单行",
    qc_status: "质检结果", received_date: "本行收货日期", as_of_date: "数据快照日期",
    created_at: "创建时间", received_qty: "收货数量", accepted_qty: "验收合格数量",
    rejected_qty: "拒收数量", defect_ppm: "缺陷率（PPM，百万分率）",
  },
  SupplierInvoice: {
    supplier_invoice_id: "供应商发票编号", supplier_id: "开票供应商", po_id: "关联采购单",
    vendor_invoice_no: "供应商发票号", status: "发票状态", issue_date: "开票日期",
    as_of_date: "数据快照日期", created_at: "创建时间", total_usd: "发票总额（USD）",
    currency: "币种",
  },
  SupplierInvoiceLine: {
    supplier_invoice_line_id: "发票行编号", supplier_invoice_id: "所属供应商发票",
    po_line_id: "关联采购单行", as_of_date: "数据快照日期", created_at: "创建时间",
    unit_price_usd: "开票单价（USD）", amount_usd: "金额（USD）", qty: "数量",
  },
  PurchasePayment: {
    payment_id: "付款编号", po_id: "关联采购单", exposure_status: "敞口状态",
    paid_date: "实付日期", as_of_date: "数据快照日期", created_at: "创建时间",
    amount_usd: "付款金额（USD）", payment_type: "付款类型",
  },
  SupplierQualification: {
    qualification_id: "资质编号", supplier_id: "关联供应商", evidence_status: "证据状态",
    status: "资质状态", valid_from: "有效期起", valid_to: "有效期止",
    as_of_date: "数据快照日期", created_at: "创建时间", cert_type: "证书类型",
  },
  InventoryPosition: {
    inventory_position_id: "库存记录编号", sku_id: "关联 SKU", warehouse_id: "所在仓库",
    as_of_date: "数据快照日期", available_qty: "可用数量", reserved_qty: "已预留数量",
    in_transit_qty: "在途数量", quarantine_qty: "隔离数量", safety_stock: "安全库存",
  },
  InventoryReservation: {
    reservation_id: "预留编号", so_line_id: "关联订单行", inventory_position_id: "关联库存记录",
    status: "预留状态", as_of_date: "数据快照日期", qty: "预留数量",
  },
  CycleCount: {
    cycle_count_id: "盘点编号", inventory_position_id: "关联库存记录", warehouse_id: "所在仓库",
    status: "盘点状态", as_of_date: "数据快照日期", system_qty: "系统账面数量",
    counted_qty: "实际清点数量", variance: "盘点差异（实盘−账面）",
  },
  RFQ: {
    rfq_id: "询价单编号", sku_id: "关联 SKU", status: "询价状态", created_date: "发起日期",
    as_of_date: "数据快照日期",
  },
  RFQLine: {
    rfq_line_id: "询价行编号", rfq_id: "所属询价单", sku_id: "关联 SKU", qty: "询价数量",
  },
  Quote: {
    quote_id: "报价编号", rfq_id: "关联询价单", supplier_id: "报价供应商", status: "报价状态",
    as_of_date: "数据快照日期", unit_price_usd: "报价单价（USD）", currency: "币种",
  },
  CoordinationThread: {
    coordination_id: "协调事项编号", task_id: "关联任务", risk_event_id: "关联风险事件",
    state: "协调状态", escalation_level: "升级级别", outcome: "最终结果",
    next_action_due: "下次跟进截止日", opened_at: "发起日期", last_update: "最近更新日期",
    counterparty_type: "对接方类型", counterparty_ref: "对接方标识", ask: "诉求内容",
    followup_count: "跟进次数", owner: "内部负责人", last_response: "对方最新回复",
    policy_version: "分配策略版本",
  },
};

// ═══════════════════════════ ③ 字段分组：全 35 类（标识/状态/时间/金额/业务）═══════════════════════════
export type FieldGroupName = "标识" | "状态" | "时间" | "金额" | "业务";
export const FIELD_GROUP_ORDER: FieldGroupName[] = ["标识", "状态", "时间", "金额", "业务"];

export const FIELD_GROUPS: Record<string, Record<FieldGroupName, string[]>> = {
  Supplier: {
    "标识": ["supplier_id", "supplier_name"],
    "状态": ["factory_audit_status", "compliance_docs_status", "origin_evidence_status", "uflpa_risk_flag"],
    "时间": [],
    "金额": [],
    "业务": ["city", "lead_time_days", "payment_terms_days"],
  },
  Sku: {
    "标识": ["sku_id", "sku_name", "supplier_id"],
    "状态": ["sku_status"],
    "时间": [],
    "金额": ["unit_price_usd", "declared_value_usd"],
    "业务": ["category", "package_weight_kg", "package_l_cm", "package_w_cm", "package_h_cm", "battery_flag", "food_contact_flag", "children_product_flag", "material", "use_case", "origin_country", "platform"],
  },
  Customer: {
    "标识": ["customer_id", "customer_name"],
    "状态": ["tier", "broker_status", "risk_tier", "ior_capability"],
    "时间": [],
    "金额": [],
    "业务": ["us_state", "business_model", "sales_channel", "credit_terms"],
  },
  SalesOrder: {
    "标识": ["so_id", "customer_id"],
    "状态": ["status"],
    "时间": ["order_date"],
    "金额": [],
    "业务": [],
  },
  SalesOrderLine: {
    "标识": ["so_line_id", "so_id", "sku_id"],
    "状态": ["line_status"],
    "时间": ["promised_delivery_date", "original_promised_date"],
    "金额": ["unit_price_usd"],
    "业务": ["qty", "reschedule_count"],
  },
  PurchaseOrder: {
    "标识": ["po_id", "supplier_id", "sku_id"],
    "状态": ["status"],
    "时间": ["po_date", "expected_ready_date"],
    "金额": [],
    "业务": ["qty"],
  },
  Shipment: {
    "标识": ["shipment_id", "booking_no", "mbl_no", "container_no"],
    "状态": ["customs_status", "status_source", "status", "expedite_flag"],
    "时间": ["etd", "eta_initial", "eta_current", "ata", "last_event_time"],
    "金额": [],
    "业务": ["mode", "container_type", "gross_weight_kg", "volume_cbm", "incoterm", "vessel_voyage", "carrier_name", "carrier_scac", "origin_port", "origin_port_locode", "destination_port", "destination_port_locode", "destination_warehouse", "missing_docs", "delay_days", "po_ids"],
  },
  ShipmentMilestone: {
    "标识": ["milestone_id", "shipment_id"],
    "状态": [],
    "时间": ["event_time", "new_eta", "ingested_at"],
    "金额": [],
    "业务": ["event_type", "event_classifier", "event_locode", "source_system", "is_duplicate"],
  },
  ShipmentAllocation: {
    "标识": ["allocation_id", "shipment_id", "so_line_id"],
    "状态": [],
    "时间": [],
    "金额": [],
    "业务": ["allocated_qty"],
  },
  RiskEvent: {
    "标识": ["risk_event_id", "rule_id", "shipment_id", "po_id", "supplier_id", "warehouse_id"],
    "状态": ["severity", "status", "outcome"],
    "时间": ["detected_at", "resolved_at"],
    "金额": ["affected_value_usd"],
    "业务": ["type", "affected_po_line_ids", "affected_so_line_ids", "affected_invoice_line_ids", "affected_sku_ids", "root_cause", "resolution_summary"],
  },
  Task: {
    "标识": ["task_id", "risk_event_id", "assignee_user_id", "assignee_team_id", "assigned_by_actor_id", "proposal_actor_id"],
    "状态": ["priority", "sla_state", "escalation_level", "approval_status", "status"],
    "时间": ["due_at"],
    "金额": [],
    "业务": ["title", "assignee_role", "policy_version", "proposed_action", "proposal_params", "approved_by_role", "proposal_actor_role", "action_taken"],
  },
  AdmissionCase: {
    "标识": ["admission_case_id", "case_title", "customer_id", "sku_id"],
    "状态": ["risk_level", "status", "decision"],
    "时间": ["target_launch_date"],
    "金额": [],
    "业务": ["request_type", "incoterm_candidate", "monthly_order_estimate", "decision_reason", "conditions"],
  },
  ComplianceFinding: {
    "标识": ["compliance_finding_id", "admission_case_id", "finding_title"],
    "状态": ["severity", "evidence_status", "recommendation"],
    "时间": [],
    "金额": [],
    "业务": ["finding_type", "hts_candidate", "pga_agency", "required_document"],
  },
  LogisticsPlan: {
    "标识": ["logistics_plan_id", "admission_case_id", "plan_name"],
    "状态": ["sla_risk"],
    "时间": [],
    "金额": [],
    "业务": ["route_type", "incoterm", "origin_port_locode", "destination_port_locode", "us_warehouse_region", "last_mile_method", "estimated_transit_days", "operational_notes"],
  },
  CostScenario: {
    "标识": ["cost_scenario_id", "logistics_plan_id", "scenario_type"],
    "状态": [],
    "时间": [],
    "金额": ["quote_price_usd", "product_cost_usd", "first_mile_cost_usd", "international_freight_usd", "duty_tax_usd", "customs_brokerage_usd", "warehouse_cost_usd", "last_mile_cost_usd", "returns_allowance_usd", "risk_buffer_usd", "gross_margin_usd", "gross_margin_rate"],
    "业务": [],
  },
  Container: {
    "标识": ["container_no", "shipment_id"],
    "状态": [],
    "时间": [],
    "金额": [],
    "业务": ["container_type", "is_primary", "free_days", "gross_weight_kg", "volume_cbm"],
  },
  Invoice: {
    "标识": ["invoice_id", "vendor_invoice_no", "shipment_id"],
    "状态": ["status"],
    "时间": ["issue_date"],
    "金额": ["total_usd"],
    "业务": ["vendor_type", "vendor_name", "currency"],
  },
  InvoiceLine: {
    "标识": ["invoice_line_id", "invoice_id", "container_no"],
    "状态": [],
    "时间": [],
    "金额": ["unit_price_usd", "amount_usd"],
    "业务": ["charge_code", "qty"],
  },
  ExpectedCost: {
    "标识": ["expected_cost_id", "shipment_id", "container_no"],
    "状态": [],
    "时间": [],
    "金额": ["baseline_usd"],
    "业务": ["charge_code", "source"],
  },
  PoLine: {
    "标识": ["po_line_id", "po_id", "sku_id"],
    "状态": ["line_status"],
    "时间": ["expected_ready_date", "as_of_date", "created_at"],
    "金额": ["unit_price_usd"],
    "业务": ["qty", "currency"],
  },
  GoodsReceipt: {
    "标识": ["grn_id", "po_id"],
    "状态": ["status"],
    "时间": ["received_date", "as_of_date", "created_at"],
    "金额": [],
    "业务": [],
  },
  GoodsReceiptLine: {
    "标识": ["grn_line_id", "grn_id", "po_line_id"],
    "状态": ["qc_status"],
    "时间": ["received_date", "as_of_date", "created_at"],
    "金额": [],
    "业务": ["received_qty", "accepted_qty", "rejected_qty", "defect_ppm"],
  },
  SupplierInvoice: {
    "标识": ["supplier_invoice_id", "supplier_id", "po_id", "vendor_invoice_no"],
    "状态": ["status"],
    "时间": ["issue_date", "as_of_date", "created_at"],
    "金额": ["total_usd"],
    "业务": ["currency"],
  },
  SupplierInvoiceLine: {
    "标识": ["supplier_invoice_line_id", "supplier_invoice_id", "po_line_id"],
    "状态": [],
    "时间": ["as_of_date", "created_at"],
    "金额": ["unit_price_usd", "amount_usd"],
    "业务": ["qty"],
  },
  PurchasePayment: {
    "标识": ["payment_id", "po_id"],
    "状态": ["exposure_status"],
    "时间": ["paid_date", "as_of_date", "created_at"],
    "金额": ["amount_usd"],
    "业务": ["payment_type"],
  },
  Payment: {
    "标识": ["payment_id", "counterparty_id", "ref_id"],
    "状态": ["status"],
    "时间": ["due_date", "paid_date", "as_of_date", "created_at"],
    "金额": ["amount_usd"],
    "业务": ["direction", "counterparty_type", "ref_type"],
  },
  SupplierQualification: {
    "标识": ["qualification_id", "supplier_id"],
    "状态": ["evidence_status", "status"],
    "时间": ["valid_from", "valid_to", "as_of_date", "created_at"],
    "金额": [],
    "业务": ["cert_type"],
  },
  Warehouse: {
    "标识": ["warehouse_id"],
    "状态": [],
    "时间": ["as_of_date"],
    "金额": [],
    "业务": ["type", "operator", "region", "capacity_units"],
  },
  InventoryPosition: {
    "标识": ["inventory_position_id", "sku_id", "warehouse_id"],
    "状态": [],
    "时间": ["as_of_date"],
    "金额": [],
    "业务": ["available_qty", "reserved_qty", "in_transit_qty", "quarantine_qty", "safety_stock"],
  },
  InventoryReservation: {
    "标识": ["reservation_id", "so_line_id", "inventory_position_id"],
    "状态": ["status"],
    "时间": ["as_of_date"],
    "金额": [],
    "业务": ["qty"],
  },
  CycleCount: {
    "标识": ["cycle_count_id", "inventory_position_id", "warehouse_id"],
    "状态": ["status"],
    "时间": ["as_of_date"],
    "金额": [],
    "业务": ["system_qty", "counted_qty", "variance"],
  },
  RFQ: {
    "标识": ["rfq_id", "sku_id"],
    "状态": ["status"],
    "时间": ["created_date", "as_of_date"],
    "金额": [],
    "业务": [],
  },
  RFQLine: {
    "标识": ["rfq_line_id", "rfq_id", "sku_id"],
    "状态": [],
    "时间": [],
    "金额": [],
    "业务": ["qty"],
  },
  Quote: {
    "标识": ["quote_id", "rfq_id", "supplier_id"],
    "状态": ["status"],
    "时间": ["as_of_date"],
    "金额": ["unit_price_usd"],
    "业务": ["currency"],
  },
  CoordinationThread: {
    "标识": ["coordination_id", "task_id", "risk_event_id"],
    "状态": ["state", "escalation_level", "outcome"],
    "时间": ["next_action_due", "opened_at", "last_update"],
    "金额": [],
    "业务": ["counterparty_type", "counterparty_ref", "ask", "followup_count", "owner", "last_response", "policy_version"],
  },
};

// ═══════════════════════════ ④ 新 23 类需要的枚举翻译补充 ═══════════════════════════

// 通用词典追加项——新 token，且核对过在全 35 类范围内语义唯一、不会误伤既有字段才收进来
// （沿用 objectLabels.ts::GENERIC_ENUM_CN 同一约束）。可直接复用 GENERIC_ENUM_CN 已有翻译的
// token（如 draft/sent/rejected/closed/received/at_risk/released/low/medium/high/critical/
// USD/forwarder/customer/supplier 等）不重复登记。
export const ENUM_CN_EXT: Record<string, string> = {
  // ShipmentMilestone.event_type（arrived/delivered 已有，这里补另外 7 个）
  booking_confirmed: "订舱确认", departed: "已离港", eta_change: "预计到港日变更",
  transshipment: "中转", customs_filed: "已报关", customs_hold: "清关查验中",
  customs_released: "清关放行",
  // ShipmentMilestone.event_classifier（DCSA 语义：eta_change=EST 其余=ACT，见 ontology 属性
  // description 原文，本体本身未含中文，此处按该语义手动人话化）
  ACT: "实际事件", EST: "预计变更",
  // ShipmentMilestone.source_system
  carrier_edi: "船公司 EDI 报文", forwarder_portal: "货代系统", manual: "人工录入",
  // CoordinationThread.counterparty_type（supplier/customer/forwarder 已有，这里只补新增两个）
  customs_broker: "报关行", bank: "银行",
  // CoordinationThread.state（responded/escalated/resolved 已有）
  awaiting: "待回应", dead_ended: "已终止（无法推进）",
  // RFQ.status ∩ Quote.status（draft/sent/closed/cancelled/rejected 已有；awarded 两表共用）
  quoting: "询价中", evaluating: "评估中", awarded: "已中标",
  invited: "已邀请", submitted: "已提交", shortlisted: "已入围",
  // expired：Quote.status 与 SupplierQualification.status 共用，语义一致（有效期已过）
  expired: "已过期",
  // LogisticsPlan.route_type（ocean_fcl/ocean_lcl 已有）
  express: "快递", air_freight: "空运", warehouse_fulfillment: "海外仓直发",
  // LogisticsPlan.last_mile_method（UPS/FedEx/USPS/OnTrac 是快递公司专名，刻意不翻译）
  LTL: "零担运输（LTL）",
  // LogisticsPlan.us_warehouse_region（platform 值该字段语义特殊，见下方 FIELD_ENUM_OVERRIDES_EXT）
  west: "美西", central: "美中", east: "美东",
  // CostScenario.scenario_type
  conservative: "保守", base: "基准", optimistic: "乐观",
  // AdmissionCase.request_type
  new_sku: "新品准入", ddp_quote: "DDP 报价", dap_quote: "DAP 报价", plan_review: "方案复核",
  // AdmissionCase.incoterm_candidate（FOB/DAP/DDP 是行业通用贸易术语，刻意不翻译；tbd 是唯一需要
  // 人话化的值）
  tbd: "待定",
  // PurchasePayment.payment_type
  deposit: "定金", balance: "尾款", full: "全款",
  // SupplierQualification.status（revoked 已有）
  valid: "有效", expiring: "即将过期",
  // ComplianceFinding.finding_type（9 值全新）
  hts: "HTS 归类", pga: "PGA 审批（其他联邦机构监管）", labeling: "标签合规",
  certification: "认证要求", origin: "原产地", uflpa: "UFLPA（涉疆强迫劳动法案）",
  ad_cvd: "反倾销/反补贴税（AD/CVD）", section_301: "301 条款关税", platform_rule: "平台规则",
  // ComplianceFinding.pga_agency
  CBP: "美国海关与边境保护局（CBP）", FDA: "食品药品监督管理局（FDA）",
  FCC: "联邦通信委员会（FCC）", CPSC: "消费品安全委员会（CPSC）",
  EPA: "环境保护署（EPA）", USDA: "农业部（USDA）", none: "不涉及特定机构",
};

// 按字段精确覆盖——同一 token 在不同字段语义不同，或需要比通用词典更贴合业务语境的措辞，
// 精确优先于 ENUM_CN_EXT（沿用 objectLabels.ts::FIELD_ENUM_OVERRIDES 同一优先级设计）。
export const FIELD_ENUM_OVERRIDES_EXT: Record<string, Record<string, Record<string, string>>> = {
  AdmissionCase: {
    // status 已用 GENERIC 覆盖；decision 是独立枚举（与 status 的 approved/rejected 不同 token）
    decision: { approve: "批准", reject: "拒绝", more_info: "需要更多信息" },
  },
  ComplianceFinding: {
    recommendation: {
      accept: "可接受", more_docs: "需补充材料", dap_only: "仅可 DAP 方式清关",
      reject: "建议拒接", escalate: "升级复核",
    },
  },
  LogisticsPlan: {
    // 同一 token "platform" 在两个字段里含义不同（仓所在区域 vs 尾程配送方式），必须分字段覆盖
    us_warehouse_region: { platform: "平台仓" },
    last_mile_method: { platform: "平台仓配" },
  },
  PoLine: {
    // 与 objectLabels.ts 里 SalesOrderLine.line_status.open 的既有先例同理：generic 的"未处置"
    // 不贴合"这行还没开始收货"的语境，精确覆盖为"待收货"
    line_status: { open: "待收货", partially_received: "部分收货" },
  },
  GoodsReceipt: {
    // "partial" 的 generic 译法"部分提供"是 Supplier 合规文件语境，这里是"部分收货"，必须覆盖
    status: { partial: "部分收货" },
  },
  SupplierInvoice: {
    // "received" 的 generic 译法"已收货"是到货语境，这里是"发票已收到"，必须覆盖（与 objectLabels.ts
    // 里 Invoice.status.received 的既有先例同理）
    status: { received: "已收到", matched: "已匹配（三方对账通过）" },
  },
  CycleCount: {
    // "scheduled" 的 generic 译法"待结算"是 Payment 语境，这里是"已排期待盘点"，必须覆盖；
    // counted/variance 是 CycleCount 专属枚举值（variance 同时也是本对象一个字段名，容易和数值型
    // 字段混淆，用"清点有差异"消歧）；reconciled 复用 generic"已核对平"，语义相符不必覆盖
    status: { scheduled: "已排期", counted: "已清点", variance: "清点有差异" },
  },
  PurchasePayment: {
    // covered/released 的措辞比 generic 默认更贴合"预付款敞口是否已解除"的语境；at_risk 复用
    // generic"有风险"已经合适，不必覆盖
    exposure_status: { covered: "已覆盖（已收货）", released: "已结清" },
  },
};

// 标准值域——直接转录自 ontology/control-tower-ontology.json 各新增对象的 enum 属性 values 数组
// （同 objectLabels.ts::FIELD_ENUM_DOMAIN 的转录方式，逐条核对非手敲猜测）。行业通用代码字段
// （incoterm/origin_port_locode/destination_port_locode/container_type/last_mile_method 里的
// 快递公司专名）比照 objectLabels.ts 里 Shipment 同类字段的既有先例，刻意不登记 domain
// （见文件头注释）——它们仍会原样显示，只是不参与"标准值域"DQ 提示与安全闸门。
export const FIELD_ENUM_DOMAIN_EXT: Record<string, Record<string, string[]>> = {
  ShipmentMilestone: {
    event_type: ["booking_confirmed", "departed", "eta_change", "transshipment", "arrived", "customs_filed", "customs_hold", "customs_released", "delivered"],
    event_classifier: ["ACT", "EST"],
    source_system: ["carrier_edi", "forwarder_portal", "manual"],
  },
  AdmissionCase: {
    request_type: ["new_sku", "ddp_quote", "dap_quote", "plan_review"],
    risk_level: ["low", "medium", "high", "critical"],
    status: ["draft", "in_precheck", "plan_ready", "priced", "approved", "quote_with_conditions", "rejected", "needs_more_info"],
    decision: ["approve", "quote_with_conditions", "reject", "more_info"],
    // incoterm_candidate：U5 独立复核脚本发现 ENUM_CN_EXT 已登记 tbd→"待定"（见下方④注释），但
    // 该字段先前没有登记进这张 domain 表——ENUM_FIELDS 安全闸门只认"登记过 domain 或 override 的
    // 字段"，没登记＝闸门直接拦截，tbd 的翻译永远到不了 enumLabel()，实测会原样显示英文 "tbd"。
    // 补registered 后 FOB/DAP/DDP 仍落回④注释里说的"行业通用贸易术语，刻意不翻译"分支（原样显示，
    // 符合预期），只有 tbd 会命中 ENUM_CN_EXT 的翻译——不是新增翻译内容，是打通已有翻译的可达性。
    incoterm_candidate: ["FOB", "DAP", "DDP", "tbd"],
  },
  ComplianceFinding: {
    finding_type: ["hts", "pga", "labeling", "certification", "origin", "uflpa", "ad_cvd", "section_301", "platform_rule"],
    severity: ["low", "medium", "high", "critical"],
    pga_agency: ["CBP", "FDA", "FCC", "CPSC", "EPA", "USDA", "none"],
    evidence_status: ["missing", "provided", "verified", "rejected"],
    recommendation: ["accept", "more_docs", "dap_only", "reject", "escalate"],
  },
  LogisticsPlan: {
    route_type: ["express", "air_freight", "ocean_fcl", "ocean_lcl", "warehouse_fulfillment"],
    us_warehouse_region: ["west", "central", "east", "platform"],
    last_mile_method: ["UPS", "FedEx", "USPS", "OnTrac", "LTL", "platform"],
    sla_risk: ["low", "medium", "high"],
  },
  CostScenario: {
    scenario_type: ["conservative", "base", "optimistic"],
  },
  InvoiceLine: {
    charge_code: ["OFT", "THC", "DOC", "FSC", "CUS", "DTY", "WHS", "STO", "LMD", "DET", "DEM", "CHS", "ACC", "ISF", "INSP", "PSS"],
  },
  ExpectedCost: {
    charge_code: ["OFT", "THC", "DOC", "FSC", "CUS", "DTY", "WHS", "STO", "LMD"],
  },
  PoLine: {
    line_status: ["open", "partially_received", "received", "closed"],
    currency: ["USD"],
  },
  GoodsReceipt: {
    status: ["received", "partial", "closed"],
  },
  GoodsReceiptLine: {
    qc_status: ["passed", "failed", "waived"],
  },
  SupplierInvoice: {
    status: ["received", "matched", "disputed", "approved"],
    currency: ["USD"],
  },
  PurchasePayment: {
    payment_type: ["deposit", "balance", "full"],
    exposure_status: ["covered", "at_risk", "released"],
  },
  SupplierQualification: {
    evidence_status: ["missing", "provided", "verified", "rejected"],
    status: ["valid", "expiring", "expired", "revoked"],
  },
  InventoryReservation: {
    status: ["open", "allocated", "released", "fulfilled", "backordered"],
  },
  CycleCount: {
    status: ["scheduled", "counted", "variance", "reconciled"],
  },
  RFQ: {
    status: ["draft", "sent", "quoting", "evaluating", "awarded", "closed", "cancelled"],
  },
  Quote: {
    status: ["invited", "submitted", "shortlisted", "awarded", "rejected", "expired"],
    currency: ["USD"],
  },
  CoordinationThread: {
    counterparty_type: ["supplier", "forwarder", "customs_broker", "bank", "customer"],
    state: ["awaiting", "responded", "escalated", "resolved", "dead_ended"],
  },
};

// 费用代码 → 中文，镜像自 app/ux_copy.py::CHARGE_CODE_CN（13 项逐字复制）+ 3 项新费种补充
// （ISF/INSP/PSS，翻译依据见文件头注释）。供 InvoiceLine.charge_code / ExpectedCost.charge_code
// 两个字段的"代码 + 中文"组合展示（同 objectLabels.ts 里 RiskEvent.rule_id 的渲染方式）。
export const CHARGE_CODE_CN: Record<string, string> = {
  OFT: "海运费", THC: "码头操作费", DOC: "文件费", FSC: "燃油附加费",
  CUS: "清关费", DTY: "关税", WHS: "仓库操作费", STO: "仓储费",
  LMD: "尾程派送费", DET: "滞箱费", DEM: "滞港费", CHS: "车架费", ACC: "地址更正费",
  ISF: "ISF 申报费", INSP: "海关查验费", PSS: "旺季附加费",
};
