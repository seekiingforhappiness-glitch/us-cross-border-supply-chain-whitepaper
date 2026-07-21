#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""apps/api/test_decisions_sim.py —— 模拟世界（X-World: sim）人类决策通道回归（V19 勘误看守）。

缘起（2026-07-19，V22 驾驶舱验证链）：sim 库缺 action_log 时，POST /decisions/ApproveMitigation
在 app.actions._log 撞 "no such table: action_log"，except 分支调 _fail、其内 _log 再抛同错 →
异常穿透 → HTTP 500（V19 勘误 6f91b1c，Daniel 实测暴露）。V22④ 驾驶舱默认世界已切 sim，
默认演示路径的审批因此全断。修复 = 存量库迁移（pipeline.apply_seam_columns.
ensure_operational_tables）+ 重建管线接线（sim/store.py，防复发看守见 sim/test_store.py）。
本文件在 API 层看守端到端行为：X-World: sim 下带理由批准一条待批任务 → 200，
且审计行落在 **sim 库** action_log（既不 500、也不静默丢审计）。
若 sim 库被重建后丢了运营态表，本用例会以 500 变红——如实暴露，指向 sim.backfill/迁移。

隔离纪律（同 test_waveU）：data/simworld.sqlite 拷临时副本**保留原文件名** + monkeypatch
apps.api.main.SIMWORLD_DB_PATH + 摘 dependency_overrides，使 X-World 请求头走真实解析链路；
全程绝不写真库。
"""
from __future__ import annotations

import json
import shutil
import sqlite3

import pytest
from fastapi.testclient import TestClient

import apps.api.main as apimain
from apps.api.main import REPO_ROOT, app, get_db_path

SIM_DB = REPO_ROOT / "data" / "simworld.sqlite"
MANAGER_ACTOR = "u-manager-us"     # demo 经理演员（≠ sim 提案人 sim-ai → maker-checker 可过）


@pytest.fixture(scope="module")
def sim_api(tmp_path_factory):
    """sim 库临时副本（原名 → _infer_world → simulation）+ monkeypatch SIMWORLD_DB_PATH +
    摘 get_db_path override，让 X-World: sim 走真实生产解析链路。yield (client, sim_con)。"""
    assert SIM_DB.exists(), f"{SIM_DB} 不存在——需先跑 python3 -m sim.backfill"
    spath = tmp_path_factory.mktemp("decisions_sim") / "simworld.sqlite"
    shutil.copy(SIM_DB, spath)

    mp = pytest.MonkeyPatch()
    mp.setattr(apimain, "SIMWORLD_DB_PATH", spath)
    saved = app.dependency_overrides.pop(get_db_path, None)

    scon = sqlite3.connect(spath)
    scon.row_factory = sqlite3.Row
    try:
        with TestClient(app) as c:
            yield c, scon
    finally:
        scon.close()
        if saved is not None:
            app.dependency_overrides[get_db_path] = saved
        mp.undo()


def _pick_pending_task(scon) -> str:
    """现查一条待批任务；活库可能被演示批完清零（2026-07-16 test_decisions 实发同款），
    则从任意历史提案任务克隆整行自愈种子（临时副本内，绝不碰真库）。"""
    row = scon.execute(
        "SELECT task_id FROM tasks WHERE approval_status='pending' AND proposal_actor_id IS NOT NULL "
        "AND proposed_action IS NOT NULL ORDER BY task_id LIMIT 1").fetchone()
    if row:
        return row["task_id"]
    donor = scon.execute(
        "SELECT * FROM tasks WHERE proposal_actor_id IS NOT NULL AND proposed_action IS NOT NULL "
        "ORDER BY task_id LIMIT 1").fetchone()
    assert donor, "sim 库连历史提案任务都没有——sim.backfill 未生成 AI 回路提案，测试前提缺失"
    seed = dict(donor)
    seed.update(task_id="TSK-SIM-TEST-SEED", approval_status="pending", status="in_progress",
                proposal_actor_id="sim-ai", approved_by_role=None, action_taken=None)
    cols = list(seed.keys())
    scon.execute(f"INSERT INTO tasks ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                 [seed[c] for c in cols])
    scon.commit()
    return seed["task_id"]


def test_sim_world_approve_returns_200_and_audits_in_sim_db(sim_api):
    """X-World: sim 下带理由批准待批任务 → 200 + ok=True + 任务落库 approved +
    审计行（含理由）写进 sim 库 action_log。V19 前此路径 = HTTP 500。"""
    client, scon = sim_api
    tid = _pick_pending_task(scon)
    log_before = scon.execute("SELECT count(*) FROM action_log").fetchone()[0]
    reason = "延误已复核，按提案处置（sim 世界审批回归测试）"

    resp = client.post(
        "/decisions/ApproveMitigation",
        json={"task_id": tid, "decision": "approved", "comment": reason},
        headers={"X-World": "sim", "X-Role": "manager", "X-Actor": MANAGER_ACTOR})

    assert resp.status_code == 200, f"sim 世界审批应 200（V19 前=500）：{resp.status_code} {resp.text}"
    body = resp.json()
    assert body["ok"] is True and body["object_id"] == tid

    # 落库：任务 approved（写进的是 sim 副本，不是验证世界）
    after = scon.execute("SELECT approval_status, status FROM tasks WHERE task_id=?", (tid,)).fetchone()
    assert after["approval_status"] == "approved" and after["status"] == "done"

    # 审计可查：sim 库 action_log 新增 ApproveMitigation ok 行，理由随 params 留痕（审计不可静默丢红线）
    log_after = scon.execute("SELECT count(*) FROM action_log").fetchone()[0]
    assert log_after > log_before, "审批成功但 sim 库 action_log 未新增行——审计被静默丢弃"
    audit = scon.execute(
        "SELECT params_json, result FROM action_log WHERE action='ApproveMitigation' "
        "AND target_object_id=? ORDER BY log_id DESC LIMIT 1", (tid,)).fetchone()
    assert audit is not None and audit["result"] == "ok"
    assert reason in (audit["params_json"] or ""), "审批理由未落 action_log params_json"
    assert json.loads(audit["params_json"])["decision"] == "approved"
