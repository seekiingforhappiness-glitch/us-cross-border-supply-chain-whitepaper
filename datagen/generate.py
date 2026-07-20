"""W2 主入口：python3 -m datagen.generate [--config config/datagen.yaml]

产出：
  data/raw/    源系统导出（含噪声）：oms_* / srm_* / tms_* / catalog_skus + _manifest.json
  data/truth/  ground truth（仅 datagen/verify/W4 评估可读）：expected_risk_events、
               injected_noise_log、world_snapshot.json
  data/mock_source.sqlite
"""
import argparse
import csv
import json
import random
import sqlite3
from pathlib import Path

import yaml

from . import world as W
from . import admission as ADM
from . import cost as COST
from . import procurement as PROC
from . import warehouse as WH
from . import sourcing as SRC
from . import finance as FIN
from . import supplier_risk as SUPR
from .design_cases import apply_design_cases
from .noise import apply_noise, apply_doc_refs
from .oracle import sweep


def build(cfg):
    rng = random.Random(cfg["seed"])
    w = W.build_world(cfg, rng)
    design_noise = apply_design_cases(w, rng)
    W.ensure_multi_customer_breach(w, cfg, rng)
    W.enrich_shipments(w, rng)      # D11 Tier 1 字段
    W.enrich_milestones(w, rng)
    W.sync_po_status(w)
    W.finalize_line_status(w)
    expected = sweep(w, cfg)
    noise = apply_noise(w, design_noise, cfg, rng)
    # v0.3 准入世界：独立随机流（seed+1000），控制塔数据零扰动（V2 决策）
    adm_rng = random.Random(cfg["seed"] + 1000)
    ADM.extend_shared_objects(w, adm_rng)
    ADM.build_admission_world(w, cfg, adm_rng)
    # v0.4 费用对账：独立随机流（seed+2000），既有数据零扰动（X2 决策）
    cost_rng = random.Random(cfg["seed"] + 2000)
    COST.build_cost_world(w, cfg, cost_rng)
    # P1 采购三方对账（Build 1/3）：独立随机流（seed+procurement.seed_offset），既有数据零扰动
    proc_rng = random.Random(cfg["seed"] + cfg["procurement"]["seed_offset"])
    PROC.build_procurement_world(w, cfg, proc_rng)
    # W1 仓储库存主线（Build 1/3）：独立随机流（seed+warehouse.seed_offset），既有数据零扰动
    wh_rng = random.Random(cfg["seed"] + cfg["warehouse"]["seed_offset"])
    WH.build_warehouse_world(w, cfg, wh_rng)
    # P3 采购富化2（Build A）：RFQ 询价 + R14 单一来源 + R15 maverick——独立随机流
    # （seed+sourcing.seed_offset），须在 procurement 之后（依赖其 po_lines + R7/R9 真值）。
    src_rng = random.Random(cfg["seed"] + cfg["sourcing"]["seed_offset"])
    SRC.build_sourcing_world(w, cfg, src_rng)
    # F1 资金流域（V8-②）：独立随机流（seed+finance.seed_offset），build() 最后一个域——
    # 消费 supplier_invoices(proc)/invoices(cost)/sos+lines(world) 生成 Payment；对既有数据零扰动。
    fin_rng = random.Random(cfg["seed"] + cfg["finance"]["seed_offset"])
    FIN.build_finance_world(w, cfg, fin_rng)
    # V23① 供应商风险 R22/R23 真值：**纯派生**（无 rng 参数，零随机流消耗——物理上不可能扰动
    # 既有数据）；从采购世界既有事实独立推导，落新增 truth 文件，旧 9 文件逐字节不变。
    SUPR.build_supplier_risk_truth(w, cfg)
    # v0.6 专题二 H3：milestone 单证号（booking_no/container_no）填充 + doc_ref_typo。
    # 独立随机流（seed+3000），须在 cost 建柜之后（primary 柜号已就位）。
    doc_rng = random.Random(cfg["seed"] + 3000)
    apply_doc_refs(noise["ms_rows"], w, cfg, doc_rng, noise["noise_log"])
    return w, expected, noise


