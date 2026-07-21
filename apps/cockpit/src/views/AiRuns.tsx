import { useEffect, useState } from "react";
import {
  fetchRuntimeRunDetail,
  fetchRuntimeRuns,
  killRuntimeRun,
  refToObjectRef,
  resumeRuntimeRun,
  startRuntimeRun,
  type ObjectRef,
  type Role,
  type RuntimeBudget,
  type RuntimeRunDetail,
  type RuntimeRunEnvelope,
  type RuntimeRunListItem,
  type RuntimeStep,
  type World,
} from "../api";
import Icon, { type IconName } from "../components/Icons";
import StateHint from "../components/StateHint";
import { actorForRole } from "../roleActors";

// 规格④ 驾驶舱 AI 处置正脸 · 右栏「AI 任务」区（+ ImpactPanel 的「让 AI 处置」按钮）。
// 后端契约：apps/api/runtime.py（本组件不改一行后端）——list/detail 只读、start/resume/kill 三写。
//
// 白话（这一块给用户看什么）：AI「处置差事」(run) 就是让 AI 按剧本处置一个风险——它查详情、派单、
// 提交处置提案，然后**停在「待拍板」等人批**（提案-only，AI 绝不自行批准=冻结区不可达）。本区把每趟
// 差事跑到哪、每步干了什么、预算还剩多少摊开给人看；等审批的高亮提示去拍板；批完点「继续执行」让 AI
// 做写后复读核实；经理可「急停」。全部走 StateHint 诚实空态、双世界跟随 X-World。
//
// 为什么这样建（≤5 行，AGENTS.md §4）：
//   · 列表轻量（只读 GET /runtime/runs），步时间线按需在展开时才拉详情（GET …/{id}）——不做 N+1 轮询，
//     run 不自走（start 同步推进、resume 手动），无需轮询即可反映真状态。
//   · 权限双层：kill 前端对非 manager 灰态 + 后端 X-Role 独立鉴权 403（灰态只是体验预判，不放松后端）。
//   · 幂等：启动前先查本世界是否已有针对该风险的 run（AiDispatchButton），避免造出必然 failed 的垃圾 run。

// ── run 状态 → 徽章语义色（复用既有语义色盘：琥珀=需人、绿=成、红=停/杀、蓝=在跑） ──
type StatusTone = "neutral" | "beacon" | "amber" | "green" | "red";
const STATUS_TONE: Record<string, StatusTone> = {
  created: "neutral",
  running: "beacon",
  waiting_approval: "amber",
  done: "green",
  failed: "red",
  timeout: "amber",
  budget_exhausted: "amber",
  killed: "red",
};
const TERMINAL = new Set(["done", "failed", "timeout", "budget_exhausted", "killed"]);
// 风险终态（镜像 app/actions.py::RISK_TERMINAL，前端预判用）：已终态风险无需再发起 AI 处置。
const RISK_TERMINAL = new Set(["resolved", "escalated"]);

// ── 步 kind → 时间线圆点配色/图标（复用 cp-tl__dot 既有 data-kind 色：propose=琥珀 写入/等人、
//    approve=绿 写后复读；think/tool 落默认信标蓝）——不新增色板 ──
const STEP_DOT_KIND: Record<string, string> = {
  think: "think",
  tool: "tool",
  command: "propose",
  wait: "propose",
  verify: "approve",
};
const STEP_ICON: Record<string, IconName> = {
  think: "chip",
  tool: "detect",
  command: "action",
  wait: "stamp",
  verify: "approve",
};

// 时间戳（真实 UTC ISO）→ 紧凑展示：去 T/毫秒/Z。null → —。
function fmtTs(s: string | null | undefined): string {
  if (!s) return "—";
  return s.replace("T", " ").replace(/\.\d+/, "").replace("Z", "");
}

