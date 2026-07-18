import { useEffect, useMemo, useState } from "react";
import {
  fetchAiFlow,
  fetchCollaborationThreads,
  refToObjectRef,
  type AiFlow,
  type CollabThread,
  type CollaborationThreads,
  type ObjectRef,
  type Role,
  type World,
} from "../api";
import Icon, { type IconName } from "../components/Icons";
import StateHint from "../components/StateHint";
import AiRuns from "./AiRuns";
import { buildStories, KIND_CN, SEV_CN, type Story, type StoryState } from "./aiFlowModel";
import { SEV_RANK } from "./severityRank";

// 页面第二主角：AI 工作流「用户故事卡」流（V10 补记：从事件日志重构为用户视角）。同一风险/任务链的
// 检测→提案→审批/结案 折叠为一张卡——主句人话叙事（醒目金额），点展开=完整时间线（原始事件保序）。
// [sim] 前缀清除（SIM 徽标已表达）、规则码/动作码/severity 人话化、裸 TSK/RSK id 收进「查看详情」。
// 智能感：最新一条呼吸高亮 + 卡片滑入。底部两 tab：AI 工作流（默认）/ 协作流（U6 真数据）。

type Tab = "ai" | "runs" | "collab";

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
  // 思考轨迹「逐步点亮回放」（子任务一）：litCount=null → 静态全亮；数字 → 回放进度（已点亮步数），
  // 从 1 递增到 steps，每 ~600ms 亮一步，最后一步定格。可"跳过"直接全亮。零新依赖、纯 CSS 过渡。
  const steps = story.events.length;
  const [litCount, setLitCount] = useState<number | null>(null);
  const replaying = litCount !== null && litCount < steps;
  const shownLit = litCount === null ? steps : litCount; // 静态时全亮

  useEffect(() => {
    if (litCount === null || litCount >= steps) return; // 静态 / 已放完 → 不再推进
    const t = setTimeout(() => setLitCount((n) => (n === null ? null : n + 1)), 600);
    return () => clearTimeout(t);
  }, [litCount, steps]);

  const startReplay = () => {
    setOpen(true);
    setLitCount(1); // 第一步立即亮，随后逐步点亮
  };
  const skipReplay = () => setLitCount(steps); // 跳过=直接全亮定格
  const toggle = () =>
    setOpen((v) => {
      if (v) setLitCount(null); // 收起时复位回放态，下次展开为静态全亮
      return !v;
    });

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
        {steps > 1 && (
          <div className="cp-story__acts">
            {replaying ? (
              <button className="cp-story__replay is-active" onClick={skipReplay} title="直接看完整时间线">
                跳过
              </button>
            ) : (
              <button className="cp-story__replay" onClick={startReplay} title="逐步点亮 AI 的思考过程">
                <Icon name="play" size={10} />
                {litCount === null && !open ? "回放思考" : "重放"}
              </button>
            )}
            <button className={`cp-story__toggle ${open ? "is-open" : ""}`} onClick={toggle} aria-expanded={open}>
              {open ? "收起" : "展开"}时间线 · {steps} 步
              <Icon name="chevron-right" size={12} />
            </button>
          </div>
        )}
      </div>

      {open && steps > 1 && (
        <ol className="cp-story__timeline">
          {story.events.map((e, i) => {
            const lit = i < shownLit;
            const activating = replaying && i === shownLit - 1; // 当前正点亮的一步：脉冲高亮
            return (
              <li
                key={`${e.ts}-${i}`}
                className={`cp-tl ${lit ? "is-lit" : "is-dim"} ${activating ? "is-activating" : ""}`}
              >
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
            );
          })}
        </ol>
      )}
    </div>
  );
}

// ═══════════════════════════ 协作流真身（U6）═══════════════════════════
// 右栏「协作流」标签：GET /collaboration/threads 真数据——对外协调的跟进线程（改配船期 / 工厂确认
// 交期 / 客户接受拆单…），按风险聚合展示（参与角色 / 最后动态 / 条数）。缺表世界 → available:false
// 空态；0 条 → 空态白话（验证世界 0 条属正常）；有数据 → 按 by_risk 分组渲染。X-World 双世界随顶栏切。
// 留飞书 / 企微 / Slack 实时接入的 UI 形状——数据已真，实时 IM 是后期路线（脚注标注）。

function sevChip(sev: string | null | undefined): { cls: string; text: string } | null {
  if (!sev) return null;
  const rank = SEV_RANK[sev] ?? 0;
  const cls = rank >= 2 ? "cp-chip red" : rank >= 1 ? "cp-chip amber" : "cp-chip";
  return { cls, text: SEV_CN[sev] ?? sev };
}

