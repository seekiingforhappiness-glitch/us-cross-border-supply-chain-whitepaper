// 对象卡用户语言化映射层（V12：Daniel 贴驾驶舱 Supplier 对象卡截图「这里不容易懂」）——
// 单一权威：字段名中文 / 枚举值人话 / 关系词条中文 + 方向语义 / 对象类型中文名。
// 其余组件（透视镜、后续对象相关 UI）需要同类翻译时应复用本文件，不再各自建表
// （B5/G3 已踩过「同一张表三处各自定义、译名互相打架」的坑——见 aiFlowModel.ts 顶部注释）。
//
// ── 语义红线：只翻译不加工事实 ──────────────────────────────────────────────
// 本文件只改变"呈现文字"，不改变数据本身；API 返回的原始字段值不变，ObjectCard 拿到什么就展示
// 什么（人话化后）。未登记的字段/枚举值一律兜底原样（原字段名 / 原始 token），绝不为了"看起来完整"
// 而编造中文。对确认超出本体声明域的残留值（如历史模拟数据），默认也不臆造翻译——除非有本节②
// 明确记录的一手依据（决策日志 / G5-sim 处置单）支持该值的真实含义，才收进 FIELD_ENUM_OVERRIDES。
//
// ── ① 已核对的权威源（先查后镜像，不重复定义）──────────────────────────────
// - app/ux_copy.py（Streamlit 呈现层清洗表）：STATUS_CN 提供了通用状态词的中文译名（open/pending/
//   approved/provided/...）——本文件的 GENERIC_ENUM_CN 以它为基线镜像，两处例外见②。另镜像了
//   _MISSING_DOC_CN（commercial_invoice/packing_list/bill_of_lading/isf 四个单证 token）。
// - app/streamlit_app.py::ROLE_SHORT_CN：角色短名中文（ops→运营/cs→客户成功/...），本文件 ROLE_CN
//   逐字镜像（Python/TS 语言边界无法 import，改为复制+本注释标注出处，后续两处应一起改）。
// - apps/cockpit/src/views/aiFlowModel.ts：RULE_CN（R1-R21 规则中文名）/SEV_CN（严重度）/
//   ACTION_CN（处置动作中文动词）已是本项目 B5/G3 归一后的权威表——本文件直接 import 复用，
//   不重新复制字符串（避免第 4 处重复定义）。ACTION_CN 缺 3 个 Task.proposed_action 枚举值
//   （collect / chase_docs / escalate，均为 v0.11.2 词表对齐后新增的真实值——见决策日志 V11/G4），
//   已在 aiFlowModel.ts 就地补齐（同一权威表扩展，非另开一张）。
// - app/standard_object_view.py::KEY_FIELDS：核对后确认 Streamlit 本身从未做过"字段名→中文"的
//   翻译（属性表直接用原始 snake_case 列名），故字段名中文表在本仓库是**新增**，非迁移已有资产。
// - apps/builder-console：核对后确认无同类翻译层。
// - docs/cross-border-ontology-manual.md（v0.1，AGENTS.md §0 标注只读模板参考）与
//   docs/control-tower-ontology-manual.md（v0.2 早期提案稿）：两份手册对多数字段已有中文业务定义
//   （如"验厂状态""合规文件状态""常规交期"），本文件的字段中文标签在此基础上统一措辞（如
//   lead_time_days 统一为「交期天数」而非手册原文「常规交期」，跟随任务书给定的例词并保持
//   "_days 后缀字段一律带「天数」二字"的内部一致性）。
// - ontology/control-tower-ontology.json：各字段 description（如 Payment 系列、affected_sku_ids）
//   本身已是中文，直接作为语义核验依据；rule_id↔type 的对应关系已对照 engine/*.py 全部 emit()
//   调用逐条核实（非猜测），见下方 RISK_TYPE_TO_RULE 注释。
//
// ── ② 与 ①权威源的两处刻意偏离（有意为之，非疏漏）──────────────────────────
// - "open"：ux_copy.STATUS_CN 通用译"待处理"，但该表服务于动作回执叙事句（"风险 A→B"），与本文件
//   服务的"对象卡字段值单元格"是不同展示位——核对后确认 Streamlit 的对象属性表格从未走过 STATUS_CN
//   （§①已述），故此处无真实先例冲突。本文件按任务书明确给定的例词，取"未处置"作对象卡场景的
//   默认译法（更贴合"这条记录还没人处理"的卡片语境）；因不同字段的"open"语义并不相同（销售订单的
//   open≠风险事件的open≠SLA的open），已按字段分别在 FIELD_ENUM_OVERRIDES 精确重写，通用译法仅在
//   语义确实相符（RiskEvent.status）时生效。
// - "pending"：ux_copy.STATUS_CN 译"待审批"（专为 Task.approval_status 审批语境而定），任务书给的
//   例词是"待处理"。处理：把"待处理"设为通用默认（覆盖 Supplier.factory_audit_status 等非审批语境），
//   同时为 Task.approval_status 单独保留"待审批"覆盖（与 ux_copy 原意一致）——两者并存不冲突。
//
// ── ③ 对"确认超出本体声明域"的残留值的处理（真实数据核验，非假设）──────────
// 用 pipeline/ontology_models.py 生成的 Pydantic 模型对 data/simworld.sqlite 全表跑过
// model_validate 核验：Supplier 当前 0 违例（V12 提到的 compliance_docs_status='complete' /
// uflpa_risk_flag='low' 残留已被 G5-sim 处置单清空，历史遗留，本文件仍保留其翻译作兜底防线，
// 不因数据已修复而删除——防止未来同类残留复现时静默显示原始英文）。同批核验还发现 PurchaseOrder.
// status='open'（全量 3519/3519）、RiskEvent.outcome='accepted'（43/257）两类真实超出本体声明域
// 的残留，均属 sim/ 数据生成域（不在本任务改动范围，已在报告里另行提出）——本文件不为这些未经
// 决策记录确认的残留值编造翻译，一律走"域外原样呈现 + 数据质量提示徽标解释"路径（见 enumLabel
// 的 domain 兜底分支）。
// 〔U5 独立复核勘误，2026-07-16〕本节曾把 Sku.category='seasonal_gift'（42/300）、Shipment.
// destination_port 的 new_york/savannah/rotterdam/hamburg 四值也算作同类域外残留——经复核脚本
// 对 ontology/control-tower-ontology.json 全量 enum values 逐字段核对证伪：这 5 个值早已被
// ontology.json（v0.11.3 "G6 enum-absorbs-reality" 决策，property description 原文可查）与
// pipeline/ontology_models.py 的 Pydantic Literal 一并纳入声明域（sim 世界的节庆礼品 SKU、
// 美东/欧洲目的港是真实设计数据，非残留）——是本文件 FIELD_ENUM_DOMAIN 的转录遗漏，不是数据
// 问题，已在下方补全域值并补上中文译法，不再让这两个字段的真实业务值原样漏译成英文。
//
// ── ④ 已发现并顺手修正的 1 处小 bug（非本任务目标，但直接相关且高置信）───────
// aiFlowModel.ts::RULE_TYPE_CN 里 R20 现金水位键写成了 "cash_runway"，但 engine/finance_rules.py
// 实际 emit 的 type 值是 "cash_watch"（已用 grep 核对，"cash_runway" 全仓库唯一出现处就是这个
// 错键本身）。该 fallback 分支平时不会被命中（RULE_CN 按 rule_id 已优先匹配上），但本文件按
// type token 推导 RiskEvent.type 译名需要这张表键值correct，故顺手把错键改成真实值——纯字符串
// 修正，不改变任何既有调用路径的行为（原键从未匹配成功过，修正只会让它开始正确匹配，不会破坏
// 任何依赖"匹配失败"的逻辑）。
import { ACTION_CN, RULE_CN, SEV_CN } from "./aiFlowModel";
// U5（35 类白话铺满）：核心 12 类之外另 23 类的字段名/分组/枚举翻译数据，物理上放在独立数据文件
// 里（apps/cockpit/src/views/objectLabelsData.ts），本文件只 import 后在下方各查表函数里"先查
// 原表、查不到再查 _EXT 表"合并使用——查表逻辑仍只有这一处，不搬动/不重复已审过的原 12 类内容。
import {
  CHARGE_CODE_CN,
  ENUM_CN_EXT,
  FIELD_ENUM_DOMAIN_EXT,
  FIELD_ENUM_OVERRIDES_EXT,
  FIELD_GROUP_ORDER,
  FIELD_GROUPS,
  FIELD_LABELS_EXT,
  OBJECT_TYPE_DESC,
  type FieldGroupName,
} from "./objectLabelsData";