// goal（"处置 RSK-0007"）里的对象号 → 可点对象引用（开风险卡）；无法映射 → null。
// 修复（P1·正则截断404）：原 /[A-Z]{2,}-[A-Za-z0-9]+/ 只贪一段，遇多段号（模拟世界 RSK-SIM-00012）
// 只吃到 "RSK-SIM" 丢末段编号，refToObjectRef 拿半截 id 打开风险卡必 404。改 (?:-…)+ 贪全部连字段，
// 完整命中 RSK-SIM-00012（单段号 RSK-0007 仍照常匹配）。
function goalRef(goal: string): ObjectRef | null {
  const m = goal.match(/[A-Z]{2,}(?:-[A-Za-z0-9]+)+/);
  return m ? refToObjectRef(m[0]) : null;
}

// 轮2·P1-4（动线）：从 run summary 里提取处置任务号 → 可点对象引用，让"派单失败：这个风险已有处置任务
// TSK-xxx 在办"里的任务号可一键打开（此前只给了编号点不了）。用 (?:-…)+ 贪全部连字段，兼容两段式号
// （TSK-2026-0099，别退化成轮1 修过的"只吃一段"）；单段号（TSK-FF98F24AD9）照常命中。
function taskRefFromText(text: string | null | undefined): ObjectRef | null {
  if (!text) return null;
  const m = text.match(/TSK(?:-[A-Za-z0-9]+)+/);
  return m ? refToObjectRef(m[0]) : null;
}

// 轮2·P1-4（时间线不漏内部码）：派单命令步的原样错误若命中"单风险单任务（D9/C4）"整句 → 业务短语；
// 内部代号只留在步 result_json（审计可查），不进用户可见时间线文案。其余错误原样保留（不过度改写）。
function humanizeStepErr(err: string): string {
  const m = err.match(/^已存在非终态任务 (\S+)（单风险单任务，D9\/C4）$/);
  return m ? `该风险已有处置任务 ${m[1]} 在办，不能重复派单` : err;
}

// ── 状态徽章 ──
function StatusBadge({ status, label, killed }: { status: string; label: string; killed?: number }) {
  const tone = STATUS_TONE[status] ?? "neutral";
  const killedNote = killed && status !== "killed"; // 终态 run 上留了杀令痕（"有人下过杀令"这个事实本身可查）
  return (
    <span className={`cp-run__badge is-${tone}`} title={killedNote ? "该 run 上留有人工杀令痕迹" : undefined}>
      {label}
      {killedNote ? " · 留杀令痕" : ""}
    </span>
  );
}

// ── 预算条：步数用量（列表/详情共用）。接近耗尽 → 琥珀提醒 ──
function BudgetBar({ budget }: { budget: RuntimeBudget }) {
  const used = budget.steps_used ?? 0;
  const max = budget.max_steps ?? 0;
  const pct = max > 0 ? Math.min(100, Math.round((used / max) * 100)) : 0;
  const tone = pct >= 80 ? "amber" : "beacon";
  const secTip = budget.seconds_remaining != null ? ` · 剩余 ${budget.seconds_remaining}s` : "";
  return (
    <div className="cp-run__budget" title={`步数 ${used}/${max || "?"}${secTip}（等人审批的时间不吃预算）`}>
      <div className="cp-run__budget-track">
        <div className={`cp-run__budget-fill is-${tone}`} style={{ width: `${max > 0 ? pct : 0}%` }} />
      </div>
      <span className="cp-run__budget-txt num">
        {used}/{max || "?"} 步
      </span>
    </div>
  );
}

