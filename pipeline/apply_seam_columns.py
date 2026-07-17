#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""波2-2b 接缝列迁移（V14 接缝①②，V18）：给**全部本体对象表**补齐系统列 + 版本触发器。

    python3 -m pipeline.apply_seam_columns [--db data/ontology.sqlite]

为什么存在（≤5 行）：
- 桥3 生成器只建"build 期有行数据"的对象表；tasks/risk_events/dq_issues 等由引擎在链中自建
  （engine/ 属只读红线，不改它的 DDL）——本步在**链尾**以 ALTER 补列 + 建触发器，零侵入。
- 幂等（可反复跑/空库/旧库均安全，AGENTS §6.1.3）：缺列才 ALTER、触发器 IF NOT EXISTS；
  已是新结构时整步为 no-op。同一脚本双用途：验证世界链尾步 + 模拟世界一次性迁移。
- 只前向修复：不删列不回滚；version 默认 0、tenant_id 默认 'default'（单租户期恒默认，
  不做任何隔离逻辑——V14 原文）。
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from pipeline.generate_ddl import version_trigger_sql
from pipeline.ontology_lint import load_ontology, object_table_name

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = REPO_ROOT / "data" / "ontology.sqlite"

SEAM_COLUMN_DDL = {
    "version": "INTEGER NOT NULL DEFAULT 0",
    "tenant_id": "TEXT NOT NULL DEFAULT 'default'",
}


def apply(db_path: str | Path) -> dict:
    """返回 {added: [(表,列)...], triggers: [表...], skipped_tables: [...]}——全量如实。"""
    onto = load_ontology()
    con = sqlite3.connect(str(db_path))
    added, triggers, skipped = [], [], []
    try:
        existing = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        for obj in onto["objects"]:
            table = object_table_name(obj)
            if table not in existing:
                skipped.append(table)          # 该世界没这张表（如实报，不视为错）
                continue
            cols = {c[1] for c in con.execute(f"PRAGMA table_info({table})")}
            for col, ddl in SEAM_COLUMN_DDL.items():
                if col not in cols:
                    con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
                    added.append((table, col))
            trig = f"trg_{table}_version"
            has_trig = con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='trigger' AND name=?",
                (trig,)).fetchone()
            con.execute(version_trigger_sql(table))
            if not has_trig:
                triggers.append(table)
        con.commit()
    finally:
        con.close()
    return {"added": added, "triggers": triggers, "skipped_tables": skipped}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=str(DEFAULT_DB))
    args = ap.parse_args()
    r = apply(args.db)
    print(f"接缝列迁移完成 @ {args.db}")
    print(f"  新增列 {len(r['added'])} 处；新建触发器 {len(r['triggers'])} 个；"
          f"无表跳过 {len(r['skipped_tables'])} 个（{r['skipped_tables'] or '无'}）")
    if not r["added"] and not r["triggers"]:
        print("  （no-op：目标库已是新结构，幂等成立）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
