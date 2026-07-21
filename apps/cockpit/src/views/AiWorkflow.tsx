import { useEffect, useMemo, useRef, useState } from "react";
import {
  fetchAiFlow,
  fetchCollaborationThreads,
  postCoordinationAction,
  refToObjectRef,
  type AiFlow,
  type CollabThread,
  type CollaborationThreads,
  type CoordActionId,
  type ObjectRef,
  type Role,
  type World,
} from "../api";
import Icon, { type IconName } from "../components/Icons";
import StateHint from "../components/StateHint";
import { roleLabel } from "../roleActors";
import AiRuns from "./AiRuns";
import { OpenCoordForm } from "./ImpactPanel";
import { buildStories, KIND_CN, MODE_CN, SEV_CN, sortStoriesForRole, type Story, type StoryState } from "./aiFlowModel";
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
        {/* 执行方式徽标（不变量11）：确定性剧本 vs 真模型——仅后端判得了的卡渲染，判不了不显（不编造）。
            "真模型"用信标蓝(AI 信号)、"剧本"用弱化中性——克制、复用既有徽标图案。 */}
        {story.mode && (
          <span className="cp-story__mode" data-mode={story.mode} title={story.mode === "llm" ? "这一步由真模型（LLM）产出" : "这一步由确定性剧本产出，未调用模型"}>
            {MODE_CN[story.mode]}
          </span>
        )}
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

// ═══════════════════ 协调操作区（V22②：催办进驾驶舱）═══════════════════
// 缘起：L-UX 轮2 李珊任务3 完全失败——协作流只读断头路（"系统里没有任何地方能让我实际点一下催一下"）。
// 五个对既有线程的写动作经人类协调通道 POST /collaboration/threads/{id}/actions/{name} 进驾驶舱。
// 按线程当前状态只渲染**状态机合法**的动作（app/coordination_actions.py COORD_TRANSITIONS 的镜像；
// 后端仍是权威——即使镜像漂移，后端会以 422 白话拒绝，绝不静默）。非法动作不渲染而非置灰报错；
// resolved / dead_ended 终态不在表中 = 零动作渲染（只读）。
// CoordActionId 类型 + postCoordinationAction 写通道已下沉 api.ts（V22 余量，见其定义处），此处引用。
const COORD_LEGAL: Record<string, CoordActionId[]> = {
  awaiting: ["record_outreach", "record_response", "escalate_coordination", "resolve_coordination", "mark_dead_ended"],
  responded: ["escalate_coordination", "resolve_coordination", "mark_dead_ended"],
  escalated: ["record_response", "resolve_coordination", "mark_dead_ended"],
};

// 驾驶舱已适配角色中属于协调权限组的（COORD_PERMS.ManageCoordination={ops,cs,procurement,finance,
// compliance} 的前端镜像 ∩ roleActors 已适配集）——V23② compliance 接入（Daniel 批：合规获协调发起/
// 跟进权，缘起轮3合规陌生人"发现问题后系统内无处置入口"）后镜像与后端权限组**严格同集**；
// 老板 manager、sales 仍不在组内 → 只读。后端 COORD_PERMS 仍是权威（越权走 403+denied 审计）。
const COORD_UI_ROLES: Role[] = ["ops", "cs", "procurement", "finance", "compliance"];

const COORD_META: Record<CoordActionId, { label: string; hint: string }> = {
  record_outreach: { label: "催办", hint: "再追一次对方，并重设下一步截止日" },
  record_response: { label: "记回应", hint: "记录对方的回复内容" },
  escalate_coordination: { label: "升级", hint: "升一级处理（如上升到对方主管），可顺带改截止日" },
  resolve_coordination: { label: "达成", hint: "协调有结果——写下结论并归档" },
  mark_dead_ended: { label: "谈崩", hint: "协调无解 / 放弃——写下原因并归档" },
};

// CoordResult 类型 + postCoordinationAction 写通道已下沉 api.ts（V22 余量重构：与 postDecision/
// openCoordination 同风格集中在薄 API 客户端；行为零变化，仅位置迁移+import）。此处直接引用。

// 表单控件统一内联样式（全部取既有 CSS 变量，与主题一致）。
const coordInput: React.CSSProperties = {
  background: "var(--bg-1)",
  border: "1px solid var(--line-strong)",
  borderRadius: "var(--radius-sm)",
  color: "var(--ink-0)",
  fontFamily: "var(--font-ui)",
  fontSize: 11,
  padding: "4px 7px",
};

