import { aiActivity } from "../data";
import ViewHead from "../components/ViewHead";

export default function ViewAIActivity() {
  const d = aiActivity;
  const maxAction = Math.max(...d.automation.byAction.map((a) => a.n), 1);
  const maxFunnel = Math.max(...d.funnel.map((f) => f.count), 1);
  const llmEmpty = d.llm.total <= 0;

  return (
    <div>
      <ViewHead idx="05" question={d.question} subtitle={d.subtitle} />

      {/* 两类账：自动化 vs LLM */}
      <div className="grid-2">
        <section className="panel ledger-card">
          <div className="panel-head">
            <span className="panel-title">自动化引擎动作</span>
            <span className="tag cyan">已发生 · 有留痕</span>
          </div>
          <div style={{ padding: "18px" }}>
            <div className="row" style={{ justifyContent: "space-between", alignItems: "flex-end" }}>
              <div className="stat">
                <span className="stat-num cyan">{d.automation.total.toLocaleString()}</span>
                <span className="stat-label">action_log 总行数</span>
              </div>
              <span className="mono muted" style={{ fontSize: 11 }}>
                {d.automation.span.from} → {d.automation.span.to}
              </span>
            </div>
            <p className="plain" style={{ marginTop: 12, fontSize: 12.5 }}>
              {d.automation.plain}
            </p>
          </div>
        </section>

        <section className="panel ledger-card">
          <div className="panel-head">
            <span className="panel-title">LLM 推理调用</span>
            {llmEmpty ? (
              <span className="empty-badge">快照为空</span>
            ) : (
              <span className="tag amber">花 token 的部分</span>
            )}
          </div>
          <div style={{ padding: "18px" }}>
            <div className="row" style={{ gap: 28 }}>
              <div className="stat">
                <span className={`stat-num ${llmEmpty ? "" : "amber"}`}>{d.llm.total.toLocaleString()}</span>
                <span className="stat-label">调用次数</span>
              </div>
              <div className="stat">
                <span className="stat-num">{d.llm.degraded}</span>
                <span className="stat-label">降级次数</span>
              </div>
              <div className="stat">
                <span className="stat-num">{d.llm.estTokens.toLocaleString()}</span>
                <span className="stat-label">估算 token</span>
              </div>
            </div>
            <p className="plain" style={{ marginTop: 12, fontSize: 12.5, color: llmEmpty ? "var(--amber-2)" : undefined }}>
              {d.llmNote}
            </p>
          </div>
        </section>
      </div>

      {/* 自动化动作分布 */}
      <section className="panel" style={{ marginTop: 16 }}>
        <div className="panel-head">
          <span className="panel-title">自动化引擎干了哪些活</span>
          <span className="muted mono" style={{ fontSize: 11 }}>按动作类型</span>
        </div>
        <div style={{ padding: "16px 18px" }}>
          {d.automation.byAction.map((a) => (
            <div className="act-row" key={a.action}>
              <span className="act-name mono">{a.action}</span>
              <div className="act-bar-wrap">
                <div className="act-bar" style={{ width: `${(a.n / maxAction) * 100}%` }} />
                <span className="act-n num">{a.n}</span>
              </div>
              <span className="act-plain muted">{a.plain}</span>
            </div>
          ))}
        </div>
      </section>

      {/* 提案-采纳漏斗 */}
      <section className="panel" style={{ marginTop: 16 }}>
        <div className="panel-head">
          <span className="panel-title">提案 → 采纳漏斗</span>
          <span className="muted mono" style={{ fontSize: 11 }}>注意力落在刀刃上</span>
        </div>
        <div style={{ padding: "16px 18px" }}>
          <div className="mini-funnel">
            {d.funnel.map((f, i) => (
              <div className="mini-funnel-step" key={f.stage}>
                <span className="mini-funnel-n num">{f.count}</span>
                <div
                  className="mini-funnel-bar"
                  style={{ height: `${Math.max((f.count / maxFunnel) * 132, 4)}px`, opacity: 1 - i * 0.14 }}
                />
                <span className="mini-funnel-stage">{f.stage}</span>
                <span className="mini-funnel-note muted">{f.note}</span>
              </div>
            ))}
          </div>
          <p className="plain" style={{ marginTop: 14, fontSize: 12.5 }}>
            {d.funnelPlain}
          </p>
        </div>
      </section>
    </div>
  );
}
