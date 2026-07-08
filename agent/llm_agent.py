"""W6 可插拔 LLM 层：python3 -m agent.llm_agent "你的问题"

把 TOOL_DEFS 注册给任何支持 tool-use 的 LLM，模型只能通过 AgentSession.dispatch
接触数据。默认 provider 为 OpenAI API（AGENT_PROVIDER=openai，AGENT_MODEL=gpt-5.5）。
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


def run_agent(question, session=None, max_turns=8, verbose=True):
    session = session or AgentSession()
    provider = os.environ.get("AGENT_PROVIDER", "openai").lower()
    if provider == "openai":
        return _run_openai(question, session, max_turns, verbose)
    if provider == "anthropic":
        return _run_anthropic(question, session, max_turns, verbose)
    raise SystemExit(f"不支持的 AGENT_PROVIDER={provider}（可选：openai / anthropic）")


if __name__ == "__main__":
    q = sys.argv[1] if len(sys.argv) > 1 else "现在有哪些 critical 风险？最该先处理哪个？"
    print(run_agent(q))
