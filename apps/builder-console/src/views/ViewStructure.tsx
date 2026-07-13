import { structure } from "../data";
import ViewHead from "../components/ViewHead";
import IsometricScene from "../components/IsometricScene";

export default function ViewStructure() {
  const d = structure;
  const t = d.totals;
  return (
    <div>
      <ViewHead idx="01" question={d.question} subtitle={d.subtitle} />

      <IsometricScene data={d} />

      <div className="struct-totals">
        {[
          { n: t.objects, l: "对象类型", tone: "" },
          { n: t.links, l: "关系", tone: "" },
          { n: t.actions, l: "动作", tone: "" },
          { n: t.riskRules, l: "风险规则", tone: "cyan" },
          { n: t.roles, l: "角色", tone: "" },
          { n: t.records, l: "业务记录", tone: "amber" },
        ].map((s) => (
          <div className="struct-stat" key={s.l}>
            <div className={`stat-num ${s.tone}`}>{s.n.toLocaleString()}</div>
            <div className="stat-label">{s.l}</div>
          </div>
        ))}
      </div>

      <div className="struct-note panel">
        <div className="panel-head">
          <span className="panel-title">怎么读这张图</span>
          <span className="tag cyan">Palantir 等距分层</span>
        </div>
        <div style={{ padding: "16px 18px" }} className="plain">
          <p style={{ marginBottom: 10 }}>
            <b style={{ color: "var(--layer-intel)" }}>顶层</b>是 AI 与自动化怎么<b>接入</b>业务——它是主角，不是角落里的按钮；
            <b style={{ color: "var(--layer-biz)" }}>中层</b>是被建模的<b>业务世界</b>，五个真实场景域摆成具象场景，不是节点连线图；
            <b style={{ color: "var(--layer-gov)" }}>底层</b>是<b>治理层</b>，宪法/权限/审计在下面兜住一切。
          </p>
          <p className="muted" style={{ fontSize: 12.5 }}>
            关键：三层动的是<b>同一批对象</b>。采购看的 PO 和财务看的 PO 是同一个对象——这正是本体范式的命根子，也是这张图想让你一眼看见的事。
          </p>
        </div>
      </div>
    </div>
  );
}
