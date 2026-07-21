"""sim/test_store.py —— write_simworld 落库必须自带运营态表（V19 勘误防复发回归）。

缘起（2026-07-19，V22 驾驶舱验证链）：V19 勘误（6f91b1c）发现模拟世界缺 action_log 等
运营态表时，任何写动作（如驾驶舱 X-World: sim 下审批）在 app.actions._log 撞
"no such table" → 且 _fail 内再次撞同错 → HTTP 500。当时修法是迁移工具
pipeline.apply_seam_columns.ensure_operational_tables 对存量库一次性补建——但
sim.backfill 重建库时 write_simworld 先 unlink 再只按 OBJECT/SIM/S2 DDL 建表，
三张运营态表会再度消失，bug 必然复发。本文件看守「生成即带表」：
write_simworld 产出的库必须含 action_log / dq_issues / integration_outbox
（空表 = 从零开始记录，语义正确；V19 刻意不补读基建表的口径不变）。
"""
import copy
import sqlite3
from pathlib import Path

import pytest
import yaml

from sim.backfill import build
from sim.store import write_simworld

REPO_ROOT = Path(__file__).resolve().parent.parent

# 写路径必需的三张运营态表（口径同 pipeline.apply_seam_columns._OPERATIONAL_DDL + outbox）
OPERATIONAL_TABLES = ("action_log", "dq_issues", "integration_outbox")


@pytest.fixture(scope="module")
def small_world():
    """两周小窗口世界（生成快、覆盖完整落库路径）；输出路径由各用例显式传 tmp，绝不碰真库。"""
    cfg = copy.deepcopy(yaml.safe_load(open(REPO_ROOT / "sim" / "config.yaml", encoding="utf-8")))
    cfg["window"].update(start="2025-05-01", end="2025-05-14", as_of="2025-05-14")
    return build(cfg), cfg


def test_write_simworld_includes_empty_operational_tables(small_world, tmp_path):
    """重建出的 sim 库直接就能承接写动作：三张运营态表存在且为空（从零记录）。"""
    world, cfg = small_world
    db = tmp_path / "sw.sqlite"
    write_simworld(world, cfg, str(db))
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for t in OPERATIONAL_TABLES:
            assert t in tables, f"重建库缺运营态表 {t}——sim 世界写动作将再度 HTTP 500（V19 复发）"
            assert con.execute(f"SELECT count(*) FROM {t}").fetchone()[0] == 0, \
                f"{t} 应为空表（运营态从零记录，生成物不预填审计）"
        # action_log 列集须含 trace_id：app.actions._log 主路径按 9 列写入（G-Trace 对齐）
        cols = {c[1] for c in con.execute("PRAGMA table_info(action_log)")}
        assert "trace_id" in cols, "action_log 缺 trace_id 列——_log 将走 8 列回落，G-Trace 血缘断"
        # 接缝列（V22 批A 教训的另一半：重建后 simworld 需补 version/tenant_id + 版本触发器，
        # 否则乐观并发/多租户接缝在 sim 世界静默缺位）——重建即带，不再依赖手动补跑迁移
        task_cols = {c[1] for c in con.execute("PRAGMA table_info(tasks)")}
        assert {"version", "tenant_id"} <= task_cols, "重建库 tasks 缺接缝列 version/tenant_id"
        assert con.execute("SELECT count(*) FROM sqlite_master WHERE type='trigger' "
                           "AND name='trg_tasks_version'").fetchone()[0] == 1, \
            "重建库缺 tasks 版本触发器——乐观并发 version 不再自增"
    finally:
        con.close()


def test_write_simworld_stays_byte_deterministic(small_world, tmp_path):
    """同一世界写两次逐字节一致——运营态表接线不得破坏 sim.verify 的复现性铁律。"""
    world, cfg = small_world
    p1, p2 = tmp_path / "a.sqlite", tmp_path / "b.sqlite"
    write_simworld(world, cfg, str(p1))
    write_simworld(world, cfg, str(p2))
    assert p1.read_bytes() == p2.read_bytes()
