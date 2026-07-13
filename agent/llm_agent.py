"""W6 可插拔 LLM 层：python3 -m agent.llm_agent "你的问题"

把 TOOL_DEFS 注册给任何支持 tool-use 的 LLM，模型只能通过 AgentSession.dispatch
接触数据。默认 provider 为本账号 Claude 订阅（AGENT_PROVIDER=claude_cli，走 claude CLI
无头子进程，无需 API key，AGENT_MODEL=claude-opus-4-8）；亦可切 openai / anthropic（需对应 API key）。
确定性简报（agent/explain.py）与评估（agent/evaluate.py）不依赖本模块。

出境治理（spec v3.0 §9，agent.egress_gate）：三个 provider 的出境 payload 统一先过白名单
摘除闸门（PI 摘除）；每次外部调用（含失败/降级）落 llm_calls 一行；简报 grounding 超上下文
预算时显式降级为结构化字段摘要（绝不静默截断）；claude_cli 连续失败 3 次进降级模式
（fail-fast 回退确定性简报 + 告警行），恢复成功一次即清零。
"""
import json
import os
import sqlite3
import subprocess
import sys
import time

from .egress_gate import (FailureTracker, apply_context_budget, budget_for, empty_report,
                          load_llm_config, log_llm_call, merge_reports, sanitize_for_egress)
from .tools import AgentSession, TOOL_DEFS

DEFAULT_DB = "data/ontology.sqlite"  # llm_calls 落库位置（与对象库同库，spec §9）
CLAUDE_CLI_TRACKER = FailureTracker("claude_cli")  # claude_cli 连续失败降级（进程内单例）
CLAUDE_CLI_MCP_TRACKER = FailureTracker("claude_cli_mcp")  # M4 主通道 MCP 多轮，独立降级计数
# M4 正式 MCP config（绝对路径，脱 cwd 依赖）：claude -p --mcp-config 用它起本体只读 server
_MCP_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mcp-config.json")


def _last_cli_status(db=DEFAULT_DB):
    """读 llm_calls 最近一条 claude_cli 调用的 status（ok/error/degraded）；无表/无行/异常一律返回 None。
    绝不触发任何出境——只查审计流水，供 UI 便宜自检用（不必真调 CLI 就能知道上次通没通）。"""
    own = isinstance(db, (str, os.PathLike))
    try:
        conn = sqlite3.connect(db) if own else db
    except Exception:  # noqa: BLE001 —— 自检读库失败绝不反噬业务
        return None
    try:
        row = conn.execute("SELECT status FROM llm_calls WHERE provider='claude_cli' "
                           "ORDER BY call_id DESC LIMIT 1").fetchone()
        return row[0] if row else None
    except Exception:  # noqa: BLE001 —— 表不存在/库损坏等
        return None
    finally:
        if own:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


def probe_cli_availability(db=DEFAULT_DB):
    """便宜自检 AI 助手可用性（返回 (available: bool, reason: 人话)）——绝不调用 CLI，故可每次 rerun 廉价复算，
    亦供 UI 缓存进 session_state。判据三层：① claude 命令不在 PATH → 不可用 ② 连续失败进降级冷却 → 不可用
    ③ 上次 claude_cli 调用为 error/degraded → 不可用（捕捉「已安装但未登录」：which 找得到却调不通的情形，
    首次失败后 llm_calls 落 error，自检据此自愈翻牌）。reason 为面向用户的中文，不含退出码/stderr 技术细节。"""
    import shutil
    if not shutil.which("claude"):
        return False, "未检测到本地 AI 通道（未安装或不在 PATH）"
    if not CLAUDE_CLI_TRACKER.should_attempt():
        return False, "多次调用未成功，正在冷却，稍后自动重试"
    if _last_cli_status(db) in ("error", "degraded"):
        return False, "上次调用未成功，可稍后重试"
    return True, ""


class LLMDegradedError(RuntimeError):
    """claude_cli 处于降级模式（连续失败 ≥ 阈值且冷却未满）：本次未出境，
    调用方（UI except Exception）回退确定性简报模板。"""


