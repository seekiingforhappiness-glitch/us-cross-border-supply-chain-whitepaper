#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""波2-2b 接缝列堵门测试（临时副本，绝不写真库）：python3 -m pipeline.test_seam_columns"""
import shutil, sqlite3, sys, tempfile
from pathlib import Path
from pipeline.apply_seam_columns import apply
from pipeline.ontology_lint import load_ontology, object_table_name

REPO = Path(__file__).resolve().parent.parent
FAILS = []
def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail and not ok else ""))
    if not ok: FAILS.append(name)

def main():
    tmp = Path(tempfile.mkdtemp()) / "seam.sqlite"
    shutil.copy(REPO / "data" / "ontology.sqlite", tmp)
    con = sqlite3.connect(tmp)
    onto = load_ontology()
    tables = [object_table_name(o) for o in onto["objects"]]
    missing = [t for t in tables for c in ("version", "tenant_id")
               if c not in {x[1] for x in con.execute(f"PRAGMA table_info({t})")}]
    check(f"全部 {len(tables)} 对象表带 version+tenant_id 系统列", not missing, str(missing))
    trig = con.execute("SELECT count(*) FROM sqlite_master WHERE type='trigger' "
                       "AND name LIKE 'trg_%_version'").fetchone()[0]
    check(f"版本触发器满编（{trig}/{len(tables)}）", trig == len(tables))
    tid, v0 = con.execute("SELECT task_id, version FROM tasks LIMIT 1").fetchone()
    con.execute("UPDATE tasks SET priority=priority WHERE task_id=?", (tid,)); con.commit()
    v1 = con.execute("SELECT version FROM tasks WHERE task_id=?", (tid,)).fetchone()[0]
    check("普通 UPDATE 自动 version+1（乐观锁地基）", v1 == v0 + 1, f"{v0}->{v1}")
    con.execute("UPDATE tasks SET priority=priority, version=99 WHERE task_id=?", (tid,)); con.commit()
    v2 = con.execute("SELECT version FROM tasks WHERE task_id=?", (tid,)).fetchone()[0]
    check("显式设 version 时触发器让位（未来乐观锁写路径可控）", v2 == 99, f"got {v2}")
    tenants = con.execute("SELECT DISTINCT tenant_id FROM shipments").fetchall()
    check("tenant_id 单租户期恒 'default'（V14 接缝①）", tenants == [("default",)], str(tenants))
    r = apply(tmp)
    check("迁移幂等（新结构库上 no-op）", not r["added"] and not r["triggers"], str(r))
    con.close()
    print("=" * 44); print("结果:", "全部通过 ✔" if not FAILS else f"{len(FAILS)} 项失败: {FAILS}")
    print("（临时副本测试，data/ 真库零写入）")
    return 1 if FAILS else 0

if __name__ == "__main__":
    sys.exit(main())
