#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""apps/api/test_decisions.py —— 人类决策通道 POST /decisions/{name} 验收用例（A-1，V13①）。

隔离纪律（同 apps/api/test_api.py）：先把 data/ontology.sqlite 拷到临时文件，测试全程只读写临时文件，
经 app.dependency_overrides 把 apps.api.main.get_db_path 换成临时路径——**绝不写真库**（真值不动红线）。
待批任务锚点在模块级 autouse fixture 里一次性从"尚未被本文件任何写用例改写过"的库现查/快照，
maker-checker、无权两条用例用**快照克隆**出独立任务（新 task_id），与执行顺序无关、互不相交。

覆盖（任务书 §测试）：
  ① manager 批准待批任务 → 200，任务状态落库变化，action_log 新行 trace_id 为 NULL（人类通道无 AI 追踪号）。
  ② 提案者=审批者 → maker-checker 拒绝（4xx）。
  ③ 无权角色 → 403。
  ④ 缺 X-Actor → 422。
  ⑤ 安全红线不变量：frozen ∩ build_tool_defs 暴露工具名 == ∅；四冻结动作 /actions 维持 404、
     /decisions 命中白名单（非 404）；非冻结动作在 /decisions 维持 404。
"""
from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from apps.api.main import REPO_ROOT, app, get_db_path
from pipeline.ontology_runtime import (build_forbidden_tools, build_tool_defs,
                                       load_ontology, snake_case)

REPO_DB = REPO_ROOT / "data" / "ontology.sqlite"
MANAGER_ACTOR = "u-manager-us"     # demo 经理演员（app/actions.py DEMO_ROSTER，能审批 ApproveMitigation）
OPS_ACTOR = "u-ops-us"             # demo 运营演员（无审批权，用于 403 路径）


# ═══════════════════════════════════════════════════════════════════════════
# fixtures：临时库 + TestClient + 一次性快照的待批任务锚点
# ═══════════════════════════════════════════════════════════════════════════
@pytest.fixture(scope="module")
def tmp_db_path(tmp_path_factory):
    assert REPO_DB.exists(), f"{REPO_DB} 不存在——需先跑 datagen/build_ontology/seed_demo_ops"
    dest = tmp_path_factory.mktemp("decisions_test_db") / "ontology.sqlite"
    shutil.copy(REPO_DB, dest)     # 纯字节读源库 + 写临时副本，绝不触碰真库
    return str(dest)


@pytest.fixture(scope="module")
def client(tmp_db_path):
    app.dependency_overrides[get_db_path] = lambda: tmp_db_path
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(get_db_path, None)


@pytest.fixture(scope="module")
def con(tmp_db_path):
    c = sqlite3.connect(tmp_db_path)
    c.row_factory = sqlite3.Row
    yield c
    c.close()


@pytest.fixture(scope="module", autouse=True)
def anchors(con):
    """先于本模块任何写用例执行：一次性快照待批任务（approval_status='pending' 且带提案人），
    存下第一条的**完整行**作克隆模板（快照后即使被某用例改写，模板数据也不变，故各用例互不相交）。"""
    rows = con.execute(
        "SELECT * FROM tasks WHERE approval_status='pending' AND proposal_actor_id IS NOT NULL "
        "AND proposed_action IS NOT NULL ORDER BY task_id").fetchall()
    if not rows:
        # 自愈种子：演示库的待批余量是易变状态（人在驾驶舱批完即清零，2026-07-16 实发）。
        # 测试不该依赖它——从任意已审批任务克隆出一条 pending 模板（临时副本内，绝不碰真库）。
        donor = con.execute(
            "SELECT * FROM tasks WHERE proposal_actor_id IS NOT NULL "
            "AND proposed_action IS NOT NULL ORDER BY task_id LIMIT 1").fetchone()
        assert donor, "测试库连历史提案任务都没有——库未跑过 seed_demo_ops，回归链缺步（勘误#2）"
        seed = dict(donor)
        seed.update(task_id="TSK-TEST-DEC-SEED", approval_status="pending",
                    approved_by_role=None, action_taken=None, status="in_progress",
                    proposal_actor_id="u-ops-us")
        cols = list(seed.keys())
        con.execute(f"INSERT INTO tasks ({','.join(cols)}) VALUES ({','.join('?'*len(cols))})",
                    [seed[c] for c in cols])
        con.commit()
        rows = con.execute(
            "SELECT * FROM tasks WHERE approval_status='pending' AND proposal_actor_id IS NOT NULL "
            "AND proposed_action IS NOT NULL ORDER BY task_id").fetchall()
    template = dict(rows[0])
    assert template["proposal_actor_id"] != MANAGER_ACTOR, \
        "happy-path 需提案人≠审批人：模板提案人不应恰是经理演员"
    return {"pending_task_ids": [r["task_id"] for r in rows], "template": template}


def _clone_pending_task(con, template: dict, new_task_id: str, proposal_actor_id: str | None = None):
    """按快照模板克隆一条待批任务（新 task_id，可选覆盖提案人）——供 maker-checker/无权用例造独立目标。
    克隆整行（含所有 NOT NULL 列），故满足表约束；与真实待批任务同构（approval_status='pending'）。"""
    row = dict(template)
    row["task_id"] = new_task_id
    if proposal_actor_id is not None:
        row["proposal_actor_id"] = proposal_actor_id
    cols = list(row.keys())
    placeholders = ",".join("?" * len(cols))
    con.execute(f"INSERT INTO tasks ({','.join(cols)}) VALUES ({placeholders})",
                [row[c] for c in cols])
    con.commit()
    return new_task_id


# ═══════════════════════════════════════════════════════════════════════════
# ① manager 批准待批任务 → 200 + 落库 + action_log.trace_id 为 NULL（人类通道无 AI 追踪号）
# ═══════════════════════════════════════════════════════════════════════════
def test_manager_approve_pending_task_200_and_trace_id_null(client, con, anchors):
    tid = anchors["pending_task_ids"][0]
    # 前置：确认这条任务此刻确为待批（现查现算，不誊抄）
    before = con.execute("SELECT approval_status, status FROM tasks WHERE task_id=?", (tid,)).fetchone()
    assert before["approval_status"] == "pending"

    resp = client.post(
        "/decisions/ApproveMitigation",
        json={"task_id": tid, "decision": "approved", "comment": "驾驶舱批准（测试）"},
        headers={"X-Role": "manager", "X-Actor": MANAGER_ACTOR})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["object_id"] == tid

    # 任务状态落库变化：pending→approved、→done
    after = con.execute("SELECT approval_status, status FROM tasks WHERE task_id=?", (tid,)).fetchone()
    assert after["approval_status"] == "approved"
    assert after["status"] == "done"

    # action_log 新行：人类通道无 AI 追踪号 → trace_id 为 NULL（G-Trace 血缘语义）
    log = con.execute(
        "SELECT actor, role, trace_id FROM action_log WHERE action='ApproveMitigation' "
        "AND target_object_id=? AND result='ok' ORDER BY log_id DESC LIMIT 1", (tid,)).fetchone()
    assert log is not None, "成功审批必须走 app.actions 既有审计路径留痕"
    assert log["actor"] == MANAGER_ACTOR, "审计 actor 必须是真实决策人 id（X-Actor 透传，非常量）"
    assert log["role"] == "manager"
    assert log["trace_id"] is None, "人类决策通道不设 trace_id → action_log.trace_id 应为 NULL"


# ═══════════════════════════════════════════════════════════════════════════
# ② 提案者=审批者 → maker-checker 拒绝（4xx）；app 层未绕过
# ═══════════════════════════════════════════════════════════════════════════
def test_same_proposer_approver_maker_checker_rejected_4xx(client, con, anchors):
    # 克隆一条待批任务，把提案人改成经理本人 → 经理审批时"提案人==审批人"，maker-checker 应拒
    tid = _clone_pending_task(con, anchors["template"], "TSK-TEST-DEC-MAKER",
                              proposal_actor_id=MANAGER_ACTOR)
    resp = client.post(
        "/decisions/ApproveMitigation",
        json={"task_id": tid, "decision": "approved", "comment": "自批自（应被拒）"},
        headers={"X-Role": "manager", "X-Actor": MANAGER_ACTOR})
    assert 400 <= resp.status_code < 500, resp.text
    assert "maker" in resp.json()["detail"].lower() or "审批边界" in resp.json()["detail"]

    # 未绕过 app 层：任务仍为待批（maker-checker 前置失败在状态变更前返回）
    row = con.execute("SELECT approval_status, status FROM tasks WHERE task_id=?", (tid,)).fetchone()
    assert row["approval_status"] == "pending"


# ═══════════════════════════════════════════════════════════════════════════
# ③ 无权角色 → 403 且 app 层审计已记录（denied）
# ═══════════════════════════════════════════════════════════════════════════
def test_unauthorized_role_403_with_audit(client, con, anchors):
    tid = _clone_pending_task(con, anchors["template"], "TSK-TEST-DEC-403")
    before = con.execute(
        "SELECT count(*) FROM action_log WHERE action='ApproveMitigation' "
        "AND target_object_id=? AND result LIKE 'denied%'", (tid,)).fetchone()[0]

    resp = client.post(
        "/decisions/ApproveMitigation",
        json={"task_id": tid, "decision": "approved", "comment": "ops 越权批（应 403）"},
        headers={"X-Role": "ops", "X-Actor": OPS_ACTOR})
    assert resp.status_code == 403, resp.text

    after = con.execute(
        "SELECT count(*) FROM action_log WHERE action='ApproveMitigation' "
        "AND target_object_id=? AND result LIKE 'denied%'", (tid,)).fetchone()[0]
    assert after == before + 1, "无权调用必须走 app.actions 既有 _denied() 审计路径留痕"

    # 任务未被改写（拒绝路径不触发状态迁移）
    row = con.execute("SELECT approval_status FROM tasks WHERE task_id=?", (tid,)).fetchone()
    assert row["approval_status"] == "pending"


# ═══════════════════════════════════════════════════════════════════════════
# ④ 缺 X-Actor → 422（人类通道必须带真实决策人 id，系统不代填）
# ═══════════════════════════════════════════════════════════════════════════
def test_missing_x_actor_422(client, anchors):
    # 命中冻结区白名单（否则会先 404），但不带 X-Actor → 应 422，且报错为白话中文
    resp = client.post(
        "/decisions/ApproveMitigation",
        json={"task_id": anchors["pending_task_ids"][0], "decision": "approved", "comment": "无身份"},
        headers={"X-Role": "manager"})
    assert resp.status_code == 422, resp.text
    assert "X-Actor" in resp.json()["detail"]


# ═══════════════════════════════════════════════════════════════════════════
# ⑤ 安全红线不变量：冻结区永不进 AI 工具面 + 双通道分工（/actions 维持 404、/decisions 命中白名单）
# ═══════════════════════════════════════════════════════════════════════════
def test_frozen_channel_security_invariants(client):
    onto = load_ontology()
    frozen_names = {a["name"] for a in onto["actions"] if a.get("ai_executable") == "frozen"}
    assert frozen_names, "本体应有冻结区动作"
    frozen_snake = {snake_case(n) for n in frozen_names}

    # (a) 核心不变量：frozen 动作集合 ∩ build_tool_defs 暴露工具名集合 == ∅
    #     （人类决策通道暴露这四个动作，故必须证明它们从不出现在 AI 的工具面 build_tool_defs）
    tool_names = {t["name"] for t in build_tool_defs(onto)}
    leak = tool_names & frozen_snake
    assert leak == set(), f"冻结区动作泄漏进 AI 工具面 build_tool_defs：{sorted(leak)}"
    # 双保险：FORBIDDEN 拉黑集恰为冻结区四动作的 snake（纵深防御名单同源）
    assert build_forbidden_tools(onto) == frozen_snake

    # (b) 四个冻结动作在 /actions（AI 面平行验证通道）维持 404——Pascal 与 snake 两种拼法都 404
    for name in frozen_names:
        assert client.post(f"/actions/{name}", json={}).status_code == 404, f"/actions/{name} 应 404"
        assert client.post(f"/actions/{snake_case(name)}", json={}).status_code == 404

    # (c) 同样四个动作在 /decisions（人类通道）**命中白名单**（非 404）——两种拼法都可路由；
    #     此处不带 X-Actor，命中后应走到 422（证明"路由存在 + 白名单命中"，而非 404 落空）
    for name in frozen_names:
        for alias in (name, snake_case(name)):
            r = client.post(f"/decisions/{alias}", json={}, headers={"X-Role": "manager"})
            assert r.status_code != 404, f"/decisions/{alias} 应在人类通道白名单内（当前 {r.status_code}）"
            assert r.status_code == 422, f"/decisions/{alias} 缺 X-Actor 应 422（当前 {r.status_code}）"

    # (d) 非冻结动作在 /decisions 维持 404（AI 面动作如 AssignTask 不该进人类决策通道）
    r = client.post("/decisions/AssignTask", json={},
                    headers={"X-Role": "ops", "X-Actor": OPS_ACTOR})
    assert r.status_code == 404, "非冻结动作不在人类决策通道白名单，应 404"


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
