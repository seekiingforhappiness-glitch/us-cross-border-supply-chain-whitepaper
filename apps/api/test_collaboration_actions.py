#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""apps/api/test_collaboration_actions.py —— 协作流人类协调通道验收（V22②）。

POST /collaboration/threads/{coordination_id}/actions/{action_name}：CL1 五个对既有线程的写动作
（催办 record_outreach / 记回应 record_response / 升级 escalate_coordination / 达成 resolve_coordination /
谈崩 mark_dead_ended）的人类 HTTP 通道。

隔离纪律（同 test_waveU.py）：绝不碰真库——data/ontology.sqlite、data/simworld.sqlite 各拷一份
**保留原文件名**的临时副本（原名保证 _infer_world 推断正确），monkeypatch main.DEFAULT_DB_PATH /
SIMWORLD_DB_PATH 指向副本、摘掉 dependency_overrides、清 ONTOLOGY_DB 环境变量——X-World 请求头的
真实解析链路（读头 → 选库 → 写库）被完整覆盖，而非绕开它换路径。

"现查现算对照，勿硬编码具体值"：锚点线程（awaiting / resolved / escalated）运行时从临时库现查发现，
断言期望值当场对库跑 SQL（followup_count 前后差、审计行数差），不誊抄字面量。

覆盖（任务书 §测试）：
  ① sim 世界成功路径：催办 awaiting 线程 → 200，next_action_due 更新 + followup_count++ + audit 落账（actor 透传）。
  ② verify 世界成功路径：同款催办 → 200 + 落库 + 审计（双世界写通道都真实可用）。
  ③ 越权（manager ∉ COORD_PERMS.ManageCoordination）→ 403 + denied 审计（coordination_actions._denied 语义）。
  ④ 非法状态转换：对 resolved（终态）线程催办 → 422 白话（"非法转移"）+ rejected 审计 + 线程未被改写。
  ⑤ 幂等重放：同 Idempotency-Key 双发 → 第二次原样返回首次结果、不重复执行（followup_count 只 +1，
     commands 台账该键恰 1 行）。
  ⑥ 缺 X-Actor → 422（人类通道必须带真实操作人 id，系统不代填）。
  ⑦ 安全红线不变量：OpenCoordination（开新线程，本批不做）→ 404；未知动作 → 404；五动作从不进 AI 工具面
     build_tool_defs；五动作在 /actions（AI 面）维持 404；Pascal / snake 两种拼法都可路由（RecordResponse
     Pascal 成功路径顺带覆盖第二动作）。
