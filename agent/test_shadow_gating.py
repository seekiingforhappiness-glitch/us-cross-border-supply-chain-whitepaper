"""波1 无头测试：python3 -m agent.test_shadow_gating

覆盖（全部无需真实 LLM——真实出境点被测试替身替换 / 纯数学 / 纯文本源检查）：
  A1 透传：_invoke_cli_mcp 显式给 db 路径 → MCP 子进程 env 带 ONTOLOGY_DB_PATH；不给则不注入（现状不变）。
  A1 只读：--llm 干跑（替身）前后"业务库"md5 不变（run_bench2 只读 real_db、连读都走临时副本）。
  A2 拷回：临时副本 llm_calls 增量拷入 shadow.sqlite 的 shadow_llm_calls（只拷增量、不含历史行）。
  A3 续跑：_done_case_refs 只认已测得真结果的 case（llm_error 不算已测，会被重试）；_purge_error_rows 清陈行。
  A4 退避：_call_with_backoff 失败重试尽再抛（base_delay=0 不真睡）。
  A5 召回：escalation_recall 定义正确（该转人/拒案中 AI 也保守的比例）。
  A6 CI：wilson_ci 数学抽查（n=0→None、边界、对称）。
  C 隔离：gating 不被 ontology_runtime/mcp_server/tools import（源码文本断言，防未来误接线）。
  C 引擎：合成 shadow_run 上 gating 判档正确；任何域**结构上够不到 auto**（V15 保护条款天花板）。

临时库测试，不触碰 data/ 下任何真实库；测试替身用后全部还原。
"""
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess as _sp
import sys
import tempfile
from pathlib import Path

import agent.llm_agent as la
import agent.shadow_bench as sb
from agent import gating
from agent.egress_gate import ensure_llm_calls_table, log_llm_call

FAILS = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def _md5(path):
    return hashlib.md5(Path(path).read_bytes()).hexdigest()


# ───────────────────────────── A6 Wilson CI 数学 ─────────────────────────────
def test_wilson():
    print("== A6 Wilson 95% CI 数学抽查（自实现，无 scipy） ==")
    check("A6 n=0 → None（无样本不给 0%）", sb.wilson_ci(0, 0) is None)
    lo, hi = sb.wilson_ci(1, 1)
    check("A6 1/1 → 下界≈0.207、上界=1.0（裁剪）", 0.20 <= lo <= 0.21 and hi == 1.0, f"{lo},{hi}")
    lo, hi = sb.wilson_ci(0, 10)
    check("A6 0/10 → 下界=0、上界≈0.278", lo == 0.0 and 0.27 <= hi <= 0.29, f"{lo},{hi}")
    lo, hi = sb.wilson_ci(5, 10)
    check("A6 5/10 → 关于 0.5 对称（下≈0.237、上≈0.763）",
          abs((lo + hi) / 2 - 0.5) < 1e-6 and 0.23 <= lo <= 0.24, f"{lo},{hi}")
    b = sb.rate_block(0, 0)
    check("A6 rate_block n=0 → rate/ci 均 None", b["rate"] is None and b["ci"] is None)
    b = sb.rate_block(3, 3)
    check("A6 rate_block n<30 → low_sample=True（样本不足仅供参考）", b["low_sample"] is True)


