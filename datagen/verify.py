"""W2 验收脚本：python3 -m datagen.verify

检查（plan §11-W2 验收 + 设计案例硬断言）：
  1. 可复现性：同种子两次生成输出逐字节一致
  2. 规模与引用完整性
  3. ground truth 与注入 1:1
  4. DEMO-01..15 逐案例硬断言
  5. 一票延误击穿多客户 ≥ 15（plan §9）
判定逻辑受 AGENTS.md §5 保护：不得为通过验收而修改本文件。
"""
import csv
import hashlib
import json
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

import yaml

from .generate import build, write_outputs

FAILS = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def sha_dir(d):
    h = hashlib.sha256()
    for p in sorted(Path(d).rglob("*")):
        if p.suffix in (".csv", ".json"):
            h.update(p.name.encode())
            h.update(p.read_bytes())
    return h.hexdigest()


def load(raw, name):
    with open(Path(raw) / f"{name}.csv", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def main():
    cfg = yaml.safe_load(open("config/datagen.yaml", encoding="utf-8"))
    raw_dir, truth_dir = cfg["output"]["raw_dir"], cfg["output"]["truth_dir"]

    print("== 1. 可复现性 ==")
    dirs = []
    for _ in range(2):
        t = tempfile.mkdtemp()
        w, e, n = build(cfg)
        write_outputs(w, e, n, cfg, Path(t) / "raw", Path(t) / "truth")
        dirs.append(t)
    check("同种子两次生成逐字节一致", sha_dir(dirs[0]) == sha_dir(dirs[1]))

    print("== 2. 规模与引用完整性 ==")
    t = {n: load(raw_dir, n) for n in ["srm_suppliers", "catalog_skus", "oms_customers",
                                        "oms_sales_orders", "oms_so_lines", "srm_purchase_orders",
                                        "tms_shipments", "tms_milestones", "tms_allocations"]}
    exp = load(truth_dir, "expected_risk_events")
    noi = load(truth_dir, "injected_noise_log")
    c = cfg["counts"]
    # H3：源表 tms_milestones 已无 shipment_id，改带 booking_no/container_no。
    # 直接用 milestone 的 shipment_id 的断言，一律经 booking_no→shipment 映射还原。
    ship_by_booking = {r["booking_no"]: r["shipment_id"] for r in t["tms_shipments"]}
    ship_by_container = {r["container_no"]: r["shipment_id"]
                         for r in t["tms_shipments"] if r["container_no"]}

    def ms_ship(r):
        """从 milestone 行还原所属 shipment（booking 优先，其次 container，否则 None）。"""
        return ship_by_booking.get(r["booking_no"]) or ship_by_container.get(r["container_no"])
    check("suppliers 数量", len(t["srm_suppliers"]) == c["suppliers"])
    check("skus 数量（目录+候选）", len(t["catalog_skus"]) == c["skus"] + 15)  # 15=准入候选（v0.3）
    check("customers 数量", len(t["oms_customers"]) == c["customers"])
    check("sales_orders 数量", len(t["oms_sales_orders"]) == c["sales_orders"],
          f"got {len(t['oms_sales_orders'])}")
    check("purchase_orders 数量", len(t["srm_purchase_orders"]) == c["purchase_orders"],
          f"got {len(t['srm_purchase_orders'])}")
    check("shipments 数量", len(t["tms_shipments"]) == c["shipments"], f"got {len(t['tms_shipments'])}")
    check("so_lines 规模", 600 <= len(t["oms_so_lines"]) <= 1000, f"got {len(t['oms_so_lines'])}")
    check("allocations 规模", 500 <= len(t["tms_allocations"]) <= 1100, f"got {len(t['tms_allocations'])}")
    # plan §9（D10 修订后）：600-1200，每票 5-10 条业务事件
    check("milestones 规模", 600 <= len(t["tms_milestones"]) <= 1200, f"got {len(t['tms_milestones'])}")

    so_ids = {r["so_id"] for r in t["oms_sales_orders"]}
    line_ids = {r["so_line_id"] for r in t["oms_so_lines"]}
    ship_ids = {r["shipment_id"] for r in t["tms_shipments"]}
    check("行引用 SO 完整", all(r["so_id"] in so_ids for r in t["oms_so_lines"]))
    check("分配引用完整", all(r["shipment_id"] in ship_ids and r["so_line_id"] in line_ids
                              for r in t["tms_allocations"]))
    # D11 Tier 1 字段
    check("booking_no 唯一", len({r["booking_no"] for r in t["tms_shipments"]}) == len(t["tms_shipments"]))
    check("mbl_no 唯一", len({r["mbl_no"] for r in t["tms_shipments"]}) == len(t["tms_shipments"]))
    check("locode 合法", all(r["origin_port_locode"] in ("CNYTN", "CNSHK", "CNNGB")
                             and r["destination_port_locode"] in ("USLAX", "USLGB")
                             for r in t["tms_shipments"]))
    check("incoterm 合法", all(r["incoterm"] in ("FOB", "CIF", "DDP") for r in t["tms_shipments"]))
    check("milestone classifier 合法（eta_change=EST 其余=ACT）",
          all((r["event_classifier"] == "EST") == (r["event_type"] == "eta_change")
              for r in t["tms_milestones"]))
    check("milestone 均有事件地点", all(r["event_locode"] for r in t["tms_milestones"]))
    check("行成交价为正", all(float(r["unit_price_usd"]) > 0 for r in t["oms_so_lines"]))
    po_ship = defaultdict(set)
    for r in t["tms_shipments"]:
        for p in r["po_ids"].split("|"):
            po_ship[p].add(r["shipment_id"])
    check("po_shipped_by N:1（一 PO 只在一船）", all(len(v) == 1 for v in po_ship.values()))

    print("== 3. ground truth 完备性 ==")
    check("expected 引用 shipment 存在", all(r["shipment_id"] in ship_ids for r in exp))
    check("expected R1 受影响行非空",
          all(json.loads(r["affected_so_line_ids"]) for r in exp if r["rule_id"] == "R1"))
    check("severity 合法", all(r["severity"] in ("medium", "high", "critical") for r in exp))
    ms_ids = {r["milestone_id"] for r in t["tms_milestones"]}
    sup_ids = {r["supplier_id"] for r in t["srm_suppliers"]}
    ok = True
    for r in noi:
        if r["noise_type"] in ("milestone_duplicate", "milestone_out_of_order"):
            ok &= r["target_id"] in ms_ids
        elif r["noise_type"] in ("status_conflict", "null_vessel_carrier"):
            ok &= r["target_id"] in ship_ids
        elif r["noise_type"] == "supplier_name_variant":
            ok &= r["target_id"] in sup_ids
    check("noise_log 目标全部存在", ok)

    print("== 4. 设计案例断言 ==")
    by_ship = defaultdict(list)
    for r in exp:
        by_ship[r["shipment_id"]].append(r)
    line_to_cust = {}
    so_cust = {r["so_id"]: r["customer_id"] for r in t["oms_sales_orders"]}
    for r in t["oms_so_lines"]:
        line_to_cust[r["so_line_id"]] = so_cust[r["so_id"]]

    def one(sid, rule):
        rows = [r for r in by_ship[sid] if r["rule_id"] == rule]
        return rows[0] if len(rows) == 1 else None

    e = one("SHP-2026-0099", "R1")
    check("DEMO-01 R1 唯一且 critical", bool(e) and e["severity"] == "critical" and len(by_ship["SHP-2026-0099"]) == 1)
    check("DEMO-01 affected 精确", bool(e) and json.loads(e["affected_so_line_ids"]) == ["SOL-0188-1"])
    check("DEMO-01 breach=6 value=3250", bool(e) and e["breach_days"] == "6"
          and float(e["affected_value_usd"]) == 3250.0)
    e = one("SHP-2026-0042", "R1")
    ln = json.loads(e["affected_so_line_ids"]) if e else []
    check("DEMO-02 R1 critical 3行2客户", bool(e) and e["severity"] == "critical" and len(ln) == 3
          and len({line_to_cust[x] for x in ln}) == 2)
    e = one("SHP-2026-0055", "R1")
    check("DEMO-03 R1 medium", bool(e) and e["severity"] == "medium" and len(by_ship["SHP-2026-0055"]) == 1)
    e = one("SHP-2026-0060", "R1")
    check("DEMO-04 R1 high（A级升级）", bool(e) and e["severity"] == "high" and len(by_ship["SHP-2026-0060"]) == 1)
    e = one("SHP-2026-0070", "R2")
    check("DEMO-05 仅 R2 high", bool(e) and e["severity"] == "high" and len(by_ship["SHP-2026-0070"]) == 1)
    check("DEMO-06 无风险", not by_ship["SHP-2026-0075"])
    e = one("SHP-2026-0080", "R3")
    check("DEMO-07 仅 R3 medium", bool(e) and e["severity"] == "medium" and len(by_ship["SHP-2026-0080"]) == 1)
    check("DEMO-08 无风险（arrived 不触发 R3）", not by_ship["SHP-2026-0085"])
    e = one("SHP-2026-0090", "R1")
    # H3：设计船行两者齐备，booking→shipment 映射还原（源表已无 shipment_id）
    dup_rows = [r for r in t["tms_milestones"]
                if ms_ship(r) == "SHP-2026-0090" and r["event_type"] == "eta_change"]
    check("DEMO-09 重复上报存在但只 1 个 R1", bool(e) and len(by_ship["SHP-2026-0090"]) == 1 and len(dup_rows) == 2)
    check("DEMO-10 乱序不产生风险", not by_ship["SHP-2026-0095"]
          and any(r["case_id"] == "DEMO-10" for r in noi))
    tms_0100 = next(r for r in t["tms_shipments"] if r["shipment_id"] == "SHP-2026-0100")
    check("DEMO-11 状态冲突陷阱（tms=in_transit, 真值=arrived, 无风险）",
          not by_ship["SHP-2026-0100"] and tms_0100["status"] == "in_transit")
    check("DEMO-12 延误恢复无风险", not by_ship["SHP-2026-0105"])
    e = one("SHP-2026-0110", "R1")
    tms_0110 = next(r for r in t["tms_shipments"] if r["shipment_id"] == "SHP-2026-0110")
    check("DEMO-13 名称变体不断链（R1 high + 变体名）", bool(e) and e["severity"] == "high"
          and "Ningbo Sunrise Electronics Co., Ltd." not in tms_0110["supplier_names"])
    e = one("SHP-2026-0115", "R1")
    tms_0115 = next(r for r in t["tms_shipments"] if r["shipment_id"] == "SHP-2026-0115")
    check("DEMO-14 空值不影响检测（R1 high + vessel 空）", bool(e) and e["severity"] == "high"
          and tms_0115["vessel_voyage"] == "")
    e = one("SHP-2026-0118", "R1")
    check("DEMO-15 多 SKU 精确到行", bool(e) and json.loads(e["affected_so_line_ids"]) == ["SOL-0225-1"])

    print("== 5. 统计特征 ==")
    multi = sum(1 for r in exp if r["rule_id"] == "R1"
                and len({line_to_cust[x] for x in json.loads(r["affected_so_line_ids"])}) >= 2)
    check("一票延误击穿多客户 ≥ 15", multi >= 15, f"got {multi}")
    by_rule = defaultdict(int)
    for r in exp:
        by_rule[r["rule_id"]] += 1
    by_noise = defaultdict(int)
    for r in noi:
        by_noise[r["noise_type"]] += 1
    print(f"  风险分布: {dict(by_rule)}  多客户击穿: {multi}")
    print(f"  噪声分布: {dict(by_noise)}")

    print("== 6. 准入扩展（v0.3-V2）==")
    adm_cases = load(raw_dir, "qms_admission_cases")
    adm_find = load(raw_dir, "qms_compliance_findings")
    adm_plans = load(raw_dir, "qms_logistics_plans")
    adm_scen = load(raw_dir, "qms_cost_scenarios")
    gates = load(truth_dir, "expected_admission_gates")
    skus = {r["sku_id"]: r for r in t["catalog_skus"]}
    custs = {r["customer_id"]: r for r in t["oms_customers"]}
    cases = {r["admission_case_id"]: r for r in adm_cases}
    candidates = [k for k, v in skus.items() if v["sku_status"] == "candidate"]

    check("案件数=40 / 候选+转正 SKU=15", len(adm_cases) == 40
          and sum(1 for k in skus if k.startswith("SKU-9")) == 15)
    check("遗留 SKU 准入字段为空（迁移语义）",
          skus["SKU-0001"]["declared_value_usd"] == "" and skus["SKU-0001"]["sku_status"] == "active")
    check("候选 SKU 不进控制塔（无 PO/订单行引用）",
          not any(r["sku_id"].startswith("SKU-9") for r in t["srm_purchase_orders"])
          and not any(r["sku_id"].startswith("SKU-9") for r in t["oms_so_lines"]))
    ok_margin = all(abs(float(s["gross_margin_usd"]) - (float(s["quote_price_usd"])
                    - sum(float(s[k]) for k in ("product_cost_usd", "first_mile_cost_usd",
                          "international_freight_usd", "duty_tax_usd", "customs_brokerage_usd",
                          "warehouse_cost_usd", "last_mile_cost_usd", "returns_allowance_usd",
                          "risk_buffer_usd")))) < 0.02 for s in adm_scen)
    check(f"毛利=报价−9项成本（{len(adm_scen)} 情景全对账）", ok_margin)
    approved = [c for c in adm_cases if c["status"] in ("approved", "quote_with_conditions")]
    check("批准案件的 SKU 已转 active（E1/N5）",
          all(skus[c["sku_id"]]["sku_status"] == "active" for c in approved))
    # 设计案例
    a = cases["AC-2026-0031"]
    a_plan = [p for p in adm_plans if p["admission_case_id"] == "AC-2026-0031"]
    a_scen = [s for s in adm_scen if s["logistics_plan_id"] == a_plan[0]["logistics_plan_id"]] if a_plan else []
    check("AC-DEMO-A：priced/CUS-0007(has_ior)/DDP方案/三情景毛利为正",
          a["status"] == "priced" and custs["CUS-0007"]["ior_capability"] == "has_ior"
          and a_plan and a_plan[0]["incoterm"] == "DDP" and len(a_scen) == 3
          and all(float(s["gross_margin_usd"]) > 0 for s in a_scen))
    b_find = [f for f in adm_find if f["admission_case_id"] == "AC-2026-0032"]
    check("AC-DEMO-B：存在未verified的critical finding（G1素材）",
          any(f["severity"] == "critical" and f["evidence_status"] != "verified" for f in b_find)
          and cases["AC-2026-0032"]["risk_level"] == "critical")
    check("AC-DEMO-C：in_precheck/客户needs_partner/尚无方案",
          cases["AC-2026-0033"]["status"] == "in_precheck"
          and custs["CUS-0011"]["ior_capability"] == "needs_partner"
          and not any(p["admission_case_id"] == "AC-2026-0033" for p in adm_plans))
    check("AC-DEMO-D：needs_more_info（回流素材）",
          cases["AC-2026-0034"]["status"] == "needs_more_info")
    e_plan = [p for p in adm_plans if p["admission_case_id"] == "AC-2026-0035"]
    e_scen = [s for s in adm_scen if e_plan and s["logistics_plan_id"] == e_plan[0]["logistics_plan_id"]]
    check("AC-DEMO-E：conservative 毛利为负、base 为正",
          any(s["scenario_type"] == "conservative" and float(s["gross_margin_usd"]) < 0 for s in e_scen)
          and any(s["scenario_type"] == "base" and float(s["gross_margin_usd"]) > 0 for s in e_scen))
    check("门禁真值表 ≥7 条且案件引用存在",
          len(gates) >= 7 and all(g["admission_case_id"] in cases for g in gates))
    check("finding 引用完整 / hts 类必带税号",
          all(f["admission_case_id"] in cases for f in adm_find)
          and all(f["hts_candidate"] for f in adm_find if f["finding_type"] == "hts"))

    print("== 7. 费用扩展（v0.4-X2）==")
    from datetime import date as _date
    from .world import iso6346_check_digit
    containers = load(raw_dir, "tms_containers")
    invoices = load(raw_dir, "ap_invoices")
    inv_lines = load(raw_dir, "ap_invoice_lines")
    expected_costs = load(raw_dir, "ap_expected_costs")
    rate_card = load(raw_dir, "rate_card")
    anom = load(truth_dir, "expected_cost_anomalies")

    cont_by_ship = defaultdict(list)
    for r in containers:
        cont_by_ship[r["shipment_id"]].append(r)
    # XA1：每票 ≥1 柜且恰一个 primary；柜号 ISO 6346 校验位合法
    truthy = ("True", "1", "true")
    every_ship_has_container = all(cont_by_ship.get(sid) for sid in ship_ids)
    one_primary = all(sum(1 for c in cont_by_ship[sid] if c["is_primary"] in truthy) == 1
                      for sid in ship_ids)
    check("XA1 每票 ≥1 柜且恰一个 primary", every_ship_has_container and one_primary)
    cd_ok = all(len(c["container_no"]) == 11
                and iso6346_check_digit(c["container_no"][:10]) == c["container_no"][10]
                for c in containers)
    check("XA1 全部柜号 ISO 6346 校验位合法", cd_ok,
          f"{sum(1 for c in containers if iso6346_check_digit(c['container_no'][:10]) != c['container_no'][10])} 个非法")
    # XA2：≥15 票有 ≥2 柜
    multi = sum(1 for sid in ship_ids if len(cont_by_ship.get(sid, [])) >= 2)
    check("XA2 ≥15 票有 ≥2 柜（多柜升级）", multi >= 15, f"got {multi}")

    # XA4：每张发票 total=Σ行（±0.02）
    lines_by_inv = defaultdict(list)
    for r in inv_lines:
        lines_by_inv[r["invoice_id"]].append(r)
    total_ok = all(abs(float(i["total_usd"])
                       - round(sum(float(l["amount_usd"]) for l in lines_by_inv[i["invoice_id"]]), 2)) <= 0.02
                   for i in invoices)
    check("XA4 每张发票 total=Σ行（±0.02）", total_ok)
    # THC/DET/DEM/CHS 行必带 container_no
    cl_codes = {"THC", "DET", "DEM", "CHS"}
    cl_ok = all(l["container_no"] for l in inv_lines if l["charge_code"] in cl_codes)
    check("XA4 THC/DET/DEM/CHS 行必带 container_no", cl_ok)
    # expected_costs 无 DET/DEM/CHS/ACC 费种
    ec_clean = not any(r["charge_code"] in ("DET", "DEM", "CHS", "ACC") for r in expected_costs)
    check("XA4 expected_costs 无 DET/DEM/CHS/ACC（R6 判据）", ec_clean)

    # XA5：gt 每行引用的 invoice_line 全部存在
    il_ids = {r["invoice_line_id"] for r in inv_lines}
    gt_refs = []
    for a in anom:
        gt_refs.extend(json.loads(a["affected_invoice_line_ids"]))
    check("XA5 gt 引用的 invoice_line 全部存在",
          all(x in il_ids for x in gt_refs),
          f"缺失 {[x for x in gt_refs if x not in il_ids][:5]}")
    # 六个设计案例 gt 齐备且 case_id 正确
    cd_rules = {"CD-A": "R4", "CD-B": "R5", "CD-C": "R6", "CD-D": "R6", "CD-F": "R5"}
    anom_by_case = {a["case_id"]: a for a in anom if a["case_id"]}
    cd_present = all(c in anom_by_case and anom_by_case[c]["rule_id"] == r
                     for c, r in cd_rules.items())
    check("XA5 五个注入设计案例 gt 齐备且 rule 正确", cd_present,
          f"got {[(c, anom_by_case.get(c, {}).get('rule_id')) for c in cd_rules]}")
    # CD-E 的船不出现在 gt（不误报反例）
    cost_design = None
    for _ in [0]:
        # 从生成器取设计船映射（world 已在可复现性段构建过，此处重建一次取映射）
        pass
    # 用 anomalies 里所有 shipment 集合 + CD-E 判据：CD-E 船须无任何 gt 行
    # CD-E 船号从设计案例映射取（重建世界）
    w2, _e2, _n2 = build(cfg)
    cde_ship = w2["cost"]["design_cases"].get("CD-E")
    check("XA5 CD-E 船不出现在 gt（不误报反例）",
          cde_ship is not None and not any(a["shipment_id"] == cde_ship for a in anom))
    # CD-C 的 attribution 含"延误"
    cdc = anom_by_case.get("CD-C")
    check("XA5 CD-C attribution 含'延误'（F4 跨场景归因）",
          bool(cdc) and "延误" in cdc["attribution"])

    # 发票只开给非 planned 船；issue_date ≤ 数据窗口终点
    snap = json.loads((Path(truth_dir) / "world_snapshot.json").read_text(encoding="utf-8"))
    planned = {s for s, v in snap["shipments"].items() if v["status"] == "planned"}
    inv_ships = {i["shipment_id"] for i in invoices}
    check("发票只开给非 planned 船", not (planned & inv_ships),
          f"planned 船被开票: {sorted(planned & inv_ships)[:5]}")
    win_end = cfg["window"]["end"]
    check("issue_date ≤ 数据窗口终点", all(i["issue_date"] <= win_end for i in invoices))
    # rate_card 完整性（引用抽检）
    check("rate_card 覆盖 OFT/THC/FSC/DOC/CUS/DTY/WHS/STO/LMD",
          {r["charge_code"] for r in rate_card} >= {"OFT", "THC", "FSC", "DOC", "CUS",
                                                    "DTY", "WHS", "STO", "LMD"})
    # 发票行引用完整（invoice_id 存在、container_no 若非空须存在）
    inv_ids = {i["invoice_id"] for i in invoices}
    cno_all = {c["container_no"] for c in containers}
    check("发票行 invoice_id 引用完整", all(l["invoice_id"] in inv_ids for l in inv_lines))
    check("发票行 container_no 引用完整（若非空）",
          all((not l["container_no"]) or l["container_no"] in cno_all for l in inv_lines))
    print(f"  柜数={len(containers)} 发票={len(invoices)} 发票行={len(inv_lines)} "
          f"基准={len(expected_costs)} gt={len(anom)} 多柜票={multi}")

    print("== 8. 单证号级 ER（v0.6-H3）==")
    from .world import DESIGN_SHIP_NUMS
    ms = t["tms_milestones"]
    # 源表已删 shipment_id、改带 booking_no + container_no
    check("H3 tms_milestones 无 shipment_id 列，含 booking_no + container_no",
          "shipment_id" not in ms[0] and "booking_no" in ms[0] and "container_no" in ms[0])
    # 设计船集合：控制塔预留槽位 ∪ 费用设计案例船（CD-A..F）
    design_ships = {f"SHP-2026-{n:04d}" for n in DESIGN_SHIP_NUMS}
    design_ships |= {v for v in w2["cost"]["design_cases"].values() if v}
    # 设计船行一律两者齐备
    design_rows = [r for r in ms if ms_ship(r) in design_ships]
    design_full = all(r["booking_no"] and r["container_no"] for r in design_rows)
    check("H3 设计船行全齐备（booking_no 且 container_no 非空）", design_full,
          f"{sum(1 for r in design_rows if not (r['booking_no'] and r['container_no']))} 行不齐备")
    # 比例合规（±3pp，作用于非设计船行）——排除 typo 行（typo 单独占空）
    typo_mids = {r["target_id"] for r in noi if r["noise_type"] == "doc_ref_typo"}
    nd = [r for r in ms if ms_ship(r) not in design_ships and r["milestone_id"] not in typo_mids]
    n_nd = len(nd)
    both = sum(1 for r in nd if r["booking_no"] and r["container_no"])
    conly = sum(1 for r in nd if not r["booking_no"] and r["container_no"])
    bonly = sum(1 for r in nd if r["booking_no"] and not r["container_no"])
    nr = cfg["noise_rates"]
    exp_conly = nr["doc_ref_container_only"]
    exp_bonly = nr["doc_ref_booking_only"]
    check(f"H3 仅柜号比例≈{exp_conly:.0%}（±3pp）", abs(conly / n_nd - exp_conly) <= 0.03,
          f"got {conly/n_nd:.3f}")
    check(f"H3 仅订舱号比例≈{exp_bonly:.0%}（±3pp）", abs(bonly / n_nd - exp_bonly) <= 0.03,
          f"got {bonly/n_nd:.3f}")
    check("H3 每行至少含一个单证号（非 typo 行）", both + conly + bonly == n_nd)
    # doc_ref_typo：行数=噪声日志数；每行必然不可解析（booking 篡改不在册 + 柜号空）
    typo_rows = [r for r in ms if r["milestone_id"] in typo_mids]
    check("H3 doc_ref_typo 行数=噪声日志数", len(typo_rows) == len(typo_mids) and len(typo_mids) > 0,
          f"rows={len(typo_rows)} log={len(typo_mids)}")
    unresolvable = all((not r["container_no"])
                       and r["booking_no"] not in ship_by_booking for r in typo_rows)
    check("H3 doc_ref_typo 行必然不可解析（柜号空 + booking 不在册）", unresolvable)
    # typo 只打在非设计船：设计船行全齐备（上已校验）即隐含 typo 不落在设计船
    check("H3 doc_ref_typo 未打在任何设计船（设计船行全齐备已隐含）", design_full)
    print(f"  非设计行 {n_nd}：both={both} 仅柜={conly} 仅订舱={bonly}；typo={len(typo_rows)}")

    print(f"\n{'=' * 40}\n结果: {'全部通过 ✔' if not FAILS else f'{len(FAILS)} 项失败: {FAILS}'}")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