"""
from __future__ import annotations

import shutil
import sqlite3

import pytest
from fastapi.testclient import TestClient

import apps.api.main as apimain
from app.coordination_actions import COORD_PERMS
from apps.api.main import REPO_ROOT, app, get_db_path
from pipeline.ontology_runtime import build_tool_defs, load_ontology, snake_case

REPO_DB = REPO_ROOT / "data" / "ontology.sqlite"
SIM_DB = REPO_ROOT / "data" / "simworld.sqlite"

OPS_ACTOR = "u-ops-us"          # demo 运营演员（ops ∈ ManageCoordination，协调通道 happy path）
MANAGER_ACTOR = "u-manager-us"  # demo 经理演员（manager ∉ ManageCoordination——用于越权 403 路径）
SIM = {"X-World": "sim"}
VERIFY = {"X-World": "verify"}
COORD_ACTIONS = ("RecordOutreach", "RecordResponse", "EscalateCoordination",
                 "ResolveCoordination", "MarkDeadEnded")


# ═══════════════════════════════════════════════════════════════════════════
# fixture：双世界临时副本 + monkeypatch 两库常量（走 X-World 真解析链路，同 test_waveU.py）
# ═══════════════════════════════════════════════════════════════════════════
@pytest.fixture(scope="module")
def api(tmp_path_factory):
    assert REPO_DB.exists() and SIM_DB.exists(), "需先跑 datagen/build_ontology/seed + sim.backfill"
    d = tmp_path_factory.mktemp("coordactions")
    vpath = d / "ontology.sqlite"      # 原名 → _infer_world → verification
    spath = d / "simworld.sqlite"      # 原名 → _infer_world → simulation
    shutil.copy(REPO_DB, vpath)
    shutil.copy(SIM_DB, spath)

    mp = pytest.MonkeyPatch()
    mp.setattr(apimain, "DEFAULT_DB_PATH", vpath)
    mp.setattr(apimain, "SIMWORLD_DB_PATH", spath)
    mp.delenv("ONTOLOGY_DB", raising=False)
    mp.delenv("ONTOLOGY_DB_PATH", raising=False)
    saved = app.dependency_overrides.pop(get_db_path, None)

    vcon = sqlite3.connect(vpath); vcon.row_factory = sqlite3.Row
    scon = sqlite3.connect(spath); scon.row_factory = sqlite3.Row
    try:
        with TestClient(app) as c:
            yield c, vcon, scon
    finally:
        vcon.close(); scon.close()
        if saved is not None:
            app.dependency_overrides[get_db_path] = saved
        mp.undo()


def _thread_in_state(con: sqlite3.Connection, state: str, exclude: set[str] | None = None) -> dict:
    """现查一条指定状态的线程做锚点（勿硬编码 id）。exclude 排除已被前面用例改过状态的线程。"""
    ph = ""
    params: list = [state]
    if exclude:
        ph = f" AND coordination_id NOT IN ({','.join('?' * len(exclude))})"
        params += sorted(exclude)
    row = con.execute(
        f"SELECT * FROM coordination_threads WHERE state=?{ph} ORDER BY coordination_id LIMIT 1",
        params).fetchone()
    assert row is not None, f"临时库应有 state={state} 的协调线程（演示数据前置）"
    return dict(row)


def _audit_count(con: sqlite3.Connection, action: str, target: str, result_like: str) -> int:
    return con.execute(
        "SELECT count(*) FROM action_log WHERE action=? AND target_object_id=? AND result LIKE ?",
        (action, target, result_like)).fetchone()[0]


_used_sim_ids: set[str] = set()   # 已被写用例占用的 sim 线程（用例间锚点互不相交）


# ═══════════════════════════════════════════════════════════════════════════
# ① sim 成功路径：催办 awaiting 线程 → 200 + next_action_due 更新 + followup_count++ + 审计落账
# ═══════════════════════════════════════════════════════════════════════════
def test_sim_record_outreach_success_updates_due_and_audits(api):
    client, _vcon, scon = api
    t = _thread_in_state(scon, "awaiting")
    _used_sim_ids.add(t["coordination_id"])
    new_due = "2026-08-20"
    assert t["next_action_due"] != new_due, "锚点线程现有截止日不应恰等于测试新截止日（否则断言失真）"
    before_ok = _audit_count(scon, "RecordOutreach", t["coordination_id"], "ok")

    r = client.post(
        f"/collaboration/threads/{t['coordination_id']}/actions/record_outreach",
        json={"next_action_due": new_due, "note": "驾驶舱催办（测试）"},
        headers={**SIM, "X-Role": "ops", "X-Actor": OPS_ACTOR})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["object_id"] == t["coordination_id"]

    after = scon.execute("SELECT * FROM coordination_threads WHERE coordination_id=?",
                         (t["coordination_id"],)).fetchone()
    assert after["next_action_due"] == new_due, "催办必须重设 next_action_due"
    assert after["followup_count"] == t["followup_count"] + 1, "催办必须 followup_count++"
    assert after["state"] == "awaiting", "record_outreach 状态保持 awaiting"

    assert _audit_count(scon, "RecordOutreach", t["coordination_id"], "ok") == before_ok + 1, \
        "成功催办必须走 coordination_actions 既有 _log 审计路径落账"
    log = scon.execute(
        "SELECT actor, role FROM action_log WHERE action='RecordOutreach' AND target_object_id=? "
        "AND result='ok' ORDER BY log_id DESC LIMIT 1", (t["coordination_id"],)).fetchone()
    assert log["actor"] == OPS_ACTOR, "审计 actor 必须是真实操作人 id（X-Actor 透传，非常量）"
    assert log["role"] == "ops"


# ═══════════════════════════════════════════════════════════════════════════
# ② verify 成功路径：同款催办 → 200 + 落库 + 审计（双世界写通道都真实可用）
# ═══════════════════════════════════════════════════════════════════════════
def test_verify_record_outreach_success(api):
    client, vcon, _scon = api
    t = _thread_in_state(vcon, "awaiting")
    new_due = "2026-08-22"
    assert t["next_action_due"] != new_due

    r = client.post(
        f"/collaboration/threads/{t['coordination_id']}/actions/record_outreach",
        json={"next_action_due": new_due, "note": "验证世界催办（测试）"},
        headers={**VERIFY, "X-Role": "cs", "X-Actor": "u-cs-us"})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True

    after = vcon.execute("SELECT next_action_due, followup_count FROM coordination_threads "
                         "WHERE coordination_id=?", (t["coordination_id"],)).fetchone()
    assert after["next_action_due"] == new_due
    assert after["followup_count"] == t["followup_count"] + 1
    assert _audit_count(vcon, "RecordOutreach", t["coordination_id"], "ok") >= 1


# ═══════════════════════════════════════════════════════════════════════════
# ③ 越权：manager ∉ COORD_PERMS.ManageCoordination → 403 + denied 审计（app 层 _denied 语义）
# ═══════════════════════════════════════════════════════════════════════════
def test_unauthorized_role_403_with_denied_audit(api):
    client, _vcon, scon = api
    # 前置自检：manager 确实不在协调权限组（若本体变更此集合，测试应当显式失败暴露）
    assert "manager" not in COORD_PERMS["ManageCoordination"], \
        f"前置失效：manager 竟在 ManageCoordination {sorted(COORD_PERMS['ManageCoordination'])} 中"
    t = _thread_in_state(scon, "awaiting", exclude=_used_sim_ids)
    _used_sim_ids.add(t["coordination_id"])
    before_denied = _audit_count(scon, "RecordOutreach", t["coordination_id"], "denied%")

    r = client.post(
        f"/collaboration/threads/{t['coordination_id']}/actions/record_outreach",
        json={"next_action_due": "2026-08-21", "note": "经理越权催办（应 403）"},
        headers={**SIM, "X-Role": "manager", "X-Actor": MANAGER_ACTOR})
    assert r.status_code == 403, r.text
    assert "权限" in r.json()["detail"] or "无权" in r.json()["detail"]

    assert _audit_count(scon, "RecordOutreach", t["coordination_id"], "denied%") == before_denied + 1, \
        "越权必须走 coordination_actions 既有 _denied() 审计路径留痕"
    after = scon.execute("SELECT followup_count, next_action_due FROM coordination_threads "
                         "WHERE coordination_id=?", (t["coordination_id"],)).fetchone()
    assert after["followup_count"] == t["followup_count"], "越权请求不得改写线程"
    assert after["next_action_due"] == t["next_action_due"]


# ═══════════════════════════════════════════════════════════════════════════
# ④ 非法状态转换：对 resolved（终态）线程催办 → 422 白话 + rejected 审计 + 线程未改写
# ═══════════════════════════════════════════════════════════════════════════
def test_illegal_transition_on_resolved_thread_422(api):
    client, _vcon, scon = api
    t = _thread_in_state(scon, "resolved")
    before_rej = _audit_count(scon, "RecordOutreach", t["coordination_id"], "rejected%")

    r = client.post(
        f"/collaboration/threads/{t['coordination_id']}/actions/record_outreach",
        json={"next_action_due": "2026-08-25", "note": "对终态线程催办（应被拒）"},
        headers={**SIM, "X-Role": "ops", "X-Actor": OPS_ACTOR})
    assert r.status_code == 422, r.text
    assert "非法转移" in r.json()["detail"], f"应携 app 层状态机白话错误原文：{r.text}"

    assert _audit_count(scon, "RecordOutreach", t["coordination_id"], "rejected%") == before_rej + 1, \
        "非法转移必须走 coordination_actions 既有 _fail() 审计路径留痕"
    after = scon.execute("SELECT state, followup_count FROM coordination_threads "
                         "WHERE coordination_id=?", (t["coordination_id"],)).fetchone()
    assert after["state"] == "resolved" and after["followup_count"] == t["followup_count"], \
        "被拒请求不得改写终态线程"


# ═══════════════════════════════════════════════════════════════════════════
# ⑤ 幂等重放：同 Idempotency-Key 双发 → 第二次原样返回首次结果、不重复执行
# ═══════════════════════════════════════════════════════════════════════════
def test_idempotency_key_replay_executes_once(api):
    client, _vcon, scon = api
    t = _thread_in_state(scon, "awaiting", exclude=_used_sim_ids)
    _used_sim_ids.add(t["coordination_id"])
    key = "idem-coord-test-0001"
    payload = {"next_action_due": "2026-08-28", "note": "幂等双发（测试）"}
    hdrs = {**SIM, "X-Role": "ops", "X-Actor": OPS_ACTOR, "Idempotency-Key": key}
    url = f"/collaboration/threads/{t['coordination_id']}/actions/record_outreach"

    r1 = client.post(url, json=payload, headers=hdrs)
    assert r1.status_code == 200, r1.text
    r2 = client.post(url, json=payload, headers=hdrs)
    assert r2.status_code == 200, r2.text
    assert r2.json() == r1.json(), "同幂等键重放必须原样返回首次结果"

    after = scon.execute("SELECT followup_count FROM coordination_threads WHERE coordination_id=?",
                         (t["coordination_id"],)).fetchone()
    assert after["followup_count"] == t["followup_count"] + 1, \
        "幂等键命中重放不得重复执行（followup_count 只允许 +1）"
    assert scon.execute("SELECT count(*) FROM commands WHERE idempotency_key=?",
                        (key,)).fetchone()[0] == 1, "commands 台账同键恰一行（claim-first 占坑）"
    assert _audit_count(scon, "RecordOutreach", t["coordination_id"], "ok") == 1, \
        "该线程成功审计恰一行——重放未二次走动作函数"


# ═══════════════════════════════════════════════════════════════════════════
# ⑥ 缺 X-Actor → 422（人类通道必须带真实操作人 id）
# ═══════════════════════════════════════════════════════════════════════════
def test_missing_x_actor_422(api):
    client, _vcon, scon = api
    t = _thread_in_state(scon, "awaiting", exclude=_used_sim_ids)   # 只读锚点，不改写不占用
    r = client.post(
        f"/collaboration/threads/{t['coordination_id']}/actions/record_outreach",
        json={"next_action_due": "2026-08-30"},
        headers={**SIM, "X-Role": "ops"})
    assert r.status_code == 422, r.text
    assert "X-Actor" in r.json()["detail"]


# ═══════════════════════════════════════════════════════════════════════════
# ⑦ 安全红线不变量：白名单边界（OpenCoordination/未知动作 404）+ AI 面零暴露 + 双拼法路由
# ═══════════════════════════════════════════════════════════════════════════
def test_channel_whitelist_and_ai_face_invariants(api):
    client, _vcon, scon = api
    onto = load_ontology()
    coord_snake = {snake_case(n) for n in COORD_ACTIONS}

    # (a) OpenCoordination（开新线程，本批不做）→ 404，白话说明去处；未知动作同样 404
    t = _thread_in_state(scon, "awaiting", exclude=_used_sim_ids)
    for bad in ("OpenCoordination", "open_coordination", "no_such_action"):
        r = client.post(f"/collaboration/threads/{t['coordination_id']}/actions/{bad}",
                        json={}, headers={**SIM, "X-Role": "ops", "X-Actor": OPS_ACTOR})
        assert r.status_code == 404, f"{bad} 应 404（白名单外，路由生成层就不认），当前 {r.status_code}"
    assert "OpenCoordination" in client.post(
        f"/collaboration/threads/{t['coordination_id']}/actions/open_coordination",
        json={}, headers={**SIM, "X-Role": "ops", "X-Actor": OPS_ACTOR}).json()["detail"], \
        "404 文案应如实说明 OpenCoordination 未接、去处在哪"

    # (b) AI 面零暴露：五动作 ∩ build_tool_defs 工具名 == ∅（协调写动作从不进 MCP/TOOL_DEFS）
    tool_names = {td["name"] for td in build_tool_defs(onto)}
    leak = tool_names & coord_snake
    assert leak == set(), f"协调写动作泄漏进 AI 工具面 build_tool_defs：{sorted(leak)}"

    # (c) 五动作在 /actions（AI 面平行验证通道）维持 404——Pascal 与 snake 两种拼法都 404
    for name in COORD_ACTIONS:
        assert client.post(f"/actions/{name}", json={}).status_code == 404, f"/actions/{name} 应 404"
        assert client.post(f"/actions/{snake_case(name)}", json={}).status_code == 404

    # (d) 本通道 Pascal 拼法同样可路由：RecordResponse（Pascal）记回应 escalated 线程 → responded
    #     （顺带覆盖第二动作的成功路径与状态机 escalated→responded 转移）
    te = _thread_in_state(scon, "escalated", exclude=_used_sim_ids)
    _used_sim_ids.add(te["coordination_id"])
    r = client.post(
        f"/collaboration/threads/{te['coordination_id']}/actions/RecordResponse",
        json={"last_response": "工厂确认可改期到 8/30 出货（测试）"},
        headers={**SIM, "X-Role": "ops", "X-Actor": OPS_ACTOR})
    assert r.status_code == 200, r.text
    after = scon.execute("SELECT state, last_response FROM coordination_threads "
                         "WHERE coordination_id=?", (te["coordination_id"],)).fetchone()
    assert after["state"] == "responded"
    assert "8/30" in (after["last_response"] or "")


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
