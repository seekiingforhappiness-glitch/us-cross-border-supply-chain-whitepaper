import { constitution } from "../data";
import ViewHead from "../components/ViewHead";

export default function ViewConstitution() {
  const d = constitution;
  return (
    <div>
      <ViewHead idx="06" question={d.question} subtitle={d.subtitle} />

      <div className="stack">
        {d.groups.map((g) => (
          <section className="const-group" key={g.group}>
            <div className="const-group-head">
              <h2 className="const-group-title">{g.group}</h2>
              <p className="const-group-plain">{g.plain}</p>
            </div>
            <div className="const-articles">
              {g.articles.map((a) => (
                <article className="const-card" key={a.no}>
                  <div className="const-no mono">{a.no}</div>
                  <div className="const-body">
                    <div className="const-text">{a.text}</div>
                    <div className="const-line">
                      <span className="const-tag plain-chip">白话</span>
                      <span>{a.plain}</span>
                    </div>
                    <div className="const-line prevents">
                      <span className="const-tag prevents-chip">它防的事故</span>
                      <span>{a.prevents}</span>
                    </div>
                  </div>
                </article>
              ))}
            </div>
          </section>
        ))}
      </div>

      {/* 冻结区 */}
      <section className="freeze panel">
        <div className="panel-head">
          <span className="panel-title" style={{ color: "var(--cyan-2)" }}>
            {d.freezeZone.name}
          </span>
          <span className="tag frozen">FORBIDDEN · 对 AI 不存在</span>
        </div>
        <div style={{ padding: "16px 18px" }}>
          <p className="plain" style={{ marginBottom: 14 }}>
            {d.freezeZone.plain}
          </p>
          <div className="freeze-grid">
            {d.freezeZone.items.map((it) => (
              <div className="freeze-item" key={it.name}>
                <div className="freeze-name">
                  <span className="freeze-lock">▨</span>
                  {it.name}
                </div>
                <div className="freeze-why">{it.why}</div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* 三档决策权 */}
      <section className="tiers panel">
        <div className="panel-head">
          <span className="panel-title">理解冻结区的尺子：三档决策权</span>
          <span className="muted mono" style={{ fontSize: 11 }}>左 = 机器可做 → 右 = 只人可做</span>
        </div>
        <div className="tiers-grid">
          {d.decisionTiers.map((t) => (
            <div className="tier" key={t.tier}>
              <div className="tier-name">{t.tier}</div>
              <div className="tier-plain">{t.plain}</div>
            </div>
          ))}
        </div>
      </section>

      <p className="source-note mono">来源：{d.source}</p>
    </div>
  );
}
