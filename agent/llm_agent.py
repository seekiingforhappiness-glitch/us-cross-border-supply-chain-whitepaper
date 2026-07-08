"""W6 可插拔 LLM 层：python3 -m agent.llm_agent "你的问题"

把 TOOL_DEFS 注册给任何支持 tool-use 的 LLM，模型只能通过 AgentSession.dispatch
接触数据。默认 provider 为本账号 Claude 订阅（AGENT_PROVIDER=claude_cli，走 claude CLI
无头子进程，无需 API key，AGENT_MODEL=claude-opus-4-8）；亦可切 openai / anthropic（需对应 API key）。
确定性简报（agent/explain.py）与评估（agent/evaluate.py）不依赖本模块。
"""
import json
import os
import sys

from .tools import AgentSession, TOOL_DEFS

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
    messages = [{"role": "user", "content": question}]
    # 只向模型暴露本会话 role/focus 允许的工具（与 dispatch gating 对齐，approve/close 不在其中）
    tools = _openai_tools(session.tool_defs())

    for _ in range(max_turns):
        resp = client.responses.create(model=model, instructions=SYSTEM_PROMPT,
                                       input=messages, tools=tools, max_output_tokens=1500)
        tool_calls = [item for item in resp.output if item.type == "function_call"]
        if not tool_calls:
            return resp.output_text

        for tc in tool_calls:
            args = json.loads(tc.arguments or "{}")
            out = session.dispatch(tc.name, args)
            if verbose:
                print(f"  [tool] {tc.name}({json.dumps(args, ensure_ascii=False)[:120]})")
            messages.append({"type": "function_call", "call_id": tc.call_id,
                             "name": tc.name, "arguments": tc.arguments})
            messages.append({"type": "function_call_output", "call_id": tc.call_id,
                             "output": json.dumps(out, ensure_ascii=False, default=str)})
    return "（达到最大工具轮数）"


def _call_anthropic(messages, tools):
    try:
        import anthropic
    except ImportError:
        raise SystemExit("需要 pip install anthropic（可选依赖，仅 LLM 模式）")
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise SystemExit("未设置 ANTHROPIC_API_KEY。确定性简报请用：python3 -m agent.evaluate")
    client = anthropic.Anthropic(api_key=key)
    return client.messages.create(model=os.environ.get("AGENT_MODEL", "claude-sonnet-4-5"),
                                  max_tokens=1500, system=SYSTEM_PROMPT,
                                  messages=messages, tools=tools)


def _run_anthropic(question, session, max_turns, verbose):
    session = session or AgentSession()
    messages = [{"role": "user", "content": question}]
    for _ in range(max_turns):
        resp = _call_anthropic(messages, session.tool_defs())
        tool_uses = [b for b in resp.content if b.type == "tool_use"]
        if not tool_uses:
            return "".join(b.text for b in resp.content if b.type == "text")
        messages.append({"role": "assistant", "content": resp.content})
        results = []
        for tu in tool_uses:
            out = session.dispatch(tu.name, dict(tu.input))
            if verbose:
                print(f"  [tool] {tu.name}({json.dumps(tu.input, ensure_ascii=False)[:120]})")
            results.append({"type": "tool_result", "tool_use_id": tu.id,
                            "content": json.dumps(out, ensure_ascii=False, default=str)})
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


def _claude_cli_call(prompt, model, timeout=90):
    import shutil
    import subprocess
    if not shutil.which("claude"):
        raise SystemExit("未找到 claude CLI（本账号订阅渠道）。安装并登录 Claude Code 后重试，"
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


def answer_over_context(question, context_text, role=None, model=None, timeout=90, verbose=False):
    """用本账号 Claude 订阅（claude CLI）+ Opus 4.8 作答：仅依据 context_text（已按角色脱敏的
    对象数据）合成中文回答。纯文本任务（不给模型任何可调用工具），可靠单回合。"""
    model = model or os.environ.get("AGENT_MODEL", "claude-opus-4-8")
    prompt = (SYSTEM_PROMPT
              + "\n\n以下是你能看到的、已按你的角色脱敏的对象数据。你【只能依据这些数据作答】，"
                "其中没有的信息一律回答“数据中查不到”，禁止编造任何 ID / 日期 / 金额：\n"
              + "----- 数据开始 -----\n" + (context_text or "（无数据）") + "\n----- 数据结束 -----\n\n"
              + f"用户问题：{question}\n\n"
              + "请用中文简洁作答，关键事实后附对象 ID（如 RSK-0044）。你没有审批 / 关闭权限，"
                "被要求执行审批 / 关闭时说明须由人工完成。直接输出回答正文，不要使用任何工具、不要访问文件。")
    if verbose:
        print(f"  [claude_cli] model={model}，上下文 {len(context_text or '')} 字")
    return _claude_cli_call(prompt, model, timeout).strip()


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
    return answer_over_context(question, ctx, role=getattr(session, "role", None), verbose=verbose)


def run_agent(question, session=None, max_turns=8, verbose=True):
    session = session or AgentSession()
    # 默认走本账号 Claude 订阅（claude CLI）+ Opus 4.8；可用 AGENT_PROVIDER 切 openai/anthropic。
    provider = os.environ.get("AGENT_PROVIDER", "claude_cli").lower()
    if provider in ("claude_cli", "claude", "cli"):
        return _run_claude_cli(question, session, max_turns, verbose)
    if provider == "openai":
        return _run_openai(question, session, max_turns, verbose)
    if provider == "anthropic":
        return _run_anthropic(question, session, max_turns, verbose)
    raise SystemExit(f"不支持的 AGENT_PROVIDER={provider}（可选：claude_cli / openai / anthropic）")


if __name__ == "__main__":
    q = sys.argv[1] if len(sys.argv) > 1 else "现在有哪些 critical 风险？最该先处理哪个？"
    print(run_agent(q))