// a11y + 可点化（P1，李珊任务3 完全失败——协作流 tab 纯只读、卡片不可点）：优先开该线程自己的
// 任务（task_id 更具体），没有任务再退到该线程挂的风险（risk_event_id，分组头已显示同一 id）；
// 两者都没有则不可点（不给假交互）。键盘可达同 WorkQueue 行标准：tabIndex/role/Enter·Space。
function ThreadRow({ t, onOpenObject }: { t: CollabThread; onOpenObject: (r: ObjectRef) => void }) {
  const owner = t.owner || "我方";
  const counterparty = t.counterparty || t.counterparty_type || "对方";
  const esc = (t.escalation_level ?? 0) > 0;
  const taskTitle = t.task && typeof t.task.title === "string" ? (t.task.title as string) : null;
  const ref = refToObjectRef(t.task_id) ?? refToObjectRef(t.risk_event_id);
  const clickable = !!ref;
  const open = () => ref && onOpenObject(ref);
  return (
    <div
      className={`cp-thread ${esc ? "is-esc" : ""} ${clickable ? "is-click" : ""}`}
      onClick={clickable ? open : undefined}
      tabIndex={clickable ? 0 : undefined}
      role={clickable ? "button" : undefined}
      aria-label={clickable ? `打开关联${ref!.type === "Task" ? "任务" : "风险"} ${ref!.id}` : undefined}
      onKeyDown={
        clickable
          ? (e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                open();
              }
            }
          : undefined
      }
    >
      <div className="cp-thread__top">
        <span className="cp-thread__parties">
          <Icon name="users" size={11} /> <span className="num">{owner}</span>
          <Icon name="arrow-right" size={9} />
          <span className="num">{counterparty}</span>
        </span>
        {esc && <span className="cp-thread__esc">升级 L{t.escalation_level}</span>}
        {t.state && <span className="cp-thread__state">{t.state}</span>}
      </div>
      {taskTitle && <div className="cp-thread__task">{taskTitle}</div>}
      <div className="cp-thread__meta num">
        {t.counterparty_type ? `${t.counterparty_type} · ` : ""}
        最后动态 {t.last_update || t.opened_at || "—"}
        {t.next_action_due ? ` · 待办截止 ${t.next_action_due}` : ""}
      </div>
    </div>
  );
}

function CollabPanel({ role, world, onOpenObject }: { role: Role; world?: World | null; onOpenObject: (r: ObjectRef) => void }) {
  const [data, setData] = useState<CollaborationThreads | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setData(null);
    setErr(null);
    fetchCollaborationThreads(role)
      .then((d) => !cancelled && setData(d))
      .catch((e) => !cancelled && setErr((e as Error).message));
    return () => {
      cancelled = true;
    };
  }, [role, world, reload]);

  if (err)
    return (
      <StateHint
        kind="error"
        title="协作流加载失败"
        message="没能取到协作线程。确认 apps/api 服务已启动，再重试。"
        onRetry={() => setReload((n) => n + 1)}
      />
    );
  if (!data) return <StateHint kind="loading" title="协作流加载中…" />;
  if (!data.available)
    return (
      <StateHint
        kind="empty"
        title="协作流未接入"
        reason={data.reason ?? "该世界无协作线程域。"}
        suggestion="切到模拟世界看真实协调线程"
      />
    );
  if (data.count === 0)
    return (
      <StateHint
        kind="empty"
        title="暂无协作线程"
        reason="当前世界没有对外协调的跟进线程（验证世界 0 条属正常）。"
        suggestion="切到模拟世界看真实协作流"
      />
    );

  // 按风险聚合（复用后端 by_risk 排序：条数降序），组内取该风险的线程明细。
  const groups = data.by_risk.map((g) => {
    const key = g.risk_event_id ?? "(未关联风险)";
    const items = data.threads.filter((t) => (t.risk_event_id ?? "(未关联风险)") === key);
    return { g, key, items };
  });

  return (
    <div className="cp-collab-live">
      <div className="cp-collab-bar">
        <span className="cp-collab-bar__n num">{data.count} 条协作线程 · {data.by_risk.length} 个风险</span>
        {data.summary.escalated > 0 && <span className="cp-collab-bar__esc num">{data.summary.escalated} 条已升级</span>}
      </div>
      <div className="cp-collab-groups">
        {groups.map(({ g, key, items }) => {
          const sev = sevChip(g.severity);
          return (
            <div key={key} className="cp-thread-group">
              <div className="cp-thread-group__head">
                <span className="cp-thread-group__risk num">{key}</span>
                {g.rule_id && <span className="cp-thread-group__rule num">{g.rule_id}</span>}
                {sev && <span className={sev.cls}>{sev.text}</span>}
                <span className="cp-thread-group__count">{g.thread_count} 条</span>
              </div>
              {items.map((t) => (
                <ThreadRow key={t.coordination_id} t={t} onOpenObject={onOpenObject} />
              ))}
            </div>
          );
        })}
      </div>
      <div className="cp-collab-foot">
        <Icon name="chat" size={12} /> 数据来自本体协调线程（coordination_threads）。飞书 / 企微 / Slack 实时对话接入是后期路线。
      </div>
    </div>
  );
}

