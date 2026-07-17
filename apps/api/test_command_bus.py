#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""apps/api/test_command_bus.py —— 波2 Command 写总线验收用例（spec §一 写总线核心 + §二 API 契约硬化）。

隔离纪律（同 apps/api/test_api.py）：先把 data/ontology.sqlite 拷到临时文件，测试全程只读写临时文件，
**绝不写真库**（业务双库 md5 不因测试改变）。总线单元用例用 function 级新副本（各自独立、互不干扰）；
API 集成用例用 module 级临时库 + TestClient（app.dependency_overrides 换库，fastapi 标准做法）。

覆盖（任务书 §5 pytest 新用例）：
  ① 幂等双发只执行一次（顺序 + 并发线程双发）——action_func 恰调一次；含 ok=False 首次结果原样复放。
  ② 指纹篡改被拒——propose 落基线后直接改任务 proposal_params，approve 经总线被拒（白话错误，approve 未执行）。
  ③ no_baseline 放行留痕——无 propose 命令记录的任务，approve 放行且 commands 行记 fingerprint_check='no_baseline'。
  ④ 指纹一致放行——未改提案 approve 经总线放行（fingerprint_check='match'）。
  ⑤ 409——幂等键「在飞」（pending 未完成）二次请求 → HTTP 409（写锁复检冲突路径）。
  ⑥ 游标翻页完整性——keyset 逐页并集 = 全量、无重漏；无 cursor 响应结构与升级前逐字节一致。
  ⑦ 错误信封——既有 detail 键保留 + 新增 error 对象；Idempotency-Key 头透传总线。
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.actions as A
from app.command_bus import (execute_command, ensure_commands_table,
                             check_approval_binding, fingerprint_params,
                             FINGERPRINT_MISMATCH_MESSAGE)
from apps.api.main import AS_OF, REPO_ROOT, app, get_db_path

REPO_DB = REPO_ROOT / "data" / "ontology.sqlite"
OPS_ACTOR = "u-ops-us"          # ProposeMitigation 有权
MANAGER_ACTOR = "u-manager-us"  # ApproveMitigation 有权（且 ≠ OPS_ACTOR，满足 maker-checker）
DUE = "2026-08-10T00:00:00Z"


# ═══════════════════════════════════════════════════════════════════════════
# fixtures
# ═══════════════════════════════════════════════════════════════════════════
@pytest.fixture()
def fresh_db(tmp_path):
    """function 级新副本：每个总线单元用例独立一份，互不干扰、可随意改写（真库零污染）。"""
    assert REPO_DB.exists(), f"{REPO_DB} 不存在——需先跑 datagen/build_ontology/seed_demo_ops"
    dest = tmp_path / "ontology.sqlite"
    shutil.copy(REPO_DB, dest)
    return str(dest)


def _one_assigned_task(con) -> str:
    row = con.execute("SELECT task_id FROM tasks WHERE status='assigned' ORDER BY task_id LIMIT 1").fetchone()
    assert row is not None, "测试库需要至少 1 个 assigned 任务（seed_demo_ops 产物）"
    return row[0]


def _one_assignable_risk(con, skip=0) -> str:
    rows = con.execute(
        """SELECT risk_event_id FROM risk_events WHERE status='open' AND risk_event_id NOT IN (
             SELECT risk_event_id FROM tasks WHERE status NOT IN ('done','cancelled'))
           ORDER BY risk_event_id LIMIT ?""", (skip + 1,)).fetchall()
    assert len(rows) > skip, "测试库需要足够的 open 且无非终态任务的风险事件"
    return rows[skip][0]


