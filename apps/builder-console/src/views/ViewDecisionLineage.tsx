import { decisionLineage } from "../data";
import ViewHead from "../components/ViewHead";
import type { QuartetPhase } from "../types";

function usd(n: number | string | undefined) {
  if (n == null || n === "") return "—";
  const v = typeof n === "string" ? parseFloat(n) : n;
  if (Number.isNaN(v)) return String(n);
  return "$" + Math.round(v).toLocaleString();
}

const SEV_TONE: Record<string, string> = { critical: "red", high: "amber", medium: "cyan" };

function PhaseBody({ p }: { p: QuartetPhase }) {
  if (p.evidence) {
    const e = p.evidence;
    const s = e.shipment as Record<string, string>;
    return (
      <div className="ph-evidence">
        <div className="ph-mini-timeline">
          <div className="jcard-section-label">连锁上下文（sim_event_log · caused_by）</div>
          <div className="mtl">
            {e.chain.map((c, i) => (
              <div className={`mtl-node${c.family === "chain" ? " eta" : ""}`} key={i}>
                <span className="mtl-dot" />
                <span className="mtl-type mono">{c.event_kind}</span>
                <span className="mtl-time mono">{c.sim_date.slice(5)}</span>
                <span className="mtl-loc mono">{c.severity ?? ""}</span>
              </div>
            ))}
          </div>
        </div>
        <div className="ph-facts">
          <div className="ph-fact"><span className="ph-fact-l">航线</span>
            <span className="ph-fact-v">{s.origin_port} → {s.destination_port} · {s.carrier_name} · {s.mode}</span></div>
          <div className="ph-fact"><span className="ph-fact-l">ETA</span>
            <span className="ph-fact-v num">{s.eta_initial} → {s.eta_current}</span></div>
          <div className="ph-fact"><span className="ph-fact-l">受影响订单行</span>
            <span className="ph-fact-v num">{e.affectedLines.length} 条</span></div>
          <div className="ph-fact"><span className="ph-fact-l">敞口 / 定级</span>
            <span className="ph-fact-v">
              <span className="num" style={{ color: "var(--amber)" }}>{usd(e.exposureUsd)}</span>
              <span className={`tag ${SEV_TONE[e.severity] ?? "amber"}`} style={{ marginLeft: 8, fontSize: 10 }}>{e.severity}</span>
            </span></div>
        </div>
      </div>
    );
  }
  if (p.proposal) {
    const pr = p.proposal;
    const cost = Number(pr.economics.expedite_cost_usd);
    const benefit = Number(pr.economics.benefit_usd);
    return (
      <div className="ph-proposal">
        <div className="ph-prop-econ">
          <div className="ph-econ-item">
            <span className="ph-econ-n num" style={{ color: "var(--cyan)" }}>{usd(cost)}</span>
            <span className="ph-econ-l">花：加急成本</span>
          </div>
          <span className="ph-econ-vs">收益</span>
          <div className="ph-econ-item">
            <span className="ph-econ-n num" style={{ color: "var(--amber)" }}>{usd(benefit)}</span>
            <span className="ph-econ-l">挽回毛利 + 省滞箱</span>
          </div>
          {benefit > 0 && cost > 0 && (
            <div className="ph-econ-ratio">
              <span className="num">{(benefit / cost).toFixed(1)}×</span>
              <span>收益 / 成本</span>
            </div>
          )}
        </div>
        <div className="ph-facts">
          <div className="ph-fact"><span className="ph-fact-l">提议动作</span>
            <span className="ph-fact-v">
              <span className="tag violet" style={{ fontSize: 10.5 }}>{pr.action}</span>
              <span className="muted mono" style={{ marginLeft: 8, fontSize: 11 }}>提议 ≠ 执行，提交后进审批闸</span>
            </span></div>
          <div className="ph-fact"><span className="ph-fact-l">经济账裁决</span>
            <span className="ph-fact-v">{pr.verdict}</span></div>
          {pr.precedentBlock && (
            <div className="ph-fact"><span className="ph-fact-l">引用先例</span>
              <span className="ph-fact-v" style={{ fontSize: 12 }}>{pr.precedentBlock}</span></div>
          )}
        </div>
      </div>
    );
  }
  if (p.decision) {
    const dc = p.decision;
    return (
      <div className="ph-facts">
        <div className="ph-fact"><span className="ph-fact-l">审批结果</span>
          <span className="ph-fact-v">
            <span className="tag green" style={{ fontSize: 10.5 }}>{dc.approvalStatus}</span>
            <span className="muted" style={{ marginLeft: 8 }}>by {dc.approvedByRole} · 决定 {dc.decision}</span>
          </span></div>
        <div className="ph-fact"><span className="ph-fact-l">执行回写</span>
          <span className="ph-fact-v mono" style={{ fontSize: 11.5 }}>{dc.actionTaken || "—"}</span></div>
      </div>
    );
  }
  if (p.outcome) {
    const o = p.outcome;
    return (
      <div className="ph-facts">
        <div className="ph-fact"><span className="ph-fact-l">结果 / 用时</span>
          <span className="ph-fact-v">
            <span className="tag green" style={{ fontSize: 10.5 }}>{o.outcomeResolved}</span>
            <span className="muted num" style={{ marginLeft: 8 }}>{o.outcomeDays} 天关闭</span>
          </span></div>
        <div className="ph-fact"><span className="ph-fact-l">质量标注（人手打）</span>
          <span className="ph-fact-v"><span className="tag amber" style={{ fontSize: 10.5 }}>{o.qualityLabel}</span></span></div>
        <div className="ph-fact"><span className="ph-fact-l">先例沉淀</span>
          <span className="ph-fact-v">
            {o.precedentRecorded
              ? <span className="tag cyan" style={{ fontSize: 10.5 }}>已写入 {o.memoryId}</span>
              : <span className="empty-badge">未沉淀</span>}
          </span></div>
      </div>
    );
  }
  return null;
}