export default function AiWorkflow({
  role,
  asOf,
  world,
  onOpenObject,
}: {
  role: Role;
  asOf?: string | null;
  world?: World | null; // U1：世界切换的重取信号（本组件常驻右栏、不随切世界卸载，故要入 deps）
  onOpenObject: (r: ObjectRef) => void;
}) {
  const [data, setData] = useState<AiFlow | null>(null);
  const [err, setErr] = useState(false);
  const [reload, setReload] = useState(0); // 错误态重试计数（StateHint 重试按钮驱动）
  const [tab, setTab] = useState<Tab>("ai");

  useEffect(() => {
    let cancelled = false;
    setData(null);
    setErr(false);
    // 抓取窗口放大到 200：故事卡按 risk_event 链折叠，链首 detect/propose 可能早于结案数周，
    // 窗口过小会把老链截成只剩 approve/close（主句退化为泛称）——放大窗口让近期链完整重建。
    // asOf（世界时钟回放）：带上则各来源按时间戳≤asOf 过滤，故事卡只呈现"截至当日已发生"的留痕。
    // world 入 deps：切世界时本组件不卸载，靠它触发重取（X-World 头已由 api 模块级单例带上）。
    fetchAiFlow(role, 200, asOf)
      .then((d) => !cancelled && setData(d))
      .catch(() => !cancelled && setErr(true));
    return () => {
      cancelled = true;
    };
  }, [role, asOf, world, reload]);

  const stories = useMemo(() => (data ? buildStories(data.items) : []), [data]);

  return (
    <div className="cp-flow">
      <div className="cp-flow__tabs" role="tablist">
        <button role="tab" className={`cp-flow__tab ${tab === "ai" ? "is-active" : ""}`} aria-selected={tab === "ai"} onClick={() => setTab("ai")}>
          <Icon name="chip" size={13} /> AI 工作流{data ? ` · ${stories.length}` : ""}
        </button>
        <button role="tab" className={`cp-flow__tab ${tab === "runs" ? "is-active" : ""}`} aria-selected={tab === "runs"} onClick={() => setTab("runs")}>
          <Icon name="action" size={13} /> AI 任务
        </button>
        <button role="tab" className={`cp-flow__tab ${tab === "collab" ? "is-active" : ""}`} aria-selected={tab === "collab"} onClick={() => setTab("collab")}>
          <Icon name="users" size={13} /> 协作流
        </button>
      </div>

      {tab === "runs" ? (
        <AiRuns role={role} world={world} onOpenObject={onOpenObject} />
      ) : tab === "collab" ? (
        <div className="cp-collab-tab">
          {/* 诚实导流（P1，李珊任务3 完全失败）：协作流当前纯只读、无法在驾驶舱内催办/记回应——
              明说去处，别让人以为按钮丢了；不承诺接入时间点（"正在接入路上"不给期限）。 */}
          <div className="cp-collab-note">
            <Icon name="chat" size={12} /> 催办、记录对方回应等协调操作，目前仍在 Streamlit 操作台完成；驾驶舱正在接入这部分能力。
          </div>
          <CollabPanel role={role} world={world} onOpenObject={onOpenObject} />
        </div>
      ) : err ? (
        <StateHint
          kind="error"
          title="AI 工作流加载失败"
          message="没能取到 AI 活动留痕。确认 apps/api 服务已启动，再重试。"
          onRetry={() => setReload((n) => n + 1)}
        />
      ) : !data ? (
        <StateHint kind="loading" title="AI 工作流加载中…" />
      ) : stories.length === 0 ? (
        <StateHint
          kind="empty"
          title="该世界暂无 AI 活动留痕"
          reason={`来源探测：${data.sources_present.join(" / ") || "无"}`}
          suggestion="切到模拟世界看连续 14 个月的 AI 处置流"
        />
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