# ═══════════════════════════════════════════════════════════════════════════
# ① 幂等：顺序双发只执行一次（ok=True）+ 单库只落一行副作用
# ═══════════════════════════════════════════════════════════════════════════
def test_idempotent_sequential_executes_once(fresh_db):
    con = A.connect(fresh_db)
    rid = _one_assignable_risk(con)
    calls = []

    def spy(c, **kw):
        calls.append(1)
        return A.assign_task(c, **kw)

    key = "idem-seq-ok"
    body = {"risk_event_id": rid, "assignee_role": "ops", "priority": "P2", "due_at": DUE}
    r1 = execute_command(con, action="AssignTask", params=body, actor=OPS_ACTOR, role="ops",
                         as_of=AS_OF, action_func=spy, idempotency_key=key)
    r2 = execute_command(con, action="AssignTask", params=body, actor=OPS_ACTOR, role="ops",
                         as_of=AS_OF, action_func=spy, idempotency_key=key)
    assert r1["ok"] is True
    assert r2 == r1, "同 key 二次应原样返回首次结果"
    assert len(calls) == 1, "幂等：action_func 只执行一次"
    n = con.execute("SELECT count(*) FROM tasks WHERE risk_event_id=?", (rid,)).fetchone()[0]
    assert n == 1, "同 key 双发只应新增一个 task"
    con.close()


# ① 幂等：含 ok=False 的首次结果也原样复放（且只执行一次）
def test_idempotent_replay_returns_ok_false_verbatim(fresh_db):
    con = A.connect(fresh_db)
    tid = _one_assigned_task(con)
    calls = []

    def spy(c, **kw):
        calls.append(1)
        return A.propose_mitigation(c, **kw)

    key = "idem-okfalse"
    # accept_delay 需要 {"reason"}，给空 proposal_params → schema 不过 → 动作返回 ok=False
    params = {"task_id": tid, "proposed_action": "accept_delay", "proposal_params": {}}
    r1 = execute_command(con, action="ProposeMitigation", params=params, actor=OPS_ACTOR, role="ops",
                         as_of=AS_OF, action_func=spy, idempotency_key=key)
    r2 = execute_command(con, action="ProposeMitigation", params=params, actor=OPS_ACTOR, role="ops",
                         as_of=AS_OF, action_func=spy, idempotency_key=key)
    assert r1["ok"] is False
    assert r2 == r1, "含 ok=False 的首次结果也应原样复放"
    assert len(calls) == 1
    con.close()


# ① 幂等：并发线程双发只执行一次（claim-first + UNIQUE 挡住第二执行者）
def test_concurrent_double_send_executes_once(fresh_db):
    probe = A.connect(fresh_db)
    rid = _one_assignable_risk(probe)
    probe.close()

    key = "idem-concurrent"
    calls = []
    lock = threading.Lock()
    barrier = threading.Barrier(2)
    results: dict[str, tuple] = {}

    def spy(c, **kw):
        with lock:
            calls.append(1)
        return A.assign_task(c, **kw)

    def worker(name):
        con = A.connect(fresh_db)
        try:
            barrier.wait()  # 两线程尽量同时冲入总线，真实竞争认领
            try:
                r = execute_command(con, action="AssignTask",
                                    params={"risk_event_id": rid, "assignee_role": "ops",
                                            "priority": "P2", "due_at": DUE},
                                    actor=OPS_ACTOR, role="ops", as_of=AS_OF,
                                    action_func=spy, idempotency_key=key)
                results[name] = ("result", r)
            except A._StateConflict as exc:
                results[name] = ("conflict", str(exc))
        finally:
            con.close()

    t1 = threading.Thread(target=worker, args=("A",))
    t2 = threading.Thread(target=worker, args=("B",))
    t1.start(); t2.start(); t1.join(); t2.join()

    assert len(calls) == 1, f"并发双发 action_func 应恰好执行一次，实际 {len(calls)}"
    con = A.connect(fresh_db)
    n = con.execute("SELECT count(*) FROM tasks WHERE risk_event_id=? AND status NOT IN ('done','cancelled')",
                    (rid,)).fetchone()[0]
    con.close()
    assert n == 1, "并发双发只应新增一个 task"
    assert set(results) == {"A", "B"}, "两线程都应有明确结局（结果或 409 冲突）"
    # 恰有一方拿到真实结果（认领者），另一方要么复放同结果、要么在飞冲突
    kinds = [v[0] for v in results.values()]
    assert kinds.count("result") >= 1


