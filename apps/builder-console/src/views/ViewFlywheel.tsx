import { flywheel } from "../data";
import ViewHead from "../components/ViewHead";

const ASSET_TONE: Record<string, string> = {
  precedent: "var(--amber)",
  playbook: "var(--cyan)",
  evalset: "var(--cyan)",
  autonomy: "var(--violet)",
  freeze: "var(--cyan-2)",
};

export default function ViewFlywheel() {
  const d = flywheel;
  const maxFunnel = Math.max(...d.funnel.map((f) => f.count), 1);

  const maxQ = Math.max(...(d.quality?.map((q) => q.n) ?? [1]), 1);
  const QTONE: Record<string, string> = { effective: "green", partial: "amber", ineffective: "red" };

  return (
    <div>
      <ViewHead idx="10" question={d.question} subtitle={d.subtitle} />

      {/* 五资产接力 */}
      <div className="flow-track">
        {d.assets.map((a, i) => {
          const tone = ASSET_TONE[a.id] ?? "var(--cyan)";
          const isFreeze = a.id === "freeze";
          return (
            <div className="flow-node" key={a.id}>
              <article
                className={`flow-card${isFreeze ? " freeze-wall" : ""}`}
                style={{ ["--ac" as string]: tone }}
              >
                <div className="flow-order mono">资产 {a.order}</div>
                <h3 className="flow-name">{a.name}</h3>
                <p className="flow-plain">{a.plain}</p>
                <div className="flow-metric">
                  <span className="num flow-metric-n" style={{ color: tone }}>
                    {a.metric.toLocaleString()}
                    {a.metricUnit && <span className="flow-metric-u"> {a.metricUnit}</span>}
                  </span>
                  <span className="flow-metric-l">{a.metricLabel}</span>
                </div>
                <p className="flow-sub">{a.sub}</p>
              </article>
              {i < d.assets.length - 1 && (
                <div className="flow-arrow" aria-hidden>
                  {d.assets[i + 1].id === "freeze" ? "⊣" : "→"}
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* 真实漏斗：飞轮当前所在处 */}
      <section className="panel" style={{ marginTop: 26 }}>
        <div className="panel-head">
          <span className="panel-title">飞轮当前跑到哪一环（本快照真实计数）</span>
          <span className="muted mono" style={{ fontSize: 11 }}>越往下越窄</span>
        </div>
        <div style={{ padding: "16px 18px" }}>
          {d.funnel.map((f, i) => (
            <div className="funnel-row" key={f.stage}>
              <span className="funnel-stage">{f.stage}</span>
              <div className="funnel-bar-wrap">
                <div
                  className="funnel-bar"
                  style={{
                    width: `${Math.max((f.count / maxFunnel) * 100, f.count > 0 ? 4 : 0)}%`,
                    opacity: 1 - i * 0.13,
                  }}
                />
                <span className="funnel-n num">{f.count}</span>
              </div>
              <span className="funnel-plain muted">{f.plain}</span>
            </div>
          ))}
        </div>
      </section>

      {/* 质量标签分布（人手打，越用越强的成绩单原料） */}
      {d.quality && d.quality.length > 0 && (
        <section className="panel" style={{ marginTop: 20 }}>
          <div className="panel-head">
            <span className="panel-title">先例质量标签分布（人关闭时手打）</span>
            <span className="tag cyan" style={{ fontSize: 10 }}>sim · 180 条已回填</span>
          </div>
          <div style={{ padding: "14px 18px" }}>
            {d.quality.map((q) => (
              <div className="funnel-row" key={q.ql}>
                <span className="funnel-stage">{q.ql}</span>
                <div className="funnel-bar-wrap">
                  <div className="funnel-bar" style={{ width: `${Math.max((q.n / maxQ) * 100, 4)}%`, background: `var(--${QTONE[q.ql] ?? "cyan"})` }} />
                  <span className="funnel-n num">{q.n}</span>
                </div>
                <span className="funnel-plain muted">{q.ql === "effective" ? "处置有效" : q.ql === "partial" ? "部分有效" : q.ql === "ineffective" ? "无效（诚实记录，进评估集）" : "未标注"}</span>
              </div>
            ))}
          </div>
        </section>
      )}

      <div className="honest-banner">
        <span className="honest-icon">◈</span>
        <div>
          <div className="honest-label mono">诚实状态 · sim</div>
          <p className="honest-text">{d.honestState}</p>
        </div>
      </div>
    </div>
  );
}
