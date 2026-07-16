"""放权门禁引擎（波1·C，display-only）：python3 -m agent.gating

一句话：读影子测量台攒下的证据（shadow.sqlite 的 shadow_run 全历史）+ 主库 AI 调用遥测
（llm_calls），按**域**（规则 R* / 题类）算出每个域当前该处在"放权阶梯"的哪一档，以及离
下一档还差什么——**只展示结论，绝不改任何工具授权**（V15 保护条款）。输出 data/gating_report.json。

放权阶梯（档位由 agent/gating_config.json 的草案阈值判定，候 Daniel 正式裁决）：
  影子 shadow → 建议 suggest → 审核 approve → 自动 auto
  升档条件（尺子，非引擎自定）：见 gating_config.json.promotions。

—— 红线（V15 保护条款，加静态测试断言守护，见 agent/test_shadow_gating.py）——
* 本模块**不得**被 pipeline/ontology_runtime.py、agent/mcp_server.py、agent/tools.py import
  （放权"算档"与放权"改权"物理隔离：算的人不摸权，防未来误接线把档位接到工具授权）；
* 不改任何工具授权生成（不碰 build_tool_defs / ROLE_PERMS / FORBIDDEN / 真值）；
* 所有域当前只可能落 shadow/suggest——样本量远不足 auto 的 n≥1000，且纠正率暂无测量手段，
  结构上够不到 auto（诚实：这是设计内天花板，非缺陷）。

为什么这样建（≤5 行）：
- 纯读旁路账本 + 遥测，零业务写、零工具注册——放权"算档"与"改权"物理隔离（算的人不摸权）。
- 阈值全从 gating_config.json 读（草案候人裁决），引擎只做"量"，不做"定标准"（标准是人的）。
- 放权判断只用**真 AI**证据：bench1 影子提案（mode=llm）+ bench2 真 AI 过题（mode=llm）；
  确定性 scripted 是天花板基准、非 AI 判断，不纳入放权算档（纳入会虚高，全局规则5）。
- CI 数学复用 agent.shadow_bench.wilson_ci（单一来源，不重造）；escalation 定义与 shadow_bench 同口径。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

# 复用影子台的 CI 数学与 escalation 保守动作集（单一来源，不重造）。shadow_bench 模块顶层不 import
# tools/mcp_server/ontology_runtime（那些是函数内延迟 import），故本 import 不违反隔离红线。
from agent.shadow_bench import ESCALATION_CONSERVATIVE, rate_block, wilson_ci  # noqa: F401

_HERE = Path(__file__).resolve().parent          # agent/
REPO_ROOT = _HERE.parent
DEFAULT_SHADOW_DB = REPO_ROOT / "data" / "shadow.sqlite"      # 证据源①：影子测量台账（只读）
DEFAULT_MAIN_DB = REPO_ROOT / "data" / "ontology.sqlite"     # 证据源②：主库 llm_calls 遥测（只读）
DEFAULT_CONFIG = _HERE / "gating_config.json"                # 阈值尺子（草案候人裁决）
DEFAULT_REPORT = REPO_ROOT / "data" / "gating_report.json"  # 输出：各域档位报告

# 域白话（规则 R* 盯什么 / 题类是什么）——供 UI 直读，不猜（缺则回退域名本身）。
RULE_PLAIN = {
    "R1": "延误击穿承诺", "R2": "改期后二次击穿", "R3": "运输停滞",
    "R4": "费率超容差", "R5": "无预算高额费", "R6": "重复计费",
    "R16": "断货", "R17": "不可履约", "R18": "盘点差异",
}


def _ro(db_path) -> sqlite3.Connection | None:
    """只读连接（物理写不进）；库不存在返回 None（如实：无证据源）。"""
    if not Path(db_path).exists():
        return None
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def load_config(path=DEFAULT_CONFIG) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _has_table(con: sqlite3.Connection, name: str) -> bool:
    return con.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?",
                       (name,)).fetchone()[0] > 0


# ─────────────────────────── 域聚合（从 shadow_run 现算） ───────────────────────────
def _bench1_domains(con: sqlite3.Connection) -> list[dict]:
    """档1 分规则域：一致率 + escalation recall + Wilson CI（只用真 AI 影子提案 mode=llm）。"""
    if not (con and _has_table(con, "shadow_run")):
        return []
    rows = [dict(r) for r in con.execute(
        "SELECT rule_id, consistent, decision, human_action, ai_action FROM shadow_run "
        "WHERE tier='bench1_resolution' AND mode='llm'")]
    by_rule: dict[str, list] = defaultdict(list)
    for r in rows:
        by_rule[r["rule_id"] or "—"].append(r)
    out = []
    for rule, rs in sorted(by_rule.items()):
        parsed = [r for r in rs if r["consistent"] is not None]  # 拿到过 AI 真回答的
        n = len(parsed)
        hits = sum(r["consistent"] for r in parsed)
        # escalation recall（与 shadow_bench 同口径）：该转人/拒案中 AI 也保守的比例
        ref = [r for r in parsed
               if r["human_action"] == "escalate" or r["decision"] == "rejected"]
        caught = [r for r in ref if r["ai_action"] in ESCALATION_CONSERVATIVE]
        rate_b = rate_block(hits, n)
        esc_b = rate_block(len(caught), len(ref))
        out.append({
            "group": "resolution", "domain": rule,
            "plain": RULE_PLAIN.get(rule, rule),
            "n": n, "hits": hits, "rate": rate_b["rate"], "ci": rate_b["ci"],
            "low_sample": rate_b["low_sample"],
            "escalation": {"ref_n": len(ref), "caught": len(caught),
                           "rate": esc_b["rate"], "ci": esc_b["ci"]},
        })
    return out


def _bench2_domains(con: sqlite3.Connection) -> list[dict]:
    """档2 分题类域：真 AI 过题率 + Wilson CI（只用 mode=llm；scripted 是天花板基准不纳入放权算档）。"""
    if not (con and _has_table(con, "shadow_run")):
        return []
    rows = [dict(r) for r in con.execute(
        "SELECT rule_id, consistent FROM shadow_run "
        "WHERE tier='bench2_goldset' AND mode='llm'")]
    by_type: dict[str, list] = defaultdict(list)
    for r in rows:
        by_type[r["rule_id"] or "—"].append(r)
    out = []
    for t, rs in sorted(by_type.items()):
        parsed = [r for r in rs if r["consistent"] is not None]
        n = len(parsed)
        hits = sum(r["consistent"] for r in parsed)
        rate_b = rate_block(hits, n)
        out.append({
            "group": "goldset", "domain": t, "plain": f"金标题类 · {t}",
            "n": n, "hits": hits, "rate": rate_b["rate"], "ci": rate_b["ci"],
            "low_sample": rate_b["low_sample"], "escalation": None,
        })
    return out


# ─────────────────────────── 档位判定（对照 gating_config 阈值） ───────────────────────────
def decide_tier(domain: dict, cfg: dict) -> dict:
    """给一个域的测量结果，按 gating_config 阈值判当前档位 + 离下一档差什么。
    走 promotions 顺序（shadow→suggest→approve→auto），停在第一个未满足的门槛，把该门槛缺项列清。
    is_bench1 域才评 escalation recall（bench2 金标题无人工升级维度，该门槛对其 N/A）。"""
    promos = cfg["promotions"]
    is_bench1 = domain["group"] == "resolution"
    rate, n = domain["rate"], domain["n"]
    esc = (domain.get("escalation") or {}).get("rate")
    esc_ref_n = (domain.get("escalation") or {}).get("ref_n", 0)

    def pct(x):
        return "—" if x is None else f"{x * 100:.1f}%"

    # shadow → suggest
    s2s = promos["shadow_to_suggest"]
    gaps = []
    if n < s2s["n_min"]:
        gaps.append(f"缺样本：n={n} < {s2s['n_min']}")
    if rate is None or rate < s2s["consistency_min"]:
        gaps.append(f"缺一致率：{pct(rate)} < {pct(s2s['consistency_min'])}")
    if is_bench1:
        if esc_ref_n == 0:
            gaps.append("缺 recall 样本：该转人的案子=0，无法评估漏升级（放权最怕漏升级）")
        elif esc is None or esc < s2s["escalation_recall_min"]:
            gaps.append(f"缺 escalation recall：{pct(esc)} < {pct(s2s['escalation_recall_min'])}")
    if gaps:
        return {"tier": "shadow", "next_tier": "suggest", "gaps": gaps}

    # suggest → approve
    s2a = promos["suggest_to_approve"]
    gaps = []
    if n < s2a["n_min"]:
        gaps.append(f"缺样本：n={n} < {s2a['n_min']}")
    if rate is None or rate < s2a["precision_min"]:
        gaps.append(f"缺精确率：{pct(rate)} < {pct(s2a['precision_min'])}")
    if gaps:
        return {"tier": "suggest", "next_tier": "approve", "gaps": gaps}

    # approve → auto（结构性天花板：n≥1000 + 纠正率<5%，纠正率暂无测量手段 → 恒不达）
    a2u = promos["approve_to_auto"]
    gaps = []
    if n < a2u["n_min"]:
        gaps.append(f"缺样本：n={n} < {a2u['n_min']}（结构性天花板，本波不追）")
    if rate is None or rate < a2u["rate_min"]:
        gaps.append(f"缺一致率：{pct(rate)} < {pct(a2u['rate_min'])}")
    gaps.append("缺纠正率测量：人事后纠正率暂无测量手段（结构性缺口，不放行自动档）")
    return {"tier": "approve", "next_tier": "auto", "gaps": gaps}


# ─────────────────────────── 遥测（主库 llm_calls，只读上下文） ───────────────────────────
def _telemetry(con: sqlite3.Connection | None) -> dict:
    """主库 llm_calls 只读概览（含 prompt_version 分布，呼应波1·B）。表未就位则如实空。"""
    if not (con and _has_table(con, "llm_calls")):
        return {"available": False, "total": 0, "by_provider": [], "by_prompt_version": [],
                "note": "主库 llm_calls 表未就位——真实 AI 推理遥测尚未接入（诚实空账）。"}
    total = con.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0]
    by_provider = [dict(r) for r in con.execute(
        "SELECT provider, COUNT(*) n FROM llm_calls GROUP BY provider ORDER BY n DESC")]
    # prompt_version 列在旧库可能尚未迁移（波1·B 的 ALTER 只在下次 log_llm_call 写该库时自愈）；
    # gating 只读，读不到就如实标"未迁移"，绝不代改主库 schema（只读红线）。
    has_pv = "prompt_version" in {r[1] for r in con.execute("PRAGMA table_info(llm_calls)")}
    if has_pv:
        by_pv = [dict(r) for r in con.execute(
            "SELECT COALESCE(prompt_version,'（未记版本·历史行）') prompt_version, COUNT(*) n "
            "FROM llm_calls GROUP BY prompt_version ORDER BY n DESC")]
        pv_note = "prompt_version 分布呼应波1·B（改 SYSTEM_PROMPT 必换号）。"
    else:
        by_pv = []
        pv_note = "prompt_version 列尚未迁移进本主库（波1·B 的 ALTER 在下次真实 AI 调用写该库时自愈）。"
    return {"available": total > 0, "total": total, "by_provider": by_provider,
            "by_prompt_version": by_pv, "prompt_version_migrated": has_pv,
            "note": "真实 AI 调用遥测（只读）。" + pv_note}


# ─────────────────────────── 报告组装 ───────────────────────────
def build_report(shadow_db=DEFAULT_SHADOW_DB, main_db=DEFAULT_MAIN_DB, config_path=DEFAULT_CONFIG) -> dict:
    cfg = load_config(config_path)
    shadow = _ro(shadow_db)
    main = _ro(main_db)
    try:
        domains = _bench1_domains(shadow) + _bench2_domains(shadow)
        for d in domains:
            d.update(decide_tier(d, cfg))
        shadow_rows = (shadow.execute("SELECT COUNT(*) FROM shadow_run").fetchone()[0]
                       if (shadow and _has_table(shadow, "shadow_run")) else 0)
        telemetry = _telemetry(main)
    finally:
        if shadow:
            shadow.close()
        if main:
            main.close()

    by_tier = defaultdict(int)
    for d in domains:
        by_tier[d["tier"]] += 1
    reached_auto = [d["domain"] for d in domains if d["tier"] == "auto"]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "display_only": True,
        "config": {
            "version": cfg.get("version"), "status": cfg.get("_status"),
            "ladder": cfg.get("ladder", []), "promotions": cfg.get("promotions", {}),
        },
        "sources": {
            "shadow_db": str(Path(shadow_db)), "shadow_run_rows": shadow_rows,
            "main_db": str(Path(main_db)), "llm_calls_rows": telemetry.get("total", 0),
            "note": "放权算档只用真 AI 证据（bench1 影子提案 + bench2 真 AI 过题，均 mode=llm）；"
                    "确定性 scripted 是天花板基准，不纳入算档。",
        },
        "domains": domains,
        "summary": {
            "domain_count": len(domains),
            "by_tier": dict(by_tier),
            "reached_auto": reached_auto,
            "note": ("各域当前档位一览。所有域结构上够不到 auto（需 n≥1000 且纠正率<5%，本波样本远不足、"
                     "纠正率暂无测量手段）——这是设计内天花板，非缺陷。" if not reached_auto
                     else "⚠️ 异常：有域被判 auto，与'结构性天花板'不符，请核查阈值/数据。"),
        },
        "telemetry": telemetry,
        "honest_note": (
            "放权门禁引擎 display-only：只算各域当前该在放权阶梯哪一档 + 离下一档差什么，"
            "**绝不改任何工具授权**（V15 保护条款：不碰 build_tool_defs/ROLE_PERMS/FORBIDDEN）。"
            "阈值为草案默认值（gating_config.json version=" + str(cfg.get("version")) + "），候 Daniel 正式裁决。"
            "无数据的域如实标 shadow + '缺样本'，绝不用小样本冒充可放权（全局规则5：自信的具体≠真实）。"),
    }


def write_report(report: dict, out_path=DEFAULT_REPORT) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    return out_path


def _print_report(report: dict) -> None:
    print("=" * 68)
    print("放权门禁引擎（display-only）——各域当前放权阶梯档位")
    print("=" * 68)
    cfg = report["config"]
    print(f"阈值尺子：version={cfg['version']}（{cfg['status']}）")
    print(f"证据源：shadow_run {report['sources']['shadow_run_rows']} 行 · "
          f"llm_calls {report['sources']['llm_calls_rows']} 行")
    doms = report["domains"]
    if not doms:
        print("\n暂无任何域数据——shadow_run 无 mode=llm 记录（需先跑 `python3 -m agent.shadow_bench --llm`）。")
    for d in doms:
        rate = "—" if d["rate"] is None else f"{d['rate'] * 100:.1f}%"
        ci = d["ci"]
        ci_s = f" [95%CI {ci[0] * 100:.0f}–{ci[1] * 100:.0f}%]" if ci else ""
        esc = d.get("escalation")
        esc_s = ""
        if esc and esc.get("ref_n"):
            er = "—" if esc["rate"] is None else f"{esc['rate'] * 100:.0f}%"
            esc_s = f" · escalation recall {er}({esc['caught']}/{esc['ref_n']})"
        print(f"\n[{d['group']}] {d['domain']}（{d['plain']}）→ 档位 **{d['tier']}**")
        print(f"    一致率/过题率 {rate}{ci_s} · n={d['n']}{esc_s}")
        print(f"    离下一档（{d['next_tier']}）差：" + "；".join(d["gaps"]))
    bt = report["summary"]["by_tier"]
    print("\n" + "-" * 68)
    print("档位分布：" + ("  ".join(f"{k}={v}" for k, v in sorted(bt.items())) if bt else "（无域）"))
    print(report["summary"]["note"])
    print("=" * 68)


def main():
    ap = argparse.ArgumentParser(description="放权门禁引擎（波1·C，display-only，不改任何工具授权）")
    ap.add_argument("--shadow-db", default=str(DEFAULT_SHADOW_DB))
    ap.add_argument("--main-db", default=str(DEFAULT_MAIN_DB))
    ap.add_argument("--config", default=str(DEFAULT_CONFIG))
    ap.add_argument("--out", default=str(DEFAULT_REPORT))
    ap.add_argument("--quiet", action="store_true", help="只写文件不打印")
    args = ap.parse_args()
    report = build_report(args.shadow_db, args.main_db, args.config)
    path = write_report(report, args.out)
    if not args.quiet:
        _print_report(report)
    print(f"\n✓ 放权报告已写 {path}（display-only；不改任何工具授权，V15 保护条款）")


if __name__ == "__main__":
    main()