def _safe_log(db, **kw):
    """llm_calls 落行失败不反噬业务调用（如库路径异常）：打印警告行而非静默丢弃/抛出。"""
    try:
        return log_llm_call(db, **kw)
    except Exception as exc:  # noqa: BLE001 —— 日志层兜底，绝不让审计故障放大为回答故障
        print(f"⚠️ [llm-log] llm_calls 落库失败：{exc}（本次调用未入账）", file=sys.stderr)
        return None

SYSTEM_PROMPT = """你是跨境供应链控制塔的 AI 协同助手，服务物流运营人员。铁律：

1. 只能陈述工具返回的数据，每个关键事实附对象 ID（如 RSK-0044 / SHP-2026-0099）
2. 工具查不到就直说"不存在/查不到"，禁止编造任何 ID、日期、金额
3. 你没有审批（approve）和关闭（close）权限。被要求执行时明确拒绝，说明须由经理/运营人工完成
4. 你的处置建议一律是 proposal-only：给方案与理由，决定权在人
5. 客户等级等敏感字段对你的角色不可见，不要猜测
"""


def _openai_tools(tools):
    return [{"type": "function",
             "name": t["name"],
             "description": t["description"],
             "parameters": t["input_schema"]}
            for t in tools]


def _run_openai(question, session, max_turns, verbose):
    try:
        from openai import OpenAI
    except ImportError:
        raise SystemExit("需要 pip install openai（可选依赖，仅 OpenAI LLM 模式）")
    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("未设置 OPENAI_API_KEY。ChatGPT 订阅不等于 API 额度；"
                         "确定性简报请用：python3 -m agent.evaluate")

    client = OpenAI()
    model = os.environ.get("AGENT_MODEL", "gpt-5.5")
    # 出境闸门：问题文本先过闸；工具结果回传模型=再次出境，逐段过闸（模型自己的输出不用闸）
    q_clean, pending = sanitize_for_egress(question)
    messages = [{"role": "user", "content": q_clean}]
    # 只向模型暴露本会话 role/focus 允许的工具（与 dispatch gating 对齐，approve/close 不在其中）
    tools = _openai_tools(session.tool_defs())

    for _ in range(max_turns):
        input_chars = len(SYSTEM_PROMPT) + len(json.dumps(messages, ensure_ascii=False, default=str))
        t0 = time.time()
        try:
            resp = client.responses.create(model=model, instructions=SYSTEM_PROMPT,
                                           input=messages, tools=tools, max_output_tokens=1500)
        except Exception as exc:
            _safe_log(session.con, call_type="briefing", provider="openai", model=model,
                      status="error", input_chars=input_chars,
                      duration_ms=int((time.time() - t0) * 1000),
                      error=str(exc)[:300], redactions=pending)
            raise
        tool_calls = [item for item in resp.output if item.type == "function_call"]
        _safe_log(session.con, call_type="briefing", provider="openai", model=model,
                  status="ok", input_chars=input_chars,
                  output_chars=len(resp.output_text or ""),
                  duration_ms=int((time.time() - t0) * 1000), redactions=pending)
        pending = empty_report()  # 本轮摘除已入账；下一行记录下一轮新过闸的内容
        if not tool_calls:
            return resp.output_text

        for tc in tool_calls:
            args = json.loads(tc.arguments or "{}")
            out = session.dispatch(tc.name, args)
            if verbose:
                print(f"  [tool] {tc.name}({json.dumps(args, ensure_ascii=False)[:120]})")
            out_clean, rep = sanitize_for_egress(json.dumps(out, ensure_ascii=False, default=str))
            pending = merge_reports(pending, rep)
            messages.append({"type": "function_call", "call_id": tc.call_id,
                             "name": tc.name, "arguments": tc.arguments})
            messages.append({"type": "function_call_output", "call_id": tc.call_id,
                             "output": out_clean})
    return "（达到最大工具轮数）"


def _call_anthropic(messages, tools, model):
    try:
        import anthropic
    except ImportError:
        raise SystemExit("需要 pip install anthropic（可选依赖，仅 LLM 模式）")
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise SystemExit("未设置 ANTHROPIC_API_KEY。确定性简报请用：python3 -m agent.evaluate")
    client = anthropic.Anthropic(api_key=key)
    return client.messages.create(model=model, max_tokens=1500, system=SYSTEM_PROMPT,
                                  messages=messages, tools=tools)


