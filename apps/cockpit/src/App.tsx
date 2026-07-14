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
import VitalsBand from "./components/VitalsBand";
import Panorama from "./views/Panorama";
import ZoneDetail from "./views/ZoneDetail";
import AiWorkflow from "./views/AiWorkflow";
import ObjectCard from "./views/ObjectCard";
import ImpactPanel from "./views/ImpactPanel";
import type { PanoSelection } from "./views/panoramaModel";

// 驾驶舱首屏编排（V9 视觉升级）：顶栏 + 公司体征带（七掌控区）+ 主舞台（中央等距全景/区详情
// 60% · 右栏 AI 工作流 40%，选中异常组块时右栏切为影响分析面板）+ 对象卡抽屉（第二层）。
// 角色（X-Role）是唯一全局开关：切换即令 vitals/panorama/ai-flow 全部按新角色重取（脱敏+粒度）。
export default function App() {
  const [role, setRole] = useState<Role>("manager");
  const [vitals, setVitals] = useState<Vitals | null>(null);
  const [vitalsErr, setVitalsErr] = useState(false);
  const [zoneId, setZoneId] = useState<ZoneId | null>(null); // null = 全景；否则该区展开
  const [card, setCard] = useState<ObjectRef | null>(null);
  const [links, setLinks] = useState<OntologyLink[]>([]);
  const [selection, setSelection] = useState<PanoSelection | null>(null); // 全景选中组块（驱动影响面板）

  // 体征带数据（角色变即重取——脱敏在服务端做）
  useEffect(() => {
    let cancelled = false;
    setVitals(null);
    setVitalsErr(false);
    setSelection(null); // 角色切换重置全景选中（粒度/脱敏会变）
    fetchVitals(role)
      .then((v) => !cancelled && setVitals(v))
      .catch(() => !cancelled && setVitalsErr(true));
    return () => {
      cancelled = true;
    };
  }, [role]);

  // 本体关系清单（对象卡的 links 列表来源，与角色无关，取一次）
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

  const activeZone = zoneId && vitals ? vitals.zones.find((z) => z.zone === zoneId) ?? null : null;

  return (
    <div className="cockpit">
      {vitalsErr && (
        <div className="cp-degrade">
          API 未连接——数据降级。启动：<span className="num">ONTOLOGY_DB=data/simworld.sqlite uvicorn apps.api.main:app --port 8100</span>
          （详见 apps/cockpit/README.md）
        </div>
      )}
      <TopBar world={vitals?.world ?? null} clock={vitals?.clock ?? null} role={role} onRole={setRole} />

      {vitals ? (
        <VitalsBand zones={vitals.zones} activeZone={zoneId} onSelect={(z) => setZoneId(z)} />
      ) : (
        !vitalsErr && <div className="cp-degrade" style={{ background: "var(--bg-1)", color: "var(--ink-2)" }}>体征带加载中…</div>
      )}

      <div className="cp-stage">
        <div className="cp-center">
          {activeZone ? (
            <ZoneDetail zone={activeZone} onBack={() => setZoneId(null)} />
          ) : (
            <Panorama
              role={role}
              selectedId={selection?.block.id ?? null}
              onSelect={setSelection}
              onOpenObject={setCard}
            />
          )}
        </div>
        <div className="cp-side">
          {selection && !activeZone ? (
            <ImpactPanel
              selection={selection}
              role={role}
              onOpenObject={setCard}
              onClose={() => setSelection(null)}
            />
          ) : (
            <AiWorkflow role={role} onOpenObject={setCard} />
          )}
        </div>
      </div>

      {card && (
        <ObjectCard
          target={card}
          role={role}
          links={links}
          onOpenObject={setCard}
          onClose={() => setCard(null)}
        />
      )}
    </div>
  );
}
