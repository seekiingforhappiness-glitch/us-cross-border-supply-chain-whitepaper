import { TooltipProvider } from "./components/Tooltip";
import { NavProvider, useNav } from "./components/Nav";
import { LogoMark } from "./components/icons";
import { meta } from "./data";
import ViewStructure from "./views/ViewStructure";
import ViewJourney from "./views/ViewJourney";
import ViewWeave from "./views/ViewWeave";
import ViewRules from "./views/ViewRules";
import ViewActions from "./views/ViewActions";
import ViewConstitution from "./views/ViewConstitution";
import ViewEvolution from "./views/ViewEvolution";
import ViewWorld from "./views/ViewWorld";
import ViewEntity from "./views/ViewEntity";
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

// 两组导航：看懂系统（结构/织网/规则/动作权限/宪法/演进史）+ 看见世界（设定集/实体/越用越强/AI账本/回放）
const GROUPS: { key: string; name: string; hint: string; views: NavDef[] }[] = [
  {
    key: "system",
    name: "看懂系统",
    hint: "结构 · 织网 · 规则 · 权限 · 宪法 · 演进",
    views: [
      { id: "structure", num: "01", label: "系统结构", q: "这个系统是什么结构？", el: <ViewStructure /> },
      { id: "journey", num: "02", label: "一票货的一生", q: "业务怎么被建模？", el: <ViewJourney /> },
      { id: "weave", num: "03", label: "关联织网", q: "本体怎么织起来的？", el: <ViewWeave /> },
      { id: "rules", num: "04", label: "规则档案", q: "规则盯什么、战绩如何？", el: <ViewRules /> },
      { id: "actions", num: "05", label: "动作与权限", q: "谁能下什么手？", el: <ViewActions /> },
      { id: "constitution", num: "06", label: "什么永远不会变", q: "哪些是冻结地基？", el: <ViewConstitution /> },
      { id: "evolution", num: "07", label: "项目演进史", q: "怎么长成今天的？", el: <ViewEvolution /> },
    ],
  },
  {
    key: "world",
    name: "看见世界",
    hint: "设定 · 实体 · 进化 · 账本 · 回放",
    views: [
      { id: "world", num: "08", label: "世界设定集", q: "这个模拟世界是谁？", el: <ViewWorld /> },
      { id: "entity", num: "09", label: "实体浏览", q: "一票具体的货长什么样？", el: <ViewEntity /> },
      { id: "flywheel", num: "10", label: "越用越强", q: "系统怎么进化？", el: <ViewFlywheel /> },
      { id: "ai-activity", num: "11", label: "AI 账本", q: "干了多少、花了多少？", el: <ViewAIActivity /> },
      { id: "lineage", num: "12", label: "决策回放", q: "决策怎么发生？", el: <ViewDecisionLineage /> },
    ],
  },
];

const ALL_VIEWS: NavDef[] = GROUPS.flatMap((g) => g.views);

function exportedShort(iso: string) {
  return iso.replace("T", " ").slice(0, 16);
}

function Shell() {
  const { route, navigate } = useNav();
  const view = ALL_VIEWS.find((v) => v.id === route.view) ?? ALL_VIEWS[0];

  return (
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
          <div className="rail-ver">v2 · ontology {meta.ontologyVersion} · 活世界</div>
        </div>

        <nav className="nav" aria-label="视图导航">
          {GROUPS.map((g) => (
            <div className="nav-group" key={g.key}>
              <div className="nav-group-head">
                <span className="nav-group-name">{g.name}</span>
                <span className="nav-group-hint">{g.hint}</span>
              </div>
              {g.views.map((v) => (
                <button
                  key={v.id}
                  className={`nav-item${v.id === route.view ? " active" : ""}`}
                  onClick={() => navigate(v.id)}
                  aria-current={v.id === route.view ? "page" : undefined}
                >
                  <span className="nav-num">{v.num}</span>
                  <span>
                    <span className="nav-label">{v.label}</span>
                    <span className="nav-q">{v.q}</span>
                  </span>
                </button>
              ))}
            </div>
          ))}
        </nav>

        <div className="rail-foot">
          <div>
            <span className="dot">●</span> 只读快照 · sim 活世界
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
  );
}

export default function App() {
  return (
    <TooltipProvider>
      <NavProvider>
        <Shell />
      </NavProvider>
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
        <span className="v">{viewNum} / 12 · {viewLabel}</span>
      </span>
      <span className="seg">
        <span className="k">WORLD</span>
        <span className="v">simworld</span>
      </span>
      <span className="seg">
        <span className="k">RISK_EVENTS</span>
        <span className="v">{tc.risk_events?.toLocaleString() ?? "—"}</span>
      </span>
      <span className="seg">
        <span className="k">AI_ACTIVITY</span>
        <span className="v">{tc.sim_ai_activity?.toLocaleString() ?? "—"}</span>
      </span>
      <span className="seg">
        <span className="k">MEMORY</span>
        <span className="v">{tc.resolution_memory ?? "—"}</span>
      </span>
    </div>
  );
}