def _run_anthropic(question, session, max_turns, verbose):
    session = session or AgentSession()
    model = os.environ.get("AGENT_MODEL", "claude-sonnet-4-5")
    # 出境闸门：问题文本先过闸；工具结果回传模型=再次出境，逐段过闸（模型自己的输出不用闸）
    q_clean, pending = sanitize_for_egress(question)
    messages = [{"role": "user", "content": q_clean}]
    for _ in range(max_turns):
        input_chars = len(SYSTEM_PROMPT) + len(json.dumps(messages, ensure_ascii=False, default=str))
        t0 = time.time()
        try:
            resp = _call_anthropic(messages, session.tool_defs(), model)
        except Exception as exc:  # 缺依赖/缺 key 走 SystemExit（未出境，非 Exception，不在此拦）
            _safe_log(session.con, call_type="briefing", provider="anthropic", model=model,
                      status="error", input_chars=input_chars,
                      duration_ms=int((time.time() - t0) * 1000),
                      error=str(exc)[:300], redactions=pending)
            raise
        tool_uses = [b for b in resp.content if b.type == "tool_use"]
        _safe_log(session.con, call_type="briefing", provider="anthropic", model=model,
                  status="ok", input_chars=input_chars,
                  output_chars=sum(len(b.text) for b in resp.content if b.type == "text"),
                  duration_ms=int((time.time() - t0) * 1000), redactions=pending)
        pending = empty_report()  # 本轮摘除已入账；下一行记录下一轮新过闸的内容
        if not tool_uses:
            return "".join(b.text for b in resp.content if b.type == "text")
        messages.append({"role": "assistant", "content": resp.content})
        results = []
        for tu in tool_uses:
            out = session.dispatch(tu.name, dict(tu.input))
            if verbose:
                print(f"  [tool] {tu.name}({json.dumps(tu.input, ensure_ascii=False)[:120]})")
            out_clean, rep = sanitize_for_egress(json.dumps(out, ensure_ascii=False, default=str))
            pending = merge_reports(pending, rep)
            results.append({"type": "tool_result", "tool_use_id": tu.id,
                            "content": out_clean})
        messages.append({"role": "user", "content": results})
    return "（达到最大工具轮数）"


# —— claude_cli provider：走本账号 Claude 订阅（claude CLI 无头子进程），无需 API key ——
# 订阅≠API。claude CLI 是完整 agentic 工具，把"工具清单"喂给它会诱发它 native tool_use（撞
# --max-turns 就报错），不可靠。故本 provider 采用【单发合成】：由调用方（UI 工作台/本模块）用
# 权限脱敏的只读工具先在 Python 侧取好上下文，再让 Opus 4.8 仅据此合成中文回答——纯文本任务、
# 可靠单回合。模型全程【无任何行动能力】（连读工具都由我们代取），故越权/审批无从发生（比 tool-use
# 更安全）；写动作（派单/提案/审批）仍只走 UI 表单 + maker-checker。


# claude CLI 是完整 agentic 工具，这里约束成"单回合纯文本补全"当作 LLM 用：--max-turns 1 禁止它自走
# agentic 循环；--allowed-tools 只放行一个不存在的 dummy → 它的所有内建工具（Bash/Read/…）都不可用，
# 于是它只能输出一段文本（即我们要的回答）。返回 result 文本；不做任何工具调用。
_CLI_NO_NATIVE_TOOLS = "AgentReachNoOpTool"  # 一个不存在的工具名 → 等价于"禁用 CLI 全部内建工具"