export { OBJECT_TYPE_DESC, type FieldGroupName };

// ═══════════════════════════ 对象类型中文名（本体全 35 类型）═══════════════════════════
// 用于：① 对象卡抬头 ② 关系 chip 里邻居类型名。命名统一参考 docs/cross-border-ontology-manual.md /
// docs/control-tower-ontology-manual.md 的业务定义 + apps/cockpit 既有画面用词（zoneModel.ts 的
// "客户"/"供应商"、LaneQueue.tsx 的"货件"）保持全站一致，不再另创新词。
export const OBJECT_TYPE_CN: Record<string, string> = {
  Supplier: "供应商",
  Sku: "SKU",
  Customer: "客户",
  SalesOrder: "销售订单",
  SalesOrderLine: "销售订单行",
  PurchaseOrder: "采购单",
  Shipment: "货件",
  ShipmentMilestone: "物流动态",
  ShipmentAllocation: "货件分配",
  RiskEvent: "风险事件",
  Task: "任务",
  AdmissionCase: "准入案件",
  ComplianceFinding: "合规发现",
  LogisticsPlan: "物流方案",
  CostScenario: "成本情景",
  Container: "集装箱",
  Invoice: "费用发票",
  InvoiceLine: "费用发票行",
  ExpectedCost: "预估费用基准",
  PoLine: "采购单行",
  GoodsReceipt: "收货单",
  GoodsReceiptLine: "收货单行",
  SupplierInvoice: "供应商发票",
  SupplierInvoiceLine: "供应商发票行",
  PurchasePayment: "采购预付款",
  Payment: "收付款记录",
  SupplierQualification: "供应商资质",
  Warehouse: "仓库",
  InventoryPosition: "库存记录",
  InventoryReservation: "库存预留",
  CycleCount: "盘点记录",
  RFQ: "询价单",
  RFQLine: "询价单行",
  Quote: "供应商报价",
  CoordinationThread: "协调事项",
};

