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

    print("== 9. 采购三方对账（P1 Build 1/3）==")
    po_lines = load(raw_dir, "srm_po_lines")
    grns = load(raw_dir, "srm_goods_receipts")
    grn_lines = load(raw_dir, "srm_goods_receipt_lines")
    sup_inv = load(raw_dir, "ap_supplier_invoices")
    sup_inv_lines = load(raw_dir, "ap_supplier_invoice_lines")
    proc_gt = load(truth_dir, "expected_procurement_risks")
    pc = cfg["procurement"]
    inj = pc["inject"]

    po_ids = {r["po_id"] for r in t["srm_purchase_orders"]}
    pol_ids = {r["po_line_id"] for r in po_lines}
    grn_ids = {r["grn_id"] for r in grns}
    sinv_ids = {r["supplier_invoice_id"] for r in sup_inv}
    proc_sup_ids = {r["supplier_id"] for r in t["srm_suppliers"]}

    # 9.1 规模：非空、PO 数=Σinject（含富化 4 桶：r11/r11_gray/r12/r12_gray 也有 po_line）、行数≥PO 数
    po_bucket_keys = ("clean", "gray", "r7", "r8", "r9", "r10", "r11", "r11_gray", "r12", "r12_gray")
    n_pos_expected = sum(inj[k] for k in po_bucket_keys)
    distinct_pos = {r["po_id"] for r in po_lines}
    check("采购 PO 数 = Σinject（含富化 4 桶）", len(distinct_pos) == n_pos_expected,
          f"got {len(distinct_pos)} vs {n_pos_expected}")
    check("po_lines/grn/grn_lines/supplier_invoices/lines 均非空",
          all([po_lines, grns, grn_lines, sup_inv, sup_inv_lines]))
    multi_line_pos = sum(1 for pid in distinct_pos
                         if sum(1 for l in po_lines if l["po_id"] == pid) >= 2)
    check("存在多 SKU PO（≥2 行，覆盖 D2 单 SKU 约束）", multi_line_pos >= 10,
          f"got {multi_line_pos}")

    # 9.2 引用完整性（全部应成立）
    check("po_lines.po_id 引用既有 purchase_orders", all(r["po_id"] in po_ids for r in po_lines))
    check("grn.po_id 引用既有 purchase_orders", all(r["po_id"] in po_ids for r in grns))
    check("grn_lines 引用完整（grn_id + po_line_id）",
          all(r["grn_id"] in grn_ids and r["po_line_id"] in pol_ids for r in grn_lines))
    check("grn_line received=accepted+rejected",
          all(int(r["received_qty"]) == int(r["accepted_qty"]) + int(r["rejected_qty"])
              for r in grn_lines))
    check("supplier_invoice 引用完整（po_id + supplier_id）",
          all(r["po_id"] in po_ids and r["supplier_id"] in proc_sup_ids for r in sup_inv))
    check("supplier_invoice_lines 引用完整（inv + po_line）",
          all(r["supplier_invoice_id"] in sinv_ids and r["po_line_id"] in pol_ids
              for r in sup_inv_lines))
    inv_sum = defaultdict(float)
    for r in sup_inv_lines:
        inv_sum[r["supplier_invoice_id"]] += float(r["amount_usd"])
    check("supplier_invoice total=Σ行（±0.02）",
          all(abs(float(i["total_usd"]) - round(inv_sum[i["supplier_invoice_id"]], 2)) <= 0.02
              for i in sup_inv))

    # 9.3 约定：币种 USD、金额正、数量正、日期在窗口内、显式 as_of
    win_end = cfg["window"]["end"]
    win_start = cfg["window"]["start"]
    as_of = cfg["window"]["as_of"]
    check("po_lines 币种全 USD、qty>0、单价>0、as_of 显式",
          all(r["currency"] == "USD" and int(r["qty"]) > 0 and float(r["unit_price_usd"]) > 0
              and r["as_of_date"] == as_of for r in po_lines))
    check("收货/开票日期在数据窗口内",
          all(win_start <= r["received_date"] <= win_end for r in grns)
          and all(win_start <= r["issue_date"] <= win_end for r in sup_inv))

    # 9.3b 行级 received_date（R7 数据模型缺口修复）：每 grn_line 有值且在窗口内；
    #   R7 延误行的行级最早到货日 > expected_ready + 容差（真实延误持久化到行级，不再被 GRN 头 min 掩盖）
    from datetime import date as _pdate
    tol_days = pc["receipt_delay_tol_days"]
    check("grn_lines 均有行级 received_date 且在数据窗口内",
          all(r.get("received_date") and win_start <= r["received_date"] <= win_end
              for r in grn_lines),
          f"缺值/越界: {[r['grn_line_id'] for r in grn_lines if not (r.get('received_date') and win_start <= r['received_date'] <= win_end)][:5]}")
    pol_ready = {r["po_line_id"]: r["expected_ready_date"] for r in po_lines}
    grn_recv_by_line = defaultdict(list)
    for r in grn_lines:
        grn_recv_by_line[r["po_line_id"]].append(r["received_date"])

    def _late_line_ok(gt_row):
        recvs = grn_recv_by_line.get(gt_row["po_line_id"], [])
        if not recvs or gt_row["po_line_id"] not in pol_ready:
            return False
        first = min(recvs)
        exp = pol_ready[gt_row["po_line_id"]]
        return (_pdate.fromisoformat(first) - _pdate.fromisoformat(exp)).days > tol_days
    r7_gt = [r for r in proc_gt if r["rule_id"] == "R7"]
    check("R7 延误行的行级最早到货日 > expected_ready + 容差",
          bool(r7_gt) and all(_late_line_ok(r) for r in r7_gt),
          f"不满足: {[r['po_line_id'] for r in r7_gt if not _late_line_ok(r)]}")

    # 9.4 ground truth：rule 合法、引用完整（按锚点）、计数=inject、severity 合法
    check("采购真值 rule 仅 R7-R13",
          all(r["rule_id"] in ("R7", "R8", "R9", "R10", "R11", "R12", "R13") for r in proc_gt))

    def _ref_ok(r):  # 引用完整性按锚点：R7-R11 锚 po_line；R12 锚 po；R13 锚 supplier
        rule = r["rule_id"]
        if rule in ("R7", "R8", "R9", "R10", "R11"):
            return (r["po_id"] in po_ids and r["po_line_id"] in pol_ids
                    and r["supplier_id"] in proc_sup_ids)
        if rule == "R12":
            return r["po_id"] in po_ids and r["supplier_id"] in proc_sup_ids and r["po_line_id"] == ""
        if rule == "R13":
            return r["supplier_id"] in proc_sup_ids and r["po_id"] == "" and r["po_line_id"] == ""
        return False
    check("采购真值引用完整（锚点：R7-R11 po_line / R12 po / R13 supplier）",
          all(_ref_ok(r) for r in proc_gt),
          f"违规: {[r['expected_procurement_risk_id'] for r in proc_gt if not _ref_ok(r)][:5]}")
    check("采购真值 severity 合法", all(r["severity"] in ("medium", "high") for r in proc_gt))
    gt_by_rule = defaultdict(int)
    for r in proc_gt:
        gt_by_rule[r["rule_id"]] += 1
    check("R7-R10 真值计数 = inject 配置",
          gt_by_rule["R7"] == inj["r7"] and gt_by_rule["R8"] == inj["r8"]
          and gt_by_rule["R9"] == inj["r9"] and gt_by_rule["R10"] == inj["r10"],
          f"got {dict(gt_by_rule)} vs r7={inj['r7']} r8={inj['r8']} r9={inj['r9']} r10={inj['r10']}")
    # 每个 PO 锚定异常恰一条真值（1:1；R7-R11/R12 po 锚，R13 supplier 锚不计此项）
    gt_per_po = defaultdict(int)
    for r in proc_gt:
        if r["po_id"]:
            gt_per_po[r["po_id"]] += 1
    check("每个异常 PO 恰一条真值（1:1，含 R11/R12）", all(v == 1 for v in gt_per_po.values()),
          f"多真值 PO: {[k for k, v in gt_per_po.items() if v > 1]}")

    # 9.5 灰区/干净不进真值（测未来误报）；画像与真值一致
    wp, _ep, _np = build(cfg)  # 重建取 profile/design_cases（可复现，section 1 已验证一致）
    proc = wp["procurement"]
    profile = proc["profile"]
    gt_pos = {r["po_id"] for r in proc_gt}
    clean_pos = {pid for pid, pr in profile.items() if pr == "clean"}
    gray_pos = {pid for pid, pr in profile.items() if pr == "gray"}
    check("干净 PO 不在真值", not (clean_pos & gt_pos), f"泄漏: {sorted(clean_pos & gt_pos)}")
    check("灰区 PO 不在真值（测未来误报）", not (gray_pos & gt_pos),
          f"泄漏: {sorted(gray_pos & gt_pos)}")
    for rule, prof in (("R7", "r7"), ("R8", "r8"), ("R9", "r9"), ("R10", "r10")):
        prof_pos = {pid for pid, pr in profile.items() if pr == prof}
        rule_pos = {r["po_id"] for r in proc_gt if r["rule_id"] == rule}
        check(f"{rule} 真值 PO 集合 == {prof} 画像 PO 集合", prof_pos == rule_pos,
              f"diff: {sorted(prof_pos ^ rule_pos)}")

    # 9.6 设计锚点 PD-A..PD-D 在真值且 rule 正确；PD-E/PD-F 不在真值
    dc = proc["design_cases"]
    gt_by_case = {r["case_id"]: r for r in proc_gt if r["case_id"]}
    pd_rule = {"PD-A": "R7", "PD-B": "R8", "PD-C": "R9", "PD-D": "R10"}
    check("PD-A..PD-D 设计锚点在真值且 rule 正确",
          all(c in gt_by_case and gt_by_case[c]["rule_id"] == r for c, r in pd_rule.items()),
          f"got {[(c, gt_by_case.get(c, {}).get('rule_id')) for c in pd_rule]}")
    check("PD-E(clean)/PD-F(gray) 不在真值",
          dc["PD-E"] not in gt_pos and dc["PD-F"] not in gt_pos)
    print(f"  采购 PO={len(distinct_pos)}(多SKU {multi_line_pos}) 行={len(po_lines)} "
          f"GRN={len(grns)} GRN行={len(grn_lines)} 供票={len(sup_inv)} 供票行={len(sup_inv_lines)}")
    print(f"  R7-R10 真值: {dict((k, gt_by_rule[k]) for k in ('R7', 'R8', 'R9', 'R10'))}  设计锚点: {dc}")

    print("== 10. 采购富化 R11-R13（P2 model+data）==")
    payments = load(raw_dir, "ap_purchase_payments")
    quals = load(raw_dir, "srm_supplier_qualifications")
    qinj = pc["qual_inject"]
    as_of = cfg["window"]["as_of"]
    grace = pc["deposit_grace_days"]

    # 10.1 新对象规模 + 引用完整
    check("PurchasePayment 每 selected PO 一笔（=Σ PO 桶）", len(payments) == n_pos_expected,
          f"got {len(payments)} vs {n_pos_expected}")
    check("SupplierQualification 非空", len(quals) > 0)
    check("payment 引用完整（po_id ∈ purchase_orders）", all(r["po_id"] in po_ids for r in payments))
    check("payment 字段合法（type/amount/exposure/as_of）",
          all(r["payment_type"] in ("deposit", "balance", "full") and float(r["amount_usd"]) >= 0
              and r["exposure_status"] in ("covered", "at_risk", "released")
              and r["as_of_date"] == as_of for r in payments))
    check("qualification 引用完整（supplier_id ∈ suppliers）",
          all(r["supplier_id"] in proc_sup_ids for r in quals))
    check("qualification 字段合法（evidence/status/valid_from≤valid_to/as_of）",
          all(r["evidence_status"] in ("missing", "provided", "verified", "rejected")
              and r["status"] in ("valid", "expiring", "expired", "revoked")
              and r["valid_from"] <= r["valid_to"] and r["as_of_date"] == as_of for r in quals))

    # 10.2 R11/R12 画像 PO 集合 == 真值集合（1:1）；R13 真值 supplier == qual_roles["r13"]
    r11_pos = {pid for pid, pr in profile.items() if pr == "r11"}
    r12_pos = {pid for pid, pr in profile.items() if pr == "r12"}
    gt_r11 = {r["po_id"] for r in proc_gt if r["rule_id"] == "R11"}
    gt_r12 = {r["po_id"] for r in proc_gt if r["rule_id"] == "R12"}
    gt_r13 = {r["supplier_id"] for r in proc_gt if r["rule_id"] == "R13"}
    check("R11 真值 PO 集合 == r11 画像", r11_pos == gt_r11, f"diff: {sorted(r11_pos ^ gt_r11)}")
    check("R12 真值 PO 集合 == r12 画像", r12_pos == gt_r12, f"diff: {sorted(r12_pos ^ gt_r12)}")
    check("R13 真值 supplier == qual_roles.r13", set(proc["qual_roles"]["r13"]) == gt_r13,
          f"diff: {sorted(set(proc['qual_roles']['r13']) ^ gt_r13)}")
    check("R11/R12/R13 真值计数 = inject",
          gt_by_rule["R11"] == inj["r11"] and gt_by_rule["R12"] == inj["r12"]
          and gt_by_rule["R13"] == qinj["r13"],
          f"got R11={gt_by_rule['R11']} R12={gt_by_rule['R12']} R13={gt_by_rule['R13']}")

    # 10.3 灰区/干净不进真值（测未来误报）：r11_gray/r12_gray PO + r13_gray/r13_expiring supplier
    gray_bucket_pos = {pid for pid, pr in profile.items() if pr in ("r11_gray", "r12_gray")}
    check("r11_gray/r12_gray PO 不在真值", not (gray_bucket_pos & gt_pos),
          f"泄漏: {sorted(gray_bucket_pos & gt_pos)}")
    gray_sups = set(proc["qual_roles"]["r13_gray"]) | set(proc["qual_roles"]["r13_expiring"])
    check("r13_gray/r13_expiring supplier 不在真值", not (gray_sups & gt_r13),
          f"泄漏: {sorted(gray_sups & gt_r13)}")

    # 10.4 R12 真值 PO 确无收货（open PO，敞口成立的数据前提）；R11 真值 PO 有收货（足量收货前提）
    grn_pos = {r["po_id"] for r in grns}
    check("R12 真值 PO 均无收货（GRN 不含）", not (gt_r12 & grn_pos),
          f"违规: {sorted(gt_r12 & grn_pos)}")
    check("R11 真值 PO 均有收货（足量收货、发票超收前提）", gt_r11 <= grn_pos,
          f"缺收货: {sorted(gt_r11 - grn_pos)}")

    # 10.5 R13 target 供应商确有 open PO（status≠closed），且过期证无同类续证
    open_sups = {r["supplier_id"] for r in t["srm_purchase_orders"] if r["status"] != "closed"}
    check("R13 target 供应商均有 open PO", gt_r13 <= open_sups,
          f"无 open PO: {sorted(gt_r13 - open_sups)}")

    def _uncovered_expired(sup):  # 存在某 cert_type 全过期且无同类有效证覆盖 as_of
        by_cert = defaultdict(list)
        for q in quals:
            if q["supplier_id"] == sup:
                by_cert[q["cert_type"]].append(q)
        for cert, qs in by_cert.items():
            if any(q["valid_to"] < as_of for q in qs) \
                    and not any(q["valid_from"] <= as_of <= q["valid_to"] for q in qs):
                return True
        return False
    check("R13 target 均有'未覆盖的过期证'", all(_uncovered_expired(s) for s in gt_r13))
    check("R13 gray（已续期）无未覆盖过期证",
          not any(_uncovered_expired(s) for s in proc["qual_roles"]["r13_gray"]))

    print(f"  富化: payment={len(payments)} qualification={len(quals)}  "
          f"真值 R11={gt_by_rule['R11']} R12={gt_by_rule['R12']} R13={gt_by_rule['R13']}")

    print("== 11. 仓储库存主线（W1 Build 1/3）==")
    whs = load(raw_dir, "wms_warehouses")
    inv_pos = load(raw_dir, "wms_inventory_positions")
    reservations = load(raw_dir, "wms_inventory_reservations")
    cyc = load(raw_dir, "wms_cycle_counts")
    wh_gt = load(truth_dir, "expected_warehouse_risks")
    wcfg = cfg["warehouse"]
    winj = wcfg["inject"]
    tol = wcfg["cycle_count_tol"]

    wh_ids = {r["warehouse_id"] for r in whs}
    pos_ids = {r["inventory_position_id"] for r in inv_pos}
    line_ids_all = {r["so_line_id"] for r in t["oms_so_lines"]}
    sku_ids_all = {r["sku_id"] for r in load(raw_dir, "catalog_skus")}

    # 11.1 规模：5 仓（覆盖 type 枚举）、position 数=Σ桶、预留/盘点非空
    n_pos_expected = sum(winj[k] for k in
                         ("clean", "r16", "r16_gray", "r17", "r17_gray", "r18", "r18_gray"))
    check("仓库 5 个且 type 覆盖 5 类枚举", len(whs) == 5
          and {r["type"] for r in whs} == {"overseas", "bonded", "domestic", "FBA", "3PL"},
          f"got {len(whs)} types={sorted({r['type'] for r in whs})}")
    check("InventoryPosition 数 = Σ注入桶", len(inv_pos) == n_pos_expected,
          f"got {len(inv_pos)} vs {n_pos_expected}")
    check("预留/盘点非空", bool(reservations) and bool(cyc))

    # 11.2 引用完整性
    check("position 引用完整（warehouse + sku）",
          all(r["warehouse_id"] in wh_ids and r["sku_id"] in sku_ids_all for r in inv_pos))
    check("(sku,warehouse) 唯一（至多一条 position）",
          len({(r["sku_id"], r["warehouse_id"]) for r in inv_pos}) == len(inv_pos))
    check("reservation 引用完整（position + so_line）",
          all(r["inventory_position_id"] in pos_ids and r["so_line_id"] in line_ids_all
              for r in reservations))
    check("cycle_count 引用完整（position + warehouse）+ variance=counted−system",
          all(r["inventory_position_id"] in pos_ids and r["warehouse_id"] in wh_ids
              and int(r["variance"]) == int(r["counted_qty"]) - int(r["system_qty"]) for r in cyc))

    # 11.3 字段合法 + 显式 as_of
    as_of = cfg["window"]["as_of"]
    check("position 桶字段非负、safety>0、as_of 显式",
          all(all(int(r[k]) >= 0 for k in ("available_qty", "reserved_qty", "in_transit_qty",
                                           "quarantine_qty")) and int(r["safety_stock"]) > 0
              and r["as_of_date"] == as_of for r in inv_pos))
    check("reservation status 合法（open/allocated/released/fulfilled/backordered）",
          all(r["status"] in ("open", "allocated", "released", "fulfilled", "backordered")
              for r in reservations))
    check("cycle_count status 合法（scheduled/counted/variance/reconciled）",
          all(r["status"] in ("scheduled", "counted", "variance", "reconciled") for r in cyc))

    # 11.4 ground truth：rule 合法、计数=inject、severity 合法、锚点引用完整
    check("仓储真值 rule 仅 R16-R18",
          all(r["rule_id"] in ("R16", "R17", "R18") for r in wh_gt))
    wh_by_rule = defaultdict(int)
    for r in wh_gt:
        wh_by_rule[r["rule_id"]] += 1
    check("R16-R18 真值计数 = inject 配置",
          wh_by_rule["R16"] == winj["r16"] and wh_by_rule["R17"] == winj["r17"]
          and wh_by_rule["R18"] == winj["r18"],
          f"got {dict(wh_by_rule)} vs r16={winj['r16']} r17={winj['r17']} r18={winj['r18']}")
    check("仓储真值 severity 合法", all(r["severity"] in ("medium", "high") for r in wh_gt))

    def _wh_ref_ok(r):  # 锚点引用：R16 锚 position / R17 锚 so_line / R18 锚 cycle_count（均 + warehouse）
        if r["warehouse_id"] not in wh_ids:
            return False
        if r["rule_id"] == "R16":
            return r["inventory_position_id"] in pos_ids
        if r["rule_id"] == "R17":
            return r["so_line_id"] in line_ids_all
        return r["cycle_count_id"] in {c["cycle_count_id"] for c in cyc}
    check("仓储真值锚点引用完整（R16 position / R17 so_line / R18 cycle_count）",
          all(_wh_ref_ok(r) for r in wh_gt),
          f"违规: {[r['expected_warehouse_risk_id'] for r in wh_gt if not _wh_ref_ok(r)][:5]}")

    # 11.5 独立 oracle：verify 侧按规则重算应检异常，真值集合必须逐一相等（灰区/干净不入真值、真值完整）
    wp_wh = wp["warehouse"]  # 复用 section 9 重建的世界（可复现，section 1 已验证一致）
    wp_lines = wp["lines"]
    pos_by_id = {p["inventory_position_id"]: p for p in wp_wh["positions"]}
    r16_oracle = {p["inventory_position_id"] for p in wp_wh["positions"]
                  if p["available_qty"] <= p["safety_stock"]}
    r17_oracle = set()
    for rsv in wp_wh["reservations"]:
        if rsv["status"] != "open":
            continue
        ln = wp_lines.get(rsv["so_line_id"])
        p = pos_by_id.get(rsv["inventory_position_id"])
        if ln and ln["line_status"] == "open" and p:
            atp = p["available_qty"] + p["in_transit_qty"] - p["reserved_qty"]
            if atp < rsv["qty"]:
                r17_oracle.add(rsv["so_line_id"])
    r18_oracle = {c["cycle_count_id"] for c in wp_wh["cycle_counts"]
                  if c["system_qty"] > 0
                  and abs(c["counted_qty"] - c["system_qty"]) / c["system_qty"] > tol}
    gt_r16 = {r["inventory_position_id"] for r in wh_gt if r["rule_id"] == "R16"}
    gt_r17 = {r["so_line_id"] for r in wh_gt if r["rule_id"] == "R17"}
    gt_r18 = {r["cycle_count_id"] for r in wh_gt if r["rule_id"] == "R18"}
    check("R16 真值 == 独立 oracle（available≤safety）→ 灰区不入、真值完整",
          gt_r16 == r16_oracle, f"diff: {sorted(gt_r16 ^ r16_oracle)}")
    check("R17 真值 == 独立 oracle（open SOL 且 ATP<需求）→ 灰区不入、真值完整",
          gt_r17 == r17_oracle, f"diff: {sorted(gt_r17 ^ r17_oracle)}")
    check("R18 真值 == 独立 oracle（|var|/system>tol）→ 灰区不入、真值完整",
          gt_r18 == r18_oracle, f"diff: {sorted(gt_r18 ^ r18_oracle)}")
    # 灰区显式存在（证明确有近阈值样本在测未来误报）：gray 桶数 > 0 且不在真值
    n_gray = winj["r16_gray"] + winj["r17_gray"] + winj["r18_gray"]
    check("灰区样本存在且总量 = 配置", n_gray == 6)
    print(f"  仓储: 仓={len(whs)} position={len(inv_pos)} 预留={len(reservations)} 盘点={len(cyc)}  "
          f"真值 R16={wh_by_rule['R16']} R17={wh_by_rule['R17']} R18={wh_by_rule['R18']}")

    print("== 12. 采购富化2 RFQ + R14/R15（P3 Build A）==")
    rfqs = load(raw_dir, "srm_rfqs")
    rfq_lines = load(raw_dir, "srm_rfq_lines")
    quotes = load(raw_dir, "srm_quotes")
    src_gt = load(truth_dir, "expected_sourcing_risks")
    scfg = cfg["sourcing"]
    sinj = scfg["inject"]
    src = wp["sourcing"]                # 复用 section 9.5 重建的世界（可复现，section 1 已验证一致）
    roles = src["roles"]
    skus_map = {r["sku_id"]: r for r in load(raw_dir, "catalog_skus")}
    sup_ids_all = {r["supplier_id"] for r in t["srm_suppliers"]}

    # 12.1 规模 + 引用完整性
    rfq_ids = {r["rfq_id"] for r in rfqs}
    check("RFQ/RFQLine/Quote 非空", bool(rfqs) and bool(rfq_lines) and bool(quotes))
    check("RFQ 引用完整（sku_id ∈ skus）+ status 合法",
          all(r["sku_id"] in skus_map and r["status"] in
              ("draft", "sent", "quoting", "evaluating", "awarded", "closed", "cancelled")
              for r in rfqs))
    check("RFQLine 引用完整（rfq_id ∈ rfqs, sku_id ∈ skus, qty>0）",
          all(r["rfq_id"] in rfq_ids and r["sku_id"] in skus_map and int(r["qty"]) > 0
              for r in rfq_lines))
    check("Quote 引用完整（rfq_id ∈ rfqs, supplier_id ∈ suppliers）+ status 合法",
          all(r["rfq_id"] in rfq_ids and r["supplier_id"] in sup_ids_all and r["status"] in
              ("invited", "submitted", "shortlisted", "awarded", "rejected", "expired")
              for r in quotes))

    # 12.2 R15 maverick 发票并入 ap_supplier_invoices，引用真实 PO/po_line、价量匹配 → 不触 R10/R11
    mav_inv = [r for r in sup_inv if r["supplier_invoice_id"].startswith("SINV-MVK")]
    mav_lines = [r for r in sup_inv_lines if r["supplier_invoice_id"].startswith("SINV-MVK")]
    pol_map = {r["po_line_id"]: r for r in po_lines}
    check("maverick 发票引用真实 PO + supplier + biller≠PO供应商",
          all(r["po_id"] in po_ids and r["supplier_id"] in sup_ids_all for r in mav_inv)
          and all(r["supplier_id"] != next(p["supplier_id"] for p in t["srm_purchase_orders"]
                                           if p["po_id"] == r["po_id"]) for r in mav_inv))
    check("maverick 发票行价=PO行价、量=PO行量（→ 不触 R10/R11）",
          all(r["po_line_id"] in pol_map
              and abs(float(r["unit_price_usd"]) - float(pol_map[r["po_line_id"]]["unit_price_usd"])) < 1e-6
              and int(r["qty"]) == int(pol_map[r["po_line_id"]]["qty"]) for r in mav_lines),
          f"违规: {[r['supplier_invoice_line_id'] for r in mav_lines if r['po_line_id'] not in pol_map or int(r['qty']) != int(pol_map.get(r['po_line_id'],{}).get('qty',-1))][:5]}")
    # maverick/gray 引用的 PO 必为 clean 画像（足量收货 → R11 前提排除）
    proc_profile = wp["procurement"]["profile"]
    check("maverick/gray 发票引用的 PO 均为 clean 画像（足量收货）",
          all(proc_profile.get(r["po_id"]) == "clean" for r in mav_inv),
          f"非 clean: {[(r['supplier_invoice_id'], proc_profile.get(r['po_id'])) for r in mav_inv if proc_profile.get(r['po_id']) != 'clean']}")

    # 12.3 独立 oracle（verify 侧按规则重算）：R14/R15 真值必须逐一相等
    disrupted = set(roles["disrupted"])
    awarded_alt = roles["awarded_alt"]
    active_skus = [k for k in skus_map if skus_map[k]["sku_status"] == "active"]
    incumbent = {k: skus_map[k]["supplier_id"] for k in active_skus}
    r14_oracle = set()
    for k in active_skus:
        approved = {incumbent[k]} | ({awarded_alt[k]} if k in awarded_alt else set())
        if len(approved) == 1 and incumbent[k] in disrupted:
            r14_oracle.add(k)
    gt_r14 = {r["sku_id"] for r in src_gt if r["rule_id"] == "R14"}
    check("R14 真值 == 独立 oracle（active 单源 且 incumbent 断供）→ 灰区不入、真值完整",
          gt_r14 == r14_oracle, f"diff: {sorted(gt_r14 ^ r14_oracle)}")

    pol_sku = {l["po_line_id"]: l["sku_id"] for l in po_lines}
    pos_sup = {p["po_id"]: p["supplier_id"] for p in t["srm_purchase_orders"]}
    mav_line_of = {l["supplier_invoice_id"]: l for l in mav_lines}
    r15_oracle = set()
    for inv in mav_inv:
        line = mav_line_of[inv["supplier_invoice_id"]]
        sku = pol_sku[line["po_line_id"]]
        biller = inv["supplier_id"]
        if biller == pos_sup[inv["po_id"]]:
            continue
        if biller == awarded_alt.get(sku):
            continue                       # approved 备源 → 豁免（灰区）
        r15_oracle.add(inv["po_id"])
    gt_r15 = {r["po_id"] for r in src_gt if r["rule_id"] == "R15"}
    check("R15 真值 == 独立 oracle（biller≠PO供应商 且非 approved 备源）→ 灰区不入、真值完整",
          gt_r15 == r15_oracle, f"diff: {sorted(gt_r15 ^ r15_oracle)}")

    # 12.4 计数 = inject；severity 合法；灰区不入真值
    src_by_rule = defaultdict(int)
    for r in src_gt:
        src_by_rule[r["rule_id"]] += 1
    check("R14/R15 真值计数 = inject 配置",
          src_by_rule["R14"] == sinj["r14"] and src_by_rule["R15"] == sinj["r15"],
          f"got R14={src_by_rule['R14']} R15={src_by_rule['R15']} vs r14={sinj['r14']} r15={sinj['r15']}")
    check("采购富化2 真值 rule 仅 R14/R15 + severity 合法",
          all(r["rule_id"] in ("R14", "R15") and r["severity"] in ("medium", "high")
              for r in src_gt))
    # 灰区：r14_gray（单源但健康）、多源 SKU、在建目标 均不入 R14 真值
    check("R14 灰区不入真值（r14_gray 单源健康）", not (set(roles["r14_gray"]) & gt_r14),
          f"泄漏: {sorted(set(roles['r14_gray']) & gt_r14)}")
    check("多源 SKU 不入 R14 真值（2+ approved 供应商）",
          not (set(roles["multi_source_skus"]) & gt_r14),
          f"泄漏: {sorted(set(roles['multi_source_skus']) & gt_r14)}")
    check("在建 RFQ 目标仍单源→仍在 R14 真值（未 awarded 不加备源）",
          all(k in gt_r14 for k in roles["r14_targets"][:sinj["r14_inflight"]]))
    # R15 灰区（approved 备源 biller）不入真值
    gray_gt_pos = {inv["po_id"] for inv in mav_inv
                   if inv["supplier_invoice_id"] in roles["r15_gray_invoices"]}
    check("R15 灰区（approved 备源 biller）不入真值", not (gray_gt_pos & gt_r15),
          f"泄漏: {sorted(gray_gt_pos & gt_r15)}")
    check("R15 灰区注入数 = 配置", len(roles["r15_gray_invoices"]) == sinj["r15_gray"],
          f"got {len(roles['r15_gray_invoices'])}")
    print(f"  询价: RFQ={len(rfqs)} RFQLine={len(rfq_lines)} Quote={len(quotes)}  "
          f"maverick 发票={len(mav_inv)}(灰区{len(roles['r15_gray_invoices'])})  "
          f"真值 R14={src_by_rule['R14']} R15={src_by_rule['R15']}  多源SKU={len(roles['multi_source_skus'])}")

    print(f"\n{'=' * 40}\n结果: {'全部通过 ✔' if not FAILS else f'{len(FAILS)} 项失败: {FAILS}'}")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
