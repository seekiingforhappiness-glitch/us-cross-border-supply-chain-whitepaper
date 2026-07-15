"""LLM 出境治理层（spec v3.0 §9，A 能力前置基础设施）：
① 出境白名单摘除闸门 sanitize_for_egress：发往任何外部 LLM 的 payload 先做 PI 摘除
② llm_calls 结构化日志表（DDL 单一事实源在此，pipeline.build_ontology 与运行期共用）
③ 上下文预算 apply_context_budget：超限=显式降级为结构化字段摘要，绝不静默截断
④ FailureTracker：claude_cli 连续失败进降级模式（半开探测恢复，成功一次清零）

为什么这样建（≤5 行）：
- 四件事共享同一出境 choke point（agent.llm_agent 的 provider 调用），收敛一个轻量模块
  （只依赖标准库+yaml，pipeline 引 DDL 不背上 app/streamlit 依赖链）。
- 白名单理念：只摘 PI 形态（手机/邮箱/身份证/银行卡），业务字段（柜号/订舱号/LOCODE/金额/
  对象 ID）天然放行——业务编号先经豁免哨兵保护再摘除，宁放行业务编号勿误伤（权衡见正则注释）。
- 本模块零业务写动作、不注册任何 agent 工具（四红线：ROLE_PERMS/FORBIDDEN/真值全不触碰）。
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import time
import uuid
from datetime import datetime, timezone

import yaml

# ---------------------------------------------------------------------------
# 配置（config/llm.yaml；缺文件用默认值；闸门开关可被环境变量 EGRESS_GATE 覆盖）
# ---------------------------------------------------------------------------
LLM_CONFIG_PATH = "config/llm.yaml"
_DEFAULTS = {
    "egress_gate": {"enabled": True},
    "context_budget": {"briefing": 12000, "parse": 12000, "proposal": 12000},
    "fallback": {"fail_threshold": 3, "cooldown_seconds": 300},
}
_cfg_cache = None


def load_llm_config(path=LLM_CONFIG_PATH, refresh=False):
    """读 LLM 治理配置（浅合并到默认值上，缺文件/缺键不炸——治理层永不成为业务可用性的故障点）。"""
    global _cfg_cache
    if _cfg_cache is not None and not refresh:
        return _cfg_cache
    cfg = {k: dict(v) for k, v in _DEFAULTS.items()}
    try:
        loaded = yaml.safe_load(open(path, encoding="utf-8")) or {}
    except FileNotFoundError:
        loaded = {}
    for key, val in loaded.items():
        if isinstance(val, dict) and isinstance(cfg.get(key), dict):
            cfg[key].update(val)
        else:
            cfg[key] = val
    _cfg_cache = cfg
    return cfg


def gate_enabled():
    """闸门开关：环境变量 EGRESS_GATE（on/off/1/0/true/false）> config/llm.yaml > 默认开启。"""
    env = os.environ.get("EGRESS_GATE", "").strip().lower()
    if env in ("0", "off", "false", "no"):
        return False
    if env in ("1", "on", "true", "yes"):
        return True
    return bool(load_llm_config().get("egress_gate", {}).get("enabled", True))


def budget_for(call_type):
    """该类调用的输入上限（chars）。未知类型用 briefing 默认（宁紧勿松）。"""
    budgets = load_llm_config().get("context_budget", {})
    return int(budgets.get(call_type, budgets.get("briefing", 12000)))


# ---------------------------------------------------------------------------
# ① 出境白名单摘除闸门
# ---------------------------------------------------------------------------
# 业务编号豁免（先保护后摘除，摘完还原）：
# - ISO 6346 柜号 = 4 字母 + 7 数字（如 MSCU1234567）——任务红线明确豁免，不校验校验位（防脆弱）
# - SCAC 订舱号/提单号 = 4 字母 + 8-12 数字（本仓库形态 4+9，如 ZIMU738021964）
# 两形态合并为一条：4 字母 + 7-12 数字。边界用 (?<![A-Za-z0-9]) 而非 \b——中文是 \w，
# "柜号MSCU1234567" 里 \b 不成立，自定义边界才护得住。
_EXEMPT_PATTERNS = [
    re.compile(r"(?<![A-Za-z0-9])[A-Z]{4}\d{7,12}(?![0-9])"),
]

# PI 摘除正则。顺序即摘除顺序（结构最特异者先行）：
# email 先（本地部含数字，防被数字类误切）→ 身份证先于银行卡（18 位 ∈ 13-19 区间会被后者吞并）。
# 数字类一律用 (?<![A-Za-z0-9])…(?![A-Za-z0-9]) 边界：字母紧贴的数字串（业务编号常态）不当 PI；
# 误伤权衡：①固话要求分隔符或括号区号（无分隔的 0 开头 11 位与纯数字业务 ID 不可分，放行）
# ②紧贴英文字母的手机号（tel13800138000）放行——白名单理念宁放行勿误伤；中文紧贴（电话13800138000）照摘。
_PI_PATTERNS = [
    ("email", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "[REDACTED-EMAIL]"),
    ("id_card", re.compile(
        r"(?<![A-Za-z0-9])\d{6}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[0-9Xx](?![A-Za-z0-9])"),
     "[REDACTED-IDCARD]"),
    ("phone", re.compile(r"(?<![A-Za-z0-9])1[3-9]\d{9}(?![A-Za-z0-9])"), "[REDACTED-PHONE]"),
    ("phone", re.compile(r"(?:\(0\d{2,3}\)|(?<![A-Za-z0-9])0\d{2,3}[- ])\d{7,8}(?![A-Za-z0-9])"),
     "[REDACTED-PHONE]"),
    ("bank_card", re.compile(r"(?<![A-Za-z0-9])\d{13,19}(?![A-Za-z0-9])"), "[REDACTED-BANKCARD]"),
]

REDACTION_TYPES = ("phone", "email", "id_card", "bank_card")


def empty_report(enabled=True):
    return {"enabled": bool(enabled), "total": 0,
            "by_type": {t: 0 for t in REDACTION_TYPES}, "exempted": 0}


def merge_reports(a, b):
    """合并两份摘除报告（tool-use 循环里同一次外呼可能含多段被闸文本）。"""
    out = empty_report(a.get("enabled", True) or b.get("enabled", True))
    for t in REDACTION_TYPES:
        out["by_type"][t] = a["by_type"].get(t, 0) + b["by_type"].get(t, 0)
    out["total"] = a.get("total", 0) + b.get("total", 0)
    out["exempted"] = a.get("exempted", 0) + b.get("exempted", 0)
    return out


def sanitize_for_egress(payload_text, enabled=None):
    """出境前 PI 摘除 → (clean_text, redaction_report)。
    enabled=None 时按 gate_enabled()（env > config > 默认开启）；关闭时原文放行但报告如实记 enabled=False。
    报告记"摘了几处什么类型"（by_type/total）+ 豁免命中数（exempted），供 llm_calls 审计。"""
    text = payload_text or ""
    if enabled is None:
        enabled = gate_enabled()
    report = empty_report(enabled)
    if not enabled or not text:
        return text, report
    # ① 白名单保护：业务编号换哨兵（\x00 不出现在正常 payload；哨兵内数字被字母紧贴、不会被 PI 正则命中）
    protected = []

    def _protect(m):
        protected.append(m.group(0))
        return f"\x00X{len(protected) - 1}\x00"

    for pat in _EXEMPT_PATTERNS:
        text = pat.sub(_protect, text)
    report["exempted"] = len(protected)
    # ② PI 摘除（顺序见 _PI_PATTERNS 注释）
    for ptype, pat, marker in _PI_PATTERNS:
        text, n = pat.subn(marker, text)
        report["by_type"][ptype] += n
    report["total"] = sum(report["by_type"].values())
    # ③ 还原业务编号（白名单字段天然通过）
    for i, token in enumerate(protected):
        text = text.replace(f"\x00X{i}\x00", token)
    return text, report


# ---------------------------------------------------------------------------
# ② llm_calls 结构化日志表（DDL 单一事实源，仿 engine.resolution_memory 模式）
# ---------------------------------------------------------------------------
# est_*_tokens = chars // 3：粗估非计费口径（无 tokenizer 依赖，够预算/成本监控用），字段名带 est 自明。
# error 列在任务列清单之外——"必写一行（含失败）"需要失败原因可复盘，最小加列（交付说明列明）。
# created_at/duration_ms 用真实 UTC 时钟（运行态遥测的诚实值，非业务 as_of；D8 例外，可显式传入注入测试）。
LLM_CALLS_DDL = """CREATE TABLE IF NOT EXISTS llm_calls (
    call_id INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id TEXT NOT NULL,
    call_type TEXT NOT NULL CHECK (call_type IN ('briefing','parse','proposal')),
    provider TEXT NOT NULL,
    model TEXT,
    input_chars INTEGER NOT NULL DEFAULT 0,
    output_chars INTEGER NOT NULL DEFAULT 0,
    est_input_tokens INTEGER NOT NULL DEFAULT 0,
    est_output_tokens INTEGER NOT NULL DEFAULT 0,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL CHECK (status IN ('ok','error','degraded')),
    error TEXT,
    redactions TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
)"""


def ensure_llm_calls_table(conn: sqlite3.Connection) -> None:
    """建表兜底（幂等）：pipeline.build_ontology 建正表；旧库副本首写运行期补建（仿 M2 兼容模式）。"""
    conn.execute(LLM_CALLS_DDL)


def log_llm_call(db, *, call_type, provider, status, model=None, input_chars=0, output_chars=0,
                 duration_ms=0, error=None, redactions=None, trace_id=None, created_at=None):
    """每次外部 LLM 调用（含失败/降级）落一行。db 可传 sqlite3.Connection 或库路径。返回 trace_id。"""
    own = isinstance(db, (str, os.PathLike))
    conn = sqlite3.connect(db) if own else db
    try:
        ensure_llm_calls_table(conn)
        trace_id = trace_id or f"LLM-{uuid.uuid4().hex[:12].upper()}"
        created_at = created_at or datetime.now(timezone.utc).isoformat(
            timespec="seconds").replace("+00:00", "Z")
        conn.execute(
            """INSERT INTO llm_calls (trace_id, call_type, provider, model, input_chars,
               output_chars, est_input_tokens, est_output_tokens, duration_ms, status,
               error, redactions, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (trace_id, call_type, provider, model, int(input_chars), int(output_chars),
             int(input_chars) // 3, int(output_chars) // 3, int(duration_ms), status,
             error, json.dumps(redactions or {}, ensure_ascii=False), created_at))
        conn.commit()
        return trace_id
    finally:
        if own:
            conn.close()


