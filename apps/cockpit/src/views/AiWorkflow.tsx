import { useEffect, useState } from "react";
import { fetchAiFlow, refToObjectRef, type AiFlow, type ObjectRef, type Role } from "../api";
import Icon, { type IconName } from "../components/Icons";

// 页面第二主角：AI 工作流时间线卡片流（不是角落小按钮）。看得见 AI 此刻在做什么，像看同事工位。
// 智能感之一：最新一条呼吸高亮 + 新条目滑入。底部两 tab：AI 工作流（默认）/ 协作流（预留态）。
// 数据来自 GET /cockpit/ai-flow，kind/sim 徽标/ref 链接全部由载荷驱动。V9-B：全 emoji 清零。

const KIND_CN: Record<string, string> = {
  llm_call: "AI 调用",
  ai_action: "AI 执行",
  task_flow: "流转",
  detect: "检测",
  propose: "提案",
  approve: "批准",
  reject: "驳回",
  close: "结案",
};

const KIND_ICON: Record<string, IconName> = {
  llm_call: "llm",
  ai_action: "action",
  task_flow: "flow",
  detect: "detect",
  propose: "propose",
  approve: "approve",
  reject: "reject",
  close: "close",
};

type Tab = "ai" | "collab";

export default function AiWorkflow({ role, onOpenObject }: { role: Role; onOpenObject: (r: ObjectRef) => void }) {
  const [data, setData] = useState<AiFlow | null>(null);
  const [err, setErr] = useState(false);
  const [tab, setTab] = useState<Tab>("ai");

  useEffect(() => {
    let cancelled = false;
    setData(null);
    setErr(false);
    fetchAiFlow(role)
      .then((d) => !cancelled && setData(d))
      .catch(() => !cancelled && setErr(true));
    return () => {
      cancelled = true;
    };
  }, [role]);

  return (
    <div className="cp-flow">
      <div className="cp-flow__tabs" role="tablist">
        <button
          role="tab"
          className={`cp-flow__tab ${tab === "ai" ? "is-active" : ""}`}
          aria-selected={tab === "ai"}
          onClick={() => setTab("ai")}
        >
          <Icon name="chip" size={13} /> AI 工作流{data ? ` · ${data.count}` : ""}
        </button>
        <button
          role="tab"
          className={`cp-flow__tab ${tab === "collab" ? "is-active" : ""}`}
          aria-selected={tab === "collab"}
          onClick={() => setTab("collab")}
        >
          <Icon name="users" size={13} /> 协作流
        </button>
      </div>

      {tab === "collab" ? (
        <div className="cp-collab">
          <div className="cp-collab__icons">
            <Icon name="chat" size={26} />
            <Icon name="mail" size={26} />
            <Icon name="users" size={26} />
          </div>
          <div className="cp-collab__title">人与人的沟通，将在此同屏</div>
          <div className="cp-collab__body">
            飞书 / 企微 / Slack / 邮箱接入后，围绕一个风险的人的对话会与 AI 的处置在同一条时间线上并排。
            本体已有协调线程对象承载，画面先留位——后期路线。
          </div>
          <div className="cp-collab__tag">预留 · 未接入</div>
        </div>
      ) : err ? (
        <div className="cp-fill-msg">AI 工作流加载失败——确认 API 已启动</div>
      ) : !data ? (
        <div className="cp-fill-msg">AI 工作流加载中…</div>
      ) : data.items.length === 0 ? (
        <div className="cp-flow__empty">
          该世界暂无 AI 活动留痕
          <br />
          <span style={{ color: "var(--ink-3)" }}>来源探测：{data.sources_present.join(" / ") || "无"}</span>
        </div>
      ) : (
        <div className="cp-flow__list">
          {data.items.map((it, i) => {
            const ref = refToObjectRef(it.ref_object);
            return (
              <div key={`${it.ts}-${i}`} className={`cp-flow-card ${i === 0 ? "is-latest" : ""}`}>
                <div className="cp-flow-card__top">
                  <span className="cp-flow-card__kind" data-kind={it.kind}>
                    {KIND_ICON[it.kind] && <Icon name={KIND_ICON[it.kind]} size={11} strokeWidth={2} />}
                    {KIND_CN[it.kind] ?? it.kind}
                  </span>
                  {it.sim && <span className="cp-flow-card__sim">SIM</span>}
                  <span className="cp-flow-card__time num">{it.ts}</span>
                </div>
                <div className="cp-flow-card__summary">{it.summary}</div>
                {it.ref_object &&
                  (ref ? (
                    <span className="cp-flow-card__ref" onClick={() => onOpenObject(ref)}>
                      ▸ {it.ref_object}
                    </span>
                  ) : (
                    <span className="cp-flow-card__ref" style={{ color: "var(--ink-2)", cursor: "default" }}>
                      ▸ {it.ref_object}
                    </span>
                  ))}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