export default function ViewDecisionLineage() {
  const d = decisionLineage;
  return (
    <div>
      <ViewHead idx="12" question={d.question} subtitle={d.subtitle} />

      <div className="case-banner">
        <div className="case-banner-l">
          <span className="eyebrow">活世界回放案例 · sim</span>
          <div className="case-ids">
            <span className="num">{d.caseId}</span>
            <span className="case-arrow">·</span>
            <span className="num" style={{ color: "var(--amber)" }}>{d.riskId}</span>
            <span className="case-arrow">·</span>
            <span className="num">{d.shipmentId}</span>
          </div>
        </div>
        <p className="case-line">经济账对比最大的加急案，四步回放决策血缘四件套，含 caused_by 连锁上下文。</p>
      </div>

      <div className="lineage">
        {d.quartet.map((p, i) => (
          <div className="ln-phase" key={i}>
            <div className="ln-rail">
              <span className="ln-tier">{p.tier}</span>
              {i < d.quartet.length - 1 && <span className="ln-line" />}
            </div>
            <article className="ln-card">
              <div className="ln-head">
                <h3 className="ln-phase-title">{p.phase}</h3>
                <span className="ln-actor mono">{p.actor}</span>
              </div>
              <p className="ln-plain">{p.plain}</p>
              <PhaseBody p={p} />
            </article>
          </div>
        ))}
      </div>

      {/* AI 全程留痕 */}
      <section className="panel" style={{ marginTop: 8 }}>
        <div className="panel-head"><span className="panel-title">AI 全程留痕（sim_ai_activity）</span></div>
        <div style={{ padding: "10px 18px" }}>
          {d.aiTrace.map((a, i) => (
            <div className="ln-trace" key={i}>
              <span className="ln-trace-date mono">{a.sim_date}</span>
              <span className="ln-trace-actor mono">{a.actor}</span>
              <span className="ln-trace-act tag" style={{ fontSize: 9.5 }}>{a.activity}</span>
              <span className="ln-trace-detail">{a.detail}</span>
            </div>
          ))}
        </div>
      </section>

      <p className="source-note mono">{d.sourceNote}</p>
    </div>
  );
}
