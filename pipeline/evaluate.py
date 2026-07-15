"""W3 评估：python3 -m pipeline.evaluate

对照 ground truth 快照测"从噪声重建真相"的精度（本脚本属评估，允许读 data/truth/；
pipeline 本体禁读）。W3 验收（plan §11）：影响传播链一条 SQL 走通；重建精度 100%。
"""
import csv
import json
import sqlite3
import sys
from collections import Counter

from engine.graph import explain_path

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


def _payload_has_fields(raw_json, required):
    try:
        payload = json.loads(raw_json)
    except json.JSONDecodeError:
        return False
    return all(k in payload for k in required) and all(
        payload.get(k) for k in ("event_type", "event_time"))


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

    print("== 5. 源事件 envelope 与 lineage（M3）==")
    source_count = con.execute("SELECT count(*) FROM source_events").fetchone()[0]
    check("source_events 行数等于 raw tms_milestones 行数",
          source_count == mr["total"], f"source_events={source_count} raw_milestones={mr['total']}")
    lineage_counts = con.execute("""
        SELECT count(*) resolved_rows,
               count(e.idempotency_key) lineage_rows
        FROM shipment_milestones m
        LEFT JOIN source_events e ON e.source_record_id = m.milestone_id
    """).fetchone()
    missing = list(con.execute("""
        SELECT m.milestone_id
        FROM shipment_milestones m
        LEFT JOIN source_events e ON e.source_record_id = m.milestone_id
        WHERE e.idempotency_key IS NULL
        ORDER BY m.milestone_id
        LIMIT 5"""))
    check("所有 resolved milestone 可追溯到 source_events",
          lineage_counts["resolved_rows"] == lineage_counts["lineage_rows"],
          f"missing={[r['milestone_id'] for r in missing]}")
    payloads = list(con.execute("""
        SELECT payload_json FROM source_events ORDER BY idempotency_key"""))
    check("source payload 保留 booking/container/event 原始字段",
          bool(payloads) and all(_payload_has_fields(
                                    r["payload_json"],
                                    ("booking_no", "container_no", "event_type", "event_time"))
                                for r in payloads))
    idem = con.execute("""
        SELECT count(*) total, count(distinct idempotency_key) distinct_keys
        FROM source_events""").fetchone()
    check("source_events idempotency_key 唯一", idem["total"] == idem["distinct_keys"])

    print("== 6. MDM crosswalk（M4）==")
    has_mdm = con.execute("""
        SELECT count(*) FROM sqlite_master
        WHERE type='table' AND name='mdm_crosswalk'""").fetchone()[0] == 1
    check("mdm_crosswalk 表存在", has_mdm)
    mdm_dq = dq.get("mdm_crosswalk", {})
    mdm_rows = list(con.execute("SELECT * FROM mdm_crosswalk")) if has_mdm else []
    allowed_statuses = {"resolved", "ambiguous", "unresolved"}
    statuses = {r["status"] for r in mdm_rows}
    check("mdm_crosswalk status 仅含 resolved/ambiguous/unresolved",
          bool(mdm_rows) and statuses <= allowed_statuses, str(sorted(statuses)))

    mdm_keys = list(con.execute("""
        SELECT source_system, object_type, external_id, status,
               count(*) candidate_rows,
               count(distinct internal_id) distinct_internal_ids
        FROM mdm_crosswalk
        GROUP BY source_system, object_type, external_id, status
    """)) if has_mdm else []
    key_counts = Counter(r["status"] for r in mdm_keys)
    check("MDM DQ 以 external_id key 计数且与 SQL 一致",
          mdm_dq.get("total") == len(mdm_keys)
          and mdm_dq.get("resolved") == key_counts.get("resolved", 0)
          and mdm_dq.get("ambiguous") == key_counts.get("ambiguous", 0)
          and mdm_dq.get("unresolved") == key_counts.get("unresolved", 0)
          and mdm_dq.get("candidate_rows") == len(mdm_rows),
          f"dq={mdm_dq} sql_keys={dict(key_counts)} rows={len(mdm_rows)}")

    ambiguous_keys = [r for r in mdm_keys if r["status"] == "ambiguous"]
    check("ambiguous external_id 保留多候选且不折成单一 resolved",
          len(ambiguous_keys) == 1
          and ambiguous_keys[0]["candidate_rows"] >= 2
          and ambiguous_keys[0]["distinct_internal_ids"] >= 2,
          f"ambiguous={[(r['external_id'], r['candidate_rows']) for r in ambiguous_keys]}")
    unresolved_keys = [r for r in mdm_keys if r["status"] == "unresolved"]
    unresolved_rows = [r for r in mdm_rows if r["status"] == "unresolved"]
    check("unresolved external_id 可见且不猜 internal_id",
          len(unresolved_keys) == 1
          and len(unresolved_rows) == 1
          and unresolved_rows[0]["internal_id"] == "",
          f"unresolved_keys={len(unresolved_keys)} unresolved_rows={len(unresolved_rows)}")

    print("== 7. object_relationships graph registry（M5）==")
    has_relationships = con.execute("""
        SELECT count(*) FROM sqlite_master
        WHERE type='table' AND name='object_relationships'""").fetchone()[0] == 1
    check("object_relationships 表存在", has_relationships)
    rel_rows = list(con.execute("""
        SELECT * FROM object_relationships
        WHERE source LIKE 'pipeline.build_ontology:%'
    """)) if has_relationships else []
    rel_dq = dq.get("object_relationships", {})
    rel_counts = Counter(r["relationship_type"] for r in rel_rows)
    check("object_relationships DQ total 与 pipeline relationship SQL count 一致",
          rel_dq.get("total") == len(rel_rows),
          f"dq={rel_dq.get('total')} sql={len(rel_rows)}")
    main_types = [
        "derived_shipment_allocates_line",
        "derived_line_belongs_to_customer",
        "derived_shipment_has_invoice",
        "derived_shipment_has_expected_cost",
    ]
    check("主要 relationship_type 计数与 DQ 一致",
          all(rel_dq.get("by_type", {}).get(t) == rel_counts.get(t, 0) for t in main_types),
          f"dq={rel_dq.get('by_type', {})} sql={dict(rel_counts)}")
    object_tables = {
        "Customer": ("customers", "customer_id"),
        "SalesOrder": ("sales_orders", "so_id"),
        "SalesOrderLine": ("sales_order_lines", "so_line_id"),
        "Sku": ("skus", "sku_id"),
        "Supplier": ("suppliers", "supplier_id"),
        "PurchaseOrder": ("purchase_orders", "po_id"),
        "Shipment": ("shipments", "shipment_id"),
        "ShipmentMilestone": ("shipment_milestones", "milestone_id"),
        "Container": ("containers", "container_no"),
        "Invoice": ("invoices", "invoice_id"),
        "InvoiceLine": ("invoice_lines", "invoice_line_id"),
        "ExpectedCost": ("expected_costs", "expected_cost_id"),
        "RiskEvent": ("risk_events", "risk_event_id"),
    }
    dangling = []
    for rel in con.execute("""SELECT relationship_id, source_type, source_id, target_type, target_id
                              FROM object_relationships"""):
        for type_field, id_field in (("source_type", "source_id"), ("target_type", "target_id")):
            table_info = object_tables.get(rel[type_field])
            if not table_info:
                dangling.append((rel["relationship_id"], rel[type_field], rel[id_field], "unknown_type"))
                continue
            table, pk = table_info
            found = con.execute(f"SELECT count(*) FROM {table} WHERE {pk}=?",
                                (rel[id_field],)).fetchone()[0]
            if not found:
                dangling.append((rel["relationship_id"], rel[type_field], rel[id_field]))
    check("object_relationships source/target 均引用已建对象",
          not dangling, str(dangling[:10]))
    graph_path = explain_path(con, "Shipment", "SHP-2026-0099",
                              "Customer", "CUS-0007", max_depth=3)
    check("Shipment -> Customer 可由 explain_path 找到",
          [e.relationship_type for e in graph_path] == [
              "derived_shipment_allocates_line",
              "derived_line_belongs_to_customer",
          ],
          str([(e.relationship_id, e.relationship_type) for e in graph_path]))

    print("== 8. DQ issue 运营队列（M6）==")
    has_dq_issues = con.execute("""
        SELECT count(*) FROM sqlite_master
        WHERE type='table' AND name='dq_issues'""").fetchone()[0] == 1
    check("dq_issues 表存在", has_dq_issues)
    dq_issues = list(con.execute("SELECT * FROM dq_issues")) if has_dq_issues else []
    invalid_unresolved_issues = list(con.execute("""
        SELECT u.milestone_id, d.dq_issue_id, d.status, d.detail_json
        FROM unresolved_milestones u
        LEFT JOIN dq_issues d
          ON d.source_table='unresolved_milestones'
         AND d.source_record_id=u.milestone_id
         AND d.issue_type='unresolved_reference'
        WHERE d.dq_issue_id IS NULL OR d.status NOT IN ('open', 'assigned', 'closed')
    """)) if has_dq_issues else []
    check("每条 unresolved_milestones 都有一个状态合法的 unresolved_reference DQ issue",
          has_dq_issues and not invalid_unresolved_issues,
          str([dict(r) for r in invalid_unresolved_issues[:5]]))

    issue_counts = Counter(r["issue_type"] for r in dq_issues)
    source_counts = Counter(r["source_table"] for r in dq_issues)
    dq_issue_report = dq.get("dq_issues", {})
    check("dq_report dq_issues total/by_issue_type/by_source_table 与 SQL 一致",
          dq_issue_report.get("total") == len(dq_issues)
          and dq_issue_report.get("by_issue_type") == dict(sorted(issue_counts.items()))
          and dq_issue_report.get("by_source_table") == dict(sorted(source_counts.items())),
          f"dq={dq_issue_report} sql_total={len(dq_issues)}")
    check("dq_report dq_issues open 记录 build-time 初始开放数",
          0 <= int(dq_issue_report.get("open", -1)) <= len(dq_issues),
          f"dq={dq_issue_report}")

    issue_details = []
    for issue in dq_issues:
        if issue["source_table"] != "unresolved_milestones":
            continue
        try:
            detail = json.loads(issue["detail_json"] or "{}")
        except json.JSONDecodeError:
            detail = {}
        issue_details.append((issue, detail))
    check("DQ issue detail 保留 milestone/source 信息",
          bool(issue_details)
          and all(detail.get("milestone_id") == issue["source_record_id"]
                  and detail.get("source_system")
                  and detail.get("event_type")
                  and "booking_no" in detail
                  and "container_no" in detail
                  for issue, detail in issue_details),
          str([(i["dq_issue_id"], d) for i, d in issue_details[:3]]))

    print(f"\n{'=' * 40}\n结果: {'全部通过 ✔' if not FAILS else f'{len(FAILS)} 项失败: {FAILS}'}")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
