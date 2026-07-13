"""模拟世界验收：python3 -m sim.verify（无头，风格同 datagen/verify.py）

检查：1 复现性（同种子两次内容摘要逐字节一致）；2 体量合理性（450 柜/年 ±10%、季节曲线可见）；
3 业务一致性（里程碑链完整单调、无孤儿、库存非负、无未来泄漏）；4 真实感抽查（柜号校验位/真实船名池/
周班节奏）；5 真实感量化底线（打印 20 条世界快照叙事供人工抽读）。
判定逻辑不得为通过验收而修改（继承 AGENTS §5 精神）。
"""
import hashlib
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
CHAIN = {"booking_confirmed": 0, "departed": 1, "transshipment": 2, "arrived": 3,
         "customs_filed": 4, "customs_released": 5, "delivered": 6}


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
        # 完整前缀：出现某里程碑则其所有前置（除可选 transshipment）都在
        for et in present:
            for pre, r in CHAIN.items():
                if pre == "transshipment":
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

    print("== 5. 真实感量化底线：20 条世界快照叙事 ==")
    _print_narratives(con, cfg)

    vs = volume_stats_from_db(con, cfg)
    print(f"\n  体量表：{vs}")
    print(f"{'=' * 44}\n结果: {'全部通过 ✔' if not FAILS else f'{len(FAILS)} 项失败: {FAILS}'}")
    con.close()
    sys.exit(1 if FAILS else 0)


def volume_stats_from_db(con, cfg):
    def n(t):
        return con.execute(f"select count(*) from {t}").fetchone()[0]
    days = (WD.D(cfg["window"]["as_of"]) - WD.D(cfg["window"]["start"])).days + 1
    nc = n("containers")
    return {"orders": n("sales_orders"), "lines": n("sales_order_lines"),
            "pos": n("purchase_orders"), "shipments": n("shipments"),
            "containers": nc, "milestones": n("shipment_milestones"),
            "invoices": n("invoices"), "invoice_lines": n("invoice_lines"),
            "sim_events": n("sim_event_log"),
            "annualized_containers": round(nc / days * 365, 1)}


PORT_CN = {"CNYTN": "盐田", "CNSHK": "蛇口", "CNNGB": "宁波", "USLAX": "洛杉矶",
           "USLGB": "长滩", "USNYC": "纽约", "USSAV": "萨凡纳", "NLRTM": "鹿特丹",
           "DEHAM": "汉堡", "SGSIN": "新加坡", "KRPUS": "釜山", "TWKHH": "高雄"}
EVT_CN = {"booking_confirmed": "订舱确认", "departed": "离港", "transshipment": "中转换船",
          "arrived": "到港", "customs_filed": "报关申报", "customs_released": "海关放行",
          "delivered": "妥投签收"}


def _print_narratives(con, cfg):
    """打印 20 条随机世界快照叙事（某天某柜在哪、发生了什么）——供人工抽读。"""
    rng = random.Random(cfg["seed"] + 777)
    ms = q(con, "select * from shipment_milestones order by milestone_id")
    ships = {s["shipment_id"]: s for s in q(con, "select * from shipments")}
    meta = {m["shipment_id"]: m for m in q(con, "select * from sim_shipment_meta")}
    conts = defaultdict(list)
    for c in q(con, "select * from containers"):
        conts[c["shipment_id"]].append(c["container_no"])
    fwd = {f["forwarder_id"]: f["name"] for f in q(con, "select * from sim_forwarders")}
    sample = rng.sample(ms, min(20, len(ms)))
    sample.sort(key=lambda m: m["event_time"])
    for m in sample:
        s = ships[m["shipment_id"]]
        box = conts[s["shipment_id"]][0] if conts[s["shipment_id"]] else s["container_no"]
        opol, dpol = s["origin_port_locode"], s["destination_port_locode"]
        loc = PORT_CN.get(m["event_locode"], m["event_locode"])
        fid = meta.get(s["shipment_id"], {}).get("forwarder_id", "")
        print(f"  {m['event_time'][:10]}｜{s['shipment_id']} 柜{box}（{PORT_CN.get(opol, opol)}→"
              f"{PORT_CN.get(dpol, dpol)}，{s['carrier_name']} {s['vessel_voyage']}，货代 "
              f"{fwd.get(fid, fid)}）：{EVT_CN.get(m['event_type'], m['event_type'])} @ {loc}")


if __name__ == "__main__":
    main()
