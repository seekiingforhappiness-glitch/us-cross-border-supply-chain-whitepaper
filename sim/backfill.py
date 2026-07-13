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
from .clock import SimClock
from .store import write_simworld

STREAMS = ("world", "orders", "booking", "transit", "invoice", "inventory")


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
    clock = SimClock(start, as_of)
    ticks = 0
    for day in clock.iter_days():          # 回填驱动：逐日 tick 到 as_of
        G.run_tick(world, day, streams)
        ticks += 1
    world["_ticks"] = ticks
    return world


def volume_stats(world):
    n_cont = len(world["containers"])
    days = (world["_as_of"] - world["_start"]).days + 1
    annualized = round(n_cont / days * 365, 1)
    return {
        "sales_orders": len(world["sos"]), "so_lines": len(world["lines"]),
        "purchase_orders": len(world["pos"]), "shipments": len(world["shipments"]),
        "containers": n_cont, "milestones": len(world["milestones"]),
        "invoices": len(world["invoices"]), "invoice_lines": len(world["invoice_lines"]),
        "sim_events": len(world["event_log"]), "window_days": days,
        "annualized_containers": annualized,
    }


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
    print(f"   落库表：{len(counts)} 张（对象层 + sim 专属）；simworld.sqlite 大小 "
          f"{Path(cfg['output']['sqlite_path']).stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