def _invoke_cli(prompt, model, timeout):
    """真实出境点（测试可替换本函数模拟成功/失败，无需真实 API）。失败一律抛 RuntimeError 族：
    可被 UI 的 except Exception 捕获 → 优雅降级为确定性简报，也进连续失败计数
    （原缺 CLI 时抛 SystemExit 会同时绕过两者，回退规则接不住——故改 RuntimeError，交付说明列明）。"""
    import shutil
    import subprocess
    if not shutil.which("claude"):
        raise RuntimeError("未找到 claude CLI（本账号订阅渠道）。安装并登录 Claude Code 后重试，"
                           "或改用确定性简报：python3 -m agent.evaluate")
    proc = subprocess.run(
        ["claude", "-p", prompt, "--model", model, "--output-format", "json",
         "--max-turns", "1", "--allowed-tools", _CLI_NO_NATIVE_TOOLS],
        capture_output=True, text=True, timeout=timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"claude CLI 退出码 {proc.returncode}：{(proc.stderr or '')[:200]}")
    env = json.loads(proc.stdout)
    if env.get("is_error"):
        raise RuntimeError(f"claude CLI 报错：{str(env.get('result'))[:200]}")
    return env.get("result", "")


def _claude_cli_call(prompt, model, timeout=90, db=DEFAULT_DB, call_type="briefing",
                     trace_id=None, degrade_note=None):
    """claude_cli 出境统一闸口：①白名单摘除 ②降级模式检查（连续失败 fail-fast，冷却后放行探测）
    ③真实调用 ④llm_calls 落行（ok/error/degraded 全路径必写）。degrade_note 非空表示
    上游已做预算降级——调用成功也记 status=degraded（超预算可追溯）。"""
    clean, report = sanitize_for_egress(prompt)
    if not CLAUDE_CLI_TRACKER.should_attempt():
        msg = (f"claude_cli 降级模式生效（连续失败 {CLAUDE_CLI_TRACKER.failures} 次 ≥ 阈值 "
               f"{CLAUDE_CLI_TRACKER.threshold}，冷却未满）：本次未出境，回退确定性简报")
        _safe_log(db, call_type=call_type, provider="claude_cli", model=model, status="degraded",
                  input_chars=len(clean), error=msg, redactions=report, trace_id=trace_id)
        raise LLMDegradedError(msg)
    t0 = time.time()
    try:
        out = _invoke_cli(clean, model, timeout)
    except Exception as exc:
        CLAUDE_CLI_TRACKER.record_failure()
        _safe_log(db, call_type=call_type, provider="claude_cli", model=model, status="error",
                  input_chars=len(clean), duration_ms=int((time.time() - t0) * 1000),
                  error=str(exc)[:300], redactions=report, trace_id=trace_id)
        raise
    CLAUDE_CLI_TRACKER.record_success()
    _safe_log(db, call_type=call_type, provider="claude_cli", model=model,
              status="degraded" if degrade_note else "ok",
              input_chars=len(clean), output_chars=len(out),
              duration_ms=int((time.time() - t0) * 1000),
              error=degrade_note, redactions=report, trace_id=trace_id)
    return out


def answer_over_context(question, context_text, role=None, model=None, timeout=90, verbose=False,
                        db=DEFAULT_DB, call_type="briefing", trace_id=None):
    """用本账号 Claude 订阅（claude CLI）+ Opus 4.8 作答：仅依据 context_text（已按角色脱敏的
    对象数据）合成中文回答。纯文本任务（不给模型任何可调用工具），可靠单回合。
    出境治理：grounding 超上下文预算时降级为结构化字段摘要（显式标注，绝不静默截断）；
    整个 prompt 出境前过白名单摘除闸门；本次调用落 llm_calls（db 可传库路径或连接）。"""
    model = model or os.environ.get("AGENT_MODEL", "claude-opus-4-8")
    ctx, degrade_note = apply_context_budget(context_text or "（无数据）", budget_for(call_type))
    prompt = (SYSTEM_PROMPT
              + "\n\n以下是你能看到的、已按你的角色脱敏的对象数据。你【只能依据这些数据作答】，"
                "其中没有的信息一律回答“数据中查不到”，禁止编造任何 ID / 日期 / 金额：\n"
              + "----- 数据开始 -----\n" + ctx + "\n----- 数据结束 -----\n\n"
              + f"用户问题：{question}\n\n"
              + "请用中文简洁作答，关键事实后附对象 ID（如 RSK-0044）。你没有审批 / 关闭权限，"
                "被要求执行审批 / 关闭时说明须由人工完成。直接输出回答正文，不要使用任何工具、不要访问文件。")
    if verbose:
        print(f"  [claude_cli] model={model}，上下文 {len(context_text or '')} 字"
              + ("（超预算已降级为结构化字段摘要）" if degrade_note else ""))
    return _claude_cli_call(prompt, model, timeout, db=db, call_type=call_type,
                            trace_id=trace_id, degrade_note=degrade_note).strip()