# ═══════════════════════════════════════════════════════════════════════════
# ② 指纹篡改被拒（approve 执行前绑提案指纹）
# ═══════════════════════════════════════════════════════════════════════════
def test_fingerprint_tamper_rejects_approve(fresh_db):
    con = A.connect(fresh_db)
    tid = _one_assigned_task(con)
    # propose 经总线 → 落 ProposeMitigation 命令指纹（基线）
    r1 = execute_command(con, action="ProposeMitigation",
                         params={"task_id": tid, "proposed_action": "accept_delay",
                                 "proposal_params": {"reason": "orig"}},
                         actor=OPS_ACTOR, role="ops", as_of=AS_OF, action_func=A.propose_mitigation)
    assert r1["ok"] is True
    # 篡改：审批间隙直接改任务 proposal_params
    con.execute("UPDATE tasks SET proposal_params=? WHERE task_id=?",
                (json.dumps({"reason": "TAMPERED"}), tid))
    con.commit()
    # approve 经总线 → 指纹不一致 → 拒绝（白话错误），approve_mitigation 未执行
    r2 = execute_command(con, action="ApproveMitigation",
                         params={"task_id": tid, "decision": "approved", "comment": "x"},
                         actor=MANAGER_ACTOR, role="manager", as_of=AS_OF,
                         action_func=A.approve_mitigation)
    assert r2["ok"] is False
    assert r2["error"] == FINGERPRINT_MISMATCH_MESSAGE
    row = con.execute("SELECT approval_status FROM tasks WHERE task_id=?", (tid,)).fetchone()
    assert row["approval_status"] == "pending", "被指纹门拦下：approve_mitigation 不应执行（任务仍待批）"
    fc = con.execute("SELECT fingerprint_check, result_status FROM commands "
                     "WHERE action='ApproveMitigation' ORDER BY rowid DESC LIMIT 1").fetchone()
    assert fc["fingerprint_check"] == "mismatch"
    assert fc["result_status"] == "rejected"
    con.close()


# ② 反例：未篡改 → 指纹一致 → 放行（approve 正常执行）
def test_fingerprint_match_allows_approve(fresh_db):
    con = A.connect(fresh_db)
    tid = _one_assigned_task(con)
    r1 = execute_command(con, action="ProposeMitigation",
                         params={"task_id": tid, "proposed_action": "accept_delay",
                                 "proposal_params": {"reason": "orig"}},
                         actor=OPS_ACTOR, role="ops", as_of=AS_OF, action_func=A.propose_mitigation)
    assert r1["ok"] is True
    r2 = execute_command(con, action="ApproveMitigation",
                         params={"task_id": tid, "decision": "approved", "comment": "x"},
                         actor=MANAGER_ACTOR, role="manager", as_of=AS_OF,
                         action_func=A.approve_mitigation)
    assert r2["error"] != FINGERPRINT_MISMATCH_MESSAGE, "未篡改不应被指纹门拦"
    assert r2["ok"] is True
    fc = con.execute("SELECT fingerprint_check FROM commands "
                     "WHERE action='ApproveMitigation' ORDER BY rowid DESC LIMIT 1").fetchone()
    assert fc["fingerprint_check"] == "match"
    con.close()


