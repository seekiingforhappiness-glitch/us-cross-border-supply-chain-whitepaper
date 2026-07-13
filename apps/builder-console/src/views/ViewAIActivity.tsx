import { aiActivity } from "../data";
import ViewHead from "../components/ViewHead";

export default function ViewAIActivity() {
  const d = aiActivity;
  const sim = d.sim;
  const llm = d.llm;
  const maxAct = Math.max(...sim.byActivity.map((a) => a.n), 1);

  return (
    <div>
      <ViewHead idx="11" question={d.question} subtitle={d.subtitle} />

      <div className="ai-two">
        {/* 左账：模拟世界运转账 */}
        <section className="panel ai-acct sim">
          <div className="panel-head">
            <span className="panel-title">{sim.label}</span>
            <span className="tag cyan" style={{ fontSize: 10 }}>sim · 已发生</span>
          </div>
          <div className="ai-acct-body">
            <div className="ai-big">
              <span className="num ai-big-n" style={{ color: "var(--cyan)" }}>{sim.total.toLocaleString()}</span>
              <span className="ai-big-l">次 AI 运转 · {sim.span.from} → {sim.span.to}</span>
            </div>
            <div className="ai-acts">
              {sim.byActivity.map((a) => (
                <div className="ai-act" key={a.activity}>
                  <span className="ai-act-name mono">{a.activity}</span>
                  <div className="ai-act-bar-wrap">
                    <div className="ai-act-bar" style={{ width: `${(a.n / maxAct) * 100}%` }} />
                    <span className="ai-act-n num">{a.n}</span>
                  </div>
                  <span className="ai-act-plain muted">{a.plain}</span>
                </div>
              ))}
            </div>
            <div className="ai-actors mono">
              {sim.byActor.map((x) => (
                <span key={x.actor} className="ai-actor">{x.actor} · {x.n}</span>
              ))}
            </div>
            <p className="ai-note">{sim.plain}</p>
          </div>
        </section>

        {/* 右账：真实 LLM 推理账 */}
        <section className="panel ai-acct llm">
          <div className="panel-head">
            <span className="panel-title">{llm.label}</span>
            <span className="tag amber" style={{ fontSize: 10 }}>真实账 · 待运转</span>
          </div>
          <div className="ai-acct-body">
            <div className="ai-big">
              <span className="num ai-big-n" style={{ color: llm.total > 0 ? "var(--amber)" : "var(--ink-3)" }}>
                {llm.total.toLocaleString()}
              </span>
              <span className="ai-big-l">次真实 LLM 调用 · {llm.estTokens.toLocaleString()} tokens（估算）</span>
            </div>
            {llm.total <= 0 ? (
              <div className="ai-empty">
                <span className="empty-badge">llm_calls 表 0 条 · 诚实的空账</span>
              </div>
            ) : (
              <div className="ai-acts">
                {llm.byType.map((x) => (
                  <div className="ai-act" key={x.type}>
                    <span className="ai-act-name mono">{x.type}</span>
                    <span className="ai-act-n num">{x.n}</span>
                  </div>
                ))}
              </div>
            )}
            <p className="ai-note">{llm.plain}</p>
          </div>
        </section>
      </div>

      <div className="honest-banner" style={{ marginTop: 22 }}>
        <span className="honest-icon">◈</span>
        <div>
          <div className="honest-label mono">两账为什么分开记</div>
          <p className="honest-text">
            左账（sim）证明「世界活着」——确定性经济账引擎把 14 个月的检测/提案/审批/关闭全跑了一遍并留痕；
            右账（真实 LLM）才是「花 token 的推理」。把模拟运转冒充成真实 AI 调用，就是全局规则 5 要防的「自信的具体」。分开报，不混算。
          </p>
        </div>
      </div>
    </div>
  );
}
