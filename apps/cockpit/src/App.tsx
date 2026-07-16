import { useEffect, useState } from "react";
import {
  fetchOntologySummary,
  fetchVitals,
  type ObjectRef,
  type OntologyLink,
  type Role,
  type Vitals,
  type ZoneId,
} from "./api";
import TopBar from "./components/TopBar";
import AiWorkflow from "./views/AiWorkflow";
import CommandWall from "./views/CommandWall";
import type { Lane } from "./views/corridorModel";
import ImpactPanel, { type ImpactFocus } from "./views/ImpactPanel";
import LaneQueue from "./views/LaneQueue";
import ObjectCard from "./views/ObjectCard";
import RouteMap from "./views/RouteMap";
import ZoneQueue from "./views/ZoneQueue";
import type { DrillTarget } from "./views/zoneModel";

// 驾驶舱首屏编排（V10 方案 C，Daniel 亲批）：顶栏 + 主舞台（中央 60% · 右栏 AI 工作流 40%）。
// 中央四态（下钻四段式：总览→队列→详情→动作）：
//   ① wall  七区指挥墙（默认首屏，体征带的放大态；顶部体征带已移除避免同信息两处）
//   ② zone  某区工作队列（点区卡进入，面包屑返回）
//   ③ map   履约航线夜景地图（履约卡「航线视图」切入，SAP tile 内切换范式；V10 补记：走廊图升真地图）
//   ④ lane  某航线异常队列（点地图弧线进入）
// 队列条目 → 右栏滑出影响面板（风险类，含动作占位）/ 打开对象卡（对象类）——两资产直接复用。
// 角色（X-Role）是唯一全局开关：切换即令 vitals/panorama/ai-flow 全部按新角色重取（脱敏+粒度），
// 并回到指挥墙、收起详情与对象卡。

type Stage = { view: "wall" } | { view: "zone"; zoneId: ZoneId } | { view: "map" } | { view: "lane"; lane: Lane };

export default function App() {
  const [role, setRole] = useState<Role>("manager");
  const [vitals, setVitals] = useState<Vitals | null>(null);
  const [vitalsErr, setVitalsErr] = useState(false);
  const [links, setLinks] = useState<OntologyLink[]>([]);

  const [stage, setStage] = useState<Stage>({ view: "wall" });
  const [detail, setDetail] = useState<ImpactFocus | null>(null); // 右栏影响面板（风险类下钻）
  const [activeKey, setActiveKey] = useState<string | null>(null); // 队列高亮行
  const [card, setCard] = useState<ObjectRef | null>(null); // 对象卡抽屉（对象类下钻）

  const closeDetail = () => {
    setDetail(null);
    setActiveKey(null);
  };

  // 人类决策（批准/驳回）成功后：只重取体征数据（队列随 vitals 刷新，已拍板的提案自动移出待批），
  // 不重置导航——用户停留在待拍板队列，仅收起右栏详情。与角色切换的整屏重置区分开。
  const refreshVitalsData = () => {
    fetchVitals(role)
      .then((v) => setVitals(v))
      .catch(() => setVitalsErr(true));
  };

  // 体征带数据（角色变即重取——脱敏在服务端做）；角色切换重置导航态。
  useEffect(() => {
    let cancelled = false;
    setVitals(null);
    setVitalsErr(false);
    setStage({ view: "wall" });
    setDetail(null);
    setActiveKey(null);
    setCard(null);
    fetchVitals(role)
      .then((v) => !cancelled && setVitals(v))
      .catch(() => !cancelled && setVitalsErr(true));
    return () => {
      cancelled = true;
    };
  }, [role]);

  // 本体关系清单（对象卡 links 列表来源，与角色无关，取一次）
  useEffect(() => {
    let cancelled = false;
    fetchOntologySummary(role)
      .then((o) => !cancelled && setLinks(o.links))
      .catch(() => {
        /* 对象卡打开时若无 links 仅少了关系区，不阻断主画面 */
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // —— 导航（每次切换收起右栏详情，回到 AI 工作流）——
  const goWall = () => {
    setStage({ view: "wall" });
    closeDetail();
  };
  const goZone = (zoneId: ZoneId) => {
    setStage({ view: "zone", zoneId });
    closeDetail();
  };
  const goMap = () => {
    setStage({ view: "map" });
    closeDetail();
  };
  const goLane = (lane: Lane) => {
    setStage({ view: "lane", lane });
    closeDetail();
  };

  // —— 下钻：风险类 → 右栏影响面板；对象类 → 对象卡抽屉 ——
  const handleDrill = (t: DrillTarget, key: string) => {
    if (t.kind === "risk") {
      setDetail({
        title: t.title,
        subtitle: t.subtitle,
        alerts: [{ risk_event_id: t.riskId, rule_id: "", type: "", severity: "" }],
        actionHint: t.actionHint,
        decision: t.decision, // 待拍板提案 → 影响面板动作区渲染批准/驳回（A-1）
      });
      setActiveKey(key);
    } else {
      setCard(t.ref);
    }
  };

  const activeZone = stage.view === "zone" && vitals ? vitals.zones.find((z) => z.zone === stage.zoneId) ?? null : null;

  return (
    <div className="cockpit">
      {vitalsErr && (
        <div className="cp-degrade">
          API 未连接——数据降级。启动：<span className="num">ONTOLOGY_DB=data/simworld.sqlite uvicorn apps.api.main:app --port 8100</span>
          （详见 apps/cockpit/README.md）
        </div>
      )}
      <TopBar world={vitals?.world ?? null} clock={vitals?.clock ?? null} role={role} onRole={setRole} />

      <div className="cp-stage">
        <div className="cp-center">
          {stage.view === "map" ? (
            <RouteMap role={role} onLane={goLane} onBack={goWall} />
          ) : stage.view === "lane" ? (
            <LaneQueue lane={stage.lane} onWall={goWall} onMap={goMap} onDrill={handleDrill} activeKey={activeKey} />
          ) : !vitals ? (
            <div className="cp-fill-msg">{vitalsErr ? "体征带不可用——确认 API 已启动" : "指挥墙加载中…"}</div>
          ) : stage.view === "zone" && activeZone ? (
            <ZoneQueue zone={activeZone} onBack={goWall} onMap={goMap} onDrill={handleDrill} activeKey={activeKey} />
          ) : (
            <CommandWall zones={vitals.zones} onZone={goZone} onMap={goMap} />
          )}
        </div>
        <div className="cp-side">
          {detail ? (
            <ImpactPanel
              focus={detail}
              role={role}
              onOpenObject={setCard}
              onClose={closeDetail}
              onActed={() => {
                refreshVitalsData();
                closeDetail();
              }}
            />
          ) : (
            <AiWorkflow role={role} onOpenObject={setCard} />
          )}
        </div>
      </div>

      {card && <ObjectCard target={card} role={role} links={links} onOpenObject={setCard} onClose={() => setCard(null)} />}
    </div>
  );
}