// ═══════════════════════════ 字段名中文（驾驶舱下钻可达的 12 个核心类型）═══════════════════════════
// 范围＝任务书指定的 12 类（Shipment/RiskEvent/Task/Customer/Supplier/SalesOrder/SalesOrderLine/
// Payment/Sku/Warehouse/Invoice/PurchaseOrder）。未登记字段兜底原字段名（见 fieldLabel）——其余
// 23 个非核心类型驾驶舱暂不下钻到，字段名兜底原样不算功能缺口。
export const FIELD_LABELS: Record<string, Record<string, string>> = {
  Shipment: {
    shipment_id: "货件编号", booking_no: "订舱号", mbl_no: "海运提单号", mode: "运输方式",
    container_no: "集装箱号", container_type: "集装箱类型", gross_weight_kg: "毛重（kg）",
    volume_cbm: "体积（CBM）", incoterm: "贸易术语", vessel_voyage: "船名航次",
    carrier_name: "承运人名称", carrier_scac: "船公司代码（SCAC）", origin_port: "起运港",
    origin_port_locode: "起运港代码", destination_port: "目的港", destination_port_locode: "目的港代码",
    destination_warehouse: "目的仓", etd: "预计/实际离港日", eta_initial: "初始预计到港日",
    eta_current: "当前预计到港日", ata: "实际到港日", customs_status: "清关状态",
    missing_docs: "缺失单证", expedite_flag: "已加急标记", delay_days: "延误天数",
    last_event_time: "最新动态时间", po_ids: "关联采购单号", status_source: "状态来源",
    status: "货运状态",
  },
  RiskEvent: {
    risk_event_id: "风险事件编号", type: "风险类型", rule_id: "触发规则", severity: "严重度",
    shipment_id: "关联货件", po_id: "关联采购单", supplier_id: "关联供应商",
    affected_po_line_ids: "受影响采购单行", warehouse_id: "关联仓库",
    affected_so_line_ids: "受影响订单行", affected_invoice_line_ids: "受影响发票行",
    affected_sku_ids: "受影响 SKU", affected_value_usd: "影响金额（USD）",
    detected_at: "检出日期", root_cause: "根因", status: "处置状态", resolved_at: "解决日期",
    outcome: "处置结果", resolution_summary: "结案说明",
  },
  Task: {
    task_id: "任务编号", risk_event_id: "关联风险事件", title: "任务标题",
    assignee_role: "负责角色", priority: "优先级", due_at: "处理截止日",
    assignee_user_id: "负责人", assignee_team_id: "负责团队", sla_state: "时效状态",
    escalation_level: "升级级别", policy_version: "分派策略版本", proposed_action: "提议动作",
    proposal_params: "提案参数", approval_status: "审批状态", approved_by_role: "审批角色",
    assigned_by_actor_id: "派单人", proposal_actor_id: "提案人", proposal_actor_role: "提案人角色",
    action_taken: "已执行动作", status: "任务状态",
  },
  Customer: {
    customer_id: "客户编号", customer_name: "客户名称", tier: "客户等级", us_state: "所在州",
    business_model: "业务模式", sales_channel: "销售渠道", ior_capability: "进口商资质（IOR）",
    broker_status: "报关行状态", credit_terms: "账期", risk_tier: "信用风险等级",
  },
  Supplier: {
    supplier_id: "供应商编号", supplier_name: "供应商名称", city: "所在城市",
    lead_time_days: "交期天数", factory_audit_status: "验厂状态",
    compliance_docs_status: "合规文件状态", uflpa_risk_flag: "UFLPA 风险标记",
    origin_evidence_status: "原产地证据状态", payment_terms_days: "账期天数",
  },
  SalesOrder: {
    so_id: "销售订单编号", customer_id: "关联客户", order_date: "下单日期", status: "订单状态",
  },
  SalesOrderLine: {
    so_line_id: "订单行编号", so_id: "所属订单", sku_id: "关联 SKU", qty: "数量",
    unit_price_usd: "行单价（USD）", promised_delivery_date: "当前承诺交期",
    original_promised_date: "初始承诺交期", reschedule_count: "改期次数",
    line_status: "订单行状态",
  },
  Payment: {
    payment_id: "付款编号", direction: "收付方向", counterparty_type: "对手方类型",
    counterparty_id: "对手方", ref_type: "挂账单据类型", ref_id: "挂账单据编号",
    amount_usd: "金额（USD）", due_date: "应收付日期", paid_date: "实际收付日期",
    status: "结算状态", as_of_date: "数据快照日期", created_at: "创建时间",
  },
  Sku: {
    sku_id: "SKU 编号", sku_name: "SKU 名称", supplier_id: "供应商", category: "品类",
    unit_price_usd: "单价（USD）", sku_status: "上架状态", declared_value_usd: "申报价值（USD）",
    package_weight_kg: "包装重量（kg）", package_l_cm: "包装长（cm）", package_w_cm: "包装宽（cm）",
    package_h_cm: "包装高（cm）", battery_flag: "含电池标记", food_contact_flag: "接触食品标记",
    children_product_flag: "儿童用品标记", material: "材质", use_case: "用途",
    origin_country: "原产国", platform: "销售平台",
  },
  Warehouse: {
    warehouse_id: "仓库编号", type: "仓库类型", operator: "运营方", region: "所在区域",
    capacity_units: "库容（件）", as_of_date: "数据快照日期",
  },
  Invoice: {
    invoice_id: "发票编号", vendor_type: "供应商类型", vendor_name: "供应商名称",
    vendor_invoice_no: "供应商发票号", shipment_id: "关联货件", issue_date: "开票日期",
    currency: "币种", total_usd: "发票总额（USD）", status: "发票状态",
  },
  PurchaseOrder: {
    po_id: "采购单编号", supplier_id: "供应商", sku_id: "采购 SKU", qty: "采购数量",
    po_date: "下单日期", expected_ready_date: "预计齐货日", status: "采购单状态",
  },
};

/** 字段名 → 中文标签；先查核心 12 类的 FIELD_LABELS，再查另 23 类的 FIELD_LABELS_EXT
 *  （U5 铺满 35 类），仍未登记的（遗漏字段/未来新字段）兜底原字段名，不编造。 */
export function fieldLabel(type: string, field: string): string {
  return FIELD_LABELS[type]?.[field] ?? FIELD_LABELS_EXT[type]?.[field] ?? field;
}

/** (type, field) → 所属分组（标识/状态/时间/金额/业务），供 ObjectCard 分组渲染（U5）。
 *  全 35 类已用程序化规则+人工复核跑满（见 objectLabelsData.ts 头部说明），理论上总能命中；
 *  未登记的类型/字段（未来新增本体字段的过渡期）优雅降级到"业务"兜底组，不丢字段、不报错。 */
export function fieldGroup(type: string, field: string): FieldGroupName {
  const groups = FIELD_GROUPS[type];
  if (!groups) return "业务";
  for (const g of FIELD_GROUP_ORDER) {
    if (groups[g]?.includes(field)) return g;
  }
  return "业务";
}
export { FIELD_GROUP_ORDER };