// ── 单步白话文案（防御式取字段——payload/result 各步形状不同，缺字段不炸） ──
function stepText(s: RuntimeStep): string {
  const p = s.payload || {};
  const r = s.result || {};
  const args = (p.args && typeof p.args === "object" ? p.args : {}) as Record<string, unknown>;
  switch (s.kind) {
    case "think": {
      const txt = typeof r.text === "string" ? r.text : typeof r.note === "string" ? r.note : "";
      const mode = typeof r.mode === "string" ? r.mode : null;
      return txt || (mode === "llm" ? "AI 思考（模型作答）" : "AI 思考（确定性剧本说明）");
    }
    case "tool": {
      const tool = typeof p.tool === "string" ? p.tool : "查询";
      const rid = args.risk_event_id ?? args.object_id ?? "";
      return `只读查询 ${tool}${rid ? ` · ${rid}` : ""}（不改数据）`;
    }
    case "command": {
      const tool = typeof p.tool === "string" ? p.tool : "写入";
      const ok = r.ok === true;
      const oid = typeof r.object_id === "string" ? r.object_id : null;
      const err = typeof r.error === "string" ? humanizeStepErr(r.error) : null;
      const verb = tool === "assign_task" ? "派单" : tool === "propose_mitigation" ? "提交处置提案" : tool;
      return `经命令总线${verb}${ok ? `：成功${oid ? ` → ${oid}` : ""}` : `：未成功${err ? ` · ${err}` : ""}`}`;
    }
    case "wait": {
      const tid = typeof p.task_id === "string" ? p.task_id : null;
      return `提案已提交，停下等人拍板${tid ? `（任务 ${tid}）` : ""}——等审批不烧预算。`;
    }
    case "verify": {
      const ok = r.ok === true;
      const note = typeof r.note === "string" ? r.note : null;
      return note || (ok ? "写后复读核实通过：副作用真的落库了（白话：批完后 AI 回头查了数据库，确认这事真办成了才报完成）。" : "写后复读未通过：AI 回查数据库没见到预期结果，如实报告不硬说成功。");
    }
    default:
      return s.kind_label;
  }
}

// 步 → 可点对象引用（结果 object_id / 参数 risk_event_id / task_id 命中前缀才给链接）。
function stepRef(s: RuntimeStep): ObjectRef | null {
  const r = s.result || {};
  const p = s.payload || {};
  const args = (p.args && typeof p.args === "object" ? p.args : {}) as Record<string, unknown>;
  const candidates = [r.object_id, args.risk_event_id, p.task_id, args.task_id];
  for (const c of candidates) {
    if (typeof c === "string") {
      const ref = refToObjectRef(c);
      if (ref) return ref;
    }
  }
  return null;
}

// ── 步时间线（复用 AI 工作流的「逐步点亮」语言：展开即回放，可跳过/重放） ──
function RunTimeline({ steps, onOpenObject }: { steps: RuntimeStep[]; onOpenObject: (r: ObjectRef) => void }) {
  const n = steps.length;
  const [litCount, setLitCount] = useState<number | null>(null); // null=静态全亮
  const replaying = litCount !== null && litCount < n;
  const shownLit = litCount === null ? n : litCount;

  useEffect(() => {
    if (litCount === null || litCount >= n) return; // 静态 / 已放完 → 不再推进
    const t = setTimeout(() => setLitCount((c) => (c === null ? null : c + 1)), 600);
    return () => clearTimeout(t);
  }, [litCount, n]);

  if (n === 0) return <div className="cp-run__nosteps">这趟差事还没有落账的步骤。</div>;

  return (
    <>
      <div className="cp-run__tl-ctl">
        {replaying ? (
          <button className="cp-story__replay is-active" onClick={() => setLitCount(n)} title="直接看完整时间线">
            跳过
          </button>
        ) : (
          <button className="cp-story__replay" onClick={() => setLitCount(1)} title="逐步点亮 AI 的处置过程">
            <Icon name="play" size={10} /> 回放
          </button>
        )}
      </div>
      <ol className="cp-story__timeline">
        {steps.map((e, i) => {
          const lit = i < shownLit;
          const activating = replaying && i === shownLit - 1;
          const ref = stepRef(e);
          return (
            <li key={e.step_no} className={`cp-tl ${lit ? "is-lit" : "is-dim"} ${activating ? "is-activating" : ""}`}>
              <span className="cp-tl__dot" data-kind={STEP_DOT_KIND[e.kind] ?? e.kind}>
                <Icon name={STEP_ICON[e.kind] ?? "flow"} size={10} strokeWidth={2} />
              </span>
              <div className="cp-tl__body">
                <div className="cp-tl__head">
                  <span className="cp-tl__kind">{e.kind_label}</span>
                  <span className="cp-tl__time num">{fmtTs(e.created_at)}</span>
                </div>
                <div className="cp-tl__text">{stepText(e)}</div>
                {ref && (
                  <button type="button" className="cp-tl__ref num" onClick={() => onOpenObject(ref)}
                    title={`打开 ${ref.type} 详情卡`}>
                    ▸ {ref.id}
                  </button>
                )}
              </div>
            </li>
          );
        })}
      </ol>
    </>
  );
}