// 线程卡内嵌操作区：合法动作 chips（点选展开对应小表单）→ 提交 → 成功回执 + 通知父层软刷新
// （状态 / 截止日变化就地可见）。失败显示后端白话错误原文。
function CoordOps({ t, role, onActed }: { t: CollabThread; role: Role; onActed: () => void }) {
  const legal = COORD_LEGAL[t.state ?? ""] ?? [];
  const [act, setAct] = useState<CoordActionId | null>(null);
  const [note, setNote] = useState("");
  const [due, setDue] = useState("");
  const [text, setText] = useState(""); // last_response / outcome 共用（同屏只开一个表单）
  const [busy, setBusy] = useState(false);
  const [receipt, setReceipt] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  // 协调权限组 COORD_PERMS.ManageCoordination = {ops,cs,procurement,finance,compliance}（本体声明，
  // V23② 加合规）——驾驶舱七角色与该权限组严格同集：运营/客服/采购/财务/合规在组内可写，老板/销售
  // 不渲染写动作（诚实只读，不给必 403 的假按钮）；后端仍是权威（绕过 UI 直接 POST 会 403 + denied 审计）。
  if (!COORD_UI_ROLES.includes(role) || legal.length === 0) {
    return receipt ? <div style={{ marginTop: 5, fontSize: 10.5, color: "var(--sev-green)" }}>{receipt}</div> : null;
  }

  const pick = (a: CoordActionId) => {
    setErr(null);
    setReceipt(null);
    setAct((cur) => (cur === a ? null : a));
  };

  const needText = act === "record_response" || act === "resolve_coordination" || act === "mark_dead_ended";
  const needDue = act === "record_outreach";
  const canSubmit = !busy && !!act && (!needText || text.trim() !== "") && (!needDue || due !== "");

  const submit = async () => {
    if (!act || !canSubmit) return;
    const body: Record<string, unknown> =
      act === "record_outreach"
        ? { next_action_due: due, note: note.trim() }
        : act === "record_response"
          ? { last_response: text.trim() }
          : act === "escalate_coordination"
            ? due
              ? { next_action_due: due }
              : {}
            : { outcome: text.trim() }; // resolve_coordination / mark_dead_ended
    setBusy(true);
    setErr(null);
    try {
      const res = await postCoordinationAction(t.coordination_id, act, body, role);
      setReceipt(res.side_effects[0] ?? "已完成");
      setAct(null);
      setNote("");
      setDue("");
      setText("");
      onActed(); // 软刷新线程列表：本卡状态 / 截止日 / 跟催次数就地更新（卡片 key 稳定不重挂）
    } catch (e) {
      setErr((e as Error).message); // 后端白话错误原文（403 无权 / 422 非法转移…），不吞不美化
    } finally {
      setBusy(false);
    }
  };

  return (
    // 操作区在可点行内：拦截冒泡，点按钮/输入框不触发行的"打开关联对象"（含键盘事件）
    <div onClick={(e) => e.stopPropagation()} onKeyDown={(e) => e.stopPropagation()} style={{ marginTop: 6 }}>
      <div style={{ display: "flex", gap: 5, flexWrap: "wrap" }}>
        {legal.map((a) => (
          <button
            key={a}
            className={`cp-story__replay ${act === a ? "is-active" : ""}`}
            title={COORD_META[a].hint}
            onClick={() => pick(a)}
            disabled={busy}
          >
            {COORD_META[a].label}
          </button>
        ))}
      </div>

      {act && (
        <div style={{ display: "flex", flexDirection: "column", gap: 5, marginTop: 6 }}>
          <span style={{ fontSize: 10.5, color: "var(--ink-2)" }}>{COORD_META[act].hint}</span>
          {act === "record_outreach" && (
            <>
              <input
                style={coordInput}
                type="text"
                placeholder="催办备注（如：已再次邮件跟催工厂）"
                value={note}
                onChange={(e) => setNote(e.target.value)}
              />
              <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 10.5, color: "var(--ink-1)" }}>
                新截止日
                <input style={coordInput} type="date" value={due} onChange={(e) => setDue(e.target.value)} required />
              </label>
            </>
          )}
          {act === "escalate_coordination" && (
            <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 10.5, color: "var(--ink-1)" }}>
              新截止日（可选）
              <input style={coordInput} type="date" value={due} onChange={(e) => setDue(e.target.value)} />
            </label>
          )}
          {needText && (
            <textarea
              style={{ ...coordInput, minHeight: 40, resize: "vertical" }}
              placeholder={
                act === "record_response"
                  ? "对方回应内容（如：工厂确认可改期到 8/30 出货）"
                  : act === "resolve_coordination"
                    ? "结论（如：客户接受拆单先发，剩余月底补齐）"
                    : "放弃 / 无解原因（如：工厂明确无法改期，转备选方案）"
              }
              value={text}
              onChange={(e) => setText(e.target.value)}
            />
          )}
          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <button className="cp-story__replay" onClick={submit} disabled={!canSubmit}>
              {busy ? "提交中…" : `确认${COORD_META[act].label}`}
            </button>
            <button className="cp-story__toggle" onClick={() => pick(act)} disabled={busy}>
              取消
            </button>
          </div>
        </div>
      )}

      {receipt && <div style={{ marginTop: 5, fontSize: 10.5, color: "var(--sev-green)" }}>{receipt}</div>}
      {err && <div style={{ marginTop: 5, fontSize: 10.5, color: "var(--sev-red)" }}>{err}</div>}
    </div>
  );
}

