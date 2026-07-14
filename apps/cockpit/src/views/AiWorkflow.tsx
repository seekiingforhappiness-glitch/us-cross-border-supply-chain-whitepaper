import { useEffect, useMemo, useState } from "react";
import { fetchAiFlow, type AiFlow, type ObjectRef, type Role } from "../api";
import Icon, { type IconName } from "../components/Icons";
import { buildStories, KIND_CN, type Story, type StoryState } from "./aiFlowModel";

// 页面第二主角：AI 工作流「用户故事卡」流（V10 补记：从事件日志重构为用户视角）。同一风险/任务链的
// 检测→提案→审批/结案 折叠为一张卡——主句人话叙事（醒目金额），点展开=完整时间线（原始事件保序）。
// [sim] 前缀清除（SIM 徽标已表达）、规则码/动作码/severity 人话化、裸 TSK/RSK id 收进「查看详情」。
// 智能感：最新一条呼吸高亮 + 卡片滑入。底部两 tab：AI 工作流（默认）/ 协作流（预留态）。

type Tab = "ai" | "collab";

// 故事状态 → 徽标（人话 + 图标 + 复用既有 data-kind 配色）
const STATE_META: Record<StoryState, { label: string; icon: IconName; dataKind: string }> = {
  detected: { label: "检测中", icon: "detect", dataKind: "detect" },
  proposed: { label: "待审批", icon: "propose", dataKind: "propose" },
  approved: { label: "处置中", icon: "approve", dataKind: "approve" },
  rejected: { label: "已驳回", icon: "reject", dataKind: "reject" },
  closed: { label: "已结案", icon: "close", dataKind: "close" },
  info: { label: "活动", icon: "flow", dataKind: "task_flow" },
};

const TL_ICON: Record<string, IconName> = {
  detect: "detect", propose: "propose", approve: "approve", reject: "reject", close: "close",
  llm_call: "llm", ai_action: "action", task_flow: "flow",
};

function StoryCard({ story, latest, onOpenObject }: { story: Story; latest: boolean; onOpenObject: (r: ObjectRef) => void }) {
  const [open, setOpen] = useState(false);
  const meta = STATE_META[story.state];
  const infoLabel = story.state === "info" ? KIND_CN[story.events[story.events.length - 1]?.kind] ?? meta.label : meta.label;
  return (
    <div className={`cp-story ${latest ? "is-latest" : ""}`}>
      <div className="cp-story__top">
        <span className="cp-story__state" data-kind={meta.dataKind}>
          <Icon name={meta.icon} size={11} strokeWidth={2} />
          {infoLabel}
        </span>
        {story.sim && <span className="cp-flow-card__sim">SIM</span>}
        <span className="cp-story__time num">{story.ts}</span>
      </div>

      <div className="cp-story__line">
        {story.lead}
        {story.amount && <b className="cp-story__amount num">{story.amount}</b>}
        {story.trail}
      </div>

      <div className="cp-story__foot">
        {story.links.map((l) => (
          <button key={l.ref.id} className="cp-story__detail" onClick={() => onOpenObject(l.ref)}>
            查看详情 <span className="num">{l.ref.id}</span>
            <Icon name="arrow-right" size={11} />
          </button>
        ))}
        {story.events.length > 1 && (
          <button className={`cp-story__toggle ${open ? "is-open" : ""}`} onClick={() => setOpen((v) => !v)} aria-expanded={open}>
            {open ? "收起" : "展开"}时间线 · {story.events.length} 步
            <Icon name="chevron-right" size={12} />
          </button>
        )}
      </div>

      {open && story.events.length > 1 && (
        <ol className="cp-story__timeline">
          {story.events.map((e, i) => (
            <li key={`${e.ts}-${i}`} className="cp-tl">
              <span className="cp-tl__dot" data-kind={e.kind}>
                <Icon name={TL_ICON[e.kind] ?? "flow"} size={10} strokeWidth={2} />
              </span>
              <div className="cp-tl__body">
                <div className="cp-tl__head">
                  <span className="cp-tl__kind">{e.kindLabel}</span>
                  <span className="cp-tl__time num">{e.ts}</span>
                </div>
                <div className="cp-tl__text">{e.text}</div>
                {e.ref && (
                  <span className="cp-tl__ref num" onClick={() => onOpenObject(e.ref!)}>
                    ▸ {e.ref.id}
                  </span>
                )}
              </div>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

export default function AiWorkflow({ role, onOpenObject }: { role: Role; onOpenObject: (r: ObjectRef) => void }) {
  const [data, setData] = useState<AiFlow | null>(null);
  const [err, setErr] = useState(false);
  const [tab, setTab] = useState<Tab>("ai");

  useEffect(() => {
    let cancelled = false;
    setData(null);
    setErr(false);
    // 抓取窗口放大到 200：故事卡按 risk_event 链折叠，链首 detect/propose 可能早于结案数周，
    // 窗口过小会把老链截成只剩 approve/close（主句退化为泛称）——放大窗口让近期链完整重建。
    fetchAiFlow(role, 200)
      .then((d) => !cancelled && setData(d))
      .catch(() => !cancelled && setErr(true));
    return () => {
      cancelled = true;
    };
  }, [role]);

  const stories = useMemo(() => (data ? buildStories(data.items) : []), [data]);

  return (
    <div className="cp-flow">
      <div className="cp-flow__tabs" role="tablist">
        <button role="tab" className={`cp-flow__tab ${tab === "ai" ? "is-active" : ""}`} aria-selected={tab === "ai"} onClick={() => setTab("ai")}>
          <Icon name="chip" size={13} /> AI 工作流{data ? ` · ${stories.length}` : ""}
        </button>
        <button role="tab" className={`cp-flow__tab ${tab === "collab" ? "is-active" : ""}`} aria-selected={tab === "collab"} onClick={() => setTab("collab")}>
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
      ) : stories.length === 0 ? (
        <div className="cp-flow__empty">
          该世界暂无 AI 活动留痕
          <br />
          <span style={{ color: "var(--ink-3)" }}>来源探测：{data.sources_present.join(" / ") || "无"}</span>
        </div>
      ) : (
        <div className="cp-flow__list">
          {stories.map((s, i) => (
            <StoryCard key={s.key} story={s} latest={i === 0} onOpenObject={onOpenObject} />
          ))}
        </div>
      )}
    </div>
  );
}