# ═══════════════════════════════════════════════════════════════════════════
# ③ no_baseline 放行留痕（历史/种子任务无 propose 命令记录）
# ═══════════════════════════════════════════════════════════════════════════
def test_no_baseline_passthrough_records_trace(fresh_db):
    con = A.connect(fresh_db)
    tid = _one_assigned_task(con)
    # 用**直调**（不经总线）造 pending 任务 → 任务有提案但 commands 表无 ProposeMitigation 命令记录
    d = A.propose_mitigation(con, tid, "accept_delay", {"reason": "seed-like"},
                             actor=OPS_ACTOR, role="ops", as_of=AS_OF)
    assert d["ok"] is True
    # approve 经总线 → 查不到 propose 命令 → no_baseline → 放行（不误杀）且留痕
    r = execute_command(con, action="ApproveMitigation",
                        params={"task_id": tid, "decision": "approved", "comment": "ok"},
                        actor=MANAGER_ACTOR, role="manager", as_of=AS_OF,
                        action_func=A.approve_mitigation)
    assert r["error"] != FINGERPRINT_MISMATCH_MESSAGE, "无基线应放行、不得当作篡改误杀"
    fc = con.execute("SELECT fingerprint_check FROM commands "
                     "WHERE action='ApproveMitigation' ORDER BY rowid DESC LIMIT 1").fetchone()
    assert fc["fingerprint_check"] == "no_baseline"
    con.close()


# ③b 绑定纯函数三态直测（隔离于 approve 内部副作用，钉死 no_baseline/match/mismatch 判定）
def test_check_approval_binding_states(fresh_db):
    con = A.connect(fresh_db)
    tid = _one_assigned_task(con)
    ensure_commands_table(con)
    # 无 propose 命令 → no_baseline
    allowed, fc, err = check_approval_binding(con, {"task_id": tid})
    assert (allowed, fc, err) == (True, "no_baseline", None)
    # 经总线 propose 落基线，任务当前提案未改 → match
    execute_command(con, action="ProposeMitigation",
                    params={"task_id": tid, "proposed_action": "accept_delay",
                            "proposal_params": {"reason": "r"}},
                    actor=OPS_ACTOR, role="ops", as_of=AS_OF, action_func=A.propose_mitigation)
    allowed, fc, err = check_approval_binding(con, {"task_id": tid})
    assert (allowed, fc, err) == (True, "match", None)
    # 直接改任务提案 → mismatch
    con.execute("UPDATE tasks SET proposal_params=? WHERE task_id=?",
                (json.dumps({"reason": "changed"}), tid))
    con.commit()
    allowed, fc, err = check_approval_binding(con, {"task_id": tid})
    assert allowed is False and fc == "mismatch" and err == FINGERPRINT_MISMATCH_MESSAGE
    con.close()


def test_fingerprint_is_sorted_compact_sha256():
    # 键序无关（sort_keys 递归）：不同书写顺序、同内容 → 同指纹
    a = {"task_id": "T1", "proposed_action": "accept_delay", "proposal_params": {"b": 2, "a": 1}}
    b = {"proposal_params": {"a": 1, "b": 2}, "proposed_action": "accept_delay", "task_id": "T1"}
    assert fingerprint_params(a) == fingerprint_params(b)
    assert fingerprint_params(a) != fingerprint_params({**a, "proposed_action": "expedite"})
    assert len(fingerprint_params(a)) == 64  # sha256 hex


# ═══════════════════════════════════════════════════════════════════════════
# API 集成（TestClient + module 级临时库）
# ═══════════════════════════════════════════════════════════════════════════
@pytest.fixture(scope="module")
def api_db(tmp_path_factory):
    dest = tmp_path_factory.mktemp("cmdbus_api_db") / "ontology.sqlite"
    shutil.copy(REPO_DB, dest)
    return str(dest)


@pytest.fixture(scope="module")
def client(api_db):
    app.dependency_overrides[get_db_path] = lambda: api_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(get_db_path, None)


@pytest.fixture(scope="module")
def api_con(api_db):
    c = sqlite3.connect(api_db)
    c.row_factory = sqlite3.Row
    yield c
    c.close()


