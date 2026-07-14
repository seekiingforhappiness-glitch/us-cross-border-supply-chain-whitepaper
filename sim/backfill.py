"""历史回填：一次性生成 2025-05-01 → 2026-07-14（14+ 个月）历史。

python3 -m sim.backfill  →  生成 data/simworld.sqlite + 打印耗时与体量统计。

为什么这样建（≤5 行）：① 回填 = 用 SimClock 从 start 逐日 iter 到 as_of，每天调 generators.run_tick
——与未来 live 驱动共用同一个 run_tick（一套逻辑两种驱动，防口径漂移）。② RNG 全部从 config.seed 派生
命名子流（orders/booking/transit/invoice/inventory），确定性可复现。③ 季节故事（旺季爆舱/春节停摆/
货代涨价）由 config 的季节曲线与事件脚本埋入，回填与 tick 同一套生成器实现，不另开分支。
"""
import argparse
import random
import time
from pathlib import Path

import yaml

from . import world_def as WD
from . import generators as G
from . import ai_loop as AI
from . import enrich as EN
from .clock import SimClock
from .store import write_simworld

# S2 追加 anomalies/ai 子流；F2 追加 procurement/admission/finance 子流（均附在末尾——
# 既有子流 seed 全不变，故 S1 世界（anomalies.enabled=false）与 S2 世界逐字节不受补灌扰动）。
STREAMS = ("world", "orders", "booking", "transit", "invoice", "inventory", "anomalies", "ai",
           "procurement", "admission", "finance")


def make_streams(seed):
    """命名 RNG 子流：同 seed → 同序列（复现性铁律）。"""
    return {name: random.Random(seed + i * 1000) for i, name in enumerate(STREAMS)}


def build(cfg):
    """构建 + 回填整个世界，返回 world（verify 复现性测试会调两次比对）。"""
    streams = make_streams(cfg["seed"])
    start = WD.D(cfg["window"]["start"])
    as_of = WD.D(cfg["window"]["as_of"])
    world = WD.build_static_world(cfg, streams["world"])
    world["_cfg"], world["_start"], world["_as_of"] = cfg, start, as_of
    G.init_dynamic(world)
    AI.init_ai(world)                      # S2：AI 回路状态（风险/任务/先例/活动流）
    clock = SimClock(start, as_of)
    cadence = cfg["ai_loop"]["cadence_days"]
    ticks = 0
    for day in clock.iter_days():          # 回填驱动：逐日 tick 到 as_of
        G.run_tick(world, day, streams)    # 产当日业务事件 + 注入异常（inline）
        # S2：AI 同事按 cadence 在时间线内运转（处置在里程碑 emit 前改计划 → 因果真实）
        if (day - start).days % cadence == 0 or day == as_of:
            AI.run_cadence(world, day, streams)
        ticks += 1
    world["_ticks"] = ticks
    EN.enrich(world, cfg, streams)         # F2：回填后补灌采购/准入/盘点/资金流域（独立子流）
    return world


def volume_stats(world):
    n_cont = len(world["containers"])
    days = (world["_as_of"] - world["_start"]).days + 1
    annualized = round(n_cont / days * 365, 1)
    ev = world["event_log"]
    anom = sum(1 for e in ev if e["event_kind"].startswith("anomaly:"))
    chain = sum(1 for e in ev if e["event_kind"].startswith("chain:"))
    mem = world.get("memory", [])
    return {
        "sales_orders": len(world["sos"]), "so_lines": len(world["lines"]),
        "purchase_orders": len(world["pos"]), "shipments": len(world["shipments"]),
        "containers": n_cont, "milestones": len(world["milestones"]),
        "invoices": len(world["invoices"]), "invoice_lines": len(world["invoice_lines"]),
        "sim_events": len(ev), "window_days": days, "annualized_containers": annualized,
        # S2 异常谱系 + AI 回路
        "anomalies": anom, "chain_events": chain,
        "risk_events": len(world.get("risk_events", {})), "tasks": len(world.get("tasks", {})),
        "precedents": len(mem), "precedents_closed": sum(1 for m in mem if m["closed_at"]),
        "ai_activity": len(world.get("ai_activity", [])),
        # F2 补灌域
        "goods_receipts": len(world.get("goods_receipts", [])),
        "goods_receipt_lines": len(world.get("goods_receipt_lines", [])),
        "supplier_invoices": len(world.get("supplier_invoices", [])),
        "admission_cases": len(world.get("admission_cases", [])),
        "cost_scenarios": len(world.get("cost_scenarios", [])),
        "cycle_counts": len(world.get("cycle_counts", [])),
        "payments": len(world.get("payments", [])),
    }