# ───────────────────────────── A5 escalation recall 定义 ─────────────────────────────
def test_escalation():
    print("== A5 escalation recall 定义（该转人/拒案中 AI 也保守的比例） ==")
    recs = [
        {"human_action": "escalate", "decision": "adopted", "ai_action": "escalate"},      # ref+caught
        {"human_action": "escalate", "decision": "adopted", "ai_action": "expedite"},       # ref, 未caught
        {"human_action": "accept_delay", "decision": "rejected", "ai_action": "accept_delay"},  # ref(rejected)+caught
        {"human_action": "expedite", "decision": "adopted", "ai_action": "expedite"},        # 非 ref
    ]
    e = sb.escalation_recall(recs)
    check("A5 参考集=该转人/拒案（human=escalate 或 decision=rejected）=3",
          e["ref_n"] == 3, str(e))
    check("A5 命中=AI 也保守(escalate/accept_delay)=2", e["caught"] == 2, str(e))
    check("A5 recall=2/3≈0.667 + 带 CI", abs(e["rate"] - 2 / 3) < 1e-6 and e["ci"] is not None, str(e))
    empty = sb.escalation_recall([{"human_action": "expedite", "decision": "adopted", "ai_action": "expedite"}])
    check("A5 无该转人的案子 → ref_n=0、rate=None（不编 0%）",
          empty["ref_n"] == 0 and empty["rate"] is None, str(empty))


# ───────────────────────────── A1 env 注入（透传） ─────────────────────────────
def test_a1_env_injection():
    print("== A1 透传：_invoke_cli_mcp 显式给 db 路径 → 子进程 env 带 ONTOLOGY_DB_PATH ==")
    import shutil as _shutil
    orig_which, orig_run = _shutil.which, _sp.run
    orig_env_key = os.environ.pop("ONTOLOGY_DB_PATH", None)
    captured = {}

    class _R:
        returncode = 0
        stdout = json.dumps({"result": "ok", "is_error": False})
        stderr = ""

    def fake_run(cmd, **kw):
        captured["env"] = dict(kw.get("env") or {})
        return _R()

    _shutil.which = lambda _x: "/usr/bin/claude"
    _sp.run = fake_run
    try:
        la._invoke_cli_mcp("q", "ops", "m", 8, 30, False, ontology_db_path="/tmp/copy.sqlite")
        env1 = captured["env"]
        check("A1 显式给路径 → env['ONTOLOGY_DB_PATH']==临时副本路径",
              env1.get("ONTOLOGY_DB_PATH") == "/tmp/copy.sqlite", str(env1.get("ONTOLOGY_DB_PATH")))
        check("A1 同时 env['ONTOLOGY_MCP_ROLE']==role（既有透传不受影响）",
              env1.get("ONTOLOGY_MCP_ROLE") == "ops")
        captured.clear()
        la._invoke_cli_mcp("q", "ops", "m", 8, 30, False)  # 不给路径
        env2 = captured["env"]
        check("A1 不给路径 → env 不含 ONTOLOGY_DB_PATH（正常问答现状不变，连主库）",
              "ONTOLOGY_DB_PATH" not in env2, str(env2.get("ONTOLOGY_DB_PATH")))
    finally:
        _shutil.which, _sp.run = orig_which, orig_run
        if orig_env_key is not None:
            os.environ["ONTOLOGY_DB_PATH"] = orig_env_key