// ── 急停按钮（manager 专属，二次确认防误触，同 CloseRisk 内联态而非原生弹窗） ──
function KillButton({ runId, role, onKilled }: { runId: string; role: Role; onKilled: () => void }) {
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const canKill = role === "manager";

  if (!canKill) {
    // 权限双层（前端灰态 + 后端独立鉴权）：非 manager 置灰按钮 + 白话，真正闸门仍在后端（X-Role≠manager→403）。
    return (
      <>
        <button className="cp-run__kill-btn" disabled>
          <Icon name="warn" size={11} /> 急停
        </button>
        <StateHint
          kind="no-permission"
          compact
          title="需经理角色才能急停"
          roleHint="急停（kill）是 manager 专属的人类安全控制：顶栏切到「老板 manager」才能强停一趟 AI 任务；后端独立鉴权。"
        />
      </>
    );
  }

  const doKill = async () => {
    if (busy) return;
    setBusy(true);
    setErr(null);
    try {
      await killRuntimeRun(role, actorForRole(role), runId);
      onKilled(); // 成功：刷新列表与详情，徽章转 killed
    } catch (e) {
      setErr((e as Error).message); // 后端白话中文原文（如非 manager 会 403，正常路径下已灰态挡住）
      setBusy(false);
    }
  };

  if (!confirming) {
    return (
      <button className="cp-run__kill-btn" onClick={() => setConfirming(true)}>
        <Icon name="warn" size={11} /> 急停
      </button>
    );
  }
  return (
    <div className="cp-run__kill-confirm">
      <span className="cp-run__kill-ask">确定急停这趟 AI 任务？停后不再推进（留痕可查，不可恢复）。</span>
      <div className="cp-decide__row">
        <button className="cp-decide-btn cp-decide-btn--reject" disabled={busy} onClick={doKill}>
          {busy ? "急停中…" : "确认急停"}
        </button>
        <button
          className="cp-decide-btn"
          disabled={busy}
          onClick={() => {
            setConfirming(false);
            setErr(null);
          }}
        >
          取消
        </button>
      </div>
      {err && (
        <div className="cp-decide__err">
          <b>没急停成功</b> · {err}
        </div>
      )}
    </div>
  );
}

// resume 结果 → 白话一行（三分支：pending 如实等 / approved 写后复读结论 / rejected 失败原因）。
function resumeResultText(env: RuntimeRunEnvelope): string {
  if (env.status === "waiting_approval" && env.approval_status === "pending")
    return env.note || "提案仍在等人审批（pending）：未推进、未耗预算，批完再来点继续。";
  if (env.status === "done") return env.summary || "处置完成：写后复读核实通过，副作用真的落库了。";
  if (env.status === "failed") return env.summary || env.note || "已停止（提案被驳回或走不下去）。";
  return env.summary || env.note || env.status_label;
}