# 按 focus 类型选用的只读工具（经 session.dispatch，权限/脱敏与 UI 同规）
_CTX_TOOLS_BY_FOCUS = {
    "focus_risk_event_id": [("get_risk", "risk_event_id"), ("get_impact_chain", "risk_event_id")],
    "focus_invoice_id": [("get_invoice_context", "invoice_id")],
    "focus_admission_case_id": [("get_admission_context", "admission_case_id")],
}


def _gather_context(session):
    """按 focus 用 session 只读工具预取上下文（权限脱敏在 dispatch 内生效）；
    无匹配 focus（如无 focus / PO / 仓库焦点）则退回列未结风险，保证有基本 grounding。
    注：UI 各对象工作台已自备更贴切的简报，会直接传给 answer_over_context，不走这里。"""
    blobs = []
    for attr, tools in _CTX_TOOLS_BY_FOCUS.items():
        fid = getattr(session, attr, None)
        if not fid:
            continue
        for tool, argname in tools:
            out = session.dispatch(tool, {argname: fid})  # 越权/超范围 → dispatch 返回 refused/error
            blobs.append(f"[{tool}] " + json.dumps(out, ensure_ascii=False, default=str))
    if not blobs:
        out = session.dispatch("list_open_risks", {})
        blobs.append("[list_open_risks] " + json.dumps(out, ensure_ascii=False, default=str))
    return "\n".join(blobs)


def _run_claude_cli(question, session, max_turns, verbose):
    ctx = _gather_context(session)
    # llm_calls 落在本会话所在库（评估用临时副本时日志随副本走，不污染工作库）
    return answer_over_context(question, ctx, role=getattr(session, "role", None),
                               verbose=verbose, db=session.con)


# —— M4 主通道 claude_cli_mcp provider：走本账号 Claude 订阅 + 本体 MCP server 多轮真调用 ——
# 与旧 claude_cli（单发合成，Python 先取好上下文让模型朗读）本质不同：这里模型经 MCP 协议
# **自己开口要数据**（查货件→沿关系走到风险→逐个深查），系统当场查库返给它。V6 裁2 起除只读工具外
# 还可调 6 个写提案工具（派单/提案/准入准备，走 server→既有 dispatch，审批仍人做）。裁3 决议：新旧
# 双通道并存，config 一键回退（provider 改回 claude_cli 即退旧档）；MCP 失败→回退 provider→确定性简报。

# V6 裁2 提案纪律（最小措辞）：告知模型现在可提案但审批在人（maker-checker）。作为前缀注入问题——
# server 侧脱敏/dispatch 鉴权是硬护栏，本前缀只是让模型行为得体（该提案时提案、明确审批归人），
# 不承担安全职责（即便去掉前缀，冻结区工具仍不可达、越权写仍被 dispatch 拒）。
_MCP_PROPOSAL_DISCIPLINE = (
    "你可查数据并在权限内提交处置提案（派单/提案/准入准备等写工具）；但你没有审批与关闭权限，"
    "提案一律交人工审批（maker-checker）。据实作答，关键事实附对象 ID，查不到就说查不到。\n\n"
)


def _mcp_allowed_tool_args():
    """允许 claude 调用的 MCP 工具全名 mcp__ontology__*（读工具 11 + 6 写提案工具 + traverse = 18，
    单一来源自 mcp_server.build_exposed_tool_defs；V6 裁2 起含写工具）。列全集即可——server 侧按 --role/env
    再过滤 tools/list + 写工具经 dispatch 二次鉴权，模型实际只见并只能调用角色可见子集。"""
    from .mcp_server import build_exposed_tool_defs, SERVER_NAME
    from pipeline.ontology_runtime import load_ontology
    names = [t["name"] for t in build_exposed_tool_defs(load_ontology())]
    return ",".join(f"mcp__{SERVER_NAME}__{n}" for n in names)