// ═══════════════════════════ 角色中文短名（镜像 app/streamlit_app.py::ROLE_SHORT_CN）═══════════════════════════
const ROLE_CN: Record<string, string> = {
  ops: "运营", cs: "客户成功", manager: "经理", finance: "财务",
  procurement: "采购", sales: "销售", compliance: "合规",
};

// ═══════════════════════════ 通用枚举词典（镜像 app/ux_copy.py::STATUS_CN + 扩展）═══════════════════════════
// 只作为「domain 内 token 且无字段专属覆盖」时的兜底；见文件头②两处刻意偏离说明。
const GENERIC_ENUM_CN: Record<string, string> = {
  // —— 镜像自 ux_copy.STATUS_CN（37 项，"open"/"pending" 两项按②改为对象卡语境默认值）——
  open: "未处置", acknowledged: "已受理", mitigating: "处置中", resolved: "已解决", escalated: "已升级",
  assigned: "已派单", in_progress: "处理中", done: "已完成", cancelled: "已取消",
  pending: "待处理", approved: "已批准", rejected: "已驳回",
  draft: "草稿", in_precheck: "预审中", plan_ready: "方案就绪", priced: "已报价",
  needs_more_info: "待补资料", quote_with_conditions: "有条件批准",
  candidate: "候选", active: "在售",
  allocated: "已分配", at_risk: "有风险", backordered: "缺货待补",
  reserved: "已预留", released: "已释放", fulfilled: "已完成履约",
  received: "已收货", under_review: "审核中", disputed: "争议中",
  sent: "已发出", on_hold: "已冻结",
  expedited: "已加急", claim_raised: "已发起索赔", variance_accepted: "差异已接受",
  revoked: "已吊销", provided: "已提供", reconciled: "已核对平", responded: "已回复",
  // —— 新增：任务书直接给定的例词 ——
  passed: "已通过", complete: "齐全（历史枚举外写法，标准写法为 provided）",
  verified: "已核验", in_transit: "在途",
  // —— 新增：Supplier 合规/验厂类 ——
  missing: "缺失", partial: "部分提供", not_started: "未开始", failed: "未通过", waived: "已豁免",
  // —— 新增：Shipment 清关/生命周期/运输方式/港口 ——
  not_filed: "未申报", filed: "已申报", hold: "查验中",
  planned: "计划中", arrived: "已到港", customs: "清关中", delivered: "已送达",
  ocean_fcl: "海运整柜", ocean_lcl: "海运拼箱",
  yantian: "盐田", shekou: "蛇口", ningbo: "宁波", los_angeles: "洛杉矶", long_beach: "长滩",
  // new_york/savannah（美东）、rotterdam/hamburg（欧洲）：U5 独立复核脚本对照 ontology.json 发现
  // Shipment.destination_port 声明域实际有 6 个值而非 2 个（v0.11.3 "G6" 决策新增，sim 世界美东/
  // 欧洲航线真实设计数据），本文件先前只登记了 2 个——转录遗漏，非本任务改动范围外的数据残留，
  // 见下方 FIELD_ENUM_DOMAIN.Shipment.destination_port 与文件头③勘误说明。
  new_york: "纽约", savannah: "萨凡纳", rotterdam: "鹿特丹", hamburg: "汉堡",
  // —— 新增：PurchaseOrder 生命周期 ——
  placed: "已下单", ready: "备货完成", shipped: "已发运", closed: "已关闭",
  // —— 新增：Payment ——
  scheduled: "待结算", paid: "已结清", in: "应收", out: "应付",
  customer: "客户", supplier: "供应商", vendor: "物流/服务商",
  sales_order: "销售订单", supplier_invoice: "供应商发票", invoice: "费用发票",
  // —— 新增：Task 时效 ——
  due_today: "今日到期", overdue: "已逾期",
  // —— 新增：Sku 品类/平台 ——
  charger: "充电器", cable: "数据线", earbuds: "耳机", phone_case: "手机壳",
  // seasonal_gift：U5 独立复核脚本对照 ontology.json 发现 Sku.category 声明域实际有 5 个值而非
  // 4 个（v0.11.3 "G6" 决策新增，sim 世界真实设计品类：LED 节日灯/礼品套装/节庆装饰品）——转录
  // 遗漏，非残留数据，见下方 FIELD_ENUM_DOMAIN.Sku.category 与文件头③勘误说明。
  seasonal_gift: "节庆礼品",
  amazon: "亚马逊", walmart: "沃尔玛", tiktok_shop: "TikTok Shop", shopify: "Shopify", other: "其他",
  // —— 新增：Customer 分类 ——
  platform_seller: "平台卖家", brand_dtc: "品牌独立站", trader: "贸易商", service_provider: "服务商",
  has_ior: "已具备进口商资质", needs_partner: "需借助合作伙伴", unknown: "待确认",
  has_broker: "已有报关行", needs_broker: "需报关行",
  low: "低", medium: "中", high: "高", critical: "严重",
  // —— 新增：布尔字符串形态（DQ 标准值域展示 / 极少数历史字符串残留场景用）——
  true: "是", false: "否",
  // —— 新增：Invoice ——
  USD: "美元",
  // —— 新增：单证类型（镜像 ux_copy.py::_MISSING_DOC_CN，供 Shipment.missing_docs 列表逐项翻译）——
  commercial_invoice: "商业发票", packing_list: "装箱单", bill_of_lading: "提单",
  isf: "ISF 进口安全申报",
  // sim/config.yaml:166 的 missing_docs_pool 与本体声明域不完全一致（certificate_of_origin/
  // ISF_filing 两 token 不在 ontology 的 Shipment.missing_docs 枚举里，已用源码核对非猜测——
  // 该 config 是另一域 sim/ 的配置漂移，不在本任务改动范围，已在报告里另行提出）。这两个 token
  // 语义清晰无歧义（国际贸易通用单证名/与 isf 明显同义只是大小写下划线写法不同），先做防御翻译，
  // 不因域外就放弃可读性；不确定语义的 token 才应该原样兜底，这两个不属于"不确定"。
  certificate_of_origin: "原产地证书", ISF_filing: "ISF 进口安全申报",
  // —— 新增：Invoice.vendor_type ——
  carrier: "船公司", forwarder: "货运代理", warehouse: "仓储服务商", last_mile: "尾程派送商",
  // —— 新增：Warehouse.type ——
  overseas: "海外仓", bonded: "保税仓", domestic: "国内仓", FBA: "FBA 仓", "3PL": "第三方仓（3PL）",
};