# ⑦ Idempotency-Key 头透传总线：同 key 双发只新增一个 task、二次原样返回首次结果
def test_idempotency_key_header_dedups(client, api_con):
    rid = _one_assignable_risk(api_con)
    body = {"risk_event_id": rid, "assignee_role": "ops", "priority": "P2", "due_at": DUE}
    before = api_con.execute("SELECT count(*) FROM tasks WHERE risk_event_id=?", (rid,)).fetchone()[0]
    h = {"X-Role": "ops", "Idempotency-Key": "api-idem-header"}
    r1 = client.post("/actions/AssignTask", json=body, headers=h)
    r2 = client.post("/actions/AssignTask", json=body, headers=h)
    assert r1.status_code == 200 and r1.json()["ok"] is True
    assert r2.status_code == 200
    assert r2.json() == r1.json(), "同 Idempotency-Key 二次应原样返回首次结果"
    after = api_con.execute("SELECT count(*) FROM tasks WHERE risk_event_id=?", (rid,)).fetchone()[0]
    assert after == before + 1, "同 key 双发只应新增一个 task"


# ⑤ 409：幂等键「在飞」（pending 未完成）二次请求 → HTTP 409（写锁复检冲突路径）
def test_inflight_idempotency_returns_409(client, api_con):
    ensure_commands_table(api_con)
    api_con.execute(
        """INSERT INTO commands (command_id, idempotency_key, action, actor, role,
           params_fingerprint, result_status, created_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        ("cmd-inflight-1", "inflight-key", "AssignTask", OPS_ACTOR, "ops", "fp-x",
         "pending", "2026-08-08T00:00:00Z"))
    api_con.commit()
    body = {"risk_event_id": "R-DOES-NOT-MATTER", "assignee_role": "ops",
            "priority": "P2", "due_at": DUE}
    r = client.post("/actions/AssignTask", json=body,
                    headers={"X-Role": "ops", "Idempotency-Key": "inflight-key"})
    assert r.status_code == 409, "在飞幂等键二次请求应 409"
    b = r.json()
    assert b["error"]["code"] == "state_conflict"
    assert "detail" in b


# ⑦ 错误信封：既有 detail 键保留 + 新增 error 对象
def test_error_envelope_preserves_detail_and_adds_error(client):
    r = client.post("/actions/TotallyUnknownAction", json={})
    assert r.status_code == 404
    b = r.json()
    assert "detail" in b and isinstance(b["detail"], str), "既有 detail 键必须逐字保留（向后兼容）"
    assert b["error"]["code"] == "not_found"
    assert b["error"]["message"] == b["detail"], "error.message 即原白话 detail"


# ⑥ 游标翻页完整性：逐页并集 = 全量、无重漏
def test_cursor_pagination_union_is_complete(client, api_con):
    all_ids = sorted(r[0] for r in api_con.execute("SELECT risk_event_id FROM risk_events").fetchall())
    collected: list[str] = []
    cursor, pages = "", 0
    while True:
        pages += 1
        assert pages < 200, "翻页未收敛（疑似游标死循环）"
        resp = client.get("/objects/RiskEvent", params={"cursor": cursor, "limit": 40},
                          headers={"X-Role": "manager"})
        assert resp.status_code == 200
        j = resp.json()
        assert "next_cursor" in j, "cursor 模式响应应带 next_cursor"
        collected += [it["risk_event_id"] for it in j["items"]]
        if j["next_cursor"] is None:
            break
        cursor = j["next_cursor"]
    assert len(collected) == len(set(collected)), "游标翻页不应有重复"
    assert sorted(collected) == all_ids, "游标翻页并集应=全量、无遗漏"


# ⑥b 无 cursor 响应与升级前逐字节一致（结构无 next_cursor）
def test_no_cursor_response_shape_unchanged(client):
    resp = client.get("/objects/RiskEvent", params={"limit": 5}, headers={"X-Role": "manager"})
    assert resp.status_code == 200
    j = resp.json()
    assert set(j.keys()) == {"type", "world", "count", "limit", "items"}, \
        "无 cursor 响应结构必须与升级前逐字节一致（不得出现 next_cursor 等新键）"
    assert j["limit"] == 5 and len(j["items"]) <= 5


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))


# ⑥ 对抗复核 F1 回归堵门：非 ProposeMitigation 的提案族（催收 collect）同样被审批绑定覆盖——
#    绑定基线=落库后按任务行现算的规范提案指纹（动作名不可知），篡改后审批必被拒。
def test_collection_family_binding_covered(fresh_db):
    con = A.connect(fresh_db)
    ensure_commands_table(con)
    tid = "TSK-TEST-COLLECT-1"

    def fake_propose_collection(con2, payment_id, actor=None, role=None, as_of=None):
        # 克隆整行满足全部 NOT NULL 约束（同 test_decisions 自愈种子模式），改写成 pending collect 提案
        cols = [r[1] for r in con2.execute("PRAGMA table_info(tasks)")]
        src = con2.execute("SELECT * FROM tasks LIMIT 1").fetchone()
        row = dict(zip(cols, src))
        row.update(task_id=tid, approval_status="pending", status="in_progress",
                   proposed_action="collect",
                   proposal_params=json.dumps({"payment_id": payment_id}))
        con2.execute(f"INSERT INTO tasks ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                     [row[c] for c in cols])
        con2.commit()
        return {"ok": True, "object_id": tid, "side_effects": ["task_created"], "error": None}

    r = execute_command(con, action="ProposeCollection", params={"payment_id": "PAY-X"},
                        actor=OPS_ACTOR, role="finance", as_of=AS_OF,
                        action_func=fake_propose_collection)
    assert r["ok"] is True
    fp = con.execute("SELECT proposal_fingerprint FROM commands WHERE object_id=?",
                     (tid,)).fetchone()[0]
    assert fp, "collect 提案落库后必须带规范提案指纹基线（F1：不再只认 ProposeMitigation）"
    allowed, fc, err = check_approval_binding(con, {"task_id": tid})
    assert (allowed, fc, err) == (True, "match", None)
    con.execute("UPDATE tasks SET proposal_params=? WHERE task_id=?",
                (json.dumps({"payment_id": "PAY-EVIL"}), tid))
    con.commit()
    allowed, fc, err = check_approval_binding(con, {"task_id": tid})
    assert allowed is False and fc == "mismatch" and err == FINGERPRINT_MISMATCH_MESSAGE
    con.close()


# ⑦ 对抗复核 F2 回归堵门：动作抛未预期异常 → 认领坑必须被撤，同一幂等键重试可正常执行
#    （否则 pending 行毒化该键，同 key 永久 409"处理中"且无法自愈）。
def test_failed_action_does_not_poison_idempotency_key(fresh_db):
    con = A.connect(fresh_db)
    ensure_commands_table(con)
    calls = {"n": 0}

    def boom(con2, **kw):
        raise RuntimeError("模拟基础设施故障（非 TypeError）")

    def ok_func(con2, **kw):
        calls["n"] += 1
        return {"ok": True, "object_id": "X-1", "side_effects": [], "error": None}

    with pytest.raises(RuntimeError):
        execute_command(con, action="AssignTask", params={"x": 1}, actor=OPS_ACTOR, role="ops",
                        as_of=AS_OF, action_func=boom, idempotency_key="poison-key-1")
    left = con.execute("SELECT count(*) FROM commands WHERE idempotency_key='poison-key-1'").fetchone()[0]
    assert left == 0, "失败后认领坑必须撤销，不许留 pending 行毒化幂等键"
    r = execute_command(con, action="AssignTask", params={"x": 1}, actor=OPS_ACTOR, role="ops",
                        as_of=AS_OF, action_func=ok_func, idempotency_key="poison-key-1")
    assert r["ok"] is True and calls["n"] == 1, "同键重试应正常执行恰一次"
    con.close()