def anomaly_family_table(world):
    """异常与处置体量统计（按族 + 连锁 + 处置口径），供 backfill/报告打印。"""
    import collections
    fam = collections.Counter()
    kind = collections.Counter()
    for e in world["event_log"]:
        k = e["event_kind"]
        if k.startswith(("anomaly:", "chain:", "texture:", "tail:")):
            fam[e["family"] or "?"] += 1 if k.startswith("anomaly:") else 0
            kind[k] += 1
    tasks = world.get("tasks", {}).values()
    approved = sum(1 for t in tasks if t["approval_status"] == "approved")
    rejected = sum(1 for t in tasks if t["approval_status"] == "rejected")
    return {"by_family_primary": dict(fam), "by_kind": dict(kind),
            "proposals": len(list(tasks)), "approved": approved, "rejected": rejected}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="sim/config.yaml")
    args = ap.parse_args()
    cfg = yaml.safe_load(open(args.config, encoding="utf-8"))

    t0 = time.perf_counter()
    world = build(cfg)
    t_build = time.perf_counter() - t0
    counts = write_simworld(world, cfg)
    t_total = time.perf_counter() - t0

    vs = volume_stats(world)
    print(f"== 回填完成 ==  window {cfg['window']['start']} → {cfg['window']['as_of']} "
          f"（{vs['window_days']} 天 / {world['_ticks']} ticks）")
    print(f"   生成耗时 {t_build:.2f}s | 落库耗时 {t_total - t_build:.2f}s | "
          f"总计 {t_total:.2f}s → {cfg['output']['sqlite_path']}")
    print(f"   体量：订单 {vs['sales_orders']} / 行 {vs['so_lines']} / PO {vs['purchase_orders']} / "
          f"船 {vs['shipments']} / 柜 {vs['containers']}（年化 {vs['annualized_containers']}）")
    print(f"        里程碑 {vs['milestones']} / 发票 {vs['invoices']} / 发票行 {vs['invoice_lines']} / "
          f"sim_event_log {vs['sim_events']}")
    # S2 异常谱系 + AI 回路统计
    ft = anomaly_family_table(world)
    prim = {m: sum(bym for k, bym in ft["by_kind"].items() if k.startswith(f"anomaly:{m}"))
            for m in ("delay", "inspection", "fee", "document", "warehouse")}
    print(f"   异常谱系：主异常 {vs['anomalies']} 起 + 连锁 {vs['chain_events']} 环（按族："
          f"延误{prim['delay']}/查验{prim['inspection']}/费用{prim['fee']}/单证{prim['document']}/"
          f"仓储{prim['warehouse']}）")
    print(f"   AI 回路：风险 {vs['risk_events']} / 提案 {vs['tasks']}（批{ft['approved']}/驳{ft['rejected']}）"
          f" / 先例 {vs['precedents']}（已闭环回填 {vs['precedents_closed']}）/ 活动流 {vs['ai_activity']}")
    print(f"   F2 补灌：收货 {vs['goods_receipts']}/行 {vs['goods_receipt_lines']} / 供票 "
          f"{vs['supplier_invoices']} / 准入 {vs['admission_cases']}/成本情景 {vs['cost_scenarios']} / "
          f"盘点 {vs['cycle_counts']} / 付款 {vs['payments']}")
    print(f"   落库表：{len(counts)} 张（对象层 + sim 专属）；simworld.sqlite 大小 "
          f"{Path(cfg['output']['sqlite_path']).stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