// RiskEvent.type（21 值）← RiskEvent.rule_id 的一手对应关系。已逐条对照 engine/rules.py、
// engine/cost_rules.py、engine/procurement_rules.py、engine/warehouse_rules.py、
// engine/sourcing_rules.py、engine/finance_rules.py 里全部 emit(rule_id, type, ...) 调用核实
// （非猜测拼词），故直接复用已 import 的 RULE_CN 推导，不另存一份可能漂移的字符串副本。
const RISK_TYPE_TO_RULE: Record<string, string> = {
  delay_breach: "R1", docs_missing: "R2", stalled: "R3",
  rate_overbilling: "R4", duplicate_charge: "R5", unplanned_charge: "R6",
  supplier_delay: "R7", short_receipt: "R8", qc_failure: "R9", price_qty_mismatch: "R10",
  invoice_over_receipt: "R11", prepayment_exposure: "R12", qualification_expired: "R13",
  single_source: "R14", maverick_spend: "R15",
  stockout: "R16", unfulfillable: "R17", shrinkage: "R18",
  overdue_receivable: "R19", cash_watch: "R20", payment_anomaly: "R21",
  performance_degradation: "R22", qualification_expiry_warning: "R23",
};
const RISK_TYPE_CN: Record<string, string> = Object.fromEntries(
  Object.entries(RISK_TYPE_TO_RULE).map(([type, ruleId]) => [type, RULE_CN[ruleId] ?? type]),
);

// ═══════════════════════════ 按字段覆盖表（精确优先于通用词典）═══════════════════════════
// 同一 token 在不同字段里语义不同时（如 "released" 对货运是"已释放"、对清关是"已放行"），
// 或需要接入外部权威表（角色/规则/处置动作）时，在此登记。命中优先于 GENERIC_ENUM_CN。
// 亦承载对已核验的历史枚举外残留值的显式翻译（见文件头③），只收录有据可查的两个 Supplier 字段。
const FIELD_ENUM_OVERRIDES: Record<string, Record<string, Record<string, string>>> = {
  Shipment: {
    customs_status: { released: "已放行" }, // 与 GENERIC 的"已释放"（库存/预留语境）区分
    status: {}, // 生命周期各值均由 GENERIC 覆盖（planned/in_transit/arrived/customs/delivered）
  },
  RiskEvent: {
    type: RISK_TYPE_CN,
    severity: SEV_CN, // 镜像 aiFlowModel.ts 权威表（critical/high/medium/low）
    outcome: {
      mitigated: "已缓解", accepted_delay: "已接受延误", false_alarm: "误报", escalated: "已升级",
    },
  },
  Task: {
    assignee_role: ROLE_CN,
    proposal_actor_role: ROLE_CN,
    approved_by_role: ROLE_CN, // 本体未声明该字段合法值列表（values=null），故无 domain 校验，见③
    proposed_action: ACTION_CN, // 镜像 aiFlowModel.ts 权威表（已补 collect/chase_docs/escalate）
    approval_status: { pending: "待审批" }, // 与②：审批语境精确覆盖通用"待处理"
    sla_state: { open: "未到期", due_today: "今日到期", overdue: "已逾期" }, // "未到期"≠"未处置"
  },
  Customer: {
    risk_tier: { low: "低", medium: "中", high: "高", critical: "严重" }, // 信用风险等级，非告警严重度
  },
  Supplier: {
    // ③ 已核验的历史枚举外残留值（V12 诊断 + G5-sim 处置单口径）：compliance_docs_status 曾出现
    // 'complete'（标准写法应为 provided）；uflpa_risk_flag 曾出现字符串 'low'/'high'（标准是
    // boolean，G5-sim 换算 low→false/high→true）。当前 data/simworld.sqlite 已核验清零，
    // 此处保留作历史残留复现时的兜底翻译（不因数据已修复而删除防线）。
    compliance_docs_status: { complete: "齐全（历史枚举外写法，标准写法为 provided）" },
    uflpa_risk_flag: { low: "否（历史枚举外写法，标准应为 boolean false）",
                        high: "是（历史枚举外写法，标准应为 boolean true）" },
  },
  SalesOrder: {
    status: { open: "未开始履约", in_fulfillment: "履约中" },
  },
  SalesOrderLine: {
    line_status: { open: "待分配" }, // 状态机 open→allocated→...，"待分配"精确对应 open 语义
  },
  Invoice: {
    status: { received: "已收到" }, // 与 GENERIC 的"已收货"（到货语境）区分——这里是发票收到
  },
};

