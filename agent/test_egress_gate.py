"""LLM 出境治理层无头测试：python3 -m agent.test_egress_gate

覆盖（任务书①-⑥，全部无需真实 API——真实出境点 _invoke_cli 被测试替身替换）：
① PI 摘除：手机号/带区号固话/邮箱/身份证/银行卡被摘，报告计数对，原值不残留
② 白名单不误伤：柜号(ISO6346)/订舱号/MBL/LOCODE/金额/日期/对象 ID/规则 ID 原样通过
③ llm_calls 落行：成功(ok)与失败(error)都必写一行；schema 与 spec §9 列清单一致；token=chars//3 估算
④ 上下文预算：超限触发显式降级（结构化字段摘要+标注），绝不静默截断；llm_calls 记 degraded
⑤ 回退规则：连续失败 3 次进降级模式（fail-fast 不出境+告警行），冷却后探测恢复成功即清零
⑥ 闸门开关：EGRESS_GATE 环境变量 on/off 可切；未设回落 config/llm.yaml 默认开启

临时库测试，不触碰 data/ontology.sqlite；测试替身/环境变量/计数器状态用后全部还原。
"""
import contextlib
import io
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

import agent.llm_agent as la
from agent.egress_gate import (apply_context_budget, budget_for, empty_report, gate_enabled,
                               merge_reports, sanitize_for_egress)

FAILS = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


PI_TEXT = ("联系人张三，手机 13800138000，座机 021-88886666，"
           "邮箱 zhangsan@example.com，身份证 110101199003078515，"
           "银行卡 6222021234567890123，备用卡 4111111111111111。")

BIZ_TEXT = ("柜号 MSCU1234567 已到港 USLAX，航线 CNSHK→USLAX，订舱号 ZIMU738021964，"
            "MBL COSU274956229，货值 1234567.89 美元、影响金额 250000 USD，"
            "风险 RSK-0044（规则 R18）、货运 SHP-2026-0099、任务 TSK-51BEB35072，"
            "as_of 2026-07-08T00:00:00Z，REF4111111111111111 为字母紧贴的业务参考号。")

EXPECTED_COLUMNS = ["call_id", "trace_id", "call_type", "provider", "model",
                    "input_chars", "output_chars", "est_input_tokens", "est_output_tokens",
                    "duration_ms", "status", "error", "redactions", "created_at"]


def rows(db, sql, *a):
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(sql, a)]
    finally:
        con.close()


def latest_call(db):
    r = rows(db, "SELECT * FROM llm_calls ORDER BY call_id DESC LIMIT 1")
    return r[0] if r else None