def _invoke_cli_mcp(prompt, role, model, max_turns, timeout, verbose):
    """真实出境点（MCP 多轮，测试可替换本函数模拟）：claude -p 经 --mcp-config 起本体只读 MCP server，
    模型多轮自调只读工具后作答。失败一律抛 RuntimeError 族（可被 run_agent 回退链 + 降级计数接住）。
    role 经环境变量 ONTOLOGY_MCP_ROLE 传给 server 决定 tools/list 过滤（claude 把父环境透传给 stdio 子进程）。"""
    import shutil
    if not shutil.which("claude"):
        raise RuntimeError("未找到 claude CLI（本账号订阅渠道）。安装并登录 Claude Code 后重试，"
                           "或改用确定性简报：python3 -m agent.evaluate")
    # V6 裁2 提案纪律前缀（常量、无 PI，不影响出境审计口径）：告知模型可提案但审批在人
    full_prompt = _MCP_PROPOSAL_DISCIPLINE + prompt
    cmd = ["claude", "-p", full_prompt, "--model", model,
           "--mcp-config", _MCP_CONFIG_PATH, "--strict-mcp-config",
           "--allowedTools", _mcp_allowed_tool_args(), "--output-format", "json",
           "--max-turns", str(max(int(max_turns), 12)),  # 多轮工具往返留足余量（PoC 实测 num_turns≈7）
           "--permission-mode", "bypassPermissions"]     # 工具均只读，可安全放行（PoC 报告 §4 备注）
    env = {**os.environ, "ONTOLOGY_MCP_ROLE": role or "ops"}
    if verbose:
        print(f"  [claude_cli_mcp] role={role} model={model} 经 MCP 只读工具多轮真调用…", file=sys.stderr)
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
    if proc.returncode != 0:
        raise RuntimeError(f"claude CLI(MCP) 退出码 {proc.returncode}：{(proc.stderr or '')[:200]}")
    try:
        envelope = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(f"claude CLI(MCP) 输出非 JSON（前 200 字）：{(proc.stdout or '')[:200]}")
    if envelope.get("is_error"):
        raise RuntimeError(f"claude CLI(MCP) 报错：{str(envelope.get('result'))[:200]}")
    return envelope.get("result", "")


def answer_via_mcp(question, role="ops", db=DEFAULT_DB, max_turns=8, verbose=False, timeout=180):
    """M4 新通道公共入口（UI 询问按钮与 run_agent 共用同一实现）——claude_cli_mcp 出境统一闸口
    （与 _claude_cli_call 同规）：①白名单摘除 ②降级 fail-fast（连续失败冷却后放行探测）③真实多轮
    MCP 工具调用 ④llm_calls 落 briefing 行（provider=claude_cli_mcp，ok/error/degraded 全写）。
    逐次工具调用另由 MCP server 审计写连接落 call_type='mcp_tool' 行——双层审计（整体一行+逐工具多行）。
    role 决定 server 侧 tools/list 过滤与脱敏（经 ONTOLOGY_MCP_ROLE 透传）；db 收 llm_calls（路径或连接）。"""
    model = os.environ.get("AGENT_MODEL", "claude-opus-4-8")
    clean, report = sanitize_for_egress(question)
    if not CLAUDE_CLI_MCP_TRACKER.should_attempt():
        msg = (f"claude_cli_mcp 降级模式生效（连续失败 {CLAUDE_CLI_MCP_TRACKER.failures} 次 ≥ 阈值 "
               f"{CLAUDE_CLI_MCP_TRACKER.threshold}，冷却未满）：本次未出境，回退 provider")
        _safe_log(db, call_type="briefing", provider="claude_cli_mcp", model=model, status="degraded",
                  input_chars=len(clean), error=msg, redactions=report)
        raise LLMDegradedError(msg)
    t0 = time.time()
    try:
        out = _invoke_cli_mcp(clean, role, model, max_turns, timeout, verbose)
    except Exception as exc:
        CLAUDE_CLI_MCP_TRACKER.record_failure()
        _safe_log(db, call_type="briefing", provider="claude_cli_mcp", model=model, status="error",
                  input_chars=len(clean), duration_ms=int((time.time() - t0) * 1000),
                  error=str(exc)[:300], redactions=report)
        raise
    CLAUDE_CLI_MCP_TRACKER.record_success()
    _safe_log(db, call_type="briefing", provider="claude_cli_mcp", model=model, status="ok",
              input_chars=len(clean), output_chars=len(out),
              duration_ms=int((time.time() - t0) * 1000), redactions=report)
    return out.strip()