// ═══════════════════════════ 字段合法值域（本体 Literal 声明，用于 DQ 徽标"标准值域"展示 + 域外安全兜底）═══════════════════════════
// 直接转录自 ontology/control-tower-ontology.json 各 enum 属性的 values 数组（已用
// pipeline/ontology_models.py 生成的 Pydantic 模型交叉核对一致）。域外的值不强行套用通用词典
// 翻译（防止借错字段语义硬翻，见文件头③），除非在 FIELD_ENUM_OVERRIDES 显式登记。
const FIELD_ENUM_DOMAIN: Record<string, Record<string, string[]>> = {
  Shipment: {
    mode: ["ocean_fcl", "ocean_lcl"],
    origin_port: ["yantian", "shekou", "ningbo"],
    destination_port: ["los_angeles", "long_beach", "new_york", "savannah", "rotterdam", "hamburg"],
    customs_status: ["not_filed", "filed", "hold", "released"],
    status: ["planned", "in_transit", "arrived", "customs", "delivered"],
    missing_docs: ["commercial_invoice", "packing_list", "bill_of_lading", "isf"],
    // container_type/incoterm/carrier_scac/*_locode：行业通用代码，刻意不翻译（见 enumLabel 说明）
  },
  RiskEvent: {
    type: Object.keys(RISK_TYPE_TO_RULE),
    rule_id: Object.values(RISK_TYPE_TO_RULE),
    severity: ["medium", "high", "critical"],
    status: ["open", "acknowledged", "mitigating", "resolved", "escalated"],
    outcome: ["mitigated", "accepted_delay", "false_alarm", "escalated"],
  },
  Task: {
    assignee_role: ["ops", "cs", "manager", "finance"],
    priority: ["P1", "P2", "P3"],
    sla_state: ["open", "due_today", "overdue"],
    approval_status: ["pending", "approved", "rejected"],
    proposal_actor_role: ["ops", "cs", "finance"],
    status: ["assigned", "in_progress", "done", "cancelled"],
    // proposed_action：值集来自 ACTION_CN 的 key 全集（口径见 aiFlowModel.ts），approved_by_role：
    // 本体未声明合法值列表，两者均不登记 domain（不代表无效，只是不做域外校验）
  },
  Customer: {
    business_model: ["platform_seller", "brand_dtc", "trader", "service_provider", "other"],
    ior_capability: ["has_ior", "needs_partner", "unknown"],
    broker_status: ["has_broker", "needs_broker", "unknown"],
    risk_tier: ["low", "medium", "high", "critical"],
  },
  Supplier: {
    factory_audit_status: ["not_started", "pending", "passed", "failed", "waived"],
    compliance_docs_status: ["missing", "partial", "provided", "verified", "rejected"],
    origin_evidence_status: ["missing", "provided", "verified", "rejected"],
    uflpa_risk_flag: ["true", "false"],
  },
  SalesOrder: { status: ["open", "in_fulfillment", "fulfilled", "cancelled"] },
  SalesOrderLine: { line_status: ["open", "allocated", "at_risk", "fulfilled", "cancelled"] },
  Payment: {
    direction: ["in", "out"],
    counterparty_type: ["customer", "supplier", "vendor"],
    ref_type: ["sales_order", "supplier_invoice", "invoice"],
    status: ["scheduled", "paid"],
  },
  Sku: {
    category: ["charger", "cable", "earbuds", "phone_case", "seasonal_gift"],
    sku_status: ["candidate", "active"],
    platform: ["amazon", "walmart", "tiktok_shop", "shopify", "other"],
  },
  Warehouse: { type: ["overseas", "bonded", "domestic", "FBA", "3PL"] },
  Invoice: {
    vendor_type: ["carrier", "forwarder", "warehouse", "last_mile"],
    currency: ["USD"],
    status: ["received", "under_review", "disputed", "approved"],
  },
  PurchaseOrder: { status: ["placed", "ready", "shipped", "closed", "cancelled"] },
};

// 哪些 (type, field) 走枚举/角色人话翻译（安全闸门：只有登记过的字段才尝试翻译，防止自由文本
// 字段——如城市名、客户名、ID——恰好撞上某个 token 而被误翻）。按 type 对四张表（含 U5 新增的
// 另 23 类 _EXT 表）的字段名取并集（注意：不能用 {...FIELD_ENUM_DOMAIN, ...FIELD_ENUM_OVERRIDES}
// 做对象浅展开——同一 type 的 value 会被整体覆盖而非按字段合并，浏览器实测抓到过这个坑：Supplier
// 的 factory_audit_status/origin_evidence_status 只在 FIELD_ENUM_DOMAIN 里登记、不在
// FIELD_ENUM_OVERRIDES 里，浅展开会让它们从合并结果里消失，导致这两个字段的枚举值原样显示
// 英文——已改成逐 type 显式取键名并集；U5 延续同一写法，不重犯）。
const ENUM_FIELDS: Record<string, Set<string>> = {};
for (const type of new Set([
  ...Object.keys(FIELD_ENUM_DOMAIN), ...Object.keys(FIELD_ENUM_OVERRIDES),
  ...Object.keys(FIELD_ENUM_DOMAIN_EXT), ...Object.keys(FIELD_ENUM_OVERRIDES_EXT),
])) {
  ENUM_FIELDS[type] = new Set([
    ...Object.keys(FIELD_ENUM_DOMAIN[type] ?? {}),
    ...Object.keys(FIELD_ENUM_OVERRIDES[type] ?? {}),
    ...Object.keys(FIELD_ENUM_DOMAIN_EXT[type] ?? {}),
    ...Object.keys(FIELD_ENUM_OVERRIDES_EXT[type] ?? {}),
  ]);
}

/**
 * 枚举/角色 token → 中文。优先级：① 字段专属覆盖（含已核验的域外残留值，见文件头③；U5 起
 * 也查另 23 类的 FIELD_ENUM_OVERRIDES_EXT）② 若该字段有已知合法值域且当前值不在域内——判定为
 * 未经决策记录确认的脏数据，原样呈现（不借别的字段的同名 token 语义硬翻，交由 DQ 徽标另行解释；
 * U5 起域表也查 FIELD_ENUM_DOMAIN_EXT）③ 通用词典（U5 起也查 ENUM_CN_EXT）④ 兜底原样。
 */
function enumLabel(type: string, field: string, raw: string): string {
  const override = FIELD_ENUM_OVERRIDES[type]?.[field] ?? FIELD_ENUM_OVERRIDES_EXT[type]?.[field];
  if (override && Object.prototype.hasOwnProperty.call(override, raw)) return override[raw];
  const domain = FIELD_ENUM_DOMAIN[type]?.[field] ?? FIELD_ENUM_DOMAIN_EXT[type]?.[field];
  if (domain && !domain.includes(raw)) return raw;
  return GENERIC_ENUM_CN[raw] ?? ENUM_CN_EXT[raw] ?? raw;
}