def main():
    tmp_db = str(Path(tempfile.mkdtemp()) / "egress.sqlite")
    orig_invoke = la._invoke_cli
    orig_env = os.environ.get("EGRESS_GATE")
    orig_cooldown = la.CLAUDE_CLI_TRACKER.cooldown_seconds
    os.environ.pop("EGRESS_GATE", None)  # 本测试从"未设 env、config 默认开启"出发

    try:
        print("== ① PI 摘除：手机/固话/邮箱/身份证/银行卡被摘 + 报告计数对 ==")
        clean, rep = sanitize_for_egress(PI_TEXT)
        check("① 手机号被摘（[REDACTED-PHONE]）",
              "13800138000" not in clean and "[REDACTED-PHONE]" in clean, clean)
        check("① 带区号固话被摘", "021-88886666" not in clean, clean)
        check("① 邮箱被摘（[REDACTED-EMAIL]）",
              "zhangsan@example.com" not in clean and "[REDACTED-EMAIL]" in clean, clean)
        check("① 身份证被摘（[REDACTED-IDCARD]）",
              "110101199003078515" not in clean and "[REDACTED-IDCARD]" in clean, clean)
        check("① 银行卡 19 位/16 位均被摘（[REDACTED-BANKCARD]）",
              "6222021234567890123" not in clean and "4111111111111111" not in clean
              and clean.count("[REDACTED-BANKCARD]") == 2, clean)
        check("① 报告计数：phone=2 email=1 id_card=1 bank_card=2，total=6",
              rep["by_type"] == {"phone": 2, "email": 1, "id_card": 1, "bank_card": 2}
              and rep["total"] == 6, str(rep))
        check("① 报告 enabled=True（闸门默认开启）", rep["enabled"] is True, str(rep))

        print("== ② 白名单不误伤：柜号/订舱号/MBL/LOCODE/金额/日期/对象 ID 原样通过 ==")
        clean_biz, rep_biz = sanitize_for_egress(BIZ_TEXT)
        check("② 业务文本零摘除、逐字不变（柜号 MSCU1234567/订舱号 ZIMU738021964/"
              "MBL COSU274956229/LOCODE CNSHK·USLAX/金额/日期/RSK·SHP·TSK/R18 全保留）",
              clean_biz == BIZ_TEXT and rep_biz["total"] == 0,
              f"total={rep_biz['total']} diff样例={clean_biz[:120]}")
        check("② 业务编号豁免命中 ≥3（MSCU/ZIMU/COSU 哨兵保护后还原）",
              rep_biz["exempted"] >= 3, str(rep_biz))
        check("② 字母紧贴的 16 位数字（REF4111111111111111）按边界规则放行（宁放行业务编号勿误伤）",
              "REF4111111111111111" in clean_biz, clean_biz)
        mixed, rep_mixed = sanitize_for_egress(BIZ_TEXT + " 联系电话 13900139000")
        check("② 业务字段与 PI 同现时：只摘 PI、业务字段仍原样",
              "MSCU1234567" in mixed and "ZIMU738021964" in mixed
              and "[REDACTED-PHONE]" in mixed and rep_mixed["total"] == 1, str(rep_mixed))

        print("== ③ llm_calls 落行（确定性替身路径，无真实 API）：成功/失败都必写 ==")
        captured = []

        def fake_ok(prompt, model, timeout):
            captured.append(prompt)
            return "回答正文 RSK-0044"

        la._invoke_cli = fake_ok
        la.CLAUDE_CLI_TRACKER.reset()
        ans = la.answer_over_context("现在最严重的风险？",
                                     "risk_event_id: RSK-0044\nseverity: critical", db=tmp_db)
        check("③ 替身成功路径返回答案", ans == "回答正文 RSK-0044", ans)
        row = latest_call(tmp_db)
        check("③ llm_calls 有行且 status=ok / provider=claude_cli / call_type=briefing",
              row is not None and row["status"] == "ok" and row["provider"] == "claude_cli"
              and row["call_type"] == "briefing", str(row))
        check("③ input_chars=出境后 payload 长度，output_chars=回答长度",
              row["input_chars"] == len(captured[-1])
              and row["output_chars"] == len("回答正文 RSK-0044"), str(row))
        check("③ est_tokens = chars//3（标注为估算口径）",
              row["est_input_tokens"] == row["input_chars"] // 3
              and row["est_output_tokens"] == row["output_chars"] // 3, str(row))
        check("③ trace_id/created_at 已填、redactions 为合法 JSON",
              (row["trace_id"] or "").startswith("LLM-") and row["created_at"]
              and isinstance(json.loads(row["redactions"]), dict), str(row))
        cols = [r["name"] for r in rows(tmp_db, "PRAGMA table_info(llm_calls)")]
        check("③ schema 与 spec §9 列清单一致（含 call_id 主键与 error 最小加列）",
              cols == EXPECTED_COLUMNS, str(cols))

        def fake_fail(prompt, model, timeout):
            raise RuntimeError("模拟 CLI 失败")

        la._invoke_cli = fake_fail
        try:
            la.answer_over_context("q", "ctx", db=tmp_db)
            failed = False
        except RuntimeError:
            failed = True
        row = latest_call(tmp_db)
        check("③ 失败也必写一行：status=error 且 error 含失败原因",
              failed and row["status"] == "error" and "模拟 CLI 失败" in (row["error"] or ""),
              str(row))
        la.CLAUDE_CLI_TRACKER.reset()  # 不让本处失败影响 ④/⑤ 计数

        print("== ④ 上下文预算：超限=显式降级为结构化字段摘要，绝不静默截断 ==")
        budget = budget_for("briefing")
        check("④ briefing 预算默认 12000 chars（config/llm.yaml 可调）", budget == 12000, str(budget))
        short_ctx = "risk_event_id: RSK-0044"
        same, note = apply_context_budget(short_ctx, budget)
        check("④ 预算内原样通过（无降级标注）", same == short_ctx and note is None, str(note))
        long_ctx = "\n".join(f"字段_{i}: 值_{i}_" + "x" * 20 for i in range(600))
        summary, note = apply_context_budget(long_ctx, budget)
        check("④ 超限触发降级：带显式标注头 + 未纳入行数计数（非静默）",
              note is not None and "[已降级：上下文超预算" in summary and "未纳入" in summary,
              summary[:160])
        check("④ 摘要不超预算且确有内容被丢弃（丢弃是显式声明的）",
              len(summary) <= budget and "字段_599" not in summary and "字段_0" in summary,
              f"len={len(summary)}")
        la._invoke_cli = fake_ok
        la.answer_over_context("q", long_ctx, db=tmp_db)
        row = latest_call(tmp_db)
        check("④ 超预算调用在 llm_calls 记 status=degraded、error 注明超预算",
              row["status"] == "degraded" and "超预算" in (row["error"] or ""), str(row))
        check("④ 降级标注随 payload 出境（模型看得见降级事实）",
              "[已降级：上下文超预算" in captured[-1], captured[-1][:200])

        print("== ⑤ 回退规则：连续失败 3 次进降级模式 + 告警行；恢复成功一次即清零 ==")
        la.CLAUDE_CLI_TRACKER.reset()
        la.CLAUDE_CLI_TRACKER.cooldown_seconds = 3600  # 降级后冷却未满 → fail-fast
        fail_count = [0]

        def fake_fail_n(prompt, model, timeout):
            fail_count[0] += 1
            raise RuntimeError("模拟连续失败")

        la._invoke_cli = fake_fail_n
        stderr_buf = io.StringIO()
        raised = []
        with contextlib.redirect_stderr(stderr_buf):
            for _ in range(3):
                try:
                    la.answer_over_context("q", "ctx", db=tmp_db)
                    raised.append(None)
                except Exception as exc:  # noqa: BLE001
                    raised.append(exc)
        check("⑤ 前 3 次为真实尝试且全失败（RuntimeError，非 fail-fast）",
              len(raised) == 3 and all(isinstance(e, RuntimeError)
                                       and not isinstance(e, la.LLMDegradedError) for e in raised)
              and fail_count[0] == 3, str(raised))
        check("⑤ 第 3 次后进入降级模式（连续失败计数=3 ≥ 阈值 3）",
              la.CLAUDE_CLI_TRACKER.is_degraded() and la.CLAUDE_CLI_TRACKER.failures == 3,
              str(la.CLAUDE_CLI_TRACKER.failures))
        check("⑤ 已打印告警行（降级模式生效）",
              "连续失败 3 次" in stderr_buf.getvalue() and "降级模式" in stderr_buf.getvalue(),
              stderr_buf.getvalue())
        try:
            la.answer_over_context("q", "ctx", db=tmp_db)
            degraded_raise = None
        except Exception as exc:  # noqa: BLE001
            degraded_raise = exc
        row = latest_call(tmp_db)
        check("⑤ 降级模式 fail-fast：抛 LLMDegradedError 且未出境（替身未被调用）",
              isinstance(degraded_raise, la.LLMDegradedError) and fail_count[0] == 3,
              str(degraded_raise))
        check("⑤ fail-fast 也落 llm_calls：status=degraded、error 注明未出境",
              row["status"] == "degraded" and "未出境" in (row["error"] or ""), str(row))
        la.CLAUDE_CLI_TRACKER.cooldown_seconds = 0  # 冷却期满 → 放行一次探测
        la._invoke_cli = fake_ok
        stderr_buf2 = io.StringIO()
        with contextlib.redirect_stderr(stderr_buf2):
            ans = la.answer_over_context("q", "ctx", db=tmp_db)
        check("⑤ 冷却后探测调用成功 → 恢复：计数清零、退出降级模式",
              ans == "回答正文 RSK-0044" and la.CLAUDE_CLI_TRACKER.failures == 0
              and not la.CLAUDE_CLI_TRACKER.is_degraded(), str(la.CLAUDE_CLI_TRACKER.failures))
        check("⑤ 恢复也有告知行（llm-recovered）", "llm-recovered" in stderr_buf2.getvalue(),
              stderr_buf2.getvalue())

        print("== ⑥ 闸门开关可配置：EGRESS_GATE 环境变量 > config，默认开启 ==")
        os.environ["EGRESS_GATE"] = "off"
        clean_off, rep_off = sanitize_for_egress(PI_TEXT)
        check("⑥ 关闸：原文放行、报告如实记 enabled=False/total=0",
              clean_off == PI_TEXT and rep_off["enabled"] is False and rep_off["total"] == 0,
              str(rep_off))
        la.answer_over_context("q", "联系手机 13800138000", db=tmp_db)
        check("⑥ 关闸全链路：出境 payload 保留原文（未摘除）",
              "13800138000" in captured[-1], captured[-1][:120])
        os.environ["EGRESS_GATE"] = "on"
        la.answer_over_context("q", "联系手机 13800138000", db=tmp_db)
        row = latest_call(tmp_db)
        check("⑥ 开闸全链路：出境 payload 已摘除，llm_calls.redactions 记 phone=1",
              "13800138000" not in captured[-1] and "[REDACTED-PHONE]" in captured[-1]
              and json.loads(row["redactions"])["by_type"]["phone"] == 1, str(row))
        os.environ.pop("EGRESS_GATE", None)
        check("⑥ 未设 env：回落 config/llm.yaml 默认=开启", gate_enabled() is True)
        mr = merge_reports(rep, empty_report())
        check("⑥ 报告合并不丢计数（tool-use 循环多段过闸用）", mr["total"] == rep["total"], str(mr))

    finally:
        la._invoke_cli = orig_invoke
        la.CLAUDE_CLI_TRACKER.reset()
        la.CLAUDE_CLI_TRACKER.cooldown_seconds = orig_cooldown
        if orig_env is None:
            os.environ.pop("EGRESS_GATE", None)
        else:
            os.environ["EGRESS_GATE"] = orig_env

    n_rows = rows(tmp_db, "SELECT count(*) c FROM llm_calls")[0]["c"]
    print(f"\n{'=' * 44}")
    print(f"llm_calls 共落 {n_rows} 行（ok/error/degraded 全路径均入账；临时库，工作库未被触碰）")
    print(f"结果: {'全部通过 ✔' if not FAILS else f'{len(FAILS)} 项失败: {FAILS}'}")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
