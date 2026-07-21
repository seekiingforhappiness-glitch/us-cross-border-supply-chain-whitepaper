import { evolution } from "../data";
import ViewHead from "../components/ViewHead";

const TONE_VAR: Record<string, string> = {
  cyan: "var(--cyan)", amber: "var(--amber)", violet: "var(--violet)",
  green: "var(--green)", red: "var(--red)",
};

export default function ViewEvolution() {
  const d = evolution;
  return (
    <div>
      <ViewHead idx="07" question={d.question} subtitle={d.subtitle} />

      <div className="ev-legend">
        {d.series.map((s) => (
          <span className="ev-leg" key={s.key}>
            <i style={{ background: TONE_VAR[s.tone] }} />{s.name}
          </span>
        ))}
        <span className="ev-leg-hint mono">共 {d.count} 项决策 · ◆ = 里程碑</span>
      </div>

      <div className="ev-timeline">
        {d.entries.map((e) => {
          const color = TONE_VAR[e.tone] ?? "var(--cyan)";
          return (
            <div className={`ev-node${e.milestone ? " ms" : ""}`} key={e.code} style={{ ["--ec" as string]: color }}>
              <div className="ev-rail">
                <span className={`ev-dot${e.milestone ? " ms" : ""}`} />
                <span className="ev-line" />
              </div>
              <article className={`ev-card${e.milestone ? " ms" : ""}`}>
                <div className="ev-card-head">
                  <span className="ev-code mono" style={{ color }}>{e.code}</span>
                  {e.milestone && <span className="ev-ms-badge">◆ 里程碑</span>}
                  <span className="ev-series mono">{e.seriesName}</span>
                  {e.date && <span className="ev-date mono">{e.date}</span>}
                </div>
                <h3 className="ev-title">{e.title}</h3>
                <p className="ev-one">{e.oneLine}。</p>
                {e.approver && <span className="ev-approver mono">✎ {e.approver}</span>}
              </article>
            </div>
          );
        })}
      </div>
      <p className="source-note mono">{d.source}</p>
    </div>
  );
}