/** 布尔值 → 是/否。 */
export function boolLabel(v: boolean): string {
  return v ? "是" : "否";
}

// 需要"code + 中文名"组合呈现的字段（而非纯替换）——RiskEvent.rule_id 与 U5 新增的
// InvoiceLine/ExpectedCost.charge_code：这些码本身在账单/决策文档里被频繁引用（"R7"/"THC"），
// 单独替换成中文会丢失这个可核对的锚点，故沿用 app/ux_copy.py::rule_label()/charge_code_label()
// 的"码+空格+中文"格式（charge_code 的中文表 CHARGE_CODE_CN 镜像自 ux_copy.py 同名表，见
// objectLabelsData.ts 头部说明）。
const SPECIAL_FIELD_RENDERERS: Record<string, Record<string, (raw: string) => string>> = {
  RiskEvent: {
    rule_id: (raw) => (RULE_CN[raw] ? `${raw} ${RULE_CN[raw]}` : raw),
  },
  InvoiceLine: {
    charge_code: (raw) => (CHARGE_CODE_CN[raw] ? `${raw} ${CHARGE_CODE_CN[raw]}` : raw),
  },
  ExpectedCost: {
    charge_code: (raw) => (CHARGE_CODE_CN[raw] ? `${raw} ${CHARGE_CODE_CN[raw]}` : raw),
  },
};

function humanizeArray(type: string, field: string, items: unknown[]): string {
  if (items.length === 0) return "—";
  return items.map((item) => enumLabel(type, field, String(item))).join("、");
}

