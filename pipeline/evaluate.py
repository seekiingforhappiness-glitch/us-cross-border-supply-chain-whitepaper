"""W3 评估：python3 -m pipeline.evaluate

对照 ground truth 快照测"从噪声重建真相"的精度（本脚本属评估，允许读 data/truth/；
pipeline 本体禁读）。W3 验收（plan §11）：影响传播链一条 SQL 走通；重建精度 100%。
"""
import json
import sqlite3
import sys

FAILS = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def main():
    con = sqlite3.connect("data/ontology.sqlite")
    con.row_factory = sqlite3.Row
    truth = json.loads(open("data/truth/world_snapshot.json", encoding="utf-8").read())

    print("== 1. 重建精度（对照干净世界快照）==")
    ships = {r["shipment_id"]: r for r in con.execute("SELECT * FROM shipments")}
    n = len(truth["shipments"])
    ok_status = sum(1 for sid, tv in truth["shipments"].items()
                    if ships[sid]["status"] == tv["status"])
    ok_eta = sum(1 for sid, tv in truth["shipments"].items()
                 if ships[sid]["eta_current"] == tv["eta_current"])
    ok_customs = sum(1 for sid, tv in truth["shipments"].items()
                     if ships[sid]["customs_status"] == tv["customs_status"])
    check(f"shipment.status 重建 {ok_status}/{n}", ok_status == n,
          str([s for s, v in truth['shipments'].items() if ships[s]['status'] != v['status']][:5]))
    check(f"shipment.eta_current 重建 {ok_eta}/{n}", ok_eta == n,
          str([s for s, v in truth['shipments'].items() if ships[s]['eta_current'] != v['eta_current']][:5]))
    check(f"shipment.customs_status 重建 {ok_customs}/{n}", ok_customs == n)
    lines = {r["so_line_id"]: r["line_status"] for r in con.execute("SELECT * FROM sales_order_lines")}
    nl = len(truth["lines"])
    # at_risk 是引擎/动作层的叠加状态（A2 副作用），等价于重建层的 allocated——
    # 允许在 detect 之后运行本评估而不误报（消除执行顺序依赖）
    ok_line = sum(1 for lid, tv in truth["lines"].items()
                  if lines[lid] == tv or (lines[lid] == "at_risk" and tv == "allocated"))
    check(f"line_status 重建 {ok_line}/{nl}", ok_line == nl,
          str([(l, lines[l], v) for l, v in truth['lines'].items() if lines[l] != v][:5]))

    print("== 2. 影响传播链（一条 SQL 走通，DEMO-01）==")
    chain = list(con.execute("""
        SELECT m.event_time, s.shipment_id, s.eta_current, l.so_line_id,
               l.promised_delivery_date, so.so_id, c.customer_id, c.customer_name, c.tier
        FROM shipment_milestones m
        JOIN shipments s            ON s.shipment_id = m.shipment_id
        JOIN shipment_allocations a ON a.shipment_id = s.shipment_id
        JOIN sales_order_lines l    ON l.so_line_id = a.so_line_id
        JOIN sales_orders so        ON so.so_id = l.so_id
        JOIN customers c            ON c.customer_id = so.customer_id
        WHERE m.event_type = 'eta_change' AND m.is_duplicate = 0
          AND s.shipment_id = 'SHP-2026-0099'"""))
    check("链路可达且含 CUS-0007/tier A",
          any(r["customer_id"] == "CUS-0007" and r["tier"] == "A" for r in chain))
    check("DEMO-01 eta_current 已重建为 2026-08-21",
          ships["SHP-2026-0099"]["eta_current"] == "2026-08-21")

    print("== 3. 噪声处理抽检 ==")
    check("DEMO-11 状态冲突已纠正（tms=in_transit → 真值 arrived）",
          ships["SHP-2026-0100"]["status"] == "arrived"
          and ships["SHP-2026-0100"]["status_source"] == "in_transit")
    dup = list(con.execute("""SELECT is_duplicate, count(*) c FROM shipment_milestones
        WHERE shipment_id='SHP-2026-0090' AND event_type='eta_change' GROUP BY is_duplicate"""))
    d = {r["is_duplicate"]: r["c"] for r in dup}
    check("DEMO-09 重复事件：1 条有效 + 1 条标记重复", d.get(0) == 1 and d.get(1) == 1)
    check("DEMO-10 乱序：eta_current 取 event_time 最新（08-15 非 08-23）",
          ships["SHP-2026-0095"]["eta_current"] == "2026-08-15")
    er_unmapped = [r for r in con.execute(
        "SELECT * FROM supplier_name_map WHERE supplier_id=''")]
    check("供应商 ER 全部命中", not er_unmapped, str([r["raw_name"] for r in er_unmapped]))

    print(f"\n{'=' * 40}\n结果: {'全部通过 ✔' if not FAILS else f'{len(FAILS)} 项失败: {FAILS}'}")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