// ── 单个 run 卡（折叠概览 + 展开步时间线 + 审批联动继续执行 + 急停） ──
function RunRow({ run, role, onOpenObject, onReload }: { run: RuntimeRunListItem; role: Role; onOpenObject: (r: ObjectRef) => void; onReload: () => void }) {
  const [open, setOpen] = useState(false);
  const [detail, setDetail] = useState<RuntimeRunDetail | null>(null);
  const [detailErr, setDetailErr] = useState<string | null>(null);
  const [detailTick, setDetailTick] = useState(0);
  const [resuming, setResuming] = useState(false);
  const [resumeMsg, setResumeMsg] = useState<string | null>(null);
  const [resumeErr, setResumeErr] = useState<string | null>(null);

  const isWaiting = run.status === "waiting_approval";
  const isTerminal = TERMINAL.has(run.status);
  const ref = goalRef(run.goal);
  const plain = run.summary; // 最后一步白话（终态/等审批时后端写入 summary；未有则下方回退状态标签）
  // P1-4 动线：summary 里点名的处置任务号（派单失败"已有任务 TSK-xxx 在办"/等审批/已完成都可能带）→ 可点打开
  const openTaskRef = taskRefFromText(plain);

  // 展开即拉详情（时间线全量 + think 步 mode）。只在打开且尚无数据/被要求刷新时拉。
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setDetailErr(null);
    fetchRuntimeRunDetail(role, run.run_id)
      .then((d) => !cancelled && setDetail(d))
      .catch((e) => !cancelled && setDetailErr((e as Error).message));
    return () => {
      cancelled = true;
    };
  }, [open, role, run.run_id, detailTick]);

  const thinkMode = detail?.steps.find((s) => s.kind === "think" && s.result && typeof s.result.mode === "string")?.result?.mode as
    | string
    | undefined;

  const doResume = async () => {
    if (resuming) return;
    setResuming(true);
    setResumeErr(null);
    setResumeMsg(null);
    try {
      const env = await resumeRuntimeRun(role, actorForRole(role), run.run_id);
      setResumeMsg(resumeResultText(env));
      setDetailTick((n) => n + 1); // 刷新本卡详情（新增 verify 步等）
      onReload(); // 刷新列表徽章（可能已 done/failed）
    } catch (e) {
      setResumeErr((e as Error).message);
    } finally {
      setResuming(false);
    }
  };

  const afterKill = () => {
    setDetailTick((n) => n + 1);
    onReload();
  };

  return (
    <div className={`cp-run ${isWaiting ? "is-waiting" : ""} ${run.status === "killed" ? "is-killed" : ""}`}>
      <div className="cp-run__top">
        <StatusBadge status={run.status} label={run.status_label} killed={run.killed} />
        {thinkMode && <span className={`cp-run__mode is-${thinkMode === "llm" ? "llm" : "det"}`}>{thinkMode === "llm" ? "AI 模型" : "确定性剧本"}</span>}
        <span className="cp-run__time num">{fmtTs(run.updated_at)}</span>
      </div>

      <div className="cp-run__goal">
        <Icon name="chip" size={12} />
        {ref ? (
          <button className="cp-run__goal-link num" onClick={() => onOpenObject(ref)} title={`打开 ${ref.type} ${ref.id}`}>
            {run.goal}
          </button>
        ) : (
          <span className="num">{run.goal}</span>
        )}
      </div>

      <BudgetBar budget={run.budget} />

      <div className="cp-run__last">{plain || run.status_label}</div>

      {isWaiting && (
        <div className="cp-run__waitbanner">
          <Icon name="stamp" size={12} /> 提案在「待拍板」等人批——去决策区批准/驳回后，回这里点「继续执行」。
        </div>
      )}

      <div className="cp-run__acts">
        {run.steps > 0 && (
          <button className={`cp-story__toggle ${open ? "is-open" : ""}`} onClick={() => setOpen((v) => !v)} aria-expanded={open}>
            {open ? "收起" : "展开"}时间线 · {run.steps} 步
            <Icon name="chevron-right" size={12} />
          </button>
        )}
        {openTaskRef && (
          <button className="cp-run__goal-link num" onClick={() => onOpenObject(openTaskRef)} title={`打开任务 ${openTaskRef.id}`}>
            打开任务 {openTaskRef.id} →
          </button>
        )}
        {isWaiting && (
          <button className="cp-run__resume" disabled={resuming} onClick={doResume}>
            <Icon name="play" size={11} /> {resuming ? "继续中…" : "继续执行"}
          </button>
        )}
      </div>

      {resumeMsg && (
        <div className="cp-run__resume-msg">
          <b>继续执行结果</b> · {resumeMsg}
        </div>
      )}
      {resumeErr && (
        <div className="cp-decide__err">
          <b>没能继续</b> · {resumeErr}
        </div>
      )}

      {open && (
        <div className="cp-run__detail">
          {detailErr ? (
            <StateHint kind="error" compact title="时间线加载失败" message={detailErr} onRetry={() => setDetailTick((n) => n + 1)} />
          ) : !detail ? (
            <StateHint kind="loading" compact title="加载时间线…" />
          ) : (
            <>
              <div className="cp-run__detail-meta num">
                预算：步 {detail.budget.steps_used}/{detail.budget.max_steps ?? "?"} · 工具{" "}
                {detail.budget.tool_calls_used ?? "?"}/{detail.budget.max_tool_calls ?? "?"} · 秒{" "}
                {detail.budget.spent_seconds}/{detail.budget.max_seconds ?? "?"}
              </div>
              <RunTimeline steps={detail.steps} onOpenObject={onOpenObject} />
            </>
          )}
        </div>
      )}

      {!isTerminal && (
        <div className="cp-run__kill">
          <KillButton runId={run.run_id} role={role} onKilled={afterKill} />
        </div>
      )}
    </div>
  );
}