# ---------------------------------------------------------------------------
# ③ 上下文预算：超限=显式降级为结构化字段摘要，绝不静默截断（spec §9）
# ---------------------------------------------------------------------------
LINE_TRUNC = 200          # 单行超长截断阈值：截断处显式标注原行长，绝不静默
_SUMMARY_RESERVE = 160    # 摘要头/尾标注行的预留空间


def apply_context_budget(text, budget):
    """预算内原样返回 (text, None)；超限降级 → (结构化字段摘要, degrade_note)。
    摘要 = 按行保留（简报是"字段: 值"行结构，保整行=保字段语义），超长行标注截断、
    装不下的行数显式计数——模型和审计都看得见"降了什么/丢了几行"，绝不静默截断。"""
    text = text or ""
    if len(text) <= budget:
        return text, None
    lines = text.splitlines()
    total = len(lines)
    header = f"[已降级：上下文超预算（{len(text)} 字 > 预算 {budget} 字），以下为结构化字段摘要]"
    limit = max(budget - len(header) - _SUMMARY_RESERVE, 0)
    kept, used = [], 0
    for i, line in enumerate(lines):
        if len(line) > LINE_TRUNC:
            line = line[:LINE_TRUNC] + f"…[行截断，原 {len(line)} 字]"
        if used + len(line) + 1 > limit:
            kept.append(f"[摘要截止：共 {total} 行，已纳入 {i} 行，未纳入 {total - i} 行]")
            break
        kept.append(line)
        used += len(line) + 1
    else:
        kept.append(f"[摘要截止：共 {total} 行全部纳入（仅超长行做了标注截断）]")
    note = f"[已降级：上下文超预算] 原 {len(text)} 字 > 预算 {budget} 字，已降为结构化字段摘要"
    return header + "\n" + "\n".join(kept), note