/** "[\"a\",\"b\"]" 形态的字符串 → 真数组；不是这个形态或解析失败 → null（不误判普通字符串）。 */
function tryParseJsonArray(raw: string): unknown[] | null {
  const t = raw.trim();
  if (!t.startsWith("[") || !t.endsWith("]")) return null;
  try {
    const parsed = JSON.parse(t);
    return Array.isArray(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

// 用 "|" 分隔而非 JSON 的列表字段——已对照源码核对（非猜测）：datagen/generate.py:132 与
// sim/store.py:234 都写 `"|".join(...)`，两个世界一致；空列表落地为空字符串 ""（非 "[]"）。
// 只登记确认过的字段，避免把恰好含 "|" 字符的自由文本字段误判成列表。
const PIPE_LIST_FIELDS: Record<string, Set<string>> = {
  Shipment: new Set(["missing_docs", "po_ids"]),
};

/**
 * 字段值 → 人话字符串；不适用本层特殊处理时返回 null（调用方走自己的默认 stringify）。
 * 处理顺序：① 布尔 → 是/否（类型判断，零误翻风险）② 数组（含两种"看起来像数组的字符串"——
 *   RiskEvent.affected_so_line_ids 等 json_list 字段在当前 Pydantic 生成模型里其实是 str
 *   类型，浏览器实测确认线上传的是 '["SOL-...", "SOL-..."]' 这种 JSON 文本而非真数组；
 *   Shipment.missing_docs/po_ids 则是 "a|b" 管道分隔——两种格式都已对照 apps/api 真实响应/
 *   datagen 源码核对，不是猜的）→ 域内成员逐项人话化、"、"顿号连接，空（含空字符串）显示"—"
 *   （数组项本身是 ID 引用时天然不会命中任何 token，安全）③ 特殊组合渲染器（rule_id）
 *   ④ 登记过的枚举字段 → enumLabel。
 */
export function humanizeFieldValue(type: string, field: string, raw: unknown): string | null {
  if (typeof raw === "boolean") return boolLabel(raw);
  if (Array.isArray(raw)) return humanizeArray(type, field, raw);
  if (typeof raw === "string" && PIPE_LIST_FIELDS[type]?.has(field)) {
    const parts = raw.split("|").map((s) => s.trim()).filter((s) => s.length > 0);
    return humanizeArray(type, field, parts);
  }
  if (typeof raw === "string") {
    const asArray = tryParseJsonArray(raw);
    if (asArray) return humanizeArray(type, field, asArray);
  }
  const special = SPECIAL_FIELD_RENDERERS[type]?.[field];
  if (special && typeof raw === "string") return special(raw);
  if (typeof raw === "string" && ENUM_FIELDS[type]?.has(field)) {
    return enumLabel(type, field, raw);
  }
  return null;
}

// ═══════════════════════════ 数据质量提示（_validation_warnings 人话化）═══════════════════════════
export interface DqWarning {
  field: string;
  problem: string;
}

/**
 * 单条 _validation_warnings → 人话句。原始值取自同一响应体里 fields[field]（apps/api 校验失败时
 * 走 `obj = dict(row)` 原样返回，未校验的原始值天然就在同一个 fields 对象里，不需要解析
 * pydantic 报错文案去猜——见 apps/api/main.py:270-279）。
 */
export function humanizeDqWarning(type: string, w: DqWarning, rawValue: unknown): string {
  const label = fieldLabel(type, w.field);
  const valueStr = rawValue === null || rawValue === undefined ? "空" : String(rawValue);
  const domain = FIELD_ENUM_DOMAIN[type]?.[w.field] ?? FIELD_ENUM_DOMAIN_EXT[type]?.[w.field];
  if (domain) {
    const domainCn = domain.map((v) => enumLabel(type, w.field, v)).join("、");
    return `〔${label}〕记录值 '${valueStr}' 与图纸标准写法不一致（标准值域：${domainCn}）——模拟数据历史遗留`;
  }
  return `〔${label}〕记录值 '${valueStr}' 不符合当前对象定义规范——模拟数据历史遗留（技术详情：${w.problem}）`;
}

// ═══════════════════════════ 关系词条中文 + 方向语义（本体 56 条 link 全覆盖）═══════════════════════════
// "正向/反向"是图论术语，Daniel 看不懂；改成"当前对象与邻居对象的关系短句"，配合箭头方向
// 呈现——不复述邻居类型名（类型名已单独显示在箭头旁），只说清"这条关系对当前对象意味着什么"。
// forward：当前对象是 link 的 source 时（对邻居/target 的描述）；
// reverse：当前对象是 link 的 target 时（对邻居/source 的描述）。
// 参考本体 links 数组的 source/target/cardinality 语义逐条撰写，非字面直译 linkType 代码。
interface LinkPhrase {
  forward: string;
  reverse: string;
}
export const LINK_CN: Record<string, LinkPhrase> = {
  customer_places: { forward: "它下的", reverse: "下单方" },
  so_has_line: { forward: "包含的行", reverse: "所属订单" },
  line_for_sku: { forward: "对应的", reverse: "购买它的订单行" },
  supplier_provides: { forward: "它供应的", reverse: "供应此项的" },
  po_from_supplier: { forward: "采购自的", reverse: "来自它的" },
  po_shipped_by: { forward: "由此发运", reverse: "承运的采购单" },
  shipment_has_milestone: { forward: "全部动态", reverse: "所属货件" },
  allocation_to_shipment: { forward: "分配自的", reverse: "分配记录" },
  allocation_to_line: { forward: "分配给的", reverse: "分配记录" },
  risk_on_shipment: { forward: "涉及的", reverse: "关联的风险" },
  risk_affects_line: { forward: "波及的", reverse: "波及此行的风险" },
  task_handles_risk: { forward: "处置的", reverse: "处置任务" },
  // reverse 短语刻意不用"协调事项"（会与邻居类型名 OBJECT_TYPE_CN.CoordinationThread 重复，
  // 拼出来变成"← 协调事项（协调事项）"——浏览器实测发现，改用"跟进记录"更点出这是什么）。
  coordination_on_task: { forward: "关联的", reverse: "跟进记录" },
  coordination_on_risk: { forward: "关联的", reverse: "跟进记录" },
  case_has_sku: { forward: "准入的", reverse: "所属准入案件" },
  case_for_customer: { forward: "服务的", reverse: "提交的准入案件" },
  case_has_finding: { forward: "案件下的", reverse: "所属案件" },
  case_has_plan: { forward: "案件下的", reverse: "所属案件" },
  plan_has_scenario: { forward: "方案下的", reverse: "所属方案" },
  shipment_has_container: { forward: "所载集装箱", reverse: "所属货件" },
  invoice_for_shipment: { forward: "对应的", reverse: "相关费用发票" },
  invoice_has_line: { forward: "发票明细", reverse: "所属发票" },
  line_bills_container: { forward: "计费柜号", reverse: "相关发票行" },
  expected_cost_of_shipment: { forward: "对应的", reverse: "费用基准行" },
  risk_affects_invoice_line: { forward: "波及的", reverse: "波及此行的风险" },
  po_has_line: { forward: "采购行", reverse: "所属采购单" },
  po_line_for_sku: { forward: "对应的", reverse: "关联的采购行" },
  grn_for_po: { forward: "收货自的", reverse: "收货记录" },
  grn_has_line: { forward: "收货明细", reverse: "所属收货单" },
  grn_line_for_po_line: { forward: "对应的", reverse: "关联的收货行" },
  supplier_invoice_for_po: { forward: "对应的", reverse: "相关供应商发票" },
  supplier_invoice_from_supplier: { forward: "开票方", reverse: "开出的发票" },
  supplier_invoice_has_line: { forward: "发票明细", reverse: "所属发票" },
  supplier_invoice_line_for_po_line: { forward: "对应的", reverse: "关联的发票行" },
  po_has_payment: { forward: "付款记录", reverse: "所属采购单" },
  supplier_has_qualification: { forward: "资质记录", reverse: "所属供应商" },
  risk_on_po: { forward: "涉及的", reverse: "关联的风险" },
  risk_on_supplier: { forward: "涉及的", reverse: "关联风险" },
  risk_affects_po_line: { forward: "波及的", reverse: "波及此行的风险" },
  position_in_warehouse: { forward: "所在仓库", reverse: "仓内库存条目" },
  position_for_sku: { forward: "对应的", reverse: "该 SKU 库存条目" },
  reservation_on_position: { forward: "预留自的", reverse: "预留记录" },
  reservation_for_line: { forward: "预留给的", reverse: "预留记录" },
  cycle_count_on_position: { forward: "盘点的", reverse: "该库存的盘点" },
  cycle_count_in_warehouse: { forward: "所在仓库", reverse: "仓内盘点" },
  shipment_to_warehouse: { forward: "运往的", reverse: "入库货件" },
  risk_on_warehouse: { forward: "涉及的", reverse: "关联的风险" },
  rfq_for_sku: { forward: "询价的", reverse: "相关询价单" },
  rfq_has_line: { forward: "询价明细", reverse: "所属询价单" },
  rfq_has_quote: { forward: "收到的报价", reverse: "所属询价单" },
  rfq_line_for_sku: { forward: "询价的", reverse: "相关询价行" },
  quote_from_supplier: { forward: "报价方", reverse: "提交的报价" },
  risk_affects_sku: { forward: "波及的", reverse: "波及此项的风险" },
  payment_settles_supplier_invoice: { forward: "结算的", reverse: "结算此发票的付款" },
  payment_settles_invoice: { forward: "结算的", reverse: "结算此发票的付款" },
  payment_collects_order: { forward: "收取的", reverse: "收此单货款的记录" },
};

/** linkType + 方向 → 人话短语（不含箭头/邻居类型名，调用方拼成"→ 类型（短语）"）。
 *  未登记的 linkType 兜底"关联的/关联"（理论上不会发生——本表已覆盖本体全部 56 条关系，
 *  这里只是防未来新增关系时的优雅降级，不是遗漏借口）。 */
export function linkPhrase(linkType: string, direction: "forward" | "reverse"): string {
  const l = LINK_CN[linkType];
  if (!l) return direction === "forward" ? "关联的" : "关联";
  return direction === "forward" ? l.forward : l.reverse;
}
