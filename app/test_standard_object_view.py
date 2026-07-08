"""标准对象视图兜底层脚本测试：python3 -m app.test_standard_object_view

在 ontology.sqlite 临时副本上验证：
① ≥5 种对象类型（Shipment/Customer/Sku/SalesOrderLine/Container 等）build_standard_view 返回
   属性 + 关联对象非空
② role 脱敏生效（ops 看 Customer 时 tier 掩码；ops 看含成本对象 CostScenario 时成本掩码；
   finance/manager 可见）
③ route_object：四核心 → rich、其余注册类型 → standard
④ 标准视图不含任何 action（只读，read_only=True、无 available_actions/actions 键）
⑤ 未知对象类型 / 不存在 id 优雅处理

只读断言 + 临时副本（绝不污染 data/ontology.sqlite）。
"""
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

from . import standard_object_view as sov

FAILS = []
# 演示锚点（种子库已验证存在）
SHIP = "SHP-2026-0099"
CUST = "CUS-0001"      # tier=A / credit_terms=NET60 / risk_tier=medium
SKU = "SKU-0001"       # supplier_id=SUP-0003 / unit_price_usd=6.5
SOLINE = "SOL-0001-1"
CONTAINER = "CBHU1014248"
COSTSCEN = "CS-00001"  # quote_price_usd / gross_margin_usd（成本字段）
SUPPLIER = "SUP-0001"  # uflpa_risk_flag
ALLOC = "ALC-000001"
PLAN = "LP-00001"
FINDING = "CF-00001"
INVLINE = "IL-000001"


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def main():
    tmp = Path(tempfile.mkdtemp()) / "sov.sqlite"
    shutil.copy("data/ontology.sqlite", tmp)
    con = sqlite3.connect(tmp)
    con.row_factory = sqlite3.Row

    print("== ① ≥5 种对象类型：属性 + 关联对象非空 ==")
    samples = {
        "Shipment": SHIP, "Customer": CUST, "Sku": SKU, "SalesOrderLine": SOLINE,
        "Container": CONTAINER, "CostScenario": COSTSCEN, "Supplier": SUPPLIER,
        "ShipmentAllocation": ALLOC, "LogisticsPlan": PLAN, "ComplianceFinding": FINDING,
        "InvoiceLine": INVLINE,
    }
    for otype, oid in samples.items():
        v = sov.build_standard_view(con, otype, oid, "manager")
        ok_props = ("error" not in v) and bool(v.get("properties"))
        ok_links = bool(v.get("linked_objects"))
        check(f"① {otype} 属性非空", ok_props, str(v.get("error")))
        check(f"① {otype} 关联对象非空", ok_links,
              f"linked={len(v.get('linked_objects', []))}")
    check("① 覆盖对象类型数 ≥ 5（含 Shipment/Customer/Sku/SalesOrderLine/Container）",
          len(samples) >= 5 and {"Shipment", "Customer", "Sku", "SalesOrderLine",
                                 "Container"} <= set(samples))
    # 关联对象可导航：每个关联组带 object_type + object_id + route
    v_ship = sov.build_standard_view(con, "Shipment", SHIP, "manager")
    grp0 = v_ship["linked_objects"][0]
    check("① 关联对象带 object_type/route/items（可导航）",
          {"object_type", "route", "items"} <= set(grp0)
          and all({"object_id", "title"} <= set(it) for it in grp0["items"]))
    # Container 关联回其 Shipment（对象图可回溯）
    v_ctn = sov.build_standard_view(con, "Container", CONTAINER, "manager")
    check("① Container 关联回 Shipment（对象图可回溯）",
          any(g["object_type"] == "Shipment" for g in v_ctn["linked_objects"]))
    # Sku 经 FK 补丁关联到 Supplier（registry 未覆盖 supplier_provides）
    v_sku = sov.build_standard_view(con, "Sku", SKU, "manager")
    check("① Sku 关联到 Supplier（FK 补丁补齐 registry 未覆盖边）",
          any(g["object_type"] == "Supplier" for g in v_sku["linked_objects"]))

    print("== ② role 脱敏（tier / 成本字段）==")
    cust_ops = sov.build_standard_view(con, "Customer", CUST, "ops")
    cust_cs = sov.build_standard_view(con, "Customer", CUST, "cs")
    cust_mgr = sov.build_standard_view(con, "Customer", CUST, "manager")
    check("② ops 看 Customer.tier 掩码", cust_ops["properties"]["tier"] == sov.MASK,
          str(cust_ops["properties"]["tier"]))
    check("② cs 看 Customer.tier 可见（=A）", cust_cs["properties"]["tier"] == "A",
          str(cust_cs["properties"]["tier"]))
    check("② manager 看 Customer.tier 可见", cust_mgr["properties"]["tier"] != sov.MASK)
    check("② ops 看 Customer.credit_terms/risk_tier 掩码（finance/manager 专属）",
          cust_ops["properties"]["credit_terms"] == sov.MASK
          and cust_ops["properties"]["risk_tier"] == sov.MASK)
    check("② tier 记入 masked_fields", "tier" in cust_ops["masked_fields"])
    # 成本对象：CostScenario 报价/毛利对 ops/cs 掩码，finance/manager 可见
    cs_ops = sov.build_standard_view(con, "CostScenario", COSTSCEN, "ops")
    cs_cs = sov.build_standard_view(con, "CostScenario", COSTSCEN, "cs")
    cs_fin = sov.build_standard_view(con, "CostScenario", COSTSCEN, "finance")
    cs_mgr = sov.build_standard_view(con, "CostScenario", COSTSCEN, "manager")
    check("② ops 看 CostScenario.quote_price_usd 掩码",
          cs_ops["properties"]["quote_price_usd"] == sov.MASK)
    check("② cs 看 CostScenario 成本掩码", cs_cs["properties"]["gross_margin_usd"] == sov.MASK)
    check("② finance 看 CostScenario 成本可见（非掩码）",
          cs_fin["properties"]["quote_price_usd"] != sov.MASK
          and cs_fin["properties"]["gross_margin_usd"] != sov.MASK)
    check("② manager 看 CostScenario 成本可见（非掩码）",
          cs_mgr["properties"]["quote_price_usd"] != sov.MASK)
    # Supplier.uflpa_risk_flag：compliance/manager 可见，ops 掩码
    sup_ops = sov.build_standard_view(con, "Supplier", SUPPLIER, "ops")
    sup_comp = sov.build_standard_view(con, "Supplier", SUPPLIER, "compliance")
    check("② ops 看 Supplier.uflpa_risk_flag 掩码",
          sup_ops["properties"]["uflpa_risk_flag"] == sov.MASK)
    check("② compliance 看 Supplier.uflpa_risk_flag 可见",
          sup_comp["properties"]["uflpa_risk_flag"] != sov.MASK)

    print("== ③ route_object：四核心 → rich、其余 → standard ==")
    for core in ("RiskEvent", "AdmissionCase", "Task", "Invoice"):
        check(f"③ {core} → rich", sov.route_object(core) == "rich")
    for std in ("Shipment", "Customer", "Sku", "SalesOrderLine", "Container", "CostScenario",
                "Supplier", "ShipmentMilestone", "InvoiceLine", "ExpectedCost", "LogisticsPlan"):
        check(f"③ {std} → standard", sov.route_object(std) == "standard")
    check("③ 未知类型 → unknown", sov.route_object("Nonexistent") == "unknown")
    check("③ OBJECT_REGISTRY 不含四核心（只覆盖非核心）",
          not (sov.RICH_OBJECT_TYPES & set(sov.OBJECT_REGISTRY)))
    check("③ OBJECT_REGISTRY 覆盖 15 个非核心类型",
          len(sov.OBJECT_REGISTRY) == 15, str(len(sov.OBJECT_REGISTRY)))
    # OBJECT_REGISTRY 值形如 (表名, 主键列, 关键展示字段)
    tbl, pkc, kf = sov.OBJECT_REGISTRY["Shipment"]
    check("③ OBJECT_REGISTRY[Shipment] = (shipments, shipment_id, 非空关键字段)",
          tbl == "shipments" and pkc == "shipment_id" and bool(kf), f"{tbl}/{pkc}/{kf}")

    print("== ④ 标准视图只读：不含任何 action ==")
    for otype, oid in list(samples.items()):
        v = sov.build_standard_view(con, otype, oid, "ops")
        no_action = ("available_actions" not in v) and ("actions" not in v) \
            and (v.get("read_only") is True)
        check(f"④ {otype} 视图无 action 且 read_only", no_action,
              f"keys={sorted(v.keys())}")
    # 视图任何嵌套结构里都不出现 action 字样键
    v_probe = sov.build_standard_view(con, "Shipment", SHIP, "ops")
    check("④ 关联对象组不携带 action（纯只读导航）",
          all("action" not in " ".join(g.keys()).lower() for g in v_probe["linked_objects"]))

    print("== ⑤ 未知类型 / 不存在 id 优雅处理 ==")
    v_unknown = sov.build_standard_view(con, "Nonexistent", "X-1", "ops")
    check("⑤ 未知对象类型返回 error（不抛异常）", "error" in v_unknown, str(v_unknown))
    v_missing = sov.build_standard_view(con, "Shipment", "SHP-DOES-NOT-EXIST", "ops")
    check("⑤ 不存在的 id 返回 error（不抛异常）", "error" in v_missing, str(v_missing))
    check("⑤ error 结果仍标注 read_only（只读兜底不破格）",
          v_unknown.get("read_only") is True and v_missing.get("read_only") is True)

    con.close()
    print(f"\n{'=' * 40}\n结果: {'全部通过 ✔' if not FAILS else f'{len(FAILS)} 项失败: {FAILS}'}")
    print("（临时副本测试，data/ontology.sqlite 未被污染）")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