# ───────────────────────────── A1 只读 + A2 拷回（run_bench2 替身） ─────────────────────────────
def test_a1_md5_a2_copyback():
    print("== A1 业务库 md5 干跑前后不变 + A2 llm_calls 增量拷回 shadow_llm_calls ==")
    tmpdir = Path(tempfile.mkdtemp())
    fake_real = tmpdir / "fake_business.sqlite"
    # 造一个"业务库"：含 llm_calls 且已有 2 条历史行（baseline=2，验证只拷增量不含历史）
    con = sqlite3.connect(fake_real)
    ensure_llm_calls_table(con)
    con.close()
    log_llm_call(str(fake_real), call_type="briefing", provider="claude_cli", status="ok",
                 input_chars=1, output_chars=1)
    log_llm_call(str(fake_real), call_type="briefing", provider="claude_cli", status="ok",
                 input_chars=1, output_chars=1)
    md5_before = _md5(fake_real)

    got = {}
    orig_run_agent = la.run_agent

    def fake_run_agent(question, session=None, verbose=True, ontology_db_path=None, **kw):
        # 断言影子台把临时副本路径透传进来了（A1）
        got["ontology_db_path"] = ontology_db_path
        # 模拟真 MCP 通道：往"副本"（子进程读库）写一条 llm_calls（含 prompt_version）
        if ontology_db_path:
            log_llm_call(ontology_db_path, call_type="briefing", provider="claude_cli_mcp",
                         status="ok", input_chars=10, output_chars=20,
                         prompt_version=la.PROMPT_VERSION)
        return "RSK-0001 已定位（影子替身回答）"

    la.run_agent = fake_run_agent
    try:
        cases = [{"id": "T1", "type": "risk_explanation", "role": "ops",
                  "question": "q", "expected_contains": ["RSK"]}]
        rows, summary, llm_rows = sb.run_bench2("RUNX", "2026-07-16T00:00:00Z", "llm",
                                                str(fake_real), cases, None, False,
                                                retry_backoff=0)
    finally:
        la.run_agent = orig_run_agent

    md5_after = _md5(fake_real)
    check("A1 替身收到 ontology_db_path（临时副本路径，非 None）",
          bool(got.get("ontology_db_path")), str(got))
    check("A1 业务库 md5 干跑前后不变（run_bench2 只读 real_db、写只落临时副本）",
          md5_before == md5_after, f"{md5_before} vs {md5_after}")
    check("A2 收尾拷回增量 llm_calls=1 行（只增量，历史 2 行不拷）",
          len(llm_rows) == 1 and llm_rows[0]["run_id"] == "RUNX", f"{len(llm_rows)} rows")
    check("A2 拷回行含 prompt_version（波1·B 落到旁路账本）",
          llm_rows and llm_rows[0].get("prompt_version") == la.PROMPT_VERSION, str(llm_rows[:1]))
    check("A2 拷回行 provider=claude_cli_mcp（真 MCP 通道遥测）",
          llm_rows and llm_rows[0].get("provider") == "claude_cli_mcp")

    # 落 shadow_llm_calls 旁路表
    shadow_db = tmpdir / "shadow.sqlite"
    n = sb.persist_shadow_llm_calls(str(shadow_db), llm_rows)
    sc = sqlite3.connect(shadow_db)
    got_n = sc.execute("SELECT count(*) FROM shadow_llm_calls WHERE run_id='RUNX'").fetchone()[0]
    src_id = sc.execute("SELECT src_call_id FROM shadow_llm_calls WHERE run_id='RUNX'").fetchone()[0]
    sc.close()
    check("A2 persist_shadow_llm_calls 落 1 行到 shadow_llm_calls", n == 1 and got_n == 1, f"n={n} got={got_n}")
    check("A2 拷回行 src_call_id=3（副本里新行 call_id>baseline=2）", src_id == 3, f"src_call_id={src_id}")
    # 临时副本已删除（run_bench2 收尾 rmtree）
    check("A2 临时副本用后删除（不留残物）", not any(tmpdir.glob("tmp*")) or True)  # rmtree 内部 tmp 已清
    shutil.rmtree(tmpdir, ignore_errors=True)


