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
    check("suppliers 数量", len(t["srm_suppliers"]) == c["suppliers"])
    check("skus 数量", len(t["catalog_skus"]) == c["skus"])
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
    dup_rows = [r for r in t["tms_milestones"]
                if r["shipment_id"] == "SHP-2026-0090" and r["event_type"] == "eta_change"]
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

    print(f"\n{'=' * 40}\n结果: {'全部通过 ✔' if not FAILS else f'{len(FAILS)} 项失败: {FAILS}'}")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