// run 排序：需人（等审批）置顶，其次在跑，其余按最近更新降序（一眼看到"该我拍板的"）。
const STATUS_PRIORITY: Record<string, number> = { waiting_approval: 0, running: 1, created: 2 };
function sortRuns(items: RuntimeRunListItem[]): RuntimeRunListItem[] {
  return [...items].sort((a, b) => {
    const pa = STATUS_PRIORITY[a.status] ?? 3;
    const pb = STATUS_PRIORITY[b.status] ?? 3;
    if (pa !== pb) return pa - pb;
    return (b.updated_at ?? "").localeCompare(a.updated_at ?? "");
  });
}

// ═══════════════════════════ 右栏「AI 任务」主面板 ═══════════════════════════

// ═══ 发起入口（2026-07-17 可发现性修复：Daniel 实测"找不到让 AI 处置的地方"）═══
// 原发起点只藏在风险详情动作区（且仅无待批提案的风险显示）——语义对但可发现性差。
// 正确的家=用户寻找它的地方：本标签页常驻一个轻量发起块。ops 可用；非 ops 灰态白话。
function DispatchLauncher({ role, onStarted }: { role: Role; onStarted: () => void }) {
  const [riskId, setRiskId] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [useRealModel, setUseRealModel] = useState(false);   // 波E②：真模型按趟 opt-in，默认省钱剧本
  const canStart = role === "ops";
  const start = async () => {
    const rid = riskId.trim().toUpperCase();
    if (!rid) return;
    setBusy(true);
    setMsg(null);
    try {
      const env = await startRuntimeRun(role, actorForRole(role), rid, useRealModel);
      setMsg(`已发起：${env.run_id}（${env.status === "waiting_approval" ? "提案已出，待拍板" : env.status}）`);
      setRiskId("");
      onStarted();
    } catch (e) {
      setMsg(e instanceof Error ? e.message : "发起失败");
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="cp-dispatch-launcher">
      <div className="cp-dispatch-launcher__head">发起 AI 处置</div>
      {canStart ? (
        <>
          <div className="cp-dispatch-launcher__row">
            <input
              className="cp-form-input"
              value={riskId}
              placeholder="风险编号，如 RSK-SIM-00012"
              onChange={(e) => setRiskId(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && !busy && start()}
              aria-label="要交给 AI 处置的风险编号"
            />
            <button className="cp-decide cp-decide--ai" disabled={busy || !riskId.trim()} onClick={start}>
              {busy ? "发起中…" : "让 AI 处置"}
            </button>
          </div>
          <label className="cp-dispatch-launcher__llm">
            <input type="checkbox" checked={useRealModel} onChange={(e) => setUseRealModel(e.target.checked)} />
            用真模型思考（慢约 2-3 分钟、走订阅通道；不勾=快速确定性剧本，时间线如实标注两种档）
          </label>
          <div className="cp-dispatch-launcher__hint">
            也可以在任何风险详情的动作区一键发起（仅对还没有待批提案的风险）。AI 只会查详情→派单→
            提交提案，然后停在「待拍板」等人批——提案-only，绝不自行批准。
          </div>
          {msg && <div className="cp-dispatch-launcher__msg">{msg}</div>}
        </>
      ) : (
        <StateHint kind="no-permission" compact title="由运营发起" roleHint="切到「运营 ops」角色即可在此发起 AI 处置；老板角色负责在待拍板区批它的提案。" />
      )}
    </div>
  );
}

export default function AiRuns({ role, world, onOpenObject }: { role: Role; world?: World | null; onOpenObject: (r: ObjectRef) => void }) {
  const [data, setData] = useState<{ items: RuntimeRunListItem[]; note?: string } | null>(null);
  const [err, setErr] = useState(false);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setData(null);
    setErr(false);
    fetchRuntimeRuns(role)
      .then((d) => !cancelled && setData({ items: d.items, note: d.note }))
      .catch(() => !cancelled && setErr(true));
    return () => {
      cancelled = true;
    };
  }, [role, world, reload]);

  if (err)
    return (
      <StateHint
        kind="error"
        title="AI 任务加载失败"
        message="没能取到 AI 处置任务。确认 apps/api 服务已在 8100 端口启动，再重试。"
        onRetry={() => setReload((n) => n + 1)}
      />
    );
  if (!data) return <StateHint kind="loading" title="AI 任务加载中…" />;
  if (data.items.length === 0)
    return (
      <div className="cp-runs">
        <DispatchLauncher role={role} onStarted={() => setReload((n) => n + 1)} />
        <StateHint
          kind="empty"
          title="本世界还没有 AI 处置任务"
          reason={data.note ?? "还没有人启动过 AI 处置差事。"}
          suggestion="就在上方输入风险编号发起第一趟（运营角色）"
        />
      </div>
    );

  const runs = sortRuns(data.items);
  return (
    <div className="cp-runs">
      <DispatchLauncher role={role} onStarted={() => setReload((n) => n + 1)} />
      <div className="cp-runs__bar">
        <span className="cp-runs__n num">{runs.length} 趟 AI 处置</span>
        {runs.some((r) => r.status === "waiting_approval") && (
          <span className="cp-runs__wait num">{runs.filter((r) => r.status === "waiting_approval").length} 趟等拍板</span>
        )}
      </div>
      <div className="cp-runs__list">
        {runs.map((r) => (
          <RunRow key={r.run_id} run={r} role={role} onOpenObject={onOpenObject} onReload={() => setReload((n) => n + 1)} />
        ))}
      </div>
      <div className="cp-runs__foot">
        <div>
          <Icon name="chip" size={12} /> AI 处置为「提案-only」：AI 只查详情、派单、提交处置提案，停在「待拍板」等人批——绝不自行批准（冻结区不可达）。
        </div>
        <div className="cp-runs__foot-mode">
          思考档如实标注：默认「确定性剧本」(deterministic)，可经部署 env 开启「AI 模型」(llm)——每趟具体档见展开后的时间线 think 步与卡顶标签，脚本产物绝不冒充模型作答。
        </div>
      </div>
    </div>
  );
}