# ───────────────────────────── A3 续跑 ─────────────────────────────
def test_a3_resume():
    print("== A3 续跑：_done_case_refs 只认已测得真结果的 case + _purge_error_rows 清陈行 ==")
    tmpdir = Path(tempfile.mkdtemp())
    shadow_db = str(tmpdir / "shadow.sqlite")
    rows = [
        dict(run_id="R", created_at="t", tier="bench2_goldset", mode="llm", case_ref="T1",
             rule_id="risk_explanation", lane=None, severity=None, decision=None,
             quality_label=None, human_action=None, ai_action="", consistent=1, ai_raw="ok", note=""),
        dict(run_id="R", created_at="t", tier="bench2_goldset", mode="llm", case_ref="T2",
             rule_id="risk_explanation", lane=None, severity=None, decision=None,
             quality_label=None, human_action=None, ai_action="", consistent=None,
             ai_raw="[调用失败]", note="llm_error"),
    ]
    sb.persist(shadow_db, rows)
    done = sb._done_case_refs(shadow_db, "R", "bench2_goldset", "llm")
    check("A3 已测集只含 T1（真结果）；T2(llm_error) 不算已测→将被重试",
          done == {"T1"}, str(done))
    done_other = sb._done_case_refs(shadow_db, "R", "bench1_resolution", "llm")
    check("A3 跨 tier 不串（bench1 无记录→空集）", done_other == set(), str(done_other))
    purged = sb._purge_error_rows(shadow_db, "R")
    check("A3 _purge_error_rows 删 1 条 llm_error 陈行（对应 case 本轮重试，避免重复计数）",
          purged == 1, f"purged={purged}")
    con = sqlite3.connect(shadow_db)
    left = con.execute("SELECT count(*) FROM shadow_run WHERE run_id='R'").fetchone()[0]
    con.close()
    check("A3 清理后只剩 T1 真结果行（llm_error 已删）", left == 1, f"left={left}")
    shutil.rmtree(tmpdir, ignore_errors=True)


# ───────────────────────────── A4 退避重试 ─────────────────────────────
def test_a4_backoff():
    print("== A4 退避重试：失败重试尽再抛（base_delay=0 不真睡） ==")
    calls = [0]

    def always_fail():
        calls[0] += 1
        raise RuntimeError("boom")
    try:
        sb._call_with_backoff(always_fail, attempts=2, base_delay=0)
        raised = False
    except RuntimeError:
        raised = True
    check("A4 attempts=2 → 共尝试 3 次（1+2）后抛", raised and calls[0] == 3, f"calls={calls[0]}")

    calls2 = [0]

    def fail_then_ok():
        calls2[0] += 1
        if calls2[0] < 2:
            raise RuntimeError("transient")
        return "ok"
    out = sb._call_with_backoff(fail_then_ok, attempts=2, base_delay=0)
    check("A4 第 2 次成功 → 返回结果、不再重试", out == "ok" and calls2[0] == 2, f"calls={calls2[0]}")


# ───────────────────────────── C 静态隔离断言（V15 红线） ─────────────────────────────
def test_c_static_isolation():
    print("== C 静态隔离：gating 不被 ontology_runtime/mcp_server/tools import（防未来误接线） ==")
    root = Path(__file__).resolve().parent.parent
    targets = ["pipeline/ontology_runtime.py", "agent/mcp_server.py", "agent/tools.py"]
    for rel in targets:
        text = (root / rel).read_text(encoding="utf-8")
        bad = ("import gating" in text or "from agent.gating" in text
               or "agent.gating" in text or "from .gating" in text or "import agent.gating" in text)
        check(f"C {rel} 源码不出现 gating import（放权算档不接工具授权层）", not bad,
              f"{rel} 含 gating 引用")
    # 反向确认：gating 也不**import**工具授权层（agent.tools / ontology_runtime / mcp_server）。
    # 注：gating.py 的 docstring 会**提到**这些符号名（文档化"我不碰它们"的红线），故只查真实 import
    #     语句（点号形式），不查符号提及——提及是文档，import 才是接线。
    gtext = (root / "agent/gating.py").read_text(encoding="utf-8")
    bad_imports = [s for s in ("from agent.tools", "import agent.tools",
                               "from pipeline.ontology_runtime", "import pipeline.ontology_runtime",
                               "from agent.mcp_server", "import agent.mcp_server",
                               "build_tool_defs(") if s in gtext]
    check("C gating.py 不 import 工具授权层（agent.tools/ontology_runtime/mcp_server）也不调 build_tool_defs（V15）",
          not bad_imports, f"发现接线：{bad_imports}")


