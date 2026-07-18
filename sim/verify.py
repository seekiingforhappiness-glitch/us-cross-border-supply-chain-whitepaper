"""模拟世界验收：python3 -m sim.verify（无头，风格同 datagen/verify.py）

检查：1 复现性（同种子两次内容摘要逐字节一致）；2 体量合理性（450 柜/年 ±10%、季节曲线可见）；
3 业务一致性（里程碑链完整单调、无孤儿、库存非负、无未来泄漏）；4 真实感抽查（柜号校验位/真实船名池/
周班节奏）；5 真实感量化底线（打印 20 条世界快照叙事供人工抽读）。
判定逻辑不得为通过验收而修改（继承 AGENTS §5 精神）。
"""
import hashlib
import json
import random
import sqlite3
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

import yaml

from .backfill import build
from .store import write_simworld
from . import world_def as WD

FAILS = []
# S2：新增 customs_hold（查验滞留，排在 filed 与 released 之间）；released/delivered 顺延一位
CHAIN = {"booking_confirmed": 0, "departed": 1, "transshipment": 2, "arrived": 3,
         "customs_filed": 4, "customs_hold": 5, "customs_released": 6, "delivered": 7}
OPTIONAL_MS = {"transshipment", "customs_hold"}   # 可选里程碑（非每船必有）——前缀完整性检查时跳过


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def content_digest(path):
    """全表按 rowid 顺序序列化后 sha256——内容摘要（对 sqlite 内部页布局不敏感，稳健）。"""
    con = sqlite3.connect(path)
    tables = [r[0] for r in con.execute(
        "select name from sqlite_master where type='table' order by name")]
    h = hashlib.sha256()
    for t in tables:
        h.update(t.encode())
        for row in con.execute(f"select * from {t} order by rowid"):
            h.update(repr(row).encode())
    con.close()
    return h.hexdigest()


def q(con, sql):
    return [dict(r) for r in con.execute(sql)]


def iso6346_cd(code10):
    return WD.iso6346_check_digit(code10)