// ═══════════════════════════ ImpactPanel 用：「让 AI 处置」按钮 ═══════════════════════════
// 归一渲染形状（列表项与启动信封字段不同，取共有位 + 白话一行）。
type RunGlimpse = { run_id: string; status: string; status_label: string; killed?: number; plain: string | null };

export function AiDispatchButton({ riskId, riskStatus, role }: { riskId: string; riskStatus: string | null; role: Role }) {
  const [glimpse, setGlimpse] = useState<RunGlimpse | null>(null);
  const [phase, setPhase] = useState<"probe" | "idle" | "starting" | "shown">("probe");
  const [err, setErr] = useState<string | null>(null);
  const canStart = role === "ops";
  const terminalRisk = riskStatus ? RISK_TERMINAL.has(riskStatus) : false;

  // 挂载即先查本世界是否已有针对该风险的 run（幂等：第二趟启动会因"风险已有在办任务"必然 failed，
  // 前端先查挡住，不造垃圾 run）。goal 契约=`处置 {riskId}`（apps/api/runtime.py::start_run_endpoint）。
  useEffect(() => {
    if (terminalRisk) return;
    let cancelled = false;
    setPhase("probe");
    setErr(null);
    setGlimpse(null);
    fetchRuntimeRuns(role)
      .then((d) => {
        if (cancelled) return;
        const goal = `处置 ${riskId}`;
        const mine = d.items.filter((r) => r.goal === goal);
        const pick =
          mine.find((r) => !TERMINAL.has(r.status)) ??
          [...mine].sort((a, b) => (b.updated_at ?? "").localeCompare(a.updated_at ?? ""))[0] ??
          null;
        if (pick) {
          setGlimpse({ run_id: pick.run_id, status: pick.status, status_label: pick.status_label, killed: pick.killed, plain: pick.summary });
          setPhase("shown");
        } else {
          setPhase("idle");
        }
      })
      .catch(() => !cancelled && setPhase("idle")); // 探测失败不挡启动（后端仍会挡二次启动）
    return () => {
      cancelled = true;
    };
  }, [riskId, role, terminalRisk]);

  if (terminalRisk) return null; // 已终态风险（resolved/escalated）无需再发起 AI 处置

  const start = async () => {
    if (!canStart || phase === "starting") return;
    setPhase("starting");
    setErr(null);
    try {
      const env = await startRuntimeRun(role, actorForRole(role), riskId);
      setGlimpse({ run_id: env.run_id, status: env.status, status_label: env.status_label, killed: env.killed, plain: env.summary ?? env.note ?? null });
      setPhase("shown");
    } catch (e) {
      setErr((e as Error).message);
      setPhase("idle");
    }
  };

  if (phase === "probe") {
    return (
      <div className="cp-dispatch">
        <StateHint kind="loading" compact title="查 AI 任务…" />
      </div>
    );
  }

  if (phase === "shown" && glimpse) {
    const waiting = glimpse.status === "waiting_approval";
    return (
      <div className="cp-dispatch is-shown">
        <div className="cp-dispatch__head">
          <span className="cp-dispatch__t">
            <Icon name="chip" size={13} /> AI 处置
          </span>
          <StatusBadge status={glimpse.status} label={glimpse.status_label} killed={glimpse.killed} />
        </div>
        {glimpse.plain && <div className="cp-dispatch__plain">{glimpse.plain}</div>}
        <div className="cp-dispatch__hint">
          {waiting
            ? "AI 已提交处置提案，正等人拍板。关闭本面板 → 右栏「AI 任务」区看逐步时间线；批准后在那里点「继续执行」。"
            : "已发起一趟 AI 处置。关闭本面板 → 右栏「AI 任务」区看逐步时间线与进度。"}
        </div>
      </div>
    );
  }

  // idle：无既有 run。ops 可发起；其余角色灰态提示"运营发起"。
  return (
    <div className="cp-dispatch">
      <button className="cp-dispatch__btn" disabled={!canStart || phase === "starting"} onClick={start}>
        <Icon name="chip" size={13} /> {phase === "starting" ? "发起中…" : "让 AI 处置"}
      </button>
      {canStart ? (
        <div className="cp-dispatch__note">
          让 AI 按剧本处置这个风险：查详情 → 派单 → 提交处置提案，然后停在「待拍板」等你批。提案-only，AI 绝不自行批准。经手身份 {actorForRole(role)}（原型级，真实系统换 SSO）。
        </div>
      ) : (
        <StateHint
          kind="no-permission"
          compact
          title="由运营发起"
          roleHint="发起 AI 处置是运营动作：顶栏切到「运营 ops」即可让 AI 起一趟差事（派单→提交处置提案→停在待拍板）。"
        />
      )}
      {err && (
        <div className="cp-decide__err">
          <b>没能发起</b> · {err}
        </div>
      )}
    </div>
  );
}