// a11y + 可点化（P1，李珊任务3 完全失败——协作流 tab 纯只读、卡片不可点）：优先开该线程自己的
// 任务（task_id 更具体），没有任务再退到该线程挂的风险（risk_event_id，分组头已显示同一 id）；
// 两者都没有则不可点（不给假交互）。键盘可达同 WorkQueue 行标准：tabIndex/role/Enter·Space。
function ThreadRow({
  t,
  role,
  onOpenObject,
  onActed,
}: {
  t: CollabThread;
  role: Role;
  onOpenObject: (r: ObjectRef) => void;
  onActed: () => void;
}) {
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
              if (e.target !== e.currentTarget) return; // 操作区输入框里的 Enter/Space 不触发开卡
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
      <CoordOps t={t} role={role} onActed={onActed} />
    </div>
  );
}

function CollabPanel({ role, world, reloadSignal, onOpenObject }: { role: Role; world?: World | null; reloadSignal?: number; onOpenObject: (r: ObjectRef) => void }) {
  const [data, setData] = useState<CollaborationThreads | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  // 软刷新（V22② 操作回执后）：重取线程但**不清屏**——保留现有卡片就地换数据（卡片 key 稳定不重挂，
  // 操作回执不丢），与硬重试 reload（错误态清屏加载）分开计数；用 ref 区分本次触发是软是硬。
  const [refresh, setRefresh] = useState(0);
  const softSeen = useRef(0);

  // C·P1：协作流 tab 顶部发起协调成功 → reloadSignal 变 → 硬刷新列表让新线程就地可见（含 0 条→1 条）。
  useEffect(() => {
    let cancelled = false;
    const soft = refresh !== softSeen.current;
    softSeen.current = refresh;
    if (!soft) {
      setData(null);
      setErr(null);
    }
    fetchCollaborationThreads(role)
      .then((d) => !cancelled && setData(d))
      .catch((e) => !cancelled && setErr((e as Error).message));
    return () => {
      cancelled = true;
    };
  }, [role, world, reload, refresh, reloadSignal]);

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
                <ThreadRow
                  key={t.coordination_id}
                  t={t}
                  role={role}
                  onOpenObject={onOpenObject}
                  onActed={() => setRefresh((n) => n + 1)}
                />
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
  const [collabReload, setCollabReload] = useState(0); // C·P1：协作流 tab 顶部发起协调成功后刷新线程列表

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
  // D·P1：按角色职责相关性稳定排序（相关规则族排前、其余在后，各组内保持时间序；manager/sales 不重排）。
  // 只重排展示序、不隐藏任何条目；reordered=false（无相关条目/不重排角色）时不显"排前"提示，避免空承诺。
  const { stories: sortedStories, reordered } = useMemo(() => sortStoriesForRole(stories, role), [stories, role]);
  // 呼吸高亮跟真·最新（buildStories 已按 ts 降序，stories[0] 即最新）——重排后不随位置跑偏到旧卡。
  const newestKey = stories[0]?.key;

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
          {/* 诚实横幅（C·P1 轮4 更新，缘起合规首屏三卡下钻到不了 ImpactPanel 发起入口的断路）：发起新
              协调线程现在**本 tab 顶部**即可（下方"发起协调"，需锚一个处置任务号）；影响面板「处置任务」
              区块的入口仍在（有预选锚更省事）。协调权限组：运营/客服/采购/财务/合规；老板/销售只读。 */}
          <div className="cp-collab-note">
            <Icon name="chat" size={12} /> 催办 / 记回应 / 升级 / 达成 / 谈崩在下方线程卡直接完成；发起新协调线程点下方「发起协调」（需锚一个处置任务号）。协调权限组：运营/客服/采购/财务/合规；老板/销售只读。
          </div>
          {/* C·P1：协作流 tab 顶部发起协调入口（无预选锚，输入任务号）——修复合规/其它协调角色从首屏
              下钻到不了发起入口的断路。用本地 COORD_UI_ROLES 门控外层容器（与 OpenCoordForm 自门控同集），
              非协调角色（老板/销售）整块不渲染，不留空边框盒。 */}
          {COORD_UI_ROLES.includes(role) && (
            <div className="cp-collab-initiate">
              <OpenCoordForm role={role} onOpened={() => setCollabReload((n) => n + 1)} />
            </div>
          )}
          <CollabPanel role={role} world={world} reloadSignal={collabReload} onOpenObject={onOpenObject} />
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
          {reordered && (
            <div className="cp-flow__relnote" title="按当前角色职责域的规则族把'更可能是你的活'提前——全部条目仍完整展示，未隐藏任何内容">
              <Icon name="spark" size={11} /> 已把与你（{roleLabel(role)}）职责相关的排前 · 全部条目仍完整展示
            </div>
          )}
          {sortedStories.map((s) => (
            <StoryCard key={s.key} story={s} latest={s.key === newestKey} onOpenObject={onOpenObject} />
          ))}
        </div>
      )}
    </div>
  );
}