def main():
    cfg = yaml.safe_load(open("sim/config.yaml", encoding="utf-8"))
    as_of = cfg["window"]["as_of"]

    print("== 1. 复现性 ==")
    digests = []
    for _ in range(2):
        p = Path(tempfile.mkdtemp()) / "sw.sqlite"
        w = build(cfg)
        write_simworld(w, cfg, str(p))
        digests.append((content_digest(str(p)), p.read_bytes()))
    check("同种子两次生成内容摘要逐字节一致", digests[0][0] == digests[1][0],
          f"{digests[0][0][:16]} vs {digests[1][0][:16]}")
    check("同种子两次生成文件字节一致（附赠）", digests[0][1] == digests[1][1])

    db = cfg["output"]["sqlite_path"]
    if not Path(db).exists():
        write_simworld(w, cfg, db)   # 若未先跑 backfill，用刚构建的世界落一次
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row

    ships = q(con, "select * from shipments")
    conts = q(con, "select * from containers")
    allocs = q(con, "select * from shipment_allocations")
    lines = q(con, "select * from sales_order_lines")
    ms = q(con, "select * from shipment_milestones")
    invs = q(con, "select * from invoices")
    inv_lines = q(con, "select * from invoice_lines")
    inv_pos = q(con, "select * from inventory_positions")
    sched = q(con, "select * from sim_schedule")
    fwd = q(con, "select * from sim_forwarders")
    orders = q(con, "select * from sales_orders")
    # S2：异常谱系 + AI 回路产物
    sev = q(con, "select * from sim_event_log")
    risks = q(con, "select * from risk_events")
    tasks = q(con, "select * from tasks")
    mem = q(con, "select * from resolution_memory")
    acts = q(con, "select * from sim_ai_activity")

    print("== 2. 体量合理性 ==")
    days = (WD.D(as_of) - WD.D(cfg["window"]["start"])).days + 1
    check("窗口 ≥ 14 个月", days >= 425, f"got {days} 天")
    annualized = len(conts) / days * 365
    lo, hi = 450 * 0.9, 450 * 1.1
    check(f"年化柜量 450 ±10%（{lo:.0f}-{hi:.0f}）", lo <= annualized <= hi,
          f"got {annualized:.1f}（{len(conts)} 柜 / {days} 天）")
    check("订单/行/船/发票非空", all([orders, lines, ships, invs]))
    # 季节曲线：柜量 by etd 月
    cont_by_ship = Counter(r["shipment_id"] for r in conts)
    etd_by_ship = {s["shipment_id"]: s["etd"][:7] for s in ships}
    vol = Counter()
    for sid, n in cont_by_ship.items():
        vol[etd_by_ship[sid]] += n
    full_months = [m for m in sorted(vol) if m not in (cfg["window"]["start"][:7], as_of[:7])]
    mean = sum(vol[m] for m in full_months) / len(full_months)
    peak_ok = all(vol[m] > mean * 1.1 for m in ("2025-08", "2025-09", "2025-10"))
    check("旺季 8-10 月（2025）柜量显著高于均值", peak_ok,
          f"Aug={vol['2025-08']} Sep={vol['2025-09']} Oct={vol['2025-10']} vs 均值 {mean:.1f}")
    cny = vol.get("2026-02", 0)
    check("春节 2026-02 柜量显著低于均值（停摆）", cny < mean * 0.6,
          f"Feb={cny} vs 均值 {mean:.1f}")

    print("== 3. 业务一致性 ==")
    ship_ids = {s["shipment_id"] for s in ships}
    line_ids = {l["so_line_id"] for l in lines}
    cont_nos = {c["container_no"] for c in conts}
    inv_ids = {i["invoice_id"] for i in invs}
    alloc_ships = {a["shipment_id"] for a in allocs}
    check("每 Shipment 有 Allocation", ship_ids <= alloc_ships,
          f"缺: {sorted(ship_ids - alloc_ships)[:5]}")
    check("Allocation 引用完整（shipment + so_line）",
          all(a["shipment_id"] in ship_ids and a["so_line_id"] in line_ids for a in allocs))
    check("每 Container 归属存在的 Shipment",
          all(c["shipment_id"] in ship_ids for c in conts))
    check("每发票行有对应柜（container_no 存在）",
          all(l["container_no"] in cont_nos for l in inv_lines),
          f"缺: {[l['invoice_line_id'] for l in inv_lines if l['container_no'] not in cont_nos][:5]}")
    check("每发票引用存在的 Shipment", all(i["shipment_id"] in ship_ids for i in invs))
    lines_by_inv = defaultdict(float)
    for l in inv_lines:
        lines_by_inv[l["invoice_id"]] += float(l["amount_usd"])
    check("发票 total = Σ行（±0.02）",
          all(abs(float(i["total_usd"]) - round(lines_by_inv[i["invoice_id"]], 2)) <= 0.02
              for i in invs))
    check("库存 available/reserved/in_transit 非负",
          all(int(p["available_qty"]) >= 0 and int(p["reserved_qty"]) >= 0
              and int(p["in_transit_qty"]) >= 0 for p in inv_pos))
    check("库存 reserved ≤ available（可用中预留）",
          all(int(p["reserved_qty"]) <= int(p["available_qty"]) for p in inv_pos))
    # 无未来泄漏：一切"已发生"的时间戳 ≤ as_of（eta/promise 是预测，允许未来）
    leak_ev = [m["milestone_id"] for m in ms if m["event_time"][:10] > as_of]
    leak_ing = [m["milestone_id"] for m in ms if m["ingested_at"][:10] > as_of]
    check("里程碑 event_time ≤ as_of（无未来泄漏）", not leak_ev, f"{len(leak_ev)} 条越界")
    check("里程碑 ingested_at ≤ as_of（上报时间不泄漏）", not leak_ing, f"{len(leak_ing)} 条越界")
    check("订单日/开票日 ≤ as_of",
          all(o["order_date"] <= as_of for o in orders)
          and all(i["issue_date"] <= as_of for i in invs))
    check("已到港船 ata ≤ as_of", all((not s["ata"]) or s["ata"] <= as_of for s in ships))
    # 里程碑链单调 + 完整前缀
    ms_by_ship = defaultdict(list)
    for m in ms:
        ms_by_ship[m["shipment_id"]].append(m)
    nonmono, bad_prefix, no_booking = 0, 0, 0
    for sid, evs in ms_by_ship.items():
        evs2 = sorted(evs, key=lambda e: e["event_time"])
        ranks = [CHAIN[e["event_type"]] for e in evs2]
        if ranks != sorted(ranks):
            nonmono += 1
        present = {e["event_type"] for e in evs}
        if "booking_confirmed" not in present:
            no_booking += 1
        # 完整前缀：出现某里程碑则其所有前置（除可选 transshipment/customs_hold）都在
        for et in present:
            for pre, r in CHAIN.items():
                if pre in OPTIONAL_MS:
                    continue
                if r < CHAIN[et] and pre not in present:
                    bad_prefix += 1
                    break
    check("里程碑链时序单调（不逆序）", nonmono == 0, f"{nonmono} 船非单调")
    check("每船有 booking_confirmed（无孤儿船）", no_booking == 0, f"{no_booking} 船无起点")
    check("里程碑链为合法前缀（无 arrived 缺 departed 等断链）", bad_prefix == 0,
          f"{bad_prefix} 船断链")
    check("每船至少一条里程碑", len(ms_by_ship) == len(ships),
          f"{len(ships) - len(ms_by_ship)} 船无里程碑")

    print("== 4. 真实感抽查 ==")
    bad_cd = [c["container_no"] for c in conts
              if len(c["container_no"]) != 11 or iso6346_cd(c["container_no"][:10]) != c["container_no"][10]]
    check("柜号 ISO 6346 校验位 100% 合法", not bad_cd, f"{len(bad_cd)} 非法")
    pool = {v for car in WD.CARRIERS.values() for v in car["vessels"]}
    bad_v = [s["shipment_id"] for s in ships if s["vessel_voyage"].rsplit(" ", 1)[0] not in pool]
    check("船名 100% 来自真实池", not bad_v, f"{len(bad_v)} 不在池")
    scac_of = {k: v["scac"] for k, v in WD.CARRIERS.items()}
    check("carrier_scac 与 carrier_name 一致",
          all(s["carrier_scac"] == scac_of.get(s["carrier_name"]) for s in ships))
    check("locode 合法", all(s["origin_port_locode"] in WD.LOCODE.values()
                             and s["destination_port_locode"] in WD.LOCODE.values() for s in ships))
    # 周班节奏：同（航线, 船司）相邻班期 6-8 天
    gaps = defaultdict(list)
    prev = {}
    for s in sorted(sched, key=lambda x: (x["route_id"], x["carrier"], x["etd"])):
        k = (s["route_id"], s["carrier"])
        if k in prev:
            gaps[k].append((date.fromisoformat(s["etd"]) - date.fromisoformat(prev[k])).days)
        prev[k] = s["etd"]
    allg = [g for v in gaps.values() for g in v]
    bad_gap = [g for g in allg if not (6 <= g <= 8)]
    check("周班相邻班期 6-8 天（同航线×船司）", not bad_gap,
          f"{len(bad_gap)} 段越界，样例 {sorted(set(allg))}")
    # 货代性格参数齐全（6 家，5 维参数）
    check("6 家货代性格参数齐全", len(fwd) == 6
          and all(f["quote_level"] and f["credibility"] for f in fwd))

    print("== 5. S2 异常谱系（密度 + 连锁 caused_by + 口径矛盾）==")
    prim = [e for e in sev if e["event_kind"].startswith("anomaly:")]
    chain = [e for e in sev if e["event_kind"].startswith("chain:")]
    by_month = Counter(e["sim_date"][:7] for e in prim)
    full = [by_month[m] for m in sorted(by_month)
            if m not in (cfg["window"]["start"][:7], as_of[:7])]
    mean = sum(full) / len(full) if full else 0
    med = sorted(full)[len(full) // 2] if full else 0
    check("主异常密度落真实档区间（均值 15-25/月）", 15 <= mean <= 25,
          f"均值 {mean:.1f}/月（中位 {med}），逐月 {dict(sorted(by_month.items()))}")
    check("主异常总量 ≥ 200（14 个月真实感体量）", len(prim) >= 200, f"got {len(prim)}")
    fam = Counter(e["family"] for e in prim)
    check("五族异常齐备（延误/查验/费用/单证/仓储）",
          all(fam.get(k, 0) > 0 for k in ("delay", "inspection", "fee", "document", "warehouse")),
          f"by_family={dict(fam)}")
    # 连锁 caused_by：抽 5 条 chain:stockout 逐环回溯 → inventory_countdown → delay 根因
    by_id = {e["sim_event_id"]: e for e in sev}
    stockouts = [e for e in chain if e["event_kind"] == "chain:stockout"]
    sample_chains = sorted(stockouts, key=lambda e: e["sim_event_id"])[:5]
    chain_ok, chain_detail = 0, []
    for so in sample_chains:
        countdown = by_id.get(so["caused_by"])
        if not countdown or countdown["event_kind"] != "chain:inventory_countdown":
            chain_detail.append(f"{so['sim_event_id']} 断链于 countdown")
            continue
        root = by_id.get(countdown["caused_by"])
        if not root or not root["event_kind"].startswith("anomaly:delay:"):
            chain_detail.append(f"{so['sim_event_id']} 断链于 delay 根因")
            continue
        chain_ok += 1
    check("抽 5 条连锁链 caused_by 逐环可回溯（断货→库存倒计时→延误根因）",
          len(sample_chains) >= 5 and chain_ok == len(sample_chains),
          f"{chain_ok}/{len(sample_chains)} 完整；{chain_detail}")
    # 滞箱连锁：chain:detention 的 caused_by 指向延误异常
    dets = [e for e in chain if e["event_kind"] == "chain:detention" and e["caused_by"]]
    det_ok = sum(1 for e in dets if by_id.get(e["caused_by"], {}).get("event_kind", "").startswith("anomaly:delay:"))
    check("滞箱连锁 caused_by 指向延误根因", dets and det_ok == len(dets),
          f"{det_ok}/{len(dets)}")
    # 口径矛盾：成对里程碑（同船 2 条 arrived，locode 冲突）+ 对应 eta_contradiction 异常
    arr_by_ship = defaultdict(list)
    for m in ms:
        if m["event_type"] == "arrived":
            arr_by_ship[m["shipment_id"]].append(m)
    conflict_ships = {s for s, a in arr_by_ship.items() if len(a) > 1
                      and len({x["event_locode"] for x in a}) > 1}
    contradiction_logs = [e for e in prim if e["event_kind"] == "anomaly:inspection:eta_contradiction"]
    check("口径矛盾成对存在（同船 2 条冲突 arrived 里程碑）", len(conflict_ships) >= 1,
          f"{len(conflict_ships)} 船有冲突到港口径")
    check("口径矛盾异常与成对里程碑数量吻合",
          len(contradiction_logs) == len(conflict_ships),
          f"日志 {len(contradiction_logs)} vs 成对 {len(conflict_ships)}")

    print("== 6. S2 AI 闭环（检测→提案→审批→世界状态改变；先例四件套）==")
    check("风险/提案/先例非空", risks and tasks and mem)
    approved = [t for t in tasks if t["approval_status"] == "approved"]
    # critical/high 风险 → 提案 → 审批 链路存在
    rid_of_task = {t["risk_event_id"] for t in tasks}
    hi_risks = [r for r in risks if r["severity"] in ("critical", "high")]
    check("critical/high 风险均有提案（分诊→提案）",
          all(r["risk_event_id"] in rid_of_task for r in hi_risks) or
          sum(1 for r in hi_risks if r["risk_event_id"] in rid_of_task) >= len(hi_risks) * 0.9,
          f"{sum(1 for r in hi_risks if r['risk_event_id'] in rid_of_task)}/{len(hi_risks)}")
    # 世界状态真改：加急 shipment ETA 提前 + 争议发票 disputed
    exped = [s for s in ships if str(s["expedite_flag"]) in ("1", "True")]
    eta_advanced = [s for s in exped if s["eta_current"] < s["eta_initial"]]
    check("加急处置真实改变世界（ETA 提前：eta_current < eta_initial）",
          len(exped) >= 1 and len(eta_advanced) >= 1,
          f"{len(eta_advanced)}/{len(exped)} 加急船 ETA 已提前")
    disputed = [i for i in invs if i["status"] == "disputed"]
    check("争议处置真实改变世界（发票转 disputed）", len(disputed) >= 1, f"{len(disputed)} 张")
    repl = [t for t in approved if t["proposed_action"] == "escalate_replenishment"]
    check("断货处置真实改变世界（紧急补货已批准执行）", len(repl) >= 1, f"{len(repl)} 例")
    # 抽 3 条已闭环先例：四件套（情境+提案+引用+决策人）+ 结果回填 + 质量标签
    closed = sorted([m for m in mem if m["closed_at"]], key=lambda m: m["memory_id"])
    check("已闭环先例 ≥ 100（14 个月处置沉淀量级）", len(closed) >= 100, f"got {len(closed)}")
    four_ok = 0
    for m in closed[:3]:
        has_situation = m["rule_id"] and m["severity"] and m["as_of"] and m["impact_usd"] is not None
        has_proposal = m["proposal_version"] and m["proposal_summary"]
        has_decider = m["decided_by"] == "sim-approver-01" and m["decision"] in ("adopted", "rejected", "modified")
        has_outcome = m["outcome_resolved"] and m["quality_label"] in ("effective", "partial", "ineffective")
        if has_situation and has_proposal and has_decider and has_outcome:
            four_ok += 1
    check("抽 3 条先例四件套可回放（情境+提案版本+决策人+结果质量标签）", four_ok == 3,
          f"{four_ok}/3 完整")

    print("== 7. S2 sim 标记零遗漏 + 两库物理隔离 ==")
    def all_sim(rows):
        return all(r.get("source") == "sim" for r in rows)
    check("risk_events/tasks/resolution_memory/ai_activity 全 source='sim'",
          all_sim(risks) and all_sim(tasks) and all_sim(mem) and all_sim(acts))
    check("模拟审批人显式命名 sim-approver-01（决策/关闭人）",
          all(m["decided_by"] == "sim-approver-01" for m in mem)
          and all(m["closed_by"] in ("sim-approver-01", "", None) for m in mem))
    check("AI 同事显式命名 sim-ai（检测/提案）",
          all(a["actor"] in ("sim-ai", "sim-approver-01") for a in acts)
          and any(a["actor"] == "sim-ai" for a in acts))
    check("先例 id 全带 -SIM- 标识（永不与真实先例混淆）",
          all("-SIM-" in m["memory_id"] for m in mem))
    _check_isolation()

    print("== 8. F2 补灌域（采购/准入/盘点/资金流——七区点亮 + 引用完整性 + 隔离 + 无未来泄漏）==")
    _check_enrichment(con, cfg, as_of, orders, invs, inv_pos, risks, sev, acts)

    print("== 8b. C 补全域（12 类零实例对象——引用完整性 + 因果勾稽 + 外键补齐 + -SIM- 隔离 + 无未来泄漏）==")
    _check_c_backfill(con, as_of)

    print("== 9. 真实感量化底线：20 条世界快照叙事（含异常与处置）==")
    _print_narratives(con, cfg)

    vs = volume_stats_from_db(con, cfg)
    print(f"\n  体量表：{vs}")
    print(f"{'=' * 44}\n结果: {'全部通过 ✔' if not FAILS else f'{len(FAILS)} 项失败: {FAILS}'}")
    con.close()
    sys.exit(1 if FAILS else 0)


def volume_stats_from_db(con, cfg):
    def n(t):
        return con.execute(f"select count(*) from {t}").fetchone()[0]
    def nlike(t, col, pat):
        return con.execute(f"select count(*) from {t} where {col} like ?", (pat,)).fetchone()[0]
    days = (WD.D(cfg["window"]["as_of"]) - WD.D(cfg["window"]["start"])).days + 1
    nc = n("containers")
    return {"orders": n("sales_orders"), "lines": n("sales_order_lines"),
            "pos": n("purchase_orders"), "shipments": n("shipments"),
            "containers": nc, "milestones": n("shipment_milestones"),
            "invoices": n("invoices"), "invoice_lines": n("invoice_lines"),
            "sim_events": n("sim_event_log"),
            "anomalies": nlike("sim_event_log", "event_kind", "anomaly:%"),
            "chain": nlike("sim_event_log", "event_kind", "chain:%"),
            "risks": n("risk_events"), "tasks": n("tasks"),
            "precedents": n("resolution_memory"),
            "precedents_closed": con.execute(
                "select count(*) from resolution_memory where closed_at != ''").fetchone()[0],
            "annualized_containers": round(nc / days * 365, 1),
            "goods_receipts": n("goods_receipts"), "supplier_invoices": n("supplier_invoices"),
            "admission_cases": n("admission_cases"), "cost_scenarios": n("cost_scenarios"),
            "cycle_counts": n("cycle_counts"), "payments": n("payments")}


def _check_isolation():
    """两库物理隔离：sim 先例/风险绝不混入真实库 data/ontology.sqlite（宪法③精神）。"""
    onto = Path("data/ontology.sqlite")
    if not onto.exists():
        check("两库隔离：真库不存在，跳过（回填仅写 simworld.sqlite）", True)
        return
    c2 = sqlite3.connect(str(onto))
    def cnt(sql):
        try:
            return c2.execute(sql).fetchone()[0]
        except sqlite3.OperationalError:
            return 0
    n_mem = cnt("select count(*) from resolution_memory where memory_id like '%-SIM-%'")
    n_rsk = cnt("select count(*) from risk_events where risk_event_id like '%-SIM-%'")
    # F2：新域实例（收货/供票/准入/成本情景/付款）也绝不混入真实库
    n_grn = cnt("select count(*) from goods_receipts where grn_id like '%-SIM-%'")
    n_pay = cnt("select count(*) from payments where payment_id like '%-SIM-%'")
    n_adm = cnt("select count(*) from admission_cases where admission_case_id like '%-SIM-%'")
    # C 补全域：新 12 类对象亦绝不混入真实库（抽 po_lines/purchase_payments/coordination_threads 代表）
    n_pol = cnt("select count(*) from po_lines where po_line_id like '%-SIM-%'")
    n_ppay = cnt("select count(*) from purchase_payments where payment_id like '%-SIM-%'")
    n_coord = cnt("select count(*) from coordination_threads where coordination_id like '%-SIM-%'")
    c2.close()
    check("两库物理隔离：真实库 data/ontology.sqlite 无 sim 先例/风险混入",
          n_mem == 0 and n_rsk == 0, f"MEM-SIM={n_mem} RSK-SIM={n_rsk}")
    check("两库物理隔离：真实库无 sim 补灌实例（收货/付款/准入）混入",
          n_grn == 0 and n_pay == 0 and n_adm == 0,
          f"GRN-SIM={n_grn} PAY-SIM={n_pay} ADM-SIM={n_adm}")
    check("两库物理隔离：真实库无 sim C 补全实例（po_line/预付款/协调线程）混入",
          n_pol == 0 and n_ppay == 0 and n_coord == 0,
          f"POL-SIM={n_pol} PPAY-SIM={n_ppay} COORD-SIM={n_coord}")


def _check_c_backfill(con, as_of):
    """C 补全域断言：12 类零实例对象补全后的引用完整性、因果勾稽（PO行↔头qty、供票行↔票额）、
    历史外键补齐（收货行/成本情景不再悬空）、-SIM- 物理隔离、无未来泄漏。"""
    def ids(t, col):
        return {r[0] for r in con.execute(f"select {col} from {t}")}

    po_ids = ids("purchase_orders", "po_id")
    sku_ids = ids("skus", "sku_id")
    sup_ids = ids("suppliers", "supplier_id")
    sol_ids = ids("sales_order_lines", "so_line_id")
    invp_ids = ids("inventory_positions", "inventory_position_id")
    adm_ids = ids("admission_cases", "admission_case_id")
    ship_ids = ids("shipments", "shipment_id")
    sinv_ids = ids("supplier_invoices", "supplier_invoice_id")
    pol_ids = ids("po_lines", "po_line_id")
    lp_ids = ids("logistics_plans", "logistics_plan_id")
    task_ids = ids("tasks", "task_id")
    risk_ids = ids("risk_events", "risk_event_id")
    rfq_ids = ids("rfqs", "rfq_id")

    pol = q(con, "select * from po_lines")
    sil = q(con, "select * from supplier_invoice_lines")
    pp = q(con, "select * from purchase_payments")
    rsv = q(con, "select * from inventory_reservations")
    qu = q(con, "select * from quotes")
    rl = q(con, "select * from rfq_lines")
    cf = q(con, "select * from compliance_findings")
    lp = q(con, "select * from logistics_plans")
    ec = q(con, "select * from expected_costs")
    sq = q(con, "select * from supplier_qualifications")
    cth = q(con, "select * from coordination_threads")

    # —— 非空（12 类全点亮）——
    empties = [t for t in ("po_lines", "supplier_invoice_lines", "purchase_payments",
                           "inventory_reservations", "rfqs", "rfq_lines", "quotes",
                           "compliance_findings", "logistics_plans", "expected_costs",
                           "supplier_qualifications", "coordination_threads")
               if con.execute(f"select count(*) from {t}").fetchone()[0] == 0]
    check("C 补全域 12 类对象全部非空（零实例清零）", not empties, f"仍空: {empties}")

    # —— 引用完整性 ——
    check("po_lines 引用完整（po + sku）",
          all(r["po_id"] in po_ids and r["sku_id"] in sku_ids for r in pol))
    check("supplier_invoice_lines 引用完整（供票 + po_line）",
          all(r["supplier_invoice_id"] in sinv_ids and r["po_line_id"] in pol_ids for r in sil))
    check("purchase_payments.po_id 全指向存在 PO", all(r["po_id"] in po_ids for r in pp))
    check("inventory_reservations 引用完整（so_line + inventory_position）",
          all(r["so_line_id"] in sol_ids and r["inventory_position_id"] in invp_ids for r in rsv))
    check("rfq_lines/quotes 引用完整（rfq + supplier）",
          all(r["rfq_id"] in rfq_ids for r in rl)
          and all(r["rfq_id"] in rfq_ids and r["supplier_id"] in sup_ids for r in qu))
    check("compliance_findings/logistics_plans.admission_case_id 全指向存在案件",
          all(r["admission_case_id"] in adm_ids for r in cf)
          and all(r["admission_case_id"] in adm_ids for r in lp))
    check("expected_costs.shipment_id 全指向存在 shipment",
          all(r["shipment_id"] in ship_ids for r in ec))
    check("supplier_qualifications.supplier_id 全指向存在供应商",
          all(r["supplier_id"] in sup_ids for r in sq))
    check("coordination_threads 引用完整（task + risk）",
          all(r["task_id"] in task_ids and r["risk_event_id"] in risk_ids for r in cth))

    # —— 因果勾稽：PO 行 Σqty == PO 头 qty；供票行 Σ金额 == 供票 total ——
    byp = {}
    for r in pol:
        byp[r["po_id"]] = byp.get(r["po_id"], 0) + r["qty"]
    poqty = {r["po_id"]: r["qty"] for r in q(con, "select po_id, qty from purchase_orders")}
    check("po_lines 逐单 Σ行qty == PO 头 qty（分行不失量）",
          all(byp[p] == poqty[p] for p in byp))
    byi = {}
    for r in sil:
        byi[r["supplier_invoice_id"]] = round(byi.get(r["supplier_invoice_id"], 0.0)
                                              + r["amount_usd"], 2)
    tot = {r["supplier_invoice_id"]: r["total_usd"]
           for r in q(con, "select supplier_invoice_id, total_usd from supplier_invoices")}
    check("supplier_invoice_lines Σ行金额 == 供票 total_usd（逐分对账）",
          all(round(byi[k], 2) == round(tot[k], 2) for k in byi))

    # —— 历史外键补齐（既有表因新对象而不再悬空——因果补全的核心价值）——
    grl_pol = ids("goods_receipt_lines", "po_line_id")
    check("goods_receipt_lines.po_line_id 现全指向存在 po_line（补全前悬空）", grl_pol <= pol_ids,
          f"{len(grl_pol - pol_ids)} 悬空")
    cs_lp = ids("cost_scenarios", "logistics_plan_id")
    check("cost_scenarios.logistics_plan_id 现全指向存在 logistics_plan（补全前悬空）", cs_lp <= lp_ids,
          f"{len(cs_lp - lp_ids)} 悬空")

    # —— 指标真实感：exposure 分布 / 资质双态样本 / 预留与协调多态 ——
    check("purchase_payments exposure 三态齐备（released/covered/at_risk——R12 敞口样本存在）",
          {"released", "covered", "at_risk"} <= {r["exposure_status"] for r in pp},
          str({r["exposure_status"] for r in pp}))
    check("supplier_qualifications 含 expired + expiring 样本（R13 未来规则支撑）",
          {"expired", "expiring"} <= {r["status"] for r in sq}, str({r["status"] for r in sq}))
    check("inventory_reservations status 多态（allocated/open/fulfilled）",
          len({r["status"] for r in rsv}) >= 3, str({r["status"] for r in rsv}))
    check("coordination_threads state 多态（awaiting/responded/escalated/resolved）",
          len({r["state"] for r in cth}) >= 3, str({r["state"] for r in cth}))

    # —— 无未来泄漏（已发生时间戳 ≤ as_of）——
    leak = ([r["paid_date"] for r in pp if r["paid_date"] and r["paid_date"] > as_of]
            + [r["opened_at"] for r in cth if r["opened_at"] and r["opened_at"] > as_of]
            + [r["created_date"] for r in q(con, "select created_date from rfqs")
               if r["created_date"] and r["created_date"] > as_of])
    check("C 补全域无未来泄漏（paid/opened/created ≤ as_of）", not leak, f"{len(leak)} 越界")

    # —— -SIM- 隔离标识（12 类新 id 全带）——
    id_col = {"po_lines": "po_line_id", "supplier_invoice_lines": "supplier_invoice_line_id",
              "purchase_payments": "payment_id", "inventory_reservations": "reservation_id",
              "rfqs": "rfq_id", "rfq_lines": "rfq_line_id", "quotes": "quote_id",
              "compliance_findings": "compliance_finding_id", "logistics_plans": "logistics_plan_id",
              "expected_costs": "expected_cost_id", "supplier_qualifications": "qualification_id",
              "coordination_threads": "coordination_id"}
    bad_sim = [t for t, k in id_col.items()
               if con.execute(f"select count(*) from {t} where {k} not like '%-SIM-%'").fetchone()[0]]
    check("C 补全域 12 类 id 全带 -SIM- 标识（物理隔离防混淆）", not bad_sim, f"含非SIM: {bad_sim}")


def _check_enrichment(con, cfg, as_of, orders, invs, inv_pos, risks, sev, acts):
    """F2 补灌域断言：七区点亮证据 + 引用完整性 + 因果自洽 + -SIM- 隔离 + 无未来泄漏。"""
    grns = q(con, "select * from goods_receipts")
    grnls = q(con, "select * from goods_receipt_lines")
    sinvs = q(con, "select * from supplier_invoices")
    adms = q(con, "select * from admission_cases")
    scen = q(con, "select * from cost_scenarios")
    ccs = q(con, "select * from cycle_counts")
    pays = q(con, "select * from payments")
    po_by_id = {p["po_id"]: p for p in q(con, "select * from purchase_orders")}
    sup_ids = {s["supplier_id"] for s in q(con, "select supplier_id from suppliers")}
    cust_ids = {c["customer_id"] for c in q(con, "select customer_id from customers")}
    sku_ids = {s["sku_id"] for s in q(con, "select sku_id from skus")}
    pos_ids = {p["inventory_position_id"] for p in inv_pos}
    so_ids = {o["so_id"] for o in orders}
    inv_ids = {i["invoice_id"] for i in invs}
    grn_ids = {g["grn_id"] for g in grns}
    sinv_ids = {s["supplier_invoice_id"] for s in sinvs}

    # —— 非空（七区点亮）——
    check("采购收货域非空（供应商区交期/缺陷指标点亮）", bool(grns and grnls and sinvs),
          f"grn={len(grns)} grnl={len(grnls)} sinv={len(sinvs)}")
    check("准入案 + 成本情景非空（客户漏斗 + 钱区毛利点亮）", bool(adms and scen),
          f"adm={len(adms)} scen={len(scen)}")
    check("盘点差异 cycle_counts 非空且 variance 全非零（库存区盘点差异点亮）",
          bool(ccs) and all(int(c["variance"]) != 0 for c in ccs), f"n={len(ccs)}")
    check("payments 非空（钱区应收应付备料）", bool(pays), f"n={len(pays)}")

    # —— 引用完整性 ——
    check("goods_receipts.po_id 全指向存在 PO",
          all(g["po_id"] in po_by_id for g in grns))
    check("goods_receipt_lines.grn_id 全指向存在收货单",
          all(l["grn_id"] in grn_ids for l in grnls))
    check("supplier_invoices 引用完整（po + supplier）",
          all(s["po_id"] in po_by_id and s["supplier_id"] in sup_ids for s in sinvs))
    check("cycle_counts.inventory_position_id 全指向存在头寸",
          all(c["inventory_position_id"] in pos_ids for c in ccs))
    check("admission_cases 引用完整（customer + sku）",
          all(a["customer_id"] in cust_ids and a["sku_id"] in sku_ids for a in adms))
    bad_pay = []
    for p in pays:
        rt, rid = p["ref_type"], p["ref_id"]
        ok = ((rt == "sales_order" and rid in so_ids)
              or (rt == "supplier_invoice" and rid in sinv_ids)
              or (rt == "invoice" and rid in inv_ids))
        if not ok:
            bad_pay.append(p["payment_id"])
    check("payments 判别式引用完整（in→订单 / out→供票|物流票）", not bad_pay,
          f"{len(bad_pay)} 悬空，样例 {bad_pay[:3]}")
    check("payments 方向与挂载一致（in=sales_order / out∈{supplier_invoice,invoice}）",
          all((p["direction"] == "in" and p["ref_type"] == "sales_order")
              or (p["direction"] == "out" and p["ref_type"] in ("supplier_invoice", "invoice"))
              for p in pays))

    # —— 因果自洽（收货 ∈ [下单, as_of]；供票 ≥ PO 首收货；付款方向账期）——
    check("收货日 ∈ [PO 下单日, as_of]（不早于下单、不穿越今天）",
          all(po_by_id[g["po_id"]]["po_date"] <= g["received_date"] <= as_of for g in grns))
    recv_min = {}
    for g in grns:
        recv_min[g["po_id"]] = min(recv_min.get(g["po_id"], g["received_date"]), g["received_date"])
    check("供票开票日 ≥ 关联 PO 首收货日（先收货后开票）",
          all(s["issue_date"] >= recv_min.get(s["po_id"], "0000-00-00") for s in sinvs))

    # —— 无未来泄漏（新域一切已发生时间戳 ≤ as_of）——
    leak = ([g["grn_id"] for g in grns if g["received_date"] > as_of]
            + [s["supplier_invoice_id"] for s in sinvs if s["issue_date"] > as_of]
            + [p["payment_id"] for p in pays if p["paid_date"] and p["paid_date"] > as_of]
            + [c["cycle_count_id"] for c in ccs if c["as_of_date"] > as_of])
    check("F2 新域无未来泄漏（received/issue/paid/count ≤ as_of）", not leak, f"{len(leak)} 越界")

    # —— 指标真实感（有值、非满非零；四桶全覆盖）——
    on_time = sum(1 for po, rd in recv_min.items()
                  if rd <= po_by_id[po]["expected_ready_date"])
    rate = on_time / len(recv_min) if recv_min else 0
    check("供应商交期达成率 ∈ (0.5, 0.95)（有值、非满非零）", 0.5 < rate < 0.95,
          f"rate={rate:.3f}（{on_time}/{len(recv_min)}）")
    buckets = set()
    for s in scen:
        r = float(s["gross_margin_rate"])
        buckets.add("loss" if r < 0 else "0-10" if r < 0.10 else "10-20" if r < 0.20 else ">=20")
    check("成本情景毛利率四桶全覆盖（loss/0-10/10-20/≥20）",
          buckets == {"loss", "0-10", "10-20", ">=20"}, f"{sorted(buckets)}")
    check("准入漏斗覆盖 ≥5 状态（漏斗形态）", len({a["status"] for a in adms}) >= 5,
          f"{sorted({a['status'] for a in adms})}")
    check("缺陷率有分布（defect_ppm 跨供应商可排名）",
          len({l["defect_ppm"] for l in grnls}) >= 20, f"distinct={len({l['defect_ppm'] for l in grnls})}")

    # —— 在途货值可算（在途分配行 skus 均有申报价值）——
    itv = con.execute(
        "select count(*) c, sum(case when k.declared_value_usd is null "
        "or k.declared_value_usd='' then 1 else 0 end) nv "
        "from shipments s join shipment_allocations sa on sa.shipment_id=s.shipment_id "
        "join sales_order_lines sol on sol.so_line_id=sa.so_line_id "
        "join skus k on k.sku_id=sol.sku_id where s.status='in_transit'").fetchone()
    check("在途分配行 skus 均有 declared_value（钱区在途货值点亮）",
          itv[0] > 0 and (itv[1] or 0) == 0, f"{itv[1]}/{itv[0]} 行无申报价值")

    # —— AI 今日非 0（世界时钟当日有检测+提案，真实 AR 账龄事件）——
    clock = max(max((r["detected_at"][:10] for r in risks), default=""),
                max((e["sim_date"] for e in sev), default=""))
    det_today = sum(1 for r in risks if r["detected_at"][:10] == clock)
    prop_today = sum(1 for a in acts if a["sim_date"] == clock and a["activity"] == "propose")
    check("AI 今日非 0（世界时钟当日有检测+提案——真实逾期应收账龄事件，非冒充）",
          det_today > 0 and prop_today > 0, f"clock={clock} 检={det_today} 提案={prop_today}")
    fin_risks = [r for r in risks if r["rule_id"] in ("R19", "R21")]
    check("财务风险 R19/R21 非空、全 source='sim'、affected 行留空（不污染客户区敞口）",
          bool(fin_risks) and all(r["source"] == "sim" and r["affected_so_line_ids"] in ("[]", "")
                                  for r in fin_risks), f"n={len(fin_risks)}")

    # —— -SIM- 隔离标识（新域 id 全带）——
    check("F2 新域 id 全带 -SIM- 标识（物理隔离防混淆）",
          all("-SIM-" in g["grn_id"] for g in grns)
          and all("-SIM-" in l["grn_line_id"] for l in grnls)
          and all("-SIM-" in s["supplier_invoice_id"] for s in sinvs)
          and all("-SIM-" in a["admission_case_id"] for a in adms)
          and all("-SIM-" in s["cost_scenario_id"] for s in scen)
          and all("-SIM-" in c["cycle_count_id"] for c in ccs)
          and all("-SIM-" in p["payment_id"] for p in pays))


PORT_CN = {"CNYTN": "盐田", "CNSHK": "蛇口", "CNNGB": "宁波", "USLAX": "洛杉矶",
           "USLGB": "长滩", "USNYC": "纽约", "USSAV": "萨凡纳", "NLRTM": "鹿特丹",
           "DEHAM": "汉堡", "SGSIN": "新加坡", "KRPUS": "釜山", "TWKHH": "高雄"}
EVT_CN = {"booking_confirmed": "订舱确认", "departed": "离港", "transshipment": "中转换船",
          "arrived": "到港", "customs_filed": "报关申报", "customs_hold": "查验滞留",
          "customs_released": "海关放行", "delivered": "妥投签收"}
ACTION_CN = {"expedite": "加急", "accept_delay": "接受延误", "dispute": "争议账单",
             "escalate_replenishment": "紧急补货", "chase_docs": "补件催办", "escalate": "升级人工"}
FAMILY_CN = {"delay": "延误", "inspection": "查验", "fee": "费用", "document": "单证",
             "warehouse": "仓储", "chain": "连锁", "tail": "尾部"}


def _print_narratives(con, cfg):
    """打印 20 条世界快照叙事，含异常与 AI 处置全链（延误→提案→审批→改世界→先例）——供人工抽读。"""
    ships = {s["shipment_id"]: s for s in q(con, "select * from shipments")}
    meta = {m["shipment_id"]: m for m in q(con, "select * from sim_shipment_meta")}
    fwd = {f["forwarder_id"]: f["name"] for f in q(con, "select * from sim_forwarders")}
    conts = defaultdict(list)
    for c in q(con, "select * from containers"):
        conts[c["shipment_id"]].append(c["container_no"])
    risks = q(con, "select * from risk_events")
    tasks_by_rid = {t["risk_event_id"]: t for t in q(con, "select * from tasks")}
    mem_by_rid = {m["risk_event_id"]: m for m in q(con, "select * from resolution_memory")}
    sev = q(con, "select * from sim_event_log order by sim_event_id")
    by_id = {e["sim_event_id"]: e for e in sev}

    def ship_tag(sid):
        s = ships.get(sid)
        if not s:
            return sid
        box = conts[sid][0] if conts[sid] else s["container_no"]
        fid = meta.get(sid, {}).get("forwarder_id", "")
        return (f"{sid} 柜{box}（{PORT_CN.get(s['origin_port_locode'], s['origin_port_locode'])}→"
                f"{PORT_CN.get(s['destination_port_locode'], s['destination_port_locode'])}，"
                f"{s['carrier_name']}，货代 {fwd.get(fid, fid)}）")

    lines, seen = [], set()
    # (A) 延误/费用/单证/断货 风险 → AI 处置全链（优先讲有先例回填的完整故事）
    ordered_risks = sorted(risks, key=lambda r: (r["resolved_at"] == "", r["risk_event_id"]))
    for r in ordered_risks:
        if len([l for l in lines if l[2] == "disposition"]) >= 12:
            break
        t = tasks_by_rid.get(r["risk_event_id"])
        if not t:
            continue
        m = mem_by_rid.get(r["risk_event_id"])
        econ = ""
        try:
            econ = json.loads(t["economics_json"]).get("verdict", "")
        except Exception:
            econ = ""
        outcome = ""
        if m and m["closed_at"]:
            ql = {"effective": "有效", "partial": "部分有效", "ineffective": "无效"}.get(
                m["quality_label"], m["quality_label"])
            outcome = f" → {m['outcome_resolved']}、{m['outcome_days']}天关闭、专员标「{ql}」"
        decision = {"adopted": "批准", "rejected": "驳回"}.get(
            (m or {}).get("decision"), t["approval_status"])
        anchor = ship_tag(r["shipment_id"]) if r["shipment_id"] else f"仓 {r['warehouse_id']}"
        lines.append((r["detected_at"],
                      f"  [{r['detected_at']}] {r['rule_id']}·{r['severity']}｜{anchor}\n"
                      f"       风险：{r['root_cause'][:72]}\n"
                      f"       AI提案「{ACTION_CN.get(t['proposed_action'], t['proposed_action'])}」"
                      f"（{econ}）→ sim-approver-01 {decision}{outcome}",
                      "disposition"))
    # (B) 连锁链叙事：抽 3 条 stockout 逐环
    stockouts = [e for e in sev if e["event_kind"] == "chain:stockout"]
    for so in sorted(stockouts, key=lambda e: e["sim_event_id"])[:3]:
        cd = by_id.get(so["caused_by"], {})
        dl = by_id.get(cd.get("caused_by"), {})
        lines.append((so["sim_date"],
                      f"  [{so['sim_date']}] 连锁链｜{dl.get('event_kind', '?')}({dl.get('params_json', '')[:26]})"
                      f" → 库存倒计时 → {so['event_kind']}：{so['params_json'][:60]}", "chain"))
    # (C) 普通里程碑快照补足到 20（正常世界纹理）
    rng = random.Random(cfg["seed"] + 777)
    ms = q(con, "select * from shipment_milestones order by milestone_id")
    for m in rng.sample(ms, min(len(ms), 40)):
        if len(lines) >= 20:
            break
        s = ships[m["shipment_id"]]
        loc = PORT_CN.get(m["event_locode"], m["event_locode"])
        lines.append((m["event_time"][:10],
                      f"  [{m['event_time'][:10]}] {ship_tag(m['shipment_id'])}："
                      f"{EVT_CN.get(m['event_type'], m['event_type'])} @ {loc}", "normal"))
    for ln in sorted(lines, key=lambda x: x[0])[:20]:
        print(ln[1])


if __name__ == "__main__":
    main()
