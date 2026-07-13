import { journey } from "../data";
import ViewHead from "../components/ViewHead";
import type { Station } from "../types";

function roleTag(r: string) {
  return (
    <span key={r} className="tag" style={{ fontSize: 10 }}>
      {r}
    </span>
  );
}

function StationCard({ s }: { s: Station }) {
  return (
    <article className={`jcard${s.frozen ? " frozen" : ""}`}>
      <div className="jcard-top">
        <span className="jcard-step num">{s.step}</span>
        <div className="jcard-titles">
          <h3 className="jcard-title">{s.title}</h3>
          <span className="jcard-obj mono">{s.object}</span>
        </div>
        {s.frozen && <span className="tag frozen" style={{ fontSize: 9.5 }}>冻结区</span>}
      </div>

      <p className="jcard-plain">{s.plain}</p>

      {s.example && (
        <div className="jcard-example">
          <span className="eyebrow" style={{ fontSize: 9.5 }}>真实样本</span>
          <span className="num">{s.example}</span>
        </div>
      )}

      <div className="jcard-fields">
        <div className="jcard-section-label">关键字段</div>
        {s.fields.slice(0, 6).map((f) => (
          <div className="jfield" key={f.name}>
            <span className="jfield-name mono">{f.name}</span>
            <span className="jfield-type mono">{f.type}</span>
          </div>
        ))}
      </div>

      <div className="jcard-can">
        <div className="jcard-section-label">谁能对它做什么</div>
        <div className="jcard-action mono">{s.canDo.action}</div>
        <div className="row" style={{ gap: 5, marginTop: 5 }}>
          {s.canDo.by.map(roleTag)}
        </div>
        <p className="jcard-note">{s.canDo.note}</p>
      </div>

      <div className="jcard-count">
        <span className="num jcard-count-n">{s.count >= 0 ? s.count.toLocaleString() : "—"}</span>
        <span className="jcard-count-l">{s.countLabel}</span>
      </div>
    </article>
  );
}

export default function ViewJourney() {
  const d = journey;
  return (
    <div>
      <ViewHead idx="02" question={d.question} subtitle={d.subtitle} />

      <div className="case-banner">
        <div className="case-banner-l">
          <span className="eyebrow">主角运单</span>
          <div className="case-ids">
            <span className="num">{d.caseHeadline.shipment}</span>
            <span className="case-arrow">→</span>
            <span className="num" style={{ color: "var(--amber)" }}>
              {d.caseHeadline.risk}
            </span>
          </div>
        </div>
        <p className="case-line">{d.caseHeadline.line}</p>
      </div>

      <div className="journey-hint mono">
        沿业务旅程从左到右 · 每一站 = 一个对象 + 它的关键字段 + 谁能对它做动作 · 横向滚动 →
      </div>

      <div className="journey-track">
        {d.stations.map((s, i) => (
          <div className="journey-node" key={s.id}>
            <StationCard s={s} />
            {i < d.stations.length - 1 && <div className="journey-connector">›</div>}
          </div>
        ))}
      </div>
    </div>
  );
}
