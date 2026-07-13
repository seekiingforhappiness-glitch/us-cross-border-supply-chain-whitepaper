import { decisionLineage } from "../data";
import ViewHead from "../components/ViewHead";
import type { QuartetPhase } from "../types";

function usd(n: number | undefined) {
  if (n == null) return "—";
  return "$" + Math.round(n).toLocaleString();
}

function PhaseEvidence({ p, exposure }: { p: QuartetPhase; exposure: number }) {
  if (p.evidence) {
    const e = p.evidence;
    const s = e.shipment as Record<string, string>;
    return (
      <div className="ph-evidence">
        <div className="ph-mini-timeline">
          <div className="jcard-section-label">里程碑流（ETA 一路推迟）</div>
          <div className="mtl">
            {e.milestones.map((m, i) => {
              const isEta = m.event_type === "eta_change";
              return (
                <div className={`mtl-node${isEta ? " eta" : ""}`} key={i}>
                  <span className="mtl-dot" />
                  <span className="mtl-type mono">{m.event_type}</span>
                  <span className="mtl-time mono">{m.event_time.slice(5, 10)}</span>
                  <span className="mtl-loc mono">{m.event_locode}</span>
                </div>
              );
            })}
          </div>
        </div>
        <div className="ph-facts">
          <div className="ph-fact">
            <span className="ph-fact-l">航线</span>
            <span className="ph-fact-v">{s.origin_port} → {s.destination_port} · {s.carrier_name} · {s.mode}</span>
          </div>
          <div className="ph-fact">
            <span className="ph-fact-l">ETA</span>
            <span className="ph-fact-v num">{s.eta_initial} → {s.eta_current}</span>
          </div>
          <div className="ph-fact">
            <span className="ph-fact-l">受影响订单行</span>
            <span className="ph-fact-v num">{e.affectedLines.length} 条 · {e.affectedLines.join(" ")}</span>
          </div>
          <div className="ph-fact">
            <span className="ph-fact-l">敞口 / 定级</span>
            <span className="ph-fact-v">
              <span className="num" style={{ color: "var(--amber)" }}>{usd(e.exposureUsd)}</span>
              <span className="tag red" style={{ marginLeft: 8, fontSize: 10 }}>{e.severity}</span>
            </span>
          </div>
        </div>
      </div>
    );
  }
  if (p.proposal) {
    const pr = p.proposal;
    const ratio = pr.estCostUsd && exposure ? (pr.estCostUsd / exposure) * 100 : null;
    return (
      <div className="ph-proposal">
        <div className="ph-prop-econ">
          <div className="ph-econ-item">
            <span className="ph-econ-n num" style={{ color: "var(--cyan)" }}>{usd(pr.estCostUsd)}</span>
            <span className="ph-econ-l">花：估算空运成本</span>
          </div>
          <span className="ph-econ-vs">救回</span>
          <div className="ph-econ-item">
            <span className="ph-econ-n num" style={{ color: "var(--amber)" }}>{usd(exposure)}</span>
            <span className="ph-econ-l">敞口</span>
          </div>
          {ratio != null && (
            <div className="ph-econ-ratio">
              <span className="num">{ratio.toFixed(1)}%</span>
              <span>成本 / 敞口</span>
            </div>
          )}
        </div>
        <div className="ph-facts">
          <div className="ph-fact">
            <span className="ph-fact-l">提议动作</span>
            <span className="ph-fact-v">
              <span className="tag violet" style={{ fontSize: 10.5 }}>{pr.action}</span>
              <span className="muted mono" style={{ marginLeft: 8, fontSize: 11 }}>提议 ≠ 执行，提交后进审批闸</span>
            </span>
          </div>
          <div className="ph-fact">
            <span className="ph-fact-l">方案参数</span>
            <span className="ph-fact-v mono" style={{ fontSize: 11.5 }}>{JSON.stringify(pr.params)}</span>
          </div>
          <div className="ph-fact">
            <span className="ph-fact-l">预期新 ETA</span>
            <span className="ph-fact-v num">{pr.expectedNewEta}</span>
          </div>
        </div>
      </div>
    );
  }
  if (p.decision) {
    const dc = p.decision;
    return (
      <div className="ph-facts">
        <div className="ph-fact">
          <span className="ph-fact-l">审批结果</span>
          <span className="ph-fact-v">
            <span className="tag green" style={{ fontSize: 10.5 }}>{dc.approvalStatus}</span>
            <span className="muted" style={{ marginLeft: 8 }}>by {dc.approvedByRole}</span>
          </span>
        </div>
        <div className="ph-fact">
          <span className="ph-fact-l">执行回写</span>
          <span className="ph-fact-v mono" style={{ fontSize: 11.5 }}>{dc.actionTaken}</span>
        </div>
      </div>
    );
  }
  if (p.outcome) {
    const o = p.outcome;
    return (
      <div className="ph-facts">
        <div className="ph-fact">
          <span className="ph-fact-l">任务状态</span>
          <span className="ph-fact-v"><span className="tag green" style={{ fontSize: 10.5 }}>{o.taskStatus}</span></span>
        </div>
        <div className="ph-fact">
          <span className="ph-fact-l">先例沉淀</span>
          <span className="ph-fact-v">
            {o.precedentRecorded ? (
              <span className="tag cyan" style={{ fontSize: 10.5 }}>已写入处置记忆</span>
            ) : (
              <span className="empty-badge">待回填 · resolution_memory 为空</span>
            )}
          </span>
        </div>
      </div>
    );
  }
  return null;
}

export default function ViewDecisionLineage() {
  const d = decisionLineage;
  const exposure = d.quartet[0]?.evidence?.exposureUsd ?? 0;
  return (
    <div>
      <ViewHead idx="06" question={d.question} subtitle={d.subtitle} />

      <div className="case-banner">
        <div className="case-banner-l">
          <span className="eyebrow">回放案例</span>
          <div className="case-ids">
            <span className="num">{d.caseId}</span>
            <span className="case-arrow">·</span>
            <span className="num" style={{ color: "var(--amber)" }}>{d.riskId}</span>
            <span className="case-arrow">·</span>
            <span className="num">{d.shipmentId}</span>
          </div>
        </div>
        <p className="case-line">从运行库里挑一桩真实、已结案的处置，分四步回放决策血缘四件套。</p>
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
              <PhaseEvidence p={p} exposure={exposure} />
            </article>
          </div>
        ))}
      </div>

      <p className="source-note mono">{d.sourceNote}</p>
    </div>
  );
}