def _run_claude_cli_mcp(question, session, max_turns=8, verbose=True, timeout=180):
    """run_agent 的 MCP 通道适配层：从 session 取 role（server 侧过滤/脱敏）与库（llm_calls 落点），
    委托 answer_via_mcp（单一实现，UI 与 CLI 入口共用）。"""
    return answer_via_mcp(question, role=getattr(session, "role", "ops") or "ops",
                          db=getattr(session, "con", None) or DEFAULT_DB,
                          max_turns=max_turns, verbose=verbose, timeout=timeout)


def _resolve_provider():
    """主通道 provider 解析（裁3 可回退）：环境变量 AGENT_PROVIDER（最高，向后兼容）> config/llm.yaml
    agent.provider（默认新通道 claude_cli_mcp）> 兜底 claude_cli（配置缺失时安全回落旧档）。"""
    env = os.environ.get("AGENT_PROVIDER")
    if env:
        return env.strip().lower()
    try:
        p = load_llm_config().get("agent", {}).get("provider")
        return str(p).strip().lower() if p else "claude_cli"
    except Exception:  # noqa: BLE001 —— 配置读失败绝不成为业务故障点
        return "claude_cli"


def _resolve_fallback_provider():
    """回退档 provider：config/llm.yaml agent.fallback_provider（缺省 claude_cli）。用于 MCP 主通道
    失败时优雅降级（MCP 失败→回退 provider→确定性简报，整条链语义保持现状）。"""
    try:
        fb = load_llm_config().get("agent", {}).get("fallback_provider")
        return str(fb).strip().lower() if fb else "claude_cli"
    except Exception:  # noqa: BLE001
        return "claude_cli"


def _dispatch_provider(provider, question, session, max_turns, verbose):
    if provider in ("claude_cli_mcp", "mcp"):
        return _run_claude_cli_mcp(question, session, max_turns, verbose)
    if provider in ("claude_cli", "claude", "cli"):
        return _run_claude_cli(question, session, max_turns, verbose)
    if provider == "openai":
        return _run_openai(question, session, max_turns, verbose)
    if provider == "anthropic":
        return _run_anthropic(question, session, max_turns, verbose)
    raise SystemExit(f"不支持的 provider={provider}"
                     "（可选：claude_cli_mcp / claude_cli / openai / anthropic）")


def run_agent(question, session=None, max_turns=8, verbose=True):
    """默认走 config 的主通道 provider（M4 起 = claude_cli_mcp：本账号订阅 + 本体只读 MCP 多轮真调用）。
    主通道失败（RuntimeError/降级/超时）→ 回退 fallback_provider（默认 claude_cli 单发合成）；
    再失败由 UI/调用方 except 兜确定性简报——裁3 优雅降级链路语义保持现状。"""
    session = session or AgentSession()
    provider = _resolve_provider()
    try:
        return _dispatch_provider(provider, question, session, max_turns, verbose)
    except (RuntimeError, LLMDegradedError, subprocess.TimeoutExpired) as exc:
        fallback = _resolve_fallback_provider()
        if fallback and fallback != provider:
            if verbose:
                print(f"  [provider] 主通道 {provider} 失败（{str(exc)[:80]}）→ 回退 {fallback}",
                      file=sys.stderr)
            return _dispatch_provider(fallback, question, session, max_turns, verbose)
        raise


if __name__ == "__main__":
    q = sys.argv[1] if len(sys.argv) > 1 else "现在有哪些 critical 风险？最该先处理哪个？"
    print(run_agent(q))
