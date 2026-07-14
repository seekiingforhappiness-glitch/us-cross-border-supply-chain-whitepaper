# GENERATED FROM ontology v0.11.3 — DO NOT EDIT，重跑 python3 -m pipeline.generate_models
# -*- coding: utf-8 -*-
"""桥3 结构生成产物：本体 35 对象的 Pydantic 模型（数据契约校验用）。

来源：ontology/control-tower-ontology.json（objects[].properties）。
生成器：pipeline/generate_models.py。手改无效——改本体后重跑生成器覆盖本文件。

用途：pipeline.build_ontology 插入前 `Model.model_validate(row)` 做行级契约校验
（warn→enforce 两档）。基类 _Base：extra 忽略（行里多余键不报错）+ 空串→None 归一
（datagen 缺省可空字段产 ""，归一后可空字段合法、必填字段暴露为违例）。
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, field_validator
from pydantic_core.core_schema import ValidationInfo


class _Base(BaseModel):
    """全模型基类：忽略多余键；before-validator 把**可空字段**的空串归一为 None。

    仅对可空（Optional，有默认 None）字段做 ""→None：datagen 对缺省可空字段产空串，语义=缺省。
    必填字段的空串保持原样（"" 对必填 str/list-as-str 是合法在场空值，如 missing_docs="" 表示无缺失
    单证）——不误伤，也不放水（必填数值/枚举字段若为 "" 会在后续类型校验如实暴露为违例）。"""

    model_config = ConfigDict(extra="ignore")

    @field_validator("*", mode="before")
    @classmethod
    def _empty_str_to_none(cls, v, info: ValidationInfo):
        if v == "":
            field = cls.model_fields.get(info.field_name)
            if field is not None and not field.is_required():
                return None
        return v



class Supplier(_Base):
    """Supplier — GENERATED, 表 suppliers。"""
    supplier_id: str
    supplier_name: str
    city: str
    lead_time_days: int
    factory_audit_status: Optional[Literal['not_started', 'pending', 'passed', 'failed', 'waived']] = None
    compliance_docs_status: Optional[Literal['missing', 'partial', 'provided', 'verified', 'rejected']] = None
    uflpa_risk_flag: Optional[bool] = None
    origin_evidence_status: Optional[Literal['missing', 'provided', 'verified', 'rejected']] = None
    payment_terms_days: Optional[int] = None


class Sku(_Base):
    """Sku — GENERATED, 表 skus。"""
    sku_id: str
    sku_name: str
    supplier_id: Optional[str] = None
    category: Literal['charger', 'cable', 'earbuds', 'phone_case', 'seasonal_gift']
    unit_price_usd: float
    sku_status: Literal['candidate', 'active']
    declared_value_usd: Optional[float] = None
    package_weight_kg: Optional[float] = None
    package_l_cm: Optional[float] = None
    package_w_cm: Optional[float] = None
    package_h_cm: Optional[float] = None
    battery_flag: Optional[bool] = None
    food_contact_flag: Optional[bool] = None
    children_product_flag: Optional[bool] = None
    material: Optional[str] = None
    use_case: Optional[str] = None
    origin_country: Optional[str] = None
    platform: Optional[Literal['amazon', 'walmart', 'tiktok_shop', 'shopify', 'other']] = None


class Customer(_Base):
    """Customer — GENERATED, 表 customers。"""
    customer_id: str
    customer_name: str
    tier: Literal['A', 'B', 'C']
    us_state: str
    business_model: Optional[Literal['platform_seller', 'brand_dtc', 'trader', 'service_provider', 'other']] = None
    sales_channel: Optional[str] = None
    ior_capability: Optional[Literal['has_ior', 'needs_partner', 'unknown']] = None
    broker_status: Optional[Literal['has_broker', 'needs_broker', 'unknown']] = None
    credit_terms: Optional[str] = None
    risk_tier: Optional[Literal['low', 'medium', 'high', 'critical']] = None


class SalesOrder(_Base):
    """SalesOrder — GENERATED, 表 sales_orders。"""
    so_id: str
    customer_id: str
    order_date: str
    status: Literal['open', 'in_fulfillment', 'fulfilled', 'cancelled']


class SalesOrderLine(_Base):
    """SalesOrderLine — GENERATED, 表 sales_order_lines。"""
    so_line_id: str
    so_id: str
    sku_id: str
    qty: int
    unit_price_usd: float
    promised_delivery_date: str
    original_promised_date: str
    reschedule_count: int
    line_status: Literal['open', 'allocated', 'at_risk', 'fulfilled', 'cancelled']


class PurchaseOrder(_Base):
    """PurchaseOrder — GENERATED, 表 purchase_orders。"""
    po_id: str
    supplier_id: str
    sku_id: str
    qty: int
    po_date: str
    expected_ready_date: str
    status: Literal['placed', 'ready', 'shipped', 'closed', 'cancelled']


class Shipment(_Base):
    """Shipment — GENERATED, 表 shipments。"""
    shipment_id: str
    booking_no: str
    mbl_no: str
    mode: Literal['ocean_fcl', 'ocean_lcl']
    container_no: Optional[str] = None
    container_type: Literal['40HC', '40GP', '20GP']
    gross_weight_kg: float
    volume_cbm: float
    incoterm: Literal['FOB', 'CIF', 'DDP']
    vessel_voyage: Optional[str] = None
    carrier_name: Optional[str] = None
    carrier_scac: Literal['COSU', 'OOLU', 'MATS', 'ZIMU', 'EGLV', 'ONEY', 'MAEU', 'MSCU', 'CMDU', 'HLCU']
    origin_port: Literal['yantian', 'shekou', 'ningbo']
    origin_port_locode: Literal['CNYTN', 'CNSHK', 'CNNGB']
    destination_port: Literal['los_angeles', 'long_beach', 'new_york', 'savannah', 'rotterdam', 'hamburg']
    destination_port_locode: Literal['USLAX', 'USLGB', 'USNYC', 'USSAV', 'NLRTM', 'DEHAM']
    destination_warehouse: str
    etd: str
    eta_initial: str
    eta_current: str
    ata: Optional[str] = None
    customs_status: Literal['not_filed', 'filed', 'hold', 'released']
    missing_docs: str
    expedite_flag: bool
    delay_days: Optional[int] = None
    last_event_time: Optional[str] = None
    po_ids: Optional[str] = None
    status_source: Optional[str] = None
    status: Literal['planned', 'in_transit', 'arrived', 'customs', 'delivered']


class ShipmentMilestone(_Base):
    """ShipmentMilestone — GENERATED, 表 shipment_milestones。"""
    milestone_id: str
    shipment_id: str
    event_type: Literal['booking_confirmed', 'departed', 'eta_change', 'transshipment', 'arrived', 'customs_filed', 'customs_hold', 'customs_released', 'delivered']
    event_classifier: Literal['ACT', 'EST']
    event_time: str
    event_locode: str
    new_eta: Optional[str] = None
    source_system: Literal['carrier_edi', 'forwarder_portal', 'manual']
    ingested_at: str
    is_duplicate: Optional[bool] = None


class ShipmentAllocation(_Base):
    """ShipmentAllocation — GENERATED, 表 shipment_allocations。"""
    allocation_id: str
    shipment_id: str
    so_line_id: str
    allocated_qty: int


class RiskEvent(_Base):
    """RiskEvent — GENERATED, 表 risk_events。"""
    risk_event_id: str
    type: Literal['delay_breach', 'docs_missing', 'stalled', 'rate_overbilling', 'duplicate_charge', 'unplanned_charge', 'supplier_delay', 'short_receipt', 'qc_failure', 'price_qty_mismatch', 'invoice_over_receipt', 'prepayment_exposure', 'qualification_expired', 'single_source', 'maverick_spend', 'stockout', 'unfulfillable', 'shrinkage', 'overdue_receivable', 'cash_watch', 'payment_anomaly']
    rule_id: Literal['R1', 'R2', 'R3', 'R4', 'R5', 'R6', 'R7', 'R8', 'R9', 'R10', 'R11', 'R12', 'R13', 'R14', 'R15', 'R16', 'R17', 'R18', 'R19', 'R20', 'R21']
    severity: Literal['medium', 'high', 'critical']
    shipment_id: Optional[str] = None
    po_id: Optional[str] = None
    supplier_id: Optional[str] = None
    affected_po_line_ids: Optional[str] = None
    warehouse_id: Optional[str] = None
    affected_so_line_ids: str
    affected_invoice_line_ids: Optional[str] = None
    affected_sku_ids: Optional[str] = None
    affected_value_usd: Optional[float] = None
    detected_at: str
    root_cause: str
    status: Literal['open', 'acknowledged', 'mitigating', 'resolved', 'escalated']
    resolved_at: Optional[str] = None
    outcome: Optional[Literal['mitigated', 'accepted_delay', 'false_alarm', 'escalated']] = None
    resolution_summary: Optional[str] = None


class Task(_Base):
    """Task — GENERATED, 表 tasks。"""
    task_id: str
    risk_event_id: str
    title: str
    assignee_role: Literal['ops', 'cs', 'manager', 'finance']
    priority: Literal['P1', 'P2', 'P3']
    due_at: str
    assignee_user_id: Optional[str] = None
    assignee_team_id: Optional[str] = None
    sla_state: Optional[Literal['open', 'due_today', 'overdue']] = None
    escalation_level: Optional[int] = None
    policy_version: Optional[str] = None
    proposed_action: Optional[Literal['expedite', 'reschedule', 'accept_delay', 'dispute', 'accept_charge', 'rebill_customer', 'expedite_po', 'raise_supplier_claim', 'dispute_supplier_invoice', 'accept_receipt_variance', 'escalate_prepayment', 'hold_balance_payment', 'request_supplier_docs', 'suspend_supplier', 'suggest_substitution', 'adjust_inventory', 'escalate_replenishment', 'initiate_second_source', 'block_non_po_payment', 'backfill_po', 'collect', 'chase_docs', 'propose_collection', 'reconcile_payment', 'escalate']] = None
    proposal_params: Optional[str] = None
    approval_status: Optional[Literal['pending', 'approved', 'rejected']] = None
    approved_by_role: Optional[str] = None
    assigned_by_actor_id: Optional[str] = None
    proposal_actor_id: Optional[str] = None
    proposal_actor_role: Optional[Literal['ops', 'cs', 'finance']] = None
    action_taken: Optional[str] = None
    status: Literal['assigned', 'in_progress', 'done', 'cancelled']


class AdmissionCase(_Base):
    """AdmissionCase — GENERATED, 表 admission_cases。"""
    admission_case_id: str
    case_title: str
    customer_id: str
    sku_id: str
    request_type: Literal['new_sku', 'ddp_quote', 'dap_quote', 'plan_review']
    incoterm_candidate: Literal['FOB', 'DAP', 'DDP', 'tbd']
    target_launch_date: str
    monthly_order_estimate: int
    risk_level: Optional[Literal['low', 'medium', 'high', 'critical']] = None
    status: Literal['draft', 'in_precheck', 'plan_ready', 'priced', 'approved', 'quote_with_conditions', 'rejected', 'needs_more_info']
    decision: Optional[Literal['approve', 'quote_with_conditions', 'reject', 'more_info']] = None
    decision_reason: Optional[str] = None
    conditions: Optional[str] = None


class ComplianceFinding(_Base):
    """ComplianceFinding — GENERATED, 表 compliance_findings。"""
    compliance_finding_id: str
    admission_case_id: str
    finding_title: str
    finding_type: Literal['hts', 'pga', 'labeling', 'certification', 'origin', 'uflpa', 'ad_cvd', 'section_301', 'platform_rule']
    severity: Literal['low', 'medium', 'high', 'critical']
    hts_candidate: Optional[str] = None
    pga_agency: Optional[Literal['CBP', 'FDA', 'FCC', 'CPSC', 'EPA', 'USDA', 'none']] = None
    required_document: Optional[str] = None
    evidence_status: Literal['missing', 'provided', 'verified', 'rejected']
    recommendation: Literal['accept', 'more_docs', 'dap_only', 'reject', 'escalate']


class LogisticsPlan(_Base):
    """LogisticsPlan — GENERATED, 表 logistics_plans。"""
    logistics_plan_id: str
    admission_case_id: str
    plan_name: str
    route_type: Literal['express', 'air_freight', 'ocean_fcl', 'ocean_lcl', 'warehouse_fulfillment']
    incoterm: Literal['FOB', 'DAP', 'DDP']
    origin_port_locode: Literal['CNYTN', 'CNSHK', 'CNNGB']
    destination_port_locode: Literal['USLAX', 'USLGB']
    us_warehouse_region: Literal['west', 'central', 'east', 'platform']
    last_mile_method: Literal['UPS', 'FedEx', 'USPS', 'OnTrac', 'LTL', 'platform']
    estimated_transit_days: int
    sla_risk: Literal['low', 'medium', 'high']
    operational_notes: Optional[str] = None


class CostScenario(_Base):
    """CostScenario — GENERATED, 表 cost_scenarios。"""
    cost_scenario_id: str
    logistics_plan_id: str
    scenario_type: Literal['conservative', 'base', 'optimistic']
    quote_price_usd: float
    product_cost_usd: float
    first_mile_cost_usd: float
    international_freight_usd: float
    duty_tax_usd: float
    customs_brokerage_usd: float
    warehouse_cost_usd: float
    last_mile_cost_usd: float
    returns_allowance_usd: float
    risk_buffer_usd: float
    gross_margin_usd: Optional[float] = None
    gross_margin_rate: Optional[float] = None


class Container(_Base):
    """Container — GENERATED, 表 containers。"""
    container_no: str
    shipment_id: str
    container_type: Literal['40HC', '40GP', '20GP']
    is_primary: bool
    free_days: int
    gross_weight_kg: float
    volume_cbm: float


class Invoice(_Base):
    """Invoice — GENERATED, 表 invoices。"""
    invoice_id: str
    vendor_type: Literal['carrier', 'forwarder', 'warehouse', 'last_mile']
    vendor_name: str
    vendor_invoice_no: str
    shipment_id: str
    issue_date: str
    currency: Literal['USD']
    total_usd: float
    status: Literal['received', 'under_review', 'disputed', 'approved']


class InvoiceLine(_Base):
    """InvoiceLine — GENERATED, 表 invoice_lines。"""
    invoice_line_id: str
    invoice_id: str
    charge_code: Literal['OFT', 'THC', 'DOC', 'FSC', 'CUS', 'DTY', 'WHS', 'STO', 'LMD', 'DET', 'DEM', 'CHS', 'ACC', 'ISF', 'INSP', 'PSS']
    container_no: Optional[str] = None
    qty: int
    unit_price_usd: float
    amount_usd: float


class ExpectedCost(_Base):
    """ExpectedCost — GENERATED, 表 expected_costs。"""
    expected_cost_id: str
    shipment_id: str
    charge_code: Literal['OFT', 'THC', 'DOC', 'FSC', 'CUS', 'DTY', 'WHS', 'STO', 'LMD']
    container_no: Optional[str] = None
    baseline_usd: float
    source: str


class PoLine(_Base):
    """PoLine — GENERATED, 表 po_lines。"""
    po_line_id: str
    po_id: str
    sku_id: str
    qty: int
    unit_price_usd: float
    currency: Literal['USD']
    expected_ready_date: str
    line_status: Literal['open', 'partially_received', 'received', 'closed']
    as_of_date: str
    created_at: str


class GoodsReceipt(_Base):
    """GoodsReceipt — GENERATED, 表 goods_receipts。"""
    grn_id: str
    po_id: str
    received_date: str
    status: Literal['received', 'partial', 'closed']
    as_of_date: str
    created_at: str


class GoodsReceiptLine(_Base):
    """GoodsReceiptLine — GENERATED, 表 goods_receipt_lines。"""
    grn_line_id: str
    grn_id: str
    po_line_id: str
    received_qty: int
    accepted_qty: int
    rejected_qty: int
    qc_status: Literal['passed', 'failed', 'waived']
    defect_ppm: int
    received_date: str
    as_of_date: str
    created_at: str


class SupplierInvoice(_Base):
    """SupplierInvoice — GENERATED, 表 supplier_invoices。"""
    supplier_invoice_id: str
    supplier_id: str
    po_id: str
    vendor_invoice_no: str
    issue_date: str
    currency: Literal['USD']
    total_usd: float
    status: Literal['received', 'matched', 'disputed', 'approved']
    as_of_date: str
    created_at: str


class SupplierInvoiceLine(_Base):
    """SupplierInvoiceLine — GENERATED, 表 supplier_invoice_lines。"""
    supplier_invoice_line_id: str
    supplier_invoice_id: str
    po_line_id: str
    qty: int
    unit_price_usd: float
    amount_usd: float
    as_of_date: str
    created_at: str


class PurchasePayment(_Base):
    """PurchasePayment — GENERATED, 表 purchase_payments。"""
    payment_id: str
    po_id: str
    payment_type: Literal['deposit', 'balance', 'full']
    amount_usd: float
    paid_date: str
    exposure_status: Literal['covered', 'at_risk', 'released']
    as_of_date: str
    created_at: str


class Payment(_Base):
    """Payment — GENERATED, 表 payments。"""
    payment_id: str
    direction: Literal['in', 'out']
    counterparty_type: Literal['customer', 'supplier', 'vendor']
    counterparty_id: str
    ref_type: Literal['sales_order', 'supplier_invoice', 'invoice']
    ref_id: str
    amount_usd: float
    due_date: str
    paid_date: Optional[str] = None
    status: Literal['scheduled', 'paid']
    as_of_date: str
    created_at: str


class SupplierQualification(_Base):
    """SupplierQualification — GENERATED, 表 supplier_qualifications。"""
    qualification_id: str
    supplier_id: str
    cert_type: str
    evidence_status: Literal['missing', 'provided', 'verified', 'rejected']
    valid_from: str
    valid_to: str
    status: Literal['valid', 'expiring', 'expired', 'revoked']
    as_of_date: str
    created_at: str


class Warehouse(_Base):
    """Warehouse — GENERATED, 表 warehouses。"""
    warehouse_id: str
    type: Literal['overseas', 'bonded', 'domestic', 'FBA', '3PL']
    operator: str
    region: str
    capacity_units: int
    as_of_date: str


class InventoryPosition(_Base):
    """InventoryPosition — GENERATED, 表 inventory_positions。"""
    inventory_position_id: str
    sku_id: str
    warehouse_id: str
    available_qty: int
    reserved_qty: int
    in_transit_qty: int
    quarantine_qty: int
    safety_stock: int
    as_of_date: str


class InventoryReservation(_Base):
    """InventoryReservation — GENERATED, 表 inventory_reservations。"""
    reservation_id: str
    so_line_id: str
    inventory_position_id: str
    qty: int
    status: Literal['open', 'allocated', 'released', 'fulfilled', 'backordered']
    as_of_date: str


class CycleCount(_Base):
    """CycleCount — GENERATED, 表 cycle_counts。"""
    cycle_count_id: str
    inventory_position_id: str
    warehouse_id: str
    system_qty: int
    counted_qty: int
    variance: int
    status: Literal['scheduled', 'counted', 'variance', 'reconciled']
    as_of_date: str


class RFQ(_Base):
    """RFQ — GENERATED, 表 rfqs。"""
    rfq_id: str
    sku_id: str
    status: Literal['draft', 'sent', 'quoting', 'evaluating', 'awarded', 'closed', 'cancelled']
    created_date: str
    as_of_date: str


class RFQLine(_Base):
    """RFQLine — GENERATED, 表 rfq_lines。"""
    rfq_line_id: str
    rfq_id: str
    sku_id: str
    qty: int


class Quote(_Base):
    """Quote — GENERATED, 表 quotes。"""
    quote_id: str
    rfq_id: str
    supplier_id: str
    unit_price_usd: float
    currency: Literal['USD']
    status: Literal['invited', 'submitted', 'shortlisted', 'awarded', 'rejected', 'expired']
    as_of_date: str


class CoordinationThread(_Base):
    """CoordinationThread — GENERATED, 表 coordination_threads。"""
    coordination_id: str
    task_id: str
    risk_event_id: str
    counterparty_type: Literal['supplier', 'forwarder', 'customs_broker', 'bank', 'customer']
    counterparty_ref: str
    ask: str
    state: Literal['awaiting', 'responded', 'escalated', 'resolved', 'dead_ended']
    followup_count: int
    escalation_level: int
    owner: str
    next_action_due: str
    last_response: Optional[str] = None
    outcome: Optional[str] = None
    opened_at: str
    last_update: str
    policy_version: Optional[str] = None


MODEL_BY_TABLE = {
    'suppliers': Supplier,
    'skus': Sku,
    'customers': Customer,
    'sales_orders': SalesOrder,
    'sales_order_lines': SalesOrderLine,
    'purchase_orders': PurchaseOrder,
    'shipments': Shipment,
    'shipment_milestones': ShipmentMilestone,
    'shipment_allocations': ShipmentAllocation,
    'risk_events': RiskEvent,
    'tasks': Task,
    'admission_cases': AdmissionCase,
    'compliance_findings': ComplianceFinding,
    'logistics_plans': LogisticsPlan,
    'cost_scenarios': CostScenario,
    'containers': Container,
    'invoices': Invoice,
    'invoice_lines': InvoiceLine,
    'expected_costs': ExpectedCost,
    'po_lines': PoLine,
    'goods_receipts': GoodsReceipt,
    'goods_receipt_lines': GoodsReceiptLine,
    'supplier_invoices': SupplierInvoice,
    'supplier_invoice_lines': SupplierInvoiceLine,
    'purchase_payments': PurchasePayment,
    'payments': Payment,
    'supplier_qualifications': SupplierQualification,
    'warehouses': Warehouse,
    'inventory_positions': InventoryPosition,
    'inventory_reservations': InventoryReservation,
    'cycle_counts': CycleCount,
    'rfqs': RFQ,
    'rfq_lines': RFQLine,
    'quotes': Quote,
    'coordination_threads': CoordinationThread,
}

MODEL_BY_TYPE = {
    'Supplier': Supplier,
    'Sku': Sku,
    'Customer': Customer,
    'SalesOrder': SalesOrder,
    'SalesOrderLine': SalesOrderLine,
    'PurchaseOrder': PurchaseOrder,
    'Shipment': Shipment,
    'ShipmentMilestone': ShipmentMilestone,
    'ShipmentAllocation': ShipmentAllocation,
    'RiskEvent': RiskEvent,
    'Task': Task,
    'AdmissionCase': AdmissionCase,
    'ComplianceFinding': ComplianceFinding,
    'LogisticsPlan': LogisticsPlan,
    'CostScenario': CostScenario,
    'Container': Container,
    'Invoice': Invoice,
    'InvoiceLine': InvoiceLine,
    'ExpectedCost': ExpectedCost,
    'PoLine': PoLine,
    'GoodsReceipt': GoodsReceipt,
    'GoodsReceiptLine': GoodsReceiptLine,
    'SupplierInvoice': SupplierInvoice,
    'SupplierInvoiceLine': SupplierInvoiceLine,
    'PurchasePayment': PurchasePayment,
    'Payment': Payment,
    'SupplierQualification': SupplierQualification,
    'Warehouse': Warehouse,
    'InventoryPosition': InventoryPosition,
    'InventoryReservation': InventoryReservation,
    'CycleCount': CycleCount,
    'RFQ': RFQ,
    'RFQLine': RFQLine,
    'Quote': Quote,
    'CoordinationThread': CoordinationThread,
}
