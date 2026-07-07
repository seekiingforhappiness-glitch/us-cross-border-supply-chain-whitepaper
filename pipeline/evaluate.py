"""W3 评估：python3 -m pipeline.evaluate

对照 ground truth 快照测"从噪声重建真相"的精度（本脚本属评估，允许读 data/truth/；
pipeline 本体禁读）。W3 验收（plan §11）：影响传播链一条 SQL 走通；重建精度 100%。
"""
import csv
import json
import sqlite3
import sys

FAILS = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def _load_known_loss(noise_path, ship_by_booking):
    """v0.6-H3 known-loss 白名单：从注入噪声日志取 doc_ref_typo 行，还原其影响的 shipment。

    typo 使 booking 末位篡改 + 柜号置空 → 该 milestone 必然 unresolved，其承载事件对该
    shipment 丢失。这些船的 status/eta/customs 允许与快照不一致，但必须如实计数并打印；
    其余船仍须 100%。原始（未篡改）booking 记在噪声日志 description 首个引号内。
    """
    known = {}
    with open(noise_path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["noise_type"] != "doc_ref_typo":
                continue
            orig_booking = r["description"].split("'")[1]
            sid = ship_by_booking.get(orig_booking)
            if sid:
                known.setdefault(sid, []).append(r["target_id"])
    return known


def main():
    con = sqlite3.connect("data/ontology.sqlite")
    con.row_factory = sqlite3.Row
    truth = json.loads(open("data/truth/world_snapshot.json", encoding="utf-8").read())

    print("== 1. 重建精度（对照干净世界快照）==")
    ships = {r["shipment_id"]: r for r in con.execute("SELECT * FROM shipments")}
    n = len(truth["shipments"])
    # H3 known-loss：doc_ref_typo 丢失的事件影响到的船（原 booking→shipment 还原）
    ship_by_booking = {r["booking_no"]: r["shipment_id"] for r in ships.values()}
    known_loss = _load_known_loss("data/truth/injected_noise_log.csv", ship_by_booking)
    kl = set(known_loss)

    def recon(field):
        """返回 (匹配数, 非 known-loss 的失配船, known-loss 内失配船)。"""
        miss_other, miss_kl = [], []
        for sid, tv in truth["shipments"].items():
            if ships[sid][field] != tv[field]:
                (miss_kl if sid in kl else miss_other).append(sid)
        return n - len(miss_other) - len(miss_kl), miss_other, miss_kl

    # 断言：非 known-loss 船必须 100%（known-loss 内失配如实计数，不放宽精度）
    for field in ("status", "eta_current", "customs_status"):
        ok, miss_other, miss_kl = recon(field)
        note = f"（known-loss 内失配 {len(miss_kl)} 船：{miss_kl}）" if miss_kl else ""
        check(f"shipment.{field} 重建 {ok}/{n} 非known-loss船 {note}",
              not miss_other, str(miss_other[:5]))
    print(f"  H3 known-loss 白名单：{len(kl)} 船受 doc_ref_typo 事件丢失影响 "
          f"（typo 行 {sum(len(v) for v in known_loss.values())}）；"
          f"这些船的 status/eta/customs 允许与快照不一致且已如实计数")
    lines = {r["so_line_id"]: r["line_status"] for r in con.execute("SELECT * FROM sales_order_lines")}
    nl = len(truth["lines"])
    # known-loss 波及行：分配到 known-loss 船的行（其 line_status 可能因缺 delivered 事件失配）
    kl_lines = set()
    for a in con.execute("SELECT so_line_id, shipment_id FROM shipment_allocations"):
        if a["shipment_id"] in kl:
            kl_lines.add(a["so_line_id"])
    # at_risk 是引擎/动作层的叠加状态（A2 副作用），等价于重建层的 allocated——
    # 允许在 detect 之后运行本评估而不误报（消除执行顺序依赖）
    def line_ok(lid, tv):
        return lines[lid] == tv or (lines[lid] == "at_risk" and tv == "allocated")
    miss_line = [(l, lines[l], v) for l, v in truth["lines"].items()
                 if not line_ok(l, v) and l not in kl_lines]
    miss_line_kl = [l for l, v in truth["lines"].items() if not line_ok(l, v) and l in kl_lines]
    ok_line = sum(1 for lid, tv in truth["lines"].items() if line_ok(lid, tv))
    note = f"（known-loss 波及行失配 {len(miss_line_kl)}）" if miss_line_kl else ""
    check(f"line_status 重建 {ok_line}/{nl} 非known-loss行 {note}", not miss_line, str(miss_line[:5]))

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

    print("== 4. 单证号级 ER（v0.6-H3）==")
    dq = json.loads(open("data/dq_report.json", encoding="utf-8").read())
    mr = dq["milestone_resolution"]
    check(f"解析率 ≥97%（实测 {mr['resolution_rate']:.2%}）", mr["resolution_rate"] >= 0.97,
          f"got {mr['resolution_rate']}")
    unresolved = list(con.execute("SELECT * FROM unresolved_milestones"))
    # unresolved 全部是 doc_ref_typo 注入行（对照 truth 噪声日志）
    typo_mids = {r["target_id"] for r in csv.DictReader(
        open("data/truth/injected_noise_log.csv", encoding="utf-8"))
        if r["noise_type"] == "doc_ref_typo"}
    unresolved_mids = {r["milestone_id"] for r in unresolved}
    check(f"unresolved({len(unresolved_mids)}) 全部是 doc_ref_typo 注入行",
          unresolved_mids == typo_mids and len(typo_mids) > 0,
          f"unresolved∖typo={sorted(unresolved_mids - typo_mids)[:5]} "
          f"typo∖unresolved={sorted(typo_mids - unresolved_mids)[:5]}")
    check("unresolved 停车表原行全列保留（含 booking_no/event_time/reason）",
          all(set(r.keys()) >= {"milestone_id", "booking_no", "container_no", "event_type",
                                "event_time", "ingested_at", "reason"} for r in unresolved))
    print(f"  解析统计：总 {mr['total']} 解析 {mr['resolved']}"
          f"（booking {mr['resolved_by_booking']} / container {mr['resolved_by_container']}）"
          f" unresolved {mr['unresolved']} 按因 {mr['unresolved_by_reason']}")

    print(f"\n{'=' * 40}\n结果: {'全部通过 ✔' if not FAILS else f'{len(FAILS)} 项失败: {FAILS}'}")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
