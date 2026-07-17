#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""业务逻辑指纹（波2-2b 确定性尺子精化）：对库中全部表的**有序行内容**算 md5，排除声明的遥测表。

    python3 -m pipeline.db_digest [--db data/ontology.sqlite]

为什么存在（诚实背景，2026-07-16 实测发现）：
- "全链重建文件 md5 逐字节一致"这条口头不变量，自 G-Ledger（rule_run_ledger 记真实墙钟
  run_ts，af180e1）起**事实上已不成立**——遥测表按设计记录"何时真的跑过"，两次重建必然不同。
  本次波2-2b 做两次重建对照时首次暴露（此前无人重建两次对照过）。
- 精化而非放松：**业务表**的重建确定性仍是硬门（本工具即其尺子）；文件 md5 继续用于
  "测试期间库未被触碰"的守恒检查（那个场景没有重建，遥测表也不该变）。
- TELEMETRY_TABLES 是唯一豁免清单（最小面）：新增遥测表必须在此登记并说明理由，
  否则确定性门照常红。
"""
from __future__ import annotations

import argparse
import hashlib
import sqlite3
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = REPO_ROOT / "data" / "ontology.sqlite"

# 遥测表豁免清单（记录真实墙钟/运行痕，重建间允许不同；每项必须有理由）：
TELEMETRY_TABLES = {
    "rule_run_ledger",   # G-Ledger：规则每次执行的真实 run_ts（何时真的跑过，本身就是证据）
    "llm_calls",         # 出境审计：真实调用时刻（D8 遥测例外，egress_gate 注释在案）
    "commands",          # Command 总线台账：created_at 真实 UTC（运行态命令痕，非 build 产物）
    "agent_runs",        # 波2-2c Agent runtime：run 生命周期 created_at/updated_at 真实 UTC（运行态，非 build 产物）
    "agent_run_steps",   # 波2-2c Agent runtime：每步 created_at 真实 UTC（运行痕，重建库中根本不存在，运行时自建）
}


def business_digest(db_path: str | Path) -> str:
    """全部非遥测表：按表名序 × 首列序逐行 md5。行序用首列排序保证跨次稳定。"""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    h = hashlib.md5()
    try:
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        for t in tables:
            if t in TELEMETRY_TABLES or t.startswith("sqlite_"):
                continue
            h.update(f"§{t}".encode())
            cols = [c[1] for c in con.execute(f"PRAGMA table_info({t})")]
            for row in con.execute(f"SELECT * FROM {t} ORDER BY {cols[0]}"):
                h.update(repr(row).encode())
    finally:
        con.close()
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=str(DEFAULT_DB))
    args = ap.parse_args()
    print(f"business_digest({args.db}) = {business_digest(args.db)}")
    print(f"（遥测豁免表：{sorted(TELEMETRY_TABLES)}——重建间允许不同，理由见模块头）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