# ---------------------------------------------------------------------------
# ④ claude_cli 回退规则：连续失败计数 → 降级模式（半开探测恢复）
# ---------------------------------------------------------------------------
class FailureTracker:
    """连续失败计数器（进程内）。为什么进程内而非落库：Streamlit 单进程跨 rerun 天然生效、
    不给对象库增加运行态写面（每次调用已逐行落 llm_calls 可复盘）、重启即复位=给通道一次重试机会。
    连续失败 ≥ threshold 进降级模式（调用方 fail-fast 回退确定性简报）+ 打印告警行；
    降级后经 cooldown_seconds 放行一次真实探测调用（半开）——恢复成功一次即清零，失败续降级。"""

    def __init__(self, provider="claude_cli", threshold=None, cooldown_seconds=None):
        cfg = load_llm_config().get("fallback", {})
        self.provider = provider
        self.threshold = int(cfg.get("fail_threshold", 3)) if threshold is None else int(threshold)
        self.cooldown_seconds = (float(cfg.get("cooldown_seconds", 300))
                                 if cooldown_seconds is None else float(cooldown_seconds))
        self.failures = 0
        self.degraded_since = None

    def is_degraded(self):
        return self.failures >= self.threshold

    def should_attempt(self, now=None):
        """未降级=放行；已降级=冷却期内 fail-fast，冷却期满放行一次探测（半开）。"""
        if not self.is_degraded():
            return True
        now = time.time() if now is None else now
        return (now - (self.degraded_since or 0)) >= self.cooldown_seconds

    def record_failure(self, now=None):
        self.failures += 1
        now = time.time() if now is None else now
        if self.failures >= self.threshold:
            self.degraded_since = now  # 探测失败也重新起算冷却
            print(f"⚠️ [llm-degraded] {self.provider} 连续失败 {self.failures} 次"
                  f"（阈值 {self.threshold}），降级模式生效：简报回退确定性模板，"
                  f"{self.cooldown_seconds:.0f}s 后放行一次探测调用", file=sys.stderr)
        return self.failures

    def record_success(self):
        if self.failures:
            print(f"✅ [llm-recovered] {self.provider} 调用成功，连续失败计数清零"
                  f"（{self.failures}→0），退出降级模式", file=sys.stderr)
        self.failures = 0
        self.degraded_since = None

    def reset(self):
        self.failures = 0
        self.degraded_since = None