def _dump(path, rows, cols):
    with open(path, "w", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        wr.writeheader()
        wr.writerows(rows)


def write_outputs(w, expected, noise, cfg, raw_dir, truth_dir, sqlite_path=None):
    raw, truth = Path(raw_dir), Path(truth_dir)
    raw.mkdir(parents=True, exist_ok=True)
    truth.mkdir(parents=True, exist_ok=True)
    vm = noise["variant_map"]

    tables = {}
    tables["srm_suppliers"] = ([dict(s) for s in sorted(w["suppliers"].values(), key=lambda x: x["supplier_id"])],
                               ["supplier_id", "supplier_name", "city", "lead_time_days",
                                "factory_audit_status", "compliance_docs_status",
                                "uflpa_risk_flag", "origin_evidence_status", "payment_terms_days"])
    tables["catalog_skus"] = ([dict(s) for s in sorted(w["skus"].values(), key=lambda x: x["sku_id"])],
                              ["sku_id", "sku_name", "category", "unit_price_usd", "supplier_id",
                               "sku_status", "declared_value_usd", "package_weight_kg",
                               "package_l_cm", "package_w_cm", "package_h_cm", "battery_flag",
                               "food_contact_flag", "children_product_flag", "material",
                               "use_case", "origin_country", "platform"])
    tables["oms_customers"] = ([dict(c) for c in sorted(w["customers"].values(), key=lambda x: x["customer_id"])],
                               ["customer_id", "customer_name", "tier", "us_state",
                                "business_model", "sales_channel", "ior_capability",
                                "broker_status", "credit_terms", "risk_tier"])
    tables["oms_sales_orders"] = ([{**s, "order_date": s["order_date"].isoformat()}
                                   for s in sorted(w["sos"].values(), key=lambda x: x["so_id"])],
                                  ["so_id", "customer_id", "order_date"])
    tables["oms_so_lines"] = ([{**l, "promised_delivery_date": l["promised_delivery_date"].isoformat()}
                               for l in sorted(w["lines"].values(), key=lambda x: x["so_line_id"])],
                              ["so_line_id", "so_id", "sku_id", "qty", "unit_price_usd",
                               "promised_delivery_date"])
    tables["srm_purchase_orders"] = ([{**p, "po_date": p["po_date"].isoformat(),
                                       "expected_ready_date": p["expected_ready_date"].isoformat()}
                                      for p in sorted(w["pos"].values(), key=lambda x: x["po_id"])],
                                     ["po_id", "supplier_id", "sku_id", "qty", "po_date",
                                      "expected_ready_date", "status"])

    ship_rows = []
    for sp in sorted(w["shipments"].values(), key=lambda x: x["shipment_id"]):
        sid = sp["shipment_id"]
        nulled = sid in noise["null_ships"]
        sup_names = sorted({vm.get(w["pos"][p]["supplier_id"],
                                   w["suppliers"][w["pos"][p]["supplier_id"]]["supplier_name"])
                            for p in sp["po_ids"]})
        ship_rows.append({
            "shipment_id": sid, "booking_no": sp["booking_no"], "mbl_no": sp["mbl_no"],
            "mode": sp["mode"], "container_no": sp["container_no"] or "",
            "container_type": sp["container_type"],
            "vessel_voyage": "" if nulled or not sp["vessel_voyage"] else sp["vessel_voyage"],
            "carrier_name": "" if nulled or not sp["carrier_name"] else sp["carrier_name"],
            "carrier_scac": sp["carrier_scac"],  # 真实形态：名字可缺，SCAC 常在
            "incoterm": sp["incoterm"],
            "gross_weight_kg": sp["gross_weight_kg"], "volume_cbm": sp["volume_cbm"],
            "origin_port": sp["origin_port"], "origin_port_locode": sp["origin_port_locode"],
            "destination_port": sp["destination_port"],
            "destination_port_locode": sp["destination_port_locode"],
            "destination_warehouse": sp["destination_warehouse"],
            "etd": sp["etd"].isoformat(), "eta_initial": sp["eta_initial"].isoformat(),
            "status": noise["emit_status"].get(sid, sp["status"]),
            "missing_docs": "|".join(sp["missing_docs"]),
            "supplier_names": "|".join(sup_names), "po_ids": "|".join(sp["po_ids"]),
        })
    tables["tms_shipments"] = (ship_rows,
                               ["shipment_id", "booking_no", "mbl_no", "mode", "container_no",
                                "container_type", "vessel_voyage", "carrier_name", "carrier_scac",
                                "incoterm", "gross_weight_kg", "volume_cbm",
                                "origin_port", "origin_port_locode", "destination_port",
                                "destination_port_locode", "destination_warehouse", "etd",
                                "eta_initial", "status", "missing_docs", "supplier_names", "po_ids"])
    milestone_rows = []
    for r in sorted(noise["ms_rows"], key=lambda r: (r["ingested_at"], r["milestone_id"])):
        row = dict(r)
        row["source_record_id"] = row["milestone_id"]
        row["message_id"] = f"MSG-{row['milestone_id']}"
        milestone_rows.append(row)
    # H3：源表删除 shipment_id，改带 booking_no + container_no（真实 EDI 形态）。
    # M3：补充 source_record_id/message_id，作为模拟 source-truth envelope 的稳定身份。
    # 列白名单不含 shipment_id → DictWriter(extrasaction="ignore") 自动不输出。
    tables["tms_milestones"] = (milestone_rows,
                                ["milestone_id", "source_record_id", "message_id",
                                 "booking_no", "container_no", "event_type",
                                 "event_classifier", "event_time", "event_locode", "new_eta",
                                 "source_system", "ingested_at"])
    tables["tms_allocations"] = (sorted(w["allocations"], key=lambda a: a["allocation_id"]),
                                 ["allocation_id", "shipment_id", "so_line_id", "allocated_qty"])
    # v0.3 准入四表（qms_ = 报价管理系统）
    adm = w["admission"]
    tables["qms_admission_cases"] = (adm["cases"],
                                     ["admission_case_id", "case_title", "customer_id", "sku_id",
                                      "request_type", "incoterm_candidate", "target_launch_date",
                                      "monthly_order_estimate", "risk_level", "status",
                                      "decision", "decision_reason", "conditions"])
    tables["qms_compliance_findings"] = (adm["findings"],
                                         ["compliance_finding_id", "admission_case_id",
                                          "finding_title", "finding_type", "severity",
                                          "hts_candidate", "pga_agency", "required_document",
                                          "evidence_status", "recommendation"])
    tables["qms_logistics_plans"] = (adm["plans"],
                                     ["logistics_plan_id", "admission_case_id", "plan_name",
                                      "route_type", "incoterm", "origin_port_locode",
                                      "destination_port_locode", "us_warehouse_region",
                                      "last_mile_method", "estimated_transit_days", "sla_risk",
                                      "operational_notes"])
    tables["qms_cost_scenarios"] = (adm["scenarios"],
                                    ["cost_scenario_id", "logistics_plan_id", "scenario_type",
                                     "quote_price_usd", "product_cost_usd", "first_mile_cost_usd",
                                     "international_freight_usd", "duty_tax_usd",
                                     "customs_brokerage_usd", "warehouse_cost_usd",
                                     "last_mile_cost_usd", "returns_allowance_usd",
                                     "risk_buffer_usd", "gross_margin_usd", "gross_margin_rate"])

    # v0.4 费用对账四表（ap_ = 应付账款系统 / tms_ = 运输管理系统）
    cost = w["cost"]
    tables["tms_containers"] = (sorted(cost["containers"], key=lambda x: x["container_no"]),
                                ["container_no", "shipment_id", "container_type", "is_primary",
                                 "free_days", "gross_weight_kg", "volume_cbm"])
    tables["rate_card"] = (cost["rate_card"],
                           ["charge_code", "scope", "key", "rate_usd"])
    tables["ap_invoices"] = (sorted(cost["invoices"], key=lambda x: x["invoice_id"]),
                             ["invoice_id", "vendor_type", "vendor_name", "vendor_invoice_no",
                              "shipment_id", "issue_date", "currency", "total_usd", "status"])
    tables["ap_invoice_lines"] = (sorted(cost["invoice_lines"], key=lambda x: x["invoice_line_id"]),
                                  ["invoice_line_id", "invoice_id", "charge_code", "container_no",
                                   "qty", "unit_price_usd", "amount_usd"])
    tables["ap_expected_costs"] = (sorted(cost["expected_costs"], key=lambda x: x["expected_cost_id"]),
                                   ["expected_cost_id", "shipment_id", "charge_code", "container_no",
                                    "baseline_usd", "source"])

    # P1 采购三方对账五表（srm_ = 供应商关系系统 / ap_ = 应付账款系统）
    proc = w["procurement"]
    tables["srm_po_lines"] = (sorted(proc["po_lines"], key=lambda x: x["po_line_id"]),
                              ["po_line_id", "po_id", "sku_id", "qty", "unit_price_usd", "currency",
                               "expected_ready_date", "line_status", "as_of_date", "created_at"])
    tables["srm_goods_receipts"] = (sorted(proc["goods_receipts"], key=lambda x: x["grn_id"]),
                                    ["grn_id", "po_id", "received_date", "status", "as_of_date",
                                     "created_at"])
    tables["srm_goods_receipt_lines"] = (sorted(proc["grn_lines"], key=lambda x: x["grn_line_id"]),
                                         ["grn_line_id", "grn_id", "po_line_id", "received_qty",
                                          "accepted_qty", "rejected_qty", "qc_status", "defect_ppm",
                                          "received_date", "as_of_date", "created_at"])
    # P3 采购富化2：R15 maverick 发票并入 ap_supplier_invoices（引用真实 PO/po_line，价量匹配
    # → 不触 R10/R11；既有 61 张合规发票行原序原字节在前，绕流程发票 append 在后）。
    src = w["sourcing"]
    tables["ap_supplier_invoices"] = (sorted(proc["supplier_invoices"],
                                             key=lambda x: x["supplier_invoice_id"])
                                      + sorted(src["maverick_invoices"],
                                               key=lambda x: x["supplier_invoice_id"]),
                                      ["supplier_invoice_id", "supplier_id", "po_id",
                                       "vendor_invoice_no", "issue_date", "currency", "total_usd",
                                       "status", "as_of_date", "created_at"])
    tables["ap_supplier_invoice_lines"] = (sorted(proc["supplier_invoice_lines"],
                                                  key=lambda x: x["supplier_invoice_line_id"])
                                           + sorted(src["maverick_invoice_lines"],
                                                    key=lambda x: x["supplier_invoice_line_id"]),
                                           ["supplier_invoice_line_id", "supplier_invoice_id",
                                            "po_line_id", "qty", "unit_price_usd", "amount_usd",
                                            "as_of_date", "created_at"])
    # P3 采购富化2：RFQ 询价三表（srm_ = 供应商关系系统）
    tables["srm_rfqs"] = (sorted(src["rfqs"], key=lambda x: x["rfq_id"]),
                          ["rfq_id", "sku_id", "status", "created_date", "as_of_date"])
    tables["srm_rfq_lines"] = (sorted(src["rfq_lines"], key=lambda x: x["rfq_line_id"]),
                               ["rfq_line_id", "rfq_id", "sku_id", "qty"])
    tables["srm_quotes"] = (sorted(src["quotes"], key=lambda x: x["quote_id"]),
                            ["quote_id", "rfq_id", "supplier_id", "unit_price_usd", "currency",
                             "status", "as_of_date"])
    # 采购富化 P2：预付款（ap_ = 应付账款/资金）+ 供应商资质（srm_ = 供应商关系系统）
    tables["ap_purchase_payments"] = (sorted(proc["payments"], key=lambda x: x["payment_id"]),
                                      ["payment_id", "po_id", "payment_type", "amount_usd",
                                       "paid_date", "exposure_status", "as_of_date", "created_at"])
    tables["srm_supplier_qualifications"] = (sorted(proc["qualifications"],
                                                    key=lambda x: x["qualification_id"]),
                                             ["qualification_id", "supplier_id", "cert_type",
                                              "evidence_status", "valid_from", "valid_to", "status",
                                              "as_of_date", "created_at"])

    # F1 资金流域（V8-②）：Payment 收付一本子（ap_ = 应付账款/资金；in 向应收亦同表，direction 区分）
    fin = w["finance"]
    tables["ap_payments"] = (sorted(fin["payments"], key=lambda x: x["payment_id"]),
                             ["payment_id", "direction", "counterparty_type", "counterparty_id",
                              "ref_type", "ref_id", "amount_usd", "due_date", "paid_date",
                              "status", "as_of_date", "created_at"])

    # W1 仓储库存主线四表（wms_ = 仓库管理系统）
    wh = w["warehouse"]
    tables["wms_warehouses"] = (sorted(wh["warehouses"], key=lambda x: x["warehouse_id"]),
                                ["warehouse_id", "type", "operator", "region", "capacity_units",
                                 "as_of_date"])
    tables["wms_inventory_positions"] = (sorted(wh["positions"],
                                                key=lambda x: x["inventory_position_id"]),
                                         ["inventory_position_id", "sku_id", "warehouse_id",
                                          "available_qty", "reserved_qty", "in_transit_qty",
                                          "quarantine_qty", "safety_stock", "as_of_date"])
    tables["wms_inventory_reservations"] = (sorted(wh["reservations"],
                                                   key=lambda x: x["reservation_id"]),
                                            ["reservation_id", "so_line_id",
                                             "inventory_position_id", "qty", "status", "as_of_date"])
    tables["wms_cycle_counts"] = (sorted(wh["cycle_counts"], key=lambda x: x["cycle_count_id"]),
                                  ["cycle_count_id", "inventory_position_id", "warehouse_id",
                                   "system_qty", "counted_qty", "variance", "status", "as_of_date"])

    for name, (rows, cols) in tables.items():
        _dump(raw / f"{name}.csv", rows, cols)
    _dump(truth / "expected_admission_gates.csv", adm["gates"],
          ["case_label", "admission_case_id", "gate", "attempt", "expected", "reason"])
    # v0.4 费用异常 ground truth（仅 datagen/verify 与 X3 评估可读）
    _dump(truth / "expected_cost_anomalies.csv", cost["anomalies"],
          ["rule_id", "type", "shipment_id", "severity", "anomaly_value_usd",
           "affected_invoice_line_ids", "case_id", "attribution"])
    # P1 采购异常 ground truth（R7-R10；仅 datagen/verify 与 Build 2 评估可读）
    _dump(truth / "expected_procurement_risks.csv", proc["anomalies"],
          ["expected_procurement_risk_id", "rule_id", "type", "po_id", "po_line_id",
           "supplier_id", "severity", "anomaly_value_usd", "note", "case_id"])
    # W1 仓储异常 ground truth（R16-R18；仅 datagen/verify 与 engine 评估可读，引擎检测禁读——§5）
    _dump(truth / "expected_warehouse_risks.csv", wh["anomalies"],
          ["expected_warehouse_risk_id", "rule_id", "type", "warehouse_id", "sku_id",
           "inventory_position_id", "so_line_id", "cycle_count_id", "severity",
           "anomaly_value_usd", "note"])
    # P3 采购富化2 ground truth（R14/R15；独立文件——R14 需 sku_id 锚点不入 R7-R13 schema，
    # 故既有 expected_procurement_risks.csv 逐字节不变，守 §5 铁律。仅 datagen/verify/evaluate 可读）
    _dump(truth / "expected_sourcing_risks.csv", src["anomalies"],
          ["expected_sourcing_risk_id", "rule_id", "type", "sku_id", "supplier_id", "po_id",
           "severity", "anomaly_value_usd", "note"])
    # F1 资金流域 ground truth（R19-R21；独立文件——payment_id 锚点不入既有 schema，故既有 8 真值
    # 文件逐字节不变，守 §5 铁律。仅 datagen/verify/evaluate_finance 可读，引擎检测禁读）
    _dump(truth / "expected_finance_risks.csv", fin["anomalies"],
          ["expected_finance_risk_id", "rule_id", "type", "payment_id", "ref_type", "ref_id",
           "direction", "counterparty_id", "severity", "anomaly_value_usd", "note"])
    # V23① 供应商风险 ground truth（R22/R23；独立新增文件——supplier/qualification 锚点不入
    # 既有 schema，纯派生零注入，故既有 9 真值文件逐字节不变，守 §5 铁律与 V23① 红线。
    # 仅 datagen/verify/evaluate_supplier_risk 可读，引擎检测禁读）
    _dump(truth / "expected_supplier_risks.csv", w["supplier_risk"]["anomalies"],
          ["expected_supplier_risk_id", "rule_id", "type", "supplier_id", "qualification_id",
           "cert_type", "severity", "anomaly_value_usd", "note"])

    # ground truth
    _dump(truth / "expected_risk_events.csv", expected,
          ["expected_risk_id", "rule_id", "type", "shipment_id", "affected_so_line_ids",
           "severity", "breach_days", "affected_value_usd", "reason", "case_id"])
    _dump(truth / "injected_noise_log.csv", noise["noise_log"],
          ["noise_id", "noise_type", "target_table", "target_id", "description", "case_id"])
    snapshot = {
        "as_of": w["as_of"].isoformat(),
        "shipments": {s["shipment_id"]: {
            "status": s["status"], "eta_current": s["eta_current"].isoformat(),
            "ata": s["ata"].isoformat() if s["ata"] else None,
            "customs_status": s["customs_status"], "missing_docs": s["missing_docs"]}
            for s in sorted(w["shipments"].values(), key=lambda x: x["shipment_id"])},
        "lines": {l["so_line_id"]: l["line_status"]
                  for l in sorted(w["lines"].values(), key=lambda x: x["so_line_id"])},
    }
    (truth / "world_snapshot.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=1))

    manifest = {"seed": cfg["seed"], "as_of": cfg["window"]["as_of"],
                "counts": {k: len(v[0]) for k, v in tables.items()},
                "expected_risk_events": len(expected), "injected_noise": len(noise["noise_log"])}
    (raw / "_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1))

    if sqlite_path:
        Path(sqlite_path).unlink(missing_ok=True)
        con = sqlite3.connect(sqlite_path)
        for name, (rows, cols) in tables.items():
            con.execute(f"CREATE TABLE {name} ({', '.join(c + ' TEXT' for c in cols)})")
            con.executemany(f"INSERT INTO {name} VALUES ({', '.join('?' * len(cols))})",
                            [[str(r.get(c, '')) for c in cols] for r in rows])
        con.commit()
        con.close()
    return manifest


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/datagen.yaml")
    args = ap.parse_args()
    cfg = yaml.safe_load(open(args.config, encoding="utf-8"))
    w, expected, noise = build(cfg)
    manifest = write_outputs(w, expected, noise, cfg,
                             cfg["output"]["raw_dir"], cfg["output"]["truth_dir"],
                             sqlite_path="data/mock_source.sqlite")
    print(json.dumps(manifest, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