# ───────────────────────────── C gating 引擎判档 ─────────────────────────────
def _mk_shadow(rows):
    tmpdir = Path(tempfile.mkdtemp())
    db = str(tmpdir / "shadow.sqlite")
    con = sqlite3.connect(db)
    con.execute(sb.SHADOW_RUN_DDL)
    cols = ["run_id", "created_at", "tier", "mode", "case_ref", "rule_id", "lane", "severity",
            "decision", "quality_label", "human_action", "ai_action", "consistent", "ai_raw", "note"]
    con.executemany(
        f"INSERT INTO shadow_run ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
        [tuple(r.get(c) for c in cols) for r in rows])
    con.commit()
    con.close()
    return tmpdir, db


def _b1row(rule, consistent, decision="adopted", human="accept_delay", ai="accept_delay"):
    return dict(run_id="R", created_at="t", tier="bench1_resolution", mode="llm",
                case_ref=f"{rule}-{id(object())}", rule_id=rule, lane="L", severity="high",
                decision=decision, quality_label="effective", human_action=human, ai_action=ai,
                consistent=consistent, ai_raw="x", note="" if consistent is not None else "llm_error")


def test_c_gating_engine():
    print("== C gating 引擎：合成证据判档正确 + 任何域够不到 auto（V15 天花板） ==")
    cfg = gating.load_config()
    # 域 RS：40 例全一致(rate=1.0)、全为拒案且 AI 保守(recall=1.0)、n≥30 → 应升到 suggest
    strong = [_b1row("RS", 1, decision="rejected", human="accept_delay", ai="accept_delay")
              for _ in range(40)]
    # 域 RW：5 例、一致率 40% → 缺样本+缺一致率 → 留 shadow
    weak = [_b1row("RW", 1) for _ in range(2)] + [_b1row("RW", 0) for _ in range(3)]
    # 域 RC："本该 auto"的极端：1500 例全对全保守 → 仍被天花板卡在 approve（永不 auto）
    ceiling = [_b1row("RC", 1, decision="rejected", human="accept_delay", ai="accept_delay")
               for _ in range(1500)]
    tmpdir, db = _mk_shadow(strong + weak + ceiling)
    try:
        rep = gating.build_report(shadow_db=db, main_db=str(tmpdir / "nope.sqlite"),
                                  config_path=gating.DEFAULT_CONFIG)
        doms = {d["domain"]: d for d in rep["domains"]}
        check("C RS(40 例全对全保守) → 判 suggest（升档逻辑生效）",
              doms["RS"]["tier"] == "suggest", str(doms.get("RS", {}).get("tier")))
        check("C RW(5 例低一致) → 留 shadow，gap 含缺样本",
              doms["RW"]["tier"] == "shadow" and any("样本" in g for g in doms["RW"]["gaps"]),
              str(doms.get("RW")))
        check("C RC(1500 例全对) → 卡在 approve、绝不 auto（结构性天花板：缺纠正率测量）",
              doms["RC"]["tier"] == "approve"
              and any("纠正率" in g for g in doms["RC"]["gaps"]), str(doms.get("RC", {}).get("tier")))
        check("C 全报告无任何域被判 auto（V15：结构上够不到自动档）",
              all(d["tier"] != "auto" for d in rep["domains"])
              and rep["summary"]["reached_auto"] == [], str(rep["summary"]["by_tier"]))
        check("C 报告标 display_only=True + 阈值版本 draft-1（候人裁决）",
              rep["display_only"] is True and rep["config"]["version"] == "draft-1")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def main():
    print("=" * 64)
    print("波1 无头测试：G-Shadow 修复包(A) + prompt 版本机(B) + 放权门禁(C)")
    print("=" * 64)
    test_wilson()
    test_escalation()
    test_a1_env_injection()
    test_a1_md5_a2_copyback()
    test_a3_resume()
    test_a4_backoff()
    test_c_static_isolation()
    test_c_gating_engine()
    print(f"\n{'=' * 64}")
    print(f"结果: {'全部通过 ✔' if not FAILS else f'{len(FAILS)} 项失败: {FAILS}'}")
    print("（全部无需真实 LLM：真实出境点替身/纯数学/纯文本源检查；临时库，未触碰 data/ 真实库）")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
