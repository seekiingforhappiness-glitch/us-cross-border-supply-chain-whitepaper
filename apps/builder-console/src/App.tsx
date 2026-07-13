import { useState } from "react";
import { TooltipProvider } from "./components/Tooltip";
import { LogoMark } from "./components/icons";
import { meta } from "./data";
import ViewStructure from "./views/ViewStructure";
import ViewJourney from "./views/ViewJourney";
import ViewConstitution from "./views/ViewConstitution";
import ViewFlywheel from "./views/ViewFlywheel";
import ViewAIActivity from "./views/ViewAIActivity";
import ViewDecisionLineage from "./views/ViewDecisionLineage";

interface NavDef {
  id: string;
  num: string;
  label: string;
  q: string;
  el: React.ReactNode;
}

const VIEWS: NavDef[] = [
  { id: "structure", num: "01", label: "系统结构", q: "这个系统是什么结构？", el: <ViewStructure /> },
  { id: "journey", num: "02", label: "一票货的一生", q: "业务怎么被建模？", el: <ViewJourney /> },
  { id: "constitution", num: "03", label: "什么永远不会变", q: "哪些是冻结地基？", el: <ViewConstitution /> },
  { id: "flywheel", num: "04", label: "越用越强", q: "系统怎么进化？", el: <ViewFlywheel /> },
  { id: "ai-activity", num: "05", label: "AI 干了多少活", q: "干了多少、花了多少？", el: <ViewAIActivity /> },
  { id: "lineage", num: "06", label: "一个决策的发生", q: "决策怎么发生？", el: <ViewDecisionLineage /> },
];

function exportedShort(iso: string) {
  // 2026-07-13T14:49:31+08:00 -> 2026-07-13 14:49
  return iso.replace("T", " ").slice(0, 16);
}

export default function App() {
  const [active, setActive] = useState(0);
  const view = VIEWS[active];

  return (
    <TooltipProvider>
      <div className="shell">
        <aside className="rail">
          <div className="rail-brand">
            <div className="rail-logo">
              <span className="rail-logo-mark">
                <LogoMark size={26} />
              </span>
              <span className="rail-title">建造者透视镜</span>
            </div>
            <div className="rail-tagline">{meta.tagline}</div>
            <div className="rail-ver">v1 · ontology {meta.ontologyVersion}</div>
          </div>

          <nav className="nav" aria-label="视图导航">
            {VIEWS.map((v, i) => (
              <button
                key={v.id}
                className={`nav-item${i === active ? " active" : ""}`}
                onClick={() => setActive(i)}
                aria-current={i === active ? "page" : undefined}
              >
                <span className="nav-num">{v.num}</span>
                <span>
                  <span className="nav-label">{v.label}</span>
                  <span className="nav-q">{v.q}</span>
                </span>
              </button>
            ))}
          </nav>

          <div className="rail-foot">
            <div>
              <span className="dot">●</span> 只读快照
            </div>
            <div>导出 {exportedShort(meta.exportedAt)}</div>
            <div>{meta.dbPath}</div>
          </div>
        </aside>

        <div className="main">
          <div className="stage" key={view.id}>
            {view.el}
          </div>
          <StatusBar viewLabel={view.label} viewNum={view.num} />
        </div>
      </div>
    </TooltipProvider>
  );
}

function StatusBar({ viewLabel, viewNum }: { viewLabel: string; viewNum: string }) {
  const tc = meta.tableCounts;
  return (
    <div className="statusbar">
      <span className="seg">
        <span className="live" />
        <span className="ro">READ-ONLY</span>
      </span>
      <span className="seg">
        <span className="k">VIEW</span>
        <span className="v">
          {viewNum} / 06 · {viewLabel}
        </span>
      </span>
      <span className="seg">
        <span className="k">EXPORTED</span>
        <span className="v">{exportedShort(meta.exportedAt)}</span>
      </span>
      <span className="seg">
        <span className="k">ACTION_LOG</span>
        <span className="v">{tc.action_log?.toLocaleString() ?? "—"}</span>
      </span>
      <span className="seg">
        <span className="k">RISK_EVENTS</span>
        <span className="v">{tc.risk_events?.toLocaleString() ?? "—"}</span>
      </span>
      <span className="seg">
        <span className="k">MEMORY</span>
        <span className="v">{tc.resolution_memory ?? "—"}</span>
      </span>
    </div>
  );
}
